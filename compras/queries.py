from __future__ import annotations

from datetime import date
from uuid import UUID

from django.db.models import QuerySet

from .models import PedidoCompra


def pedidos_queryset() -> QuerySet[PedidoCompra]:
    return PedidoCompra.objects.select_related('fornecedor', 'responsavel').prefetch_related(
        'itens__produto',
        'recebimentos__itens',
        'eventos__responsavel',
    )


def listar_pedidos(
    *, fornecedor_id: int | None = None,
    estado: str | None = None,
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> QuerySet[PedidoCompra]:
    queryset = pedidos_queryset()
    if fornecedor_id is not None:
        queryset = queryset.filter(fornecedor_id=fornecedor_id)
    if estado:
        queryset = queryset.filter(estado=estado)
    if data_inicio:
        queryset = queryset.filter(criado_em__date__gte=data_inicio)
    if data_fim:
        queryset = queryset.filter(criado_em__date__lte=data_fim)
    return queryset


def obter_pedido(identifier: int | str | UUID) -> PedidoCompra:
    queryset = pedidos_queryset()
    try:
        if isinstance(identifier, UUID) or (
            isinstance(identifier, str) and len(identifier) == 36
        ):
            return queryset.get(public_id=identifier)
        return queryset.get(pk=int(identifier))
    except (TypeError, ValueError, PedidoCompra.DoesNotExist) as error:
        raise PedidoCompra.DoesNotExist from error


def saldos_pedido(pedido: PedidoCompra) -> list[dict[str, object]]:
    rows = []
    for item in pedido.itens.all():
        received = pedido.quantidade_recebida(item)
        rows.append({
            'item': item,
            'quantidade_pedida': item.quantidade,
            'quantidade_recebida': received,
            'quantidade_cancelada': pedido.quantidade_cancelada(item),
            'quantidade_recebivel': pedido.quantidade_recebivel(item),
            'quantidade_pendente': pedido.quantidade_pendente(item),
        })
    return rows
