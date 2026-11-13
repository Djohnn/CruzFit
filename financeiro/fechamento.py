"""Avisos e trava dos lançamentos manuais do Financeiro."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from django.db.models import Min
from django.utils import timezone

from .models import ContaFinanceira, FechamentoMensal


@dataclass(frozen=True)
class AvisoFechamento:
    mes: date
    dias: int = 0
    pendente: bool = False


def mes_pendente(hoje: date) -> date | None:
    mes_atual = hoje.replace(day=1)
    corte = ContaFinanceira.objects.filter(ativo=True).aggregate(
        inicio=Min('data_corte_financeiro'),
    )['inicio']
    if corte is None or corte >= mes_atual:
        return None

    mes = corte.replace(day=1)
    fechados = set(FechamentoMensal.objects.filter(
        mes_referencia__gte=mes, mes_referencia__lt=mes_atual, vigente=True,
    ).values_list('mes_referencia', flat=True))
    while mes < mes_atual:
        if mes not in fechados:
            return mes
        mes = date(mes.year + (mes.month == 12), mes.month % 12 + 1, 1)
    return None


def aviso_fechamento(hoje: date | None = None) -> AvisoFechamento | None:
    hoje = hoje or timezone.localdate()
    pendente = mes_pendente(hoje)
    if pendente:
        return AvisoFechamento(pendente, pendente=True)

    mes = hoje.replace(day=1)
    ultimo_dia = monthrange(hoje.year, hoje.month)[1]
    if hoje.day < 25 or not ContaFinanceira.objects.filter(
        ativo=True, data_corte_financeiro__lte=hoje,
    ).exists():
        return None
    if FechamentoMensal.objects.filter(mes_referencia=mes, vigente=True).exists():
        return None
    return AvisoFechamento(mes, dias=ultimo_dia - hoje.day)
