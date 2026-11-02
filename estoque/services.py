from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import (
    BaixaEstoque,
    EntradaEstoque,
    ItemBaixaEstoque,
    ItemEntradaEstoque,
    Produto,
)

TWO_PLACES = Decimal('0.01')


class StockOperationError(ValueError):
    """Erro de validação de uma movimentação de estoque."""


@dataclass(frozen=True)
class MovementResult:
    movement: BaixaEstoque | EntradaEstoque
    created: bool


def _require_authenticated(actor):
    if actor is None or not actor.is_authenticated:
        raise StockOperationError('Usuário autenticado é obrigatório.')


def _parse_idempotency_key(key: str | None) -> str:
    normalized = (key or '').strip()
    if not normalized:
        raise StockOperationError('A chave de idempotência é obrigatória.')
    if len(normalized) > 64:
        raise StockOperationError('Chave de idempotência inválida.')
    return normalized


def _parse_lines(
    product_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
) -> list[tuple[int, Decimal]]:
    product_ids = list(product_ids)
    quantities = list(quantities)
    if not product_ids or len(product_ids) != len(quantities):
        raise StockOperationError('Adicione pelo menos um produto à operação.')

    lines = []
    selected_ids = set()
    for raw_product_id, raw_quantity in zip(product_ids, quantities):
        try:
            product_id = int(raw_product_id)
            quantity = Decimal(str(raw_quantity))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise StockOperationError('Informe quantidades maiores que zero.') from error

        try:
            normalized_quantity = quantity.quantize(TWO_PLACES)
        except InvalidOperation as error:
            raise StockOperationError(
                'Informe quantidades maiores que zero com no máximo duas casas decimais.'
            ) from error
        if (
            not quantity.is_finite()
            or quantity <= 0
            or quantity != normalized_quantity
        ):
            raise StockOperationError(
                'Informe quantidades maiores que zero com no máximo duas casas decimais.'
            )
        if product_id in selected_ids:
            raise StockOperationError('Selecione cada produto apenas uma vez.')
        selected_ids.add(product_id)
        lines.append((product_id, quantity))
    return lines


def _locked_products(lines: list[tuple[int, Decimal]]) -> dict[int, Produto]:
    selected_ids = {product_id for product_id, _ in lines}
    products = {
        product.pk: product
        for product in Produto.objects.select_for_update().filter(pk__in=selected_ids)
    }
    if len(products) != len(selected_ids):
        raise StockOperationError('Um dos produtos selecionados não existe.')
    return products


def registrar_entrada(
    *,
    actor,
    product_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
    reason: str,
    observation: str = '',
    idempotency_key: str | None,
) -> MovementResult:
    _require_authenticated(actor)
    key = _parse_idempotency_key(idempotency_key)
    lines = _parse_lines(product_ids, quantities)
    if reason not in dict(EntradaEstoque.MOTIVO_CHOICES):
        raise StockOperationError('Selecione um motivo válido para a entrada.')

    with transaction.atomic():
        existing = EntradaEstoque.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            return MovementResult(existing, created=False)

        products = _locked_products(lines)
        movement = EntradaEstoque.objects.create(
            motivo=reason,
            observacao=observation.strip(),
            responsavel=actor,
            chave_idempotencia=key,
        )
        for product_id, quantity in lines:
            product = products[product_id]
            product.quantidade += quantity
            product.save(update_fields=('quantidade',))
            ItemEntradaEstoque.objects.create(
                entrada=movement,
                produto=product,
                quantidade=quantity,
            )
    return MovementResult(movement, created=True)


def registrar_baixa(
    *,
    actor,
    product_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
    reason: str = 'VENDA',
    observation: str = '',
    idempotency_key: str | None,
) -> MovementResult:
    _require_authenticated(actor)
    key = _parse_idempotency_key(idempotency_key)
    lines = _parse_lines(product_ids, quantities)
    if reason not in dict(BaixaEstoque.MOTIVO_CHOICES):
        raise StockOperationError('Selecione um motivo válido para a baixa.')

    with transaction.atomic():
        existing = BaixaEstoque.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            return MovementResult(existing, created=False)

        products = _locked_products(lines)
        for product_id, quantity in lines:
            product = products[product_id]
            if quantity > product.quantidade:
                raise StockOperationError(f'Estoque insuficiente para {product.nome}.')

        movement = BaixaEstoque.objects.create(
            motivo=reason,
            observacao=observation.strip(),
            responsavel=actor,
            chave_idempotencia=key,
        )
        for product_id, quantity in lines:
            product = products[product_id]
            product.quantidade -= quantity
            product.save(update_fields=('quantidade',))
            ItemBaixaEstoque.objects.create(
                baixa=movement,
                produto=product,
                quantidade=quantity,
            )
    return MovementResult(movement, created=True)
