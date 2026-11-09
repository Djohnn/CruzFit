"""Read-only Caixa queries and filtered, paginated session history."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from django.core.paginator import Page, Paginator
from django.db.models import (
    BigIntegerField,
    Case,
    CharField,
    F,
    QuerySet,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Cast, Concat

from .models import (
    FechamentoCaixa,
    PagamentoRestituicao,
    PagamentoVenda,
    RecebimentoVenda,
    SangriaCaixa,
    SessaoCaixa,
    SuprimentoCaixa,
)

HISTORY_PER_PAGE = 20
HISTORY_TYPES = {"", "RECEBIMENTO", "SUPRIMENTO", "SANGRIA"}


@dataclass(frozen=True)
class TotalPagamentoFechamento:
    forma: str
    vendas: Decimal
    devolucoes: Decimal
    liquido: Decimal
    detalhes: tuple["TotalPagamentoFechamento", ...] = ()


@dataclass(frozen=True)
class ExtratoFechamento:
    pagamentos: tuple[TotalPagamentoFechamento, ...]
    vendas_dinheiro: Decimal
    devolucoes_dinheiro: Decimal
    reforcos: Decimal
    sangrias: Decimal
    movimentos: tuple[tuple[str, SangriaCaixa | SuprimentoCaixa], ...]


def extrato_fechamento(
    sessao: SessaoCaixa, fechamento: FechamentoCaixa | None = None,
) -> ExtratoFechamento:
    vendas = PagamentoVenda.objects.filter(recebimento__sessao=sessao)
    devolucoes = PagamentoRestituicao.objects.filter(restituicao__sessao=sessao)
    reforcos = SuprimentoCaixa.objects.filter(sessao=sessao)
    sangrias = SangriaCaixa.objects.filter(sessao=sessao)
    if fechamento:
        corte = fechamento.fechada_em
        vendas = vendas.filter(recebimento__criada_em__lte=corte)
        devolucoes = devolucoes.filter(restituicao__criada_em__lte=corte)
        reforcos = reforcos.filter(criada_em__lte=corte)
        sangrias = sangrias.filter(criada_em__lte=corte)

    vendidos = {
        (forma, modalidade): total
        for forma, modalidade, total in vendas.order_by().values('forma', 'modalidade_cartao')
        .annotate(total=Sum('valor_aplicado')).values_list('forma', 'modalidade_cartao', 'total')
    }
    devolvidos = {
        (forma, modalidade): total
        for forma, modalidade, total in devolucoes.order_by()
        .values('pagamento_original__forma', 'pagamento_original__modalidade_cartao')
        .annotate(total=Sum('valor'))
        .values_list('pagamento_original__forma', 'pagamento_original__modalidade_cartao', 'total')
    }
    totais_gravados = fechamento.totais_por_forma if fechamento else {}
    zero = Decimal('0.00')
    pagamentos = []
    for codigo, forma in PagamentoVenda.FORMA_PAGAMENTO_CHOICES:
        vendido = vendidos.get((codigo, ''), zero)
        devolvido = devolvidos.get((codigo, ''), zero)
        detalhes = []
        if codigo == 'CARTAO':
            if vendido or devolvido:
                detalhes.append(TotalPagamentoFechamento(
                    'Sem modalidade informada', vendido, devolvido, vendido - devolvido,
                ))
            for modalidade, label in PagamentoVenda.MODALIDADES_CARTAO:
                subtotal = vendidos.get((codigo, modalidade), zero)
                restituidas = devolvidos.get((codigo, modalidade), zero)
                detalhes.append(TotalPagamentoFechamento(
                    label, subtotal, restituidas, subtotal - restituidas,
                ))
                vendido += subtotal
                devolvido += restituidas
        liquido = Decimal(totais_gravados.get(codigo, vendido - devolvido))
        pagamentos.append(TotalPagamentoFechamento(forma, vendido, devolvido, liquido, tuple(detalhes)))

    movimentos: list[tuple[str, SangriaCaixa | SuprimentoCaixa]] = [
        ('Reforço', item) for item in reforcos
    ]
    movimentos.extend(('Sangria', item) for item in sangrias)
    movimentos.sort(key=lambda entrada: entrada[1].criada_em)
    return ExtratoFechamento(
        pagamentos=tuple(pagamentos),
        vendas_dinheiro=vendidos.get(('DINHEIRO', ''), zero),
        devolucoes_dinheiro=devolvidos.get(('DINHEIRO', ''), zero),
        reforcos=sum((item.valor for tipo, item in movimentos if tipo == 'Reforço'), zero),
        sangrias=sum((item.valor for tipo, item in movimentos if tipo == 'Sangria'), zero),
        movimentos=tuple(movimentos),
    )


class CaixaQueryError(ValueError):
    """Filter parameters are not valid for Caixa history."""


@dataclass(frozen=True)
class HistoricoCaixaFilters:
    tipo: str = ""
    motivo: str = ""
    data_inicio: date | None = None
    data_fim: date | None = None

    def as_query_string(self) -> str:
        values: dict[str, str] = {}
        if self.tipo:
            values["tipo"] = self.tipo
        if self.motivo:
            values["motivo"] = self.motivo
        if self.data_inicio:
            values["data_inicio"] = self.data_inicio.isoformat()
        if self.data_fim:
            values["data_fim"] = self.data_fim.isoformat()
        return urlencode(values)


def _parse_filter_date(value: str, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise CaixaQueryError(f"Informe uma data válida em {field}.") from error


def parse_historico_filters(params) -> HistoricoCaixaFilters:
    tipo = params.get("tipo", "").strip().upper()
    if tipo not in HISTORY_TYPES:
        raise CaixaQueryError("Selecione um tipo de operação válido.")
    data_inicio = _parse_filter_date(params.get("data_inicio", ""), "data inicial")
    data_fim = _parse_filter_date(params.get("data_fim", ""), "data final")
    if data_inicio and data_fim and data_inicio > data_fim:
        raise CaixaQueryError("A data inicial não pode ser posterior à data final.")
    return HistoricoCaixaFilters(
        tipo=tipo,
        motivo=params.get("motivo", "").strip(),
        data_inicio=data_inicio,
        data_fim=data_fim,
    )


def _history_rows(
    queryset: QuerySet,
    *,
    kind: str,
    value_field: str,
    reference_prefix: str,
    link_id_field: str | None = None,
) -> QuerySet:
    reference = Concat(
        Value(reference_prefix),
        Cast(F(link_id_field or "pk"), output_field=CharField()),
        output_field=CharField(),
    )
    link_id = F(link_id_field) if link_id_field else Value(
        0, output_field=BigIntegerField(),
    )
    display_name = Case(
        When(
            responsavel__first_name="",
            responsavel__last_name="",
            then=F("responsavel__username"),
        ),
        default=Concat(
            F("responsavel__first_name"),
            Value(" "),
            F("responsavel__last_name"),
            output_field=CharField(),
        ),
        output_field=CharField(),
    )
    return queryset.order_by().annotate(
        tipo=Value(kind, output_field=CharField()),
        quantia=F(value_field),
        responsavel_nome=display_name,
        referencia=reference,
        link_id=link_id,
    ).values(
        "id",
        "criada_em",
        "tipo",
        "quantia",
        "motivo",
        "responsavel_nome",
        "referencia",
        "link_id",
    )


def _filter_history_rows(queryset: QuerySet, filters: HistoricoCaixaFilters) -> QuerySet:
    if filters.motivo:
        queryset = queryset.filter(motivo__icontains=filters.motivo)
    if filters.data_inicio:
        queryset = queryset.filter(criada_em__date__gte=filters.data_inicio)
    if filters.data_fim:
        queryset = queryset.filter(criada_em__date__lte=filters.data_fim)
    return queryset


def historico_sessao(
    sessao: SessaoCaixa | None, params,
) -> tuple[Page, HistoricoCaixaFilters]:
    filters = parse_historico_filters(params)
    if sessao:
        receipt_rows = _history_rows(
            RecebimentoVenda.objects.filter(sessao=sessao),
            kind="RECEBIMENTO",
            value_field="valor_total",
            reference_prefix="Venda #",
            link_id_field="venda_id",
        )
        supply_rows = _history_rows(
            SuprimentoCaixa.objects.filter(sessao=sessao),
            kind="SUPRIMENTO",
            value_field="valor",
            reference_prefix="Suprimento #",
        )
        withdrawal_rows = _history_rows(
            SangriaCaixa.objects.filter(sessao=sessao),
            kind="SANGRIA",
            value_field="valor",
            reference_prefix="Sangria #",
        )
        rows_by_type = {
            "RECEBIMENTO": _filter_history_rows(receipt_rows, filters),
            "SUPRIMENTO": _filter_history_rows(supply_rows, filters),
            "SANGRIA": _filter_history_rows(withdrawal_rows, filters),
        }
        if filters.tipo:
            rows = rows_by_type[filters.tipo].order_by("-criada_em", "-id")
        else:
            rows = (
                rows_by_type["RECEBIMENTO"]
                .union(
                    rows_by_type["SUPRIMENTO"],
                    rows_by_type["SANGRIA"],
                    all=True,
                )
                .order_by("-criada_em", "-id", "tipo")
            )
    else:
        rows = []
    page = Paginator(rows, HISTORY_PER_PAGE).get_page(params.get("page", "1"))
    return page, filters
