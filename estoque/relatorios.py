"""Consultas e exportações read-only de estoque."""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from typing import TypedDict
from urllib.parse import urlencode

from django.core.paginator import Page, Paginator
from django.db.models import F, Prefetch, QuerySet

from .models import (
    BaixaEstoque,
    EntradaEstoque,
    ItemBaixaEstoque,
    ItemEntradaEstoque,
    Produto,
)

MOVEMENTS_PER_PAGE = 20
ZERO = Decimal("0.00")


class ReportFilterError(ValueError):
    """Filtro de relatório inválido."""


class MovementItem(TypedDict):
    produto_id: int
    produto: str
    quantidade: Decimal


class MovementRow(TypedDict):
    id: int
    referencia: str
    data: datetime
    tipo: str
    tipo_label: str
    motivo: str
    motivo_label: str
    observacao: str
    responsavel: str
    responsavel_id: int | None
    fornecedor: str
    fornecedor_id: int | None
    itens: list[MovementItem]
    quantidade_total: Decimal


class StockReportRow(TypedDict):
    produto_id: int
    produto: str
    codigo_barras: str
    saldo: Decimal
    estoque_minimo: Decimal | None
    situacao: str


class MovementReportRow(TypedDict):
    produto_id: int
    produto: str
    entradas: str
    saidas: str
    quantidade: str


@dataclass(frozen=True)
class MovementFilters:
    """Filtros normalizados, compartilhados entre tela e CSV."""

    data_inicio: date | None = None
    data_fim: date | None = None
    produto_id: int | None = None
    responsavel_id: int | None = None
    motivo: str = ""
    fornecedor_id: int | None = None
    tipo: str = ""

    def as_query_string(self) -> str:
        values: dict[str, str] = {}
        if self.data_inicio:
            values["data_inicio"] = self.data_inicio.isoformat()
        if self.data_fim:
            values["data_fim"] = self.data_fim.isoformat()
        if self.produto_id:
            values["produto"] = str(self.produto_id)
        if self.responsavel_id:
            values["responsavel"] = str(self.responsavel_id)
        if self.motivo:
            values["motivo"] = self.motivo
        if self.fornecedor_id:
            values["fornecedor"] = str(self.fornecedor_id)
        if self.tipo:
            values["tipo"] = self.tipo
        return urlencode(values)


