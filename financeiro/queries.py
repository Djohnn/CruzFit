from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from caixa.models import PagamentoVenda

from .models import ContaFinanceira, LancamentoFinanceiro, RecebivelCartao
from .services import calcular_saldo


def listar_contas() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for conta in ContaFinanceira.objects.filter(ativo=True).order_by('nome', 'pk'):
        rows.append({
            'conta': conta,
            'saldo': calcular_saldo(conta),
            'receita_operacional': conta.receita_operacional(),
        })
    return rows


def pix_pendentes():
    return PagamentoVenda.objects.filter(
        forma='PIX',
        recebimento_pix_financeiro__isnull=True,
    ).select_related('recebimento__venda').order_by('recebimento__criada_em', 'pk')


def recebiveis_cartao_pendentes():
    return RecebivelCartao.objects.filter(
        cancelado=False,
        liquidacao__isnull=True,
    ).select_related(
        'pagamento_origem__recebimento__venda',
    ).order_by('data_aprovacao', 'pk')


def receita_operacional_consolidada() -> Decimal:
    total = sum(
        (
            conta.receita_operacional()
            for conta in ContaFinanceira.objects.filter(ativo=True)
        ),
        Decimal('0.00'),
    )
    return total.quantize(Decimal('0.01'))


def historico_conta(conta: ContaFinanceira):
    return conta.lancamentos.select_related('responsavel').order_by(
        '-data_movimento', '-criado_em', '-pk',
    )


def saldo_consolidado() -> Decimal:
    total_initial = ContaFinanceira.objects.filter(ativo=True).aggregate(
        total=Sum('saldo_inicial'),
    )['total'] or Decimal('0.00')
    credits = LancamentoFinanceiro.objects.filter(
        conta__ativo=True,
        direcao=LancamentoFinanceiro.Direcao.CREDITO,
    ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
    debits = LancamentoFinanceiro.objects.filter(
        conta__ativo=True,
        direcao=LancamentoFinanceiro.Direcao.DEBITO,
    ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
    return (total_initial + credits - debits).quantize(Decimal('0.01'))
