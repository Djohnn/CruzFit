from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum


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

    def receita_operacional(
        self,
        *,
        start_date: object | None = None,
        end_date: object | None = None,
    ) -> Decimal:
        """Somar somente Pix confirmado e liquidações líquidas de cartão."""
        credits = self.lancamentos.filter(
            direcao=LancamentoFinanceiro.Direcao.CREDITO,
        ).filter(
            Q(recebimento_pix__isnull=False) | Q(deposito_cartao__isnull=False),
        )
        refunds = self.lancamentos.filter(
            direcao=LancamentoFinanceiro.Direcao.DEBITO,
            estorno_recebimento_cartao__isnull=False,
        )
        if start_date is not None:
            credits = credits.filter(data_movimento__gte=start_date)
            refunds = refunds.filter(data_movimento__gte=start_date)
        if end_date is not None:
            credits = credits.filter(data_movimento__lte=end_date)
            refunds = refunds.filter(data_movimento__lte=end_date)
        credit_total = credits.aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
        refund_total = refunds.aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
        total = credit_total - refund_total
        return total.quantize(Decimal('0.01'))


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


class RecebimentoPixFinanceiro(models.Model):
    """Crédito financeiro único para um pagamento Pix confirmado."""

    pagamento_origem = models.OneToOneField(
        'caixa.PagamentoVenda',
        on_delete=models.PROTECT,
        related_name='recebimento_pix_financeiro',
    )
    conta = models.ForeignKey(
        ContaFinanceira,
        on_delete=models.PROTECT,
        related_name='recebimentos_pix',
    )
    lancamento = models.OneToOneField(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        related_name='recebimento_pix',
    )
    valor = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    data_confirmacao = models.DateField()
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='recebimentos_pix_financeiros',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-data_confirmacao', '-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor__gt=0),
                name='financeiro_pix_valor_positivo',
            ),
        ]

    def __str__(self):
        return f'Pix de R$ {self.valor}'


class RecebivelCartao(models.Model):
    """Parcela aprovada no Caixa e ainda não liquidada no Financeiro."""

    pagamento_origem = models.OneToOneField(
        'caixa.PagamentoVenda',
        on_delete=models.PROTECT,
        related_name='recebivel_cartao',
    )
    valor_bruto = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    data_aprovacao = models.DateField()
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='recebiveis_cartao_registrados',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    cancelado = models.BooleanField(default=False)
    cancelado_em = models.DateField(null=True, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-data_aprovacao', '-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor_bruto__gt=0),
                name='financeiro_recebivel_cartao_positivo',
            ),
        ]

    def __str__(self):
        return f'Cartão a receber de R$ {self.valor_bruto}'


class DepositoCartao(models.Model):
    """Depósito manual de uma ou mais parcelas de cartão."""

    conta = models.ForeignKey(
        ContaFinanceira,
        on_delete=models.PROTECT,
        related_name='depositos_cartao',
    )
    lancamento = models.OneToOneField(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        related_name='deposito_cartao',
    )
    data_deposito = models.DateField()
    valor_bruto = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    taxas = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    valor_liquido = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    referencia_operadora = models.CharField(max_length=120)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='depositos_cartao_registrados',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-data_deposito', '-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor_bruto__gt=0),
                name='financeiro_deposito_cartao_bruto_positivo',
            ),
            models.CheckConstraint(
                check=models.Q(taxas__gte=0),
                name='financeiro_deposito_cartao_taxas_nao_neg',
            ),
            models.CheckConstraint(
                check=models.Q(valor_liquido__gt=0),
                name='financeiro_deposito_cartao_liquido_positivo',
            ),
            models.CheckConstraint(
                check=models.Q(taxas__lte=models.F('valor_bruto')),
                name='financeiro_deposito_cartao_taxas_ate_bruto',
            ),
        ]

    def __str__(self):
        return f'Depósito de cartão {self.referencia_operadora}'


class LiquidacaoCartao(models.Model):
    """Vínculo imutável de uma parcela de cartão ao seu depósito."""

    deposito = models.ForeignKey(
        DepositoCartao,
        on_delete=models.PROTECT,
        related_name='parcelas',
    )
    recebivel = models.OneToOneField(
        RecebivelCartao,
        on_delete=models.PROTECT,
        related_name='liquidacao',
    )
    valor_bruto = models.DecimalField(max_digits=14, decimal_places=2)
    taxas = models.DecimalField(max_digits=14, decimal_places=2)
    valor_liquido = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ('pk',)
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor_bruto__gt=0),
                name='financeiro_liquidacao_cartao_bruto_positivo',
            ),
            models.CheckConstraint(
                check=models.Q(taxas__gte=0),
                name='financeiro_liquidacao_cartao_taxas_nao_neg',
            ),
            models.CheckConstraint(
                check=models.Q(valor_liquido__gt=0),
                name='financeiro_liquidacao_cartao_liquido_positivo',
            ),
        ]

    def __str__(self):
        return f'Parcela liquidada #{self.recebivel_id}'


class EstornoRecebimentoCartao(models.Model):
    """Restituição de cartão vinculada à parcela e, se liquidada, ao débito."""

    pagamento_restituicao = models.OneToOneField(
        'caixa.PagamentoRestituicao',
        on_delete=models.PROTECT,
        related_name='estorno_recebimento_cartao',
    )
    recebivel = models.ForeignKey(
        RecebivelCartao,
        on_delete=models.PROTECT,
        related_name='estornos_recebimento',
    )
    lancamento = models.OneToOneField(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='estorno_recebimento_cartao',
    )
    valor = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    data_movimento = models.DateField()
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='estornos_recebimento_cartao',
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
                name='financeiro_estorno_receb_cartao_positivo',
            ),
        ]

    def __str__(self):
        return f'Estorno de cartão #{self.pagamento_restituicao_id}'