def _parse_date(value: str, field_name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ReportFilterError(f"Informe uma data válida em {field_name}.") from error


def _parse_positive_id(value: str, field_name: str) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ReportFilterError(f"Selecione um {field_name} válido.") from error
    if parsed <= 0:
        raise ReportFilterError(f"Selecione um {field_name} válido.")
    return parsed


def parse_movement_filters(params: Mapping[str, str]) -> MovementFilters:
    """Converte os parâmetros HTTP em filtros seguros e reutilizáveis."""
    data_inicio = _parse_date(params.get("data_inicio", ""), "data inicial")
    data_fim = _parse_date(params.get("data_fim", ""), "data final")
    if data_inicio and data_fim and data_inicio > data_fim:
        raise ReportFilterError("A data inicial não pode ser posterior à data final.")

    tipo = params.get("tipo", "").strip().lower()
    if tipo not in {"", "entrada", "baixa"}:
        raise ReportFilterError("Selecione um tipo de movimentação válido.")

    return MovementFilters(
        data_inicio=data_inicio,
        data_fim=data_fim,
        produto_id=_parse_positive_id(params.get("produto", ""), "produto"),
        responsavel_id=_parse_positive_id(
            params.get("responsavel", ""), "responsável"
        ),
        motivo=params.get("motivo", "").strip(),
        fornecedor_id=_parse_positive_id(
            params.get("fornecedor", ""), "fornecedor"
        ),
        tipo=tipo,
    )


def _date_filters(queryset: QuerySet, filters: MovementFilters) -> QuerySet:
    if filters.data_inicio:
        queryset = queryset.filter(criada_em__date__gte=filters.data_inicio)
    if filters.data_fim:
        queryset = queryset.filter(criada_em__date__lte=filters.data_fim)
    if filters.produto_id:
        queryset = queryset.filter(itens__produto_id=filters.produto_id)
    if filters.responsavel_id:
        queryset = queryset.filter(responsavel_id=filters.responsavel_id)
    if filters.motivo:
        queryset = queryset.filter(motivo=filters.motivo)
    return queryset.distinct()


def _movement_items(
    movement: BaixaEstoque | EntradaEstoque,
    product_id: int | None = None,
) -> list[MovementItem]:
    return [
        {
            "produto_id": item.produto_id,
            "produto": item.produto.nome,
            "quantidade": item.quantidade,
        }
        for item in movement.itens.all()
        if product_id is None or item.produto_id == product_id
    ]


def _responsible_name(movement: BaixaEstoque | EntradaEstoque) -> str:
    responsible = movement.responsavel
    if responsible is None:
        return "Usuário removido"
    return responsible.get_full_name() or responsible.username


def _entry_row(movement: EntradaEstoque, product_id: int | None = None) -> MovementRow:
    items = _movement_items(movement, product_id)
    supplier = movement.fornecedor
    return {
        "id": movement.pk,
        "referencia": f"Entrada #{movement.pk}",
        "data": movement.criada_em,
        "tipo": "entrada",
        "tipo_label": "Entrada",
        "motivo": movement.motivo,
        "motivo_label": movement.get_motivo_display(),
        "observacao": movement.observacao,
        "responsavel": _responsible_name(movement),
        "responsavel_id": movement.responsavel_id,
        "fornecedor": movement.fornecedor_nome or (supplier.nome if supplier else "—"),
        "fornecedor_id": movement.fornecedor_id,
        "itens": items,
        "quantidade_total": sum((item["quantidade"] for item in items), ZERO),
    }


def _outflow_row(movement: BaixaEstoque, product_id: int | None = None) -> MovementRow:
    items = _movement_items(movement, product_id)
    return {
        "id": movement.pk,
        "referencia": f"Baixa #{movement.pk}",
        "data": movement.criada_em,
        "tipo": "baixa",
        "tipo_label": "Saída",
        "motivo": movement.motivo,
        "motivo_label": movement.get_motivo_display(),
        "observacao": movement.observacao,
        "responsavel": _responsible_name(movement),
        "responsavel_id": movement.responsavel_id,
        "fornecedor": "—",
        "fornecedor_id": None,
        "itens": items,
        "quantidade_total": sum((item["quantidade"] for item in items), ZERO),
    }


def _entries(filters: MovementFilters) -> QuerySet:
    queryset = EntradaEstoque.objects.select_related(
        "responsavel", "fornecedor"
    ).prefetch_related(
        Prefetch(
            "itens",
            queryset=ItemEntradaEstoque.objects.select_related("produto").order_by(
                "produto__nome", "pk"
            ),
        )
    )
    queryset = _date_filters(queryset, filters)
    if filters.fornecedor_id:
        queryset = queryset.filter(fornecedor_id=filters.fornecedor_id)
    return queryset


def _outflows(filters: MovementFilters) -> QuerySet:
    queryset = BaixaEstoque.objects.select_related("responsavel").prefetch_related(
        Prefetch(
            "itens",
            queryset=ItemBaixaEstoque.objects.select_related("produto").order_by(
                "produto__nome", "pk"
            ),
        )
    )
    return _date_filters(queryset, filters)


def query_movements(filters: MovementFilters) -> list[MovementRow]:
    """Retorna entradas e saídas normalizadas, ordenadas da mais recente."""
    rows: list[MovementRow] = []
    if filters.tipo in {"", "entrada"}:
        rows.extend(
            _entry_row(movement, filters.produto_id) for movement in _entries(filters)
        )
    if filters.tipo in {"", "baixa"} and not filters.fornecedor_id:
        rows.extend(
            _outflow_row(movement, filters.produto_id)
            for movement in _outflows(filters)
        )
    return sorted(rows, key=lambda row: (row["data"], row["id"]), reverse=True)


def paginate_movements(params: Mapping[str, str]) -> tuple[Page, MovementFilters]:
    filters = parse_movement_filters(params)
    paginator = Paginator(query_movements(filters), MOVEMENTS_PER_PAGE)
    return paginator.get_page(params.get("page", "1")), filters


def _stock_rows(*, only_reorder: bool) -> list[StockReportRow]:
    queryset = Produto.objects.order_by("nome", "pk")
    if only_reorder:
        queryset = queryset.filter(
            ativo=True,
            estoque_minimo__isnull=False,
            quantidade__lte=F("estoque_minimo"),
        )
    rows: list[StockReportRow] = []
    for product in queryset:
        if not product.ativo:
            status = "Inativo"
        elif product.estoque_minimo is not None and product.quantidade <= product.estoque_minimo:
            status = "Reposição necessária"
        elif product.quantidade <= 0:
            status = "Sem estoque"
        else:
            status = "Disponível"
        rows.append(
            {
                "produto_id": product.pk,
                "produto": product.nome,
                "codigo_barras": product.codigo_barras or "",
                "saldo": product.quantidade,
                "estoque_minimo": product.estoque_minimo,
                "situacao": status,
            }
        )
    return rows


def movement_report(filters: MovementFilters) -> list[MovementReportRow]:
    totals: dict[int, dict[str, object]] = defaultdict(
        lambda: {"produto": "", "entradas": ZERO, "saidas": ZERO}
    )
    for movement in query_movements(filters):
        for item in movement["itens"]:
            total = totals[item["produto_id"]]
            total["produto"] = item["produto"]
            key = "entradas" if movement["tipo"] == "entrada" else "saidas"
            total[key] = total[key] + item["quantidade"]  # type: ignore[operator]

    rows: list[MovementReportRow] = []
    for product_id, total in sorted(
        totals.items(), key=lambda item: (str(item[1]["produto"]), item[0])
    ):
        entries = total["entradas"]
        outflows = total["saidas"]
        assert isinstance(entries, Decimal)
        assert isinstance(outflows, Decimal)
        rows.append(
            {
                "produto_id": product_id,
                "produto": str(total["produto"]),
                "entradas": format_decimal(entries),
                "saidas": format_decimal(outflows),
                "quantidade": format_decimal(entries - outflows),
            }
        )
    return rows


def format_decimal(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}".replace(".", ",")


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return f"'{value}"
    return value


def _csv_text(headers: list[str], rows: list[list[object]]) -> str:
    output = StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_safe(value) for value in row])
    return "\ufeff" + output.getvalue()


