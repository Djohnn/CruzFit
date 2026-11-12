from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class Venda(models.Model):
    MEIOS_PAGAMENTO_CHOICES = (
        ('DINHEIRO', 'Dinheiro'),
        ('PIX', 'Pix'),
        ('CARTAO', 'Cartão'),
    )
    FORMA_PAGAMENTO_CHOICES = (
        *MEIOS_PAGAMENTO_CHOICES,
        ('MISTO', 'Misto'),
        ('SEM_PAGAMENTO', 'Sem cobrança'),
    )

    criada_em = models.DateTimeField(auto_now_add=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='vendas',
    )
    baixa = models.OneToOneField(
        'estoque.BaixaEstoque',
        on_delete=models.PROTECT,
        related_name='venda',
    )
    forma_pagamento = models.CharField(
        max_length=16,
        choices=FORMA_PAGAMENTO_CHOICES,
    )
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    desconto = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        db_table = "estoque_venda"
        ordering = ('-criada_em', '-pk')

    def __str__(self) -> str:
        return f'Venda #{self.pk}'


class ItemVenda(models.Model):
    venda = models.ForeignKey(
        Venda,
        on_delete=models.CASCADE,
        related_name='itens',
    )
    produto = models.ForeignKey('estoque.Produto', on_delete=models.PROTECT)
    quantidade = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    preco_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    total_liquido = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )

    class Meta:
        db_table = "estoque_itemvenda"
        constraints = [
            models.UniqueConstraint(
                fields=('venda', 'produto'),
                name='unique_produto_por_venda',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.produto.nome} - {self.quantidade:g}'


class VendaCancelamento(models.Model):
    venda = models.OneToOneField(
        Venda,
        on_delete=models.PROTECT,
        related_name='cancelamento',
    )
    entrada = models.OneToOneField(
        'estoque.EntradaEstoque',
        on_delete=models.PROTECT,
        related_name='cancelamento_venda',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='cancelamentos_venda',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    valor_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )

    class Meta:
        db_table = "estoque_vendacancelamento"
        ordering = ('-criada_em', '-pk')


class VendaDevolucao(models.Model):
    venda = models.ForeignKey(
        Venda,
        on_delete=models.PROTECT,
        related_name='devolucoes',
    )
    entrada = models.OneToOneField(
        'estoque.EntradaEstoque',
        on_delete=models.PROTECT,
        related_name='devolucao_venda',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='devolucoes_venda',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    valor_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )

    class Meta:
        db_table = "estoque_vendadevolucao"
        ordering = ('-criada_em', '-pk')


class ItemVendaDevolucao(models.Model):
    devolucao = models.ForeignKey(
        VendaDevolucao,
        on_delete=models.CASCADE,
        related_name='itens',
    )
    item_venda = models.ForeignKey(
        ItemVenda,
        on_delete=models.PROTECT,
        related_name='devolucoes',
    )
    produto = models.ForeignKey('estoque.Produto', on_delete=models.PROTECT)
    quantidade = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    preco_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )

    class Meta:
        db_table = "estoque_itemvendadevolucao"
        constraints = [
            models.UniqueConstraint(
                fields=('devolucao', 'item_venda'),
                name='unique_item_por_devolucao_venda',
            ),
        ]


class TrocaImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Registros de troca confirmados são imutáveis.')

    def delete(self):
        raise ValidationError('Registros de troca confirmados são imutáveis.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('Registros de troca confirmados são imutáveis.')


class TrocaImmutableManager(models.Manager):
    def get_queryset(self) -> TrocaImmutableQuerySet:
        return TrocaImmutableQuerySet(
            self.model, using=self._db, hints=self._hints,
        )


class VendaTroca(models.Model):
    """Confirmed exchange aggregate linked to one original sale."""

    venda = models.ForeignKey(
        Venda, on_delete=models.PROTECT, related_name='trocas',
    )
    sessao = models.ForeignKey(
        'caixa.SessaoCaixa', on_delete=models.PROTECT, related_name='trocas',
    )
    entrada = models.OneToOneField(
        'estoque.EntradaEstoque', on_delete=models.PROTECT,
        related_name='troca_entrada',
    )
    baixa = models.OneToOneField(
        'estoque.BaixaEstoque', on_delete=models.PROTECT,
        related_name='troca_baixa',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='trocas_confirmadas',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    credito_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    valor_novos = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    diferenca = models.DecimalField(max_digits=12, decimal_places=2)
    versao_caixa = models.PositiveIntegerField()
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    objects = TrocaImmutableManager()

    class Meta:
        ordering = ('-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(credito_total__gte=0),
                name='troca_credito_nao_negativo',
            ),
            models.CheckConstraint(
                check=models.Q(valor_novos__gte=0),
                name='troca_novos_nao_negativo',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Registros de troca confirmados são imutáveis.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Registros de troca confirmados são imutáveis.')

    def __str__(self):
        return f'Troca da venda #{self.venda_id}'


class ItemTrocaOrigem(models.Model):
    troca = models.ForeignKey(
        VendaTroca, on_delete=models.PROTECT, related_name='itens_origem',
    )
    item_venda = models.ForeignKey(
        ItemVenda, on_delete=models.PROTECT, related_name='trocas_origem',
    )
    produto = models.ForeignKey(
        'estoque.Produto', on_delete=models.PROTECT,
    )
    quantidade = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    preco_unitario = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    credito_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )

    objects = TrocaImmutableManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('troca', 'item_venda'),
                name='unique_item_origem_por_troca',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Registros de troca confirmados são imutáveis.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Registros de troca confirmados são imutáveis.')


class ItemTrocaDestino(models.Model):
    troca = models.ForeignKey(
        VendaTroca, on_delete=models.PROTECT, related_name='itens_destino',
    )
    produto = models.ForeignKey(
        'estoque.Produto', on_delete=models.PROTECT,
    )
    quantidade = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    preco_unitario = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )

    objects = TrocaImmutableManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('troca', 'produto'),
                name='unique_item_destino_por_troca',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Registros de troca confirmados são imutáveis.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Registros de troca confirmados são imutáveis.')
