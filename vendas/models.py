from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Venda(models.Model):
    FORMA_PAGAMENTO_CHOICES = (
        ('DINHEIRO', 'Dinheiro'),
        ('PIX', 'Pix'),
        ('CARTAO', 'Cartão'),
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
        max_length=10,
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
