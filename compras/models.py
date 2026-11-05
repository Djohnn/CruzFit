from contextlib import contextmanager
from contextvars import ContextVar
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

TWO_PLACES = Decimal('0.01')
_AUTHORIZED_APPEND = ContextVar('compras_authorized_append', default=0)


@contextmanager
def authorized_purchase_append():
    token = _AUTHORIZED_APPEND.set(_AUTHORIZED_APPEND.get() + 1)
    try:
        yield
    finally:
        _AUTHORIZED_APPEND.reset(token)


def _append_is_authorized() -> bool:
    return _AUTHORIZED_APPEND.get() > 0


class PurchaseHistoryQuerySet(models.QuerySet):
    immutable_message = 'O histórico confirmado de Compras é imutável.'
    append_message = (
        'A inclusão no histórico de Compras só pode ocorrer pelo fluxo autorizado.'
    )

    def update(self, **kwargs):
        if self.exists():
            raise ValidationError(self.immutable_message)
        return super().update(**kwargs)

    def delete(self):
        if self.exists():
            raise ValidationError(self.immutable_message)
        return super().delete()

    def bulk_update(self, objs, fields, batch_size=None):
        object_ids = [obj.pk for obj in objs if obj.pk is not None]
        if object_ids and self.filter(pk__in=object_ids).exists():
            raise ValidationError(self.immutable_message)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, objs, **kwargs):
        if objs and not _append_is_authorized():
            raise ValidationError(self.append_message)
        return super().bulk_create(objs, **kwargs)


PurchaseHistoryManager = models.Manager.from_queryset(PurchaseHistoryQuerySet)


def _require_authorized_append() -> None:
    if not _append_is_authorized():
        raise ValidationError(
            'A inclusão no histórico de Compras só pode ocorrer pelo fluxo autorizado.',
        )


class PedidoCompra(models.Model):
    class Estado(models.TextChoices):
        RASCUNHO = 'RASCUNHO', 'Rascunho'
        CONFIRMADO = 'CONFIRMADO', 'Confirmado'
        PARCIALMENTE_RECEBIDO = 'PARCIALMENTE_RECEBIDO', 'Parcialmente recebido'
        RECEBIDO = 'RECEBIDO', 'Recebido'
        CANCELADO = 'CANCELADO', 'Cancelado'

    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    fornecedor = models.ForeignKey(
        'estoque.Fornecedor',
        on_delete=models.PROTECT,
        related_name='pedidos_compra',
    )
    fornecedor_nome = models.CharField(max_length=160)
    fornecedor_documento = models.CharField(max_length=14, blank=True, default='')
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='pedidos_compra_criados',
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    confirmado_em = models.DateTimeField(null=True, blank=True)
    observacao = models.TextField(blank=True, default='')
    estado = models.CharField(
        max_length=24,
        choices=Estado.choices,
        default=Estado.RASCUNHO,
    )
    versao = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('-criado_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(versao__gte=0),
                name='compras_pedido_versao_nao_negativa',
            ),
        ]

    def __str__(self):
        return f'Pedido de compra {self.public_id}'

    def quantidade_recebida(self, item):
        total = item.recebimentos.aggregate(total=models.Sum('quantidade'))['total']
        return total or Decimal('0.00')

    def quantidade_pendente(self, item):
        return self.quantidade_recebivel(item)

    def quantidade_cancelada(self, item):
        if self.estado != self.Estado.CANCELADO:
            return Decimal('0.00')
        return max(
            item.quantidade - self.quantidade_recebida(item),
            Decimal('0.00'),
        )

    def quantidade_recebivel(self, item):
        return max(
            item.quantidade
            - self.quantidade_recebida(item)
            - self.quantidade_cancelada(item),
            Decimal('0.00'),
        )

    @property
    def total_contratado(self):
        total = sum((item.total_contratado for item in self.itens.all()), Decimal('0'))
        return total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class ItemPedidoCompra(models.Model):
    pedido = models.ForeignKey(
        PedidoCompra,
        on_delete=models.CASCADE,
        related_name='itens',
    )
    produto = models.ForeignKey(
        'estoque.Produto',
        on_delete=models.PROTECT,
        related_name='itens_pedidos_compra',
    )
    produto_nome = models.CharField(max_length=40)
    produto_codigo_barras = models.CharField(max_length=13, blank=True, default='')
    quantidade = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    custo_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('pedido', 'produto'),
                name='compras_item_produto_unico_por_pedido',
            ),
            models.CheckConstraint(
                check=models.Q(quantidade__gt=0),
                name='compras_item_quantidade_positiva',
            ),
            models.CheckConstraint(
                check=models.Q(custo_unitario__gt=0),
                name='compras_item_custo_positivo',
            ),
        ]

    @property
    def total_contratado(self):
        return (self.quantidade * self.custo_unitario).quantize(
            TWO_PLACES,
            rounding=ROUND_HALF_UP,
        )

    def __str__(self):
        return f'{self.produto_nome} - {self.quantidade:g}'