class ContaPagar(models.Model):
    """Obrigação financeira sem movimento até que seja efetivamente paga."""

    class Situacao(models.TextChoices):
        ABERTA = 'ABERTA', 'Aberta'
        PAGA = 'PAGA', 'Paga'

    descricao = models.CharField(max_length=160)
    categoria = models.CharField(max_length=80)
    valor = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    data_vencimento = models.DateField()
    situacao = models.CharField(
        max_length=8,
        choices=Situacao.choices,
        default=Situacao.ABERTA,
    )
    origem_pedido = models.OneToOneField(
        'compras.PedidoCompra',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='conta_pagar',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='contas_pagar_criadas',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('data_vencimento', 'criada_em', 'pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor__gt=0),
                name='financeiro_conta_pagar_valor_positivo',
            ),
        ]

    @property
    def pagamento(self):
        """Mantém o acesso de template ao único pagamento integral vigente."""
        return self.pagamentos.filter(vigente=True).first()

    def __str__(self) -> str:
        return f'{self.descricao} — R$ {self.valor}'


class PagamentoContaPagar(models.Model):
    """Baixa integral histórica, com no máximo uma baixa vigente."""

    conta_pagar = models.ForeignKey(
        ContaPagar,
        on_delete=models.PROTECT,
        related_name='pagamentos',
    )
    conta = models.ForeignKey(
        ContaFinanceira,
        on_delete=models.PROTECT,
        related_name='pagamentos_contas_pagar',
    )
    lancamento = models.OneToOneField(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        related_name='pagamento_conta_pagar',
    )
    data_pagamento = models.DateField()
    valor = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    vigente = models.BooleanField(default=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='pagamentos_contas_pagar',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-data_pagamento', '-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor__gt=0),
                name='financeiro_pagamento_conta_pagar_positivo',
            ),
            models.UniqueConstraint(
                fields=('conta_pagar',),
                condition=models.Q(vigente=True),
                name='financeiro_pagamento_conta_pagar_vigente_unico',
            ),
        ]

    def __str__(self) -> str:
        return f'Pagamento de {self.conta_pagar}'


class EstornoPagamentoContaPagar(models.Model):
    """Reversão de um pagamento que reabre a obrigação original."""

    pagamento = models.OneToOneField(
        PagamentoContaPagar,
        on_delete=models.PROTECT,
        related_name='estorno_pagamento',
    )
    lancamento = models.OneToOneField(
        LancamentoFinanceiro,
        on_delete=models.PROTECT,
        related_name='estorno_pagamento_conta_pagar',
    )
    data_movimento = models.DateField()
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='estornos_pagamentos_contas_pagar',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-data_movimento', '-criada_em', '-pk')

    def __str__(self) -> str:
        return f'Estorno do pagamento #{self.pagamento_id}'


class FechamentoMensal(models.Model):
    """Snapshot versionado da posição financeira ao fim de um mês."""

    class Situacao(models.TextChoices):
        CONFIRMADO = 'CONFIRMADO', 'Confirmado'
        REABERTO = 'REABERTO', 'Reaberto'

    class Classificacao(models.TextChoices):
        MAL = 'MAL', 'Mal'
        NO_LIMITE = 'NO_LIMITE', 'No limite'
        BEM = 'BEM', 'Bem'

    mes_referencia = models.DateField()
    versao = models.PositiveIntegerField()
    situacao = models.CharField(
        max_length=10,
        choices=Situacao.choices,
        default=Situacao.CONFIRMADO,
    )
    vigente = models.BooleanField(default=True)
    margem = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    entradas_operacionais_liquidas = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    obrigacoes_vencimento_mes = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    pagamentos_efetivos = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    obrigacoes_pagas = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    obrigacoes_abertas = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    vencidas_anteriores = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    cartao_a_receber = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    cobertura = models.DecimalField(max_digits=14, decimal_places=2)
    classificacao = models.CharField(
        max_length=10,
        choices=Classificacao.choices,
    )
    assinatura_previa = models.CharField(max_length=64)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='fechamentos_mensais_financeiros',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    confirmado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-mes_referencia', '-versao', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(margem__gte=0),
                name='financeiro_fechamento_margem_nao_negativa',
            ),
            models.UniqueConstraint(
                fields=('mes_referencia',),
                condition=models.Q(vigente=True),
                name='financeiro_fechamento_vigente_unico_mes',
            ),
        ]

    def __str__(self) -> str:
        return f'Fechamento {self.mes_referencia:%Y-%m} v{self.versao}'


class FechamentoMensalConta(models.Model):
    """Saldos imutáveis de cada conta no snapshot mensal."""

    fechamento = models.ForeignKey(
        FechamentoMensal,
        on_delete=models.PROTECT,
        related_name='contas',
    )
    conta = models.ForeignKey(
        ContaFinanceira,
        on_delete=models.PROTECT,
        related_name='fechamentos_mensais',
    )
    saldo_inicial = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_final = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ('conta__nome', 'conta_id')
        constraints = [
            models.UniqueConstraint(
                fields=('fechamento', 'conta'),
                name='financeiro_fechamento_conta_unico',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.fechamento} — {self.conta}'


class ReaberturaFechamentoMensal(models.Model):
    """Motivo auditável para encerrar a vigência de um fechamento."""

    fechamento = models.OneToOneField(
        FechamentoMensal,
        on_delete=models.PROTECT,
        related_name='reabertura',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reaberturas_fechamentos_mensais',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-criada_em', '-pk')

    def __str__(self) -> str:
        return f'Reabertura de {self.fechamento}'


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
