"""Registros próprios de Caixa; a abertura preserva autoria e pedido original."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class SessaoCaixa(models.Model):
    aberta_em = models.DateTimeField(auto_now_add=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='sessoes_caixa_abertas',
    )
    fundo_inicial = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    motivo = models.CharField(max_length=500)
    aberta = models.BooleanField(default=True)
    versao = models.PositiveIntegerField(default=0)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-aberta_em', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=['aberta'], condition=models.Q(aberta=True),
                name='caixa_uma_sessao_aberta',
            ),
            models.CheckConstraint(
                check=models.Q(fundo_inicial__gte=0),
                name='caixa_fundo_nao_negativo',
            ),
        ]

    def __str__(self):
        return f'Caixa #{self.pk}'

    @property
    def fechamento_atrasado(self):
        return bool(
            self.aberta and self.aberta_em
            and timezone.localdate(self.aberta_em) < timezone.localdate()
        )


class RecebimentoVenda(models.Model):
    """Confirma o total líquido de uma venda em uma sessão específica."""

    sessao = models.ForeignKey(
        SessaoCaixa, on_delete=models.PROTECT, related_name='recebimentos',
    )
    venda = models.OneToOneField(
        'vendas.Venda', on_delete=models.PROTECT, related_name='recebimento_caixa',
    )
    valor_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='recebimentos_caixa',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor_total__gte=0),
                name='caixa_receb_total_nao_neg',
            ),
        ]

    def __str__(self):
        return f'Recebimento da venda #{self.venda_id}'


class PagamentoVenda(models.Model):
    """Parcela consolidada por forma; troco só é válido no pagamento em dinheiro."""

    FORMA_PAGAMENTO_CHOICES = (
        ('DINHEIRO', 'Dinheiro'),
        ('PIX', 'Pix'),
        ('CARTAO', 'Cartão'),
    )
    MODALIDADES_CARTAO = (
        ('CREDITO', 'Crédito'),
        ('DEBITO', 'Débito'),
    )
    OPCOES_CARTAO = {
        'CARTAO_CREDITO': 'CREDITO',
        'CARTAO_DEBITO': 'DEBITO',
    }
    FORMAS_CHECKOUT = (
        ('DINHEIRO', 'Dinheiro'),
        ('PIX', 'Pix'),
        ('CARTAO_CREDITO', 'Cartão de crédito'),
        ('CARTAO_DEBITO', 'Cartão de débito'),
    )

    recebimento = models.ForeignKey(
        RecebimentoVenda, on_delete=models.PROTECT, related_name='pagamentos',
    )
    forma = models.CharField(max_length=10, choices=FORMA_PAGAMENTO_CHOICES)
    modalidade_cartao = models.CharField(max_length=7, choices=MODALIDADES_CARTAO, blank=True)
    valor_aplicado = models.DecimalField(max_digits=12, decimal_places=2)
    valor_entregue = models.DecimalField(max_digits=12, decimal_places=2)
    troco = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ('forma', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('recebimento', 'forma', 'modalidade_cartao'),
                name='caixa_pg_forma_modalidade_unica',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(modalidade_cartao='')
                    | models.Q(forma='CARTAO', modalidade_cartao__in=('CREDITO', 'DEBITO'))
                ),
                name='caixa_pg_modalidade_valida',
            ),
            models.CheckConstraint(
                check=models.Q(valor_aplicado__gt=0),
                name='caixa_pg_aplicado_positivo',
            ),
            models.CheckConstraint(
                check=models.Q(valor_entregue__gte=0),
                name='caixa_pg_entregue_nao_neg',
            ),
            models.CheckConstraint(
                check=models.Q(troco__gte=0),
                name='caixa_pg_troco_nao_neg',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        forma='DINHEIRO',
                        valor_entregue=models.F('valor_aplicado') + models.F('troco'),
                    )
                    | (
                        ~models.Q(forma='DINHEIRO')
                        & models.Q(valor_entregue=models.F('valor_aplicado'), troco=0)
                    )
                ),
                name='caixa_pg_troco_por_forma',
            ),
        ]

    def __str__(self):
        return f'{self.get_forma_display()} - {self.valor_aplicado}'


class SuprimentoCaixa(models.Model):
    """Entrada física de dinheiro na gaveta, vinculada a uma sessão."""

    sessao = models.ForeignKey(
        SessaoCaixa, on_delete=models.PROTECT, related_name='suprimentos',
    )
    valor = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='suprimentos_caixa',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor__gt=0), name='caixa_suprimento_positivo',
            ),
        ]

    def __str__(self):
        return f'Suprimento de R$ {self.valor}'


class SangriaCaixa(models.Model):
    """Retirada física de dinheiro da gaveta, vinculada a uma sessão."""

    sessao = models.ForeignKey(
        SessaoCaixa, on_delete=models.PROTECT, related_name='sangrias',
    )
    valor = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='sangrias_caixa',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor__gt=0), name='caixa_sangria_positiva',
            ),
        ]

    def __str__(self):
        return f'Sangria de R$ {self.valor}'


class PendenciaCancelamentoVenda(models.Model):
    """Refund obligation created by a received sale cancellation."""

    cancelamento = models.OneToOneField(
        'vendas.VendaCancelamento', on_delete=models.PROTECT,
        related_name='pendencia_restituicao_caixa',
    )
    recebimento = models.ForeignKey(
        RecebimentoVenda, on_delete=models.PROTECT,
        related_name='pendencias_cancelamento',
    )
    sessao_origem = models.ForeignKey(
        SessaoCaixa, on_delete=models.PROTECT,
        related_name='pendencias_cancelamento_origem',
    )
    valor_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='pendencias_cancelamento_caixa',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor_total__gt=0),
                name='caixa_pend_canc_positivo',
            ),
        ]

    def __str__(self):
        return f'Restituição pendente do cancelamento #{self.cancelamento_id}'


class PendenciaDevolucaoVenda(models.Model):
    """Refund obligation created by a received sale return."""

    devolucao = models.OneToOneField(
        'vendas.VendaDevolucao', on_delete=models.PROTECT,
        related_name='pendencia_restituicao_caixa',
    )
    recebimento = models.ForeignKey(
        RecebimentoVenda, on_delete=models.PROTECT,
        related_name='pendencias_devolucao',
    )
    sessao_origem = models.ForeignKey(
        SessaoCaixa, on_delete=models.PROTECT,
        related_name='pendencias_devolucao_origem',
    )
    valor_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='pendencias_devolucao_caixa',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor_total__gt=0),
                name='caixa_pend_dev_positivo',
            ),
        ]

    def __str__(self):
        return f'Restituição pendente da devolução #{self.devolucao_id}'


class RestituicaoVenda(models.Model):
    """Financial refund paid from a specific session for one pending reversal."""

    sessao = models.ForeignKey(
        SessaoCaixa, on_delete=models.PROTECT, related_name='restituicoes',
    )
    pendencia_cancelamento = models.OneToOneField(
        PendenciaCancelamentoVenda, on_delete=models.PROTECT, null=True, blank=True,
        related_name='restituicao',
    )
    pendencia_devolucao = models.OneToOneField(
        PendenciaDevolucaoVenda, on_delete=models.PROTECT, null=True, blank=True,
        related_name='restituicao',
    )
    valor_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='restituicoes_caixa',
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    confirmacao_manual = models.BooleanField(default=False)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-criada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor_total__gt=0),
                name='caixa_restituicao_positiva',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        pendencia_cancelamento__isnull=False,
                        pendencia_devolucao__isnull=True,
                    )
                    | models.Q(
                        pendencia_cancelamento__isnull=True,
                        pendencia_devolucao__isnull=False,
                    )
                ),
                name='caixa_restituicao_uma_origem',
            ),
        ]

    def __str__(self):
        return f'Restituição #{self.pk} de R$ {self.valor_total}'


class PagamentoRestituicao(models.Model):
    """Amount refunded against one original payment line and its original form."""

    restituicao = models.ForeignKey(
        RestituicaoVenda, on_delete=models.PROTECT, related_name='pagamentos',
    )
    pagamento_original = models.ForeignKey(
        PagamentoVenda, on_delete=models.PROTECT,
        related_name='restituicoes',
    )
    valor = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )

    class Meta:
        ordering = ('pagamento_original_id', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('restituicao', 'pagamento_original'),
                name='caixa_restit_pg_orig_unica',
            ),
            models.CheckConstraint(
                check=models.Q(valor__gt=0),
                name='caixa_restit_pg_positivo',
            ),
        ]

    def __str__(self):
        return f'Restituído {self.valor} via pagamento #{self.pagamento_original_id}'


class FechamentoImutavelQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('O snapshot de fechamento é imutável.')

    def delete(self):
        raise ValidationError('O snapshot de fechamento é imutável.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('O snapshot de fechamento é imutável.')


class FechamentoImutavelManager(models.Manager):
    def get_queryset(self) -> FechamentoImutavelQuerySet:
        return FechamentoImutavelQuerySet(
            self.model, using=self._db, hints=self._hints,
        )


class CorteCaixaQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('O registro de corte do Caixa é imutável.')

    def delete(self):
        raise ValidationError('O registro de corte do Caixa é imutável.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('O registro de corte do Caixa é imutável.')


class CorteCaixaManager(models.Manager):
    def get_queryset(self) -> CorteCaixaQuerySet:
        return CorteCaixaQuerySet(
            self.model, using=self._db, hints=self._hints,
        )


class CorteCaixa(models.Model):
    """Audited, permanent marker that makes Caixa mandatory for new sales."""

    codigo = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    ativada_em = models.DateTimeField(auto_now_add=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='cortes_caixa',
    )
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    objects = CorteCaixaManager()

    class Meta:
        ordering = ('ativada_em', 'pk')
        constraints = [
            models.CheckConstraint(check=models.Q(codigo=1), name='caixa_corte_unico_codigo'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('O registro de corte do Caixa é imutável.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('O registro de corte do Caixa é imutável.')

    def __str__(self):
        return f'Corte de ativação do Caixa em {self.ativada_em:%d/%m/%Y %H:%M}'


class FechamentoCaixa(models.Model):
    """Immutable end-of-session cash count and payment-form snapshot."""

    sessao = models.OneToOneField(
        SessaoCaixa, on_delete=models.PROTECT, related_name='fechamento',
    )
    dinheiro_esperado = models.DecimalField(max_digits=12, decimal_places=2)
    dinheiro_contado = models.DecimalField(max_digits=12, decimal_places=2)
    diferenca = models.DecimalField(max_digits=12, decimal_places=2)
    totais_por_forma = models.JSONField(default=dict)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='fechamentos_caixa',
    )
    fechada_em = models.DateTimeField(auto_now_add=True)
    justificativa = models.CharField(max_length=500, blank=True)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    objects = FechamentoImutavelManager()

    class Meta:
        ordering = ('-fechada_em', '-pk')
        constraints = [
            models.CheckConstraint(
                check=models.Q(dinheiro_esperado__gte=0),
                name='caixa_fech_esperado_nao_neg',
            ),
            models.CheckConstraint(
                check=models.Q(dinheiro_contado__gte=0),
                name='caixa_fech_contado_nao_neg',
            ),
            models.CheckConstraint(
                check=models.Q(
                    diferenca=models.F('dinheiro_contado') - models.F('dinheiro_esperado'),
                ),
                name='caixa_fech_diferenca_consistente',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('O snapshot de fechamento é imutável.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('O snapshot de fechamento é imutável.')

    def __str__(self):
        return f'Fechamento da sessão #{self.sessao_id}'
