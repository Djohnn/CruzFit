from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class ContaFinanceira(models.Model):
    nome = models.CharField(max_length=120)
    saldo_inicial = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    data_corte_financeiro = models.DateField()
    ativo = models.BooleanField(default=True)
    responsavel_criacao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='contas_financeiras_criadas',
    )
    motivo_criacao = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('nome', 'pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(saldo_inicial__gte=0),
                name='financeiro_conta_saldo_inicial_nao_negativo',
            ),
        ]

    def __str__(self) -> str:
        return self.nome

    def receita_operacional(self) -> Decimal:
        """Etapa 1 registra somente fontes não operacionais."""
        return Decimal('0.00')


class LancamentoFinanceiro(models.Model):
    class Direcao(models.TextChoices):
        CREDITO = 'CREDITO', 'Crédito'
        DEBITO = 'DEBITO', 'Débito'

    conta = models.ForeignKey(
        ContaFinanceira,
        on_delete=models.PROTECT,
        related_name='lancamentos',
    )
    direcao = models.CharField(max_length=7, choices=Direcao.choices)
    valor = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    data_movimento = models.DateField()
    descricao = models.CharField(max_length=160)
    motivo = models.CharField(max_length=500)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='lancamentos_financeiros',
    )
    estorno_de = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='estornos',
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-data_movimento', '-criado_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor__gt=0),
                name='financeiro_lancamento_valor_positivo',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.get_direcao_display()} de R$ {self.valor}'


class AporteFinanceiro(models.Model):
    conta = models.ForeignKey(
        ContaFinanceira,
        on_delete=models.PROTECT,
        related_name='aportes',
    )
    lancamento = models.OneToOneField(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        related_name='aporte',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='aportes_financeiros',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-criada_em', '-pk')

    def __str__(self) -> str:
        return f'Aporte de {self.lancamento.valor}'


class TransferenciaFinanceira(models.Model):
    origem = models.ForeignKey(
        ContaFinanceira,
        on_delete=models.PROTECT,
        related_name='transferencias_enviadas',
    )
    destino = models.ForeignKey(
        ContaFinanceira,
        on_delete=models.PROTECT,
        related_name='transferencias_recebidas',
    )
    valor = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    data_movimento = models.DateField()
    debito = models.OneToOneField(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        related_name='transferencia_debito',
    )
    credito = models.OneToOneField(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        related_name='transferencia_credito',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='transferencias_financeiras',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-data_movimento', '-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor__gt=0),
                name='financeiro_transferencia_valor_positivo',
            ),
            models.CheckConstraint(
                check=~models.Q(origem=models.F('destino')),
                name='financeiro_transferencia_contas_distintas',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.origem} → {self.destino}'


class EstornoFinanceiro(models.Model):
    lancamento_original = models.ForeignKey(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        related_name='estorno_operacoes',
    )
    lancamento = models.OneToOneField(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        related_name='estorno',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='estornos_financeiros',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=~models.Q(lancamento_original=models.F('lancamento')),
                name='financeiro_estorno_origem_distinta',
            ),
        ]

    def __str__(self) -> str:
        return f'Estorno do lançamento #{self.lancamento_original_id}'


class EventoAuditoriaFinanceiro(models.Model):
    acao = models.CharField(max_length=80)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='auditorias_financeiras',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    dados = models.JSONField(default=dict)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-criado_em', '-pk')

    def __str__(self) -> str:
        return f'{self.acao} ({self.chave_idempotencia})'