def movement_csv(filters: MovementFilters) -> str:
    rows: list[list[object]] = []
    for movement in query_movements(filters):
        items = " | ".join(
            f"{item['produto']} ({format_decimal(item['quantidade'])})"
            for item in movement["itens"]
        )
        rows.append(
            [
                movement["referencia"],
                movement["data"].strftime("%d/%m/%Y %H:%M"),
                movement["tipo_label"],
                items,
                format_decimal(movement["quantidade_total"]),
                movement["responsavel"],
                movement["motivo_label"],
                movement["fornecedor"],
                movement["observacao"],
            ]
        )
    return _csv_text(
        [
            "Referência",
            "Data",
            "Tipo",
            "Produtos e quantidades",
            "Quantidade total",
            "Responsável",
            "Motivo",
            "Fornecedor",
            "Observação",
        ],
        rows,
    )


def stock_csv(*, only_reorder: bool) -> str:
    rows: list[list[object]] = [
        [
            row["produto"],
            row["codigo_barras"],
            format_decimal(row["saldo"]),
            format_decimal(row["estoque_minimo"]),
            row["situacao"],
        ]
        for row in _stock_rows(only_reorder=only_reorder)
    ]
    return _csv_text(
        ["Produto", "Código de barras", "Saldo", "Estoque mínimo", "Situação"],
        rows,
    )


def report_csv(report_type: str, filters: MovementFilters) -> str:
    if report_type == "movimentacoes":
        return movement_csv(filters)
    if report_type == "reposicao":
        return stock_csv(only_reorder=True)
    return stock_csv(only_reorder=False)


def stock_report(*, only_reorder: bool) -> list[StockReportRow]:
    return _stock_rows(only_reorder=only_reorder)