class RecebimentoCompra(models.Model):
    pedido = models.ForeignKey(
        PedidoCompra,
        on_delete=models.PROTECT,
        related_name='recebimentos',
    )
    entrada = models.OneToOneField(
        'estoque.EntradaEstoque',
        on_delete=models.PROTECT,
        related_name='recebimento_pedido',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='recebimentos_compra',
    )
    recebido_em = models.DateTimeField(auto_now_add=True)
    referencia_documental = models.CharField(max_length=160, blank=True, default='')
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    objects = PurchaseHistoryManager()

    class Meta:
        ordering = ('-recebido_em', '-pk')

    def __str__(self):
        return f'Recebimento do pedido #{self.pedido_id}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('O recebimento confirmado de Compras é imutável.')
        _require_authorized_append()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('O recebimento confirmado de Compras é imutável.')


class ItemRecebimentoCompra(models.Model):
    recebimento = models.ForeignKey(
        RecebimentoCompra,
        on_delete=models.CASCADE,
        related_name='itens',
    )
    item_pedido = models.ForeignKey(
        ItemPedidoCompra,
        on_delete=models.PROTECT,
        related_name='recebimentos',
    )
    quantidade = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    custo_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )

    objects = PurchaseHistoryManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('recebimento', 'item_pedido'),
                name='compras_item_recebimento_unico',
            ),
            models.CheckConstraint(
                check=models.Q(quantidade__gt=0),
                name='compras_item_recebido_positivo',
            ),
            models.CheckConstraint(
                check=models.Q(custo_unitario__gt=0),
                name='compras_item_recebido_custo_positivo',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('O item de recebimento de Compras é imutável.')
        _require_authorized_append()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('O item de recebimento de Compras é imutável.')


class CancelamentoPedidoCompra(models.Model):
    pedido = models.OneToOneField(
        PedidoCompra,
        on_delete=models.PROTECT,
        related_name='cancelamento',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='cancelamentos_pedido_compra',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-criada_em', '-pk')


class EventoPedidoCompra(models.Model):
    class Operacao(models.TextChoices):
        CRIACAO = 'CRIACAO', 'Criação'
        EDICAO = 'EDICAO', 'Edição'
        CONFIRMACAO = 'CONFIRMACAO', 'Confirmação'
        RECEBIMENTO = 'RECEBIMENTO', 'Recebimento'
        CANCELAMENTO = 'CANCELAMENTO', 'Cancelamento'

    pedido = models.ForeignKey(
        PedidoCompra,
        on_delete=models.PROTECT,
        related_name='eventos',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='eventos_pedido_compra',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    operacao = models.CharField(max_length=16, choices=Operacao.choices)
    motivo = models.CharField(max_length=500, blank=True, default='')
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    dados = models.JSONField(default=dict)

    objects = PurchaseHistoryManager()

    class Meta:
        ordering = ('-criada_em', '-pk')

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('O evento de Compras é append-only e imutável.')
        _require_authorized_append()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('O evento de Compras é append-only e imutável.')
