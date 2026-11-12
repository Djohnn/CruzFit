from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum

from estoque.services import (
    TWO_PLACES,
    ReversalOperationError,
    ReversalResult,
    StockOperationError,
    _check_reversal_replay,
    _locked_products,
    _parse_idempotency_key,
    _parse_lines,
    _require_authenticated,
    _require_reversal_manager,
    _reversal_key,
    _reversal_lines,
    _reversal_reason,
    _reversal_signature,
    _sum_reversed_quantities,
    criar_baixa_venda,
    registrar_retorno_venda,
    vincular_referencia_venda,
)

from .models import (
    ItemTrocaOrigem,
    ItemVenda,
    ItemVendaDevolucao,
    Venda,
    VendaCancelamento,
    VendaDevolucao,
    VendaTroca,
)


class SaleOperationError(ValueError):
    """Erro de validação ou conflito em uma venda local."""


_IN_CAIXA_FINALIZATION: ContextVar[bool] = ContextVar(
    'vendas_in_caixa_finalization', default=False,
)


@contextmanager
def _caixa_finalization_context() -> Iterator[None]:
    token = _IN_CAIXA_FINALIZATION.set(True)
    try:
        yield
    finally:
        _IN_CAIXA_FINALIZATION.reset(token)


def _require_caixa_if_cutover() -> None:
    if _IN_CAIXA_FINALIZATION.get():
        return
    # Import local keeps Caixa as orchestrator without a module import cycle.
    from caixa.models import CorteCaixa

    if CorteCaixa.objects.exists():
        raise SaleOperationError(
            'Após o corte, confirme vendas por uma sessão de Caixa aberta.',
        )


@dataclass(frozen=True)
class SaleResult:
    sale: Venda
    created: bool


@dataclass(frozen=True)
class SalePreviewLine:
    product_id: int
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal


@dataclass(frozen=True)
class SalePreviewResult:
    subtotal: Decimal
    discount: Decimal
    total: Decimal
    lines: tuple[SalePreviewLine, ...]


def _parse_sale_money(value: object, field_name: str) -> Decimal:
    if value in (None, ''):
        return Decimal('0.00')
    try:
        parsed = Decimal(str(value).strip().replace(',', '.'))
        normalized = parsed.quantize(TWO_PLACES)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise SaleOperationError(f'Informe um valor válido para {field_name}.') from error
    if (
        not parsed.is_finite()
        or parsed < 0
        or parsed != normalized
    ):
        raise SaleOperationError(
            f'Informe um valor não negativo para {field_name} com até duas casas decimais.'
        )
    return normalized


def previsualizar_venda(
    *,
    actor,
    product_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
    discount: object = '0',
) -> SalePreviewResult:
    """Calcular a revisão da venda sem gravar venda ou movimentação de estoque."""
    _require_authenticated(actor)
    lines = _parse_lines(product_ids, quantities)
    parsed_discount = _parse_sale_money(discount, 'desconto')
    with transaction.atomic():
        products = _locked_products(lines)
        preview_lines = []
        for product_id, quantity in lines:
            product = products[product_id]
            if quantity > product.quantidade:
                raise SaleOperationError(f'Estoque insuficiente para {product.nome}.')
            line_total = (product.preco_venda * quantity).quantize(TWO_PLACES)
            preview_lines.append(SalePreviewLine(
                product_id=product_id,
                product_name=product.nome,
                quantity=quantity,
                unit_price=product.preco_venda,
                total=line_total,
            ))
    subtotal = sum(
        (line.total for line in preview_lines), Decimal('0.00'),
    ).quantize(TWO_PLACES)
    if parsed_discount > subtotal:
        raise SaleOperationError('O desconto não pode superar o subtotal da venda.')
    return SalePreviewResult(
        subtotal=subtotal,
        discount=parsed_discount,
        total=(subtotal - parsed_discount).quantize(TWO_PLACES),
        lines=tuple(preview_lines),
    )


def _sale_signature(
    actor_id: int,
    payment_method: str,
    discount: Decimal,
    lines: list[tuple[int, Decimal]],
) -> str:
    payload = [
        actor_id,
        payment_method,
        str(discount),
        [[product_id, str(quantity)] for product_id, quantity in sorted(lines)],
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def _allocate_discount_by_largest_remainder(
    items: list[tuple[int, Decimal]], discount: Decimal,
) -> dict[int, Decimal]:
    """Allocate discount cents by gross share; item ID breaks equal remainders."""
    totals = {item_id: gross.quantize(TWO_PLACES) for item_id, gross in items}
    subtotal_cents = sum(int(gross * 100) for gross in totals.values())
    discount_cents = int(discount * 100)
    if not subtotal_cents:
        return {item_id: Decimal('0.00') for item_id in totals}

    floors: dict[int, int] = {}
    remainders: dict[int, int] = {}
    for item_id, gross in totals.items():
        numerator = discount_cents * int(gross * 100)
        floors[item_id], remainders[item_id] = divmod(numerator, subtotal_cents)

    cents_left = discount_cents - sum(floors.values())
    for item_id in sorted(totals, key=lambda value: (-remainders[value], value))[:cents_left]:
        floors[item_id] += 1
    return {
        item_id: (Decimal(cents) / 100).quantize(TWO_PLACES)
        for item_id, cents in floors.items()
    }


def _returned_item_net_value(
    *, item: ItemVenda, previous_quantity: Decimal,
    previous_net_value: Decimal, returned_quantity: Decimal,
) -> Decimal:
    cumulative_quantity = previous_quantity + returned_quantity
    if cumulative_quantity == item.quantidade:
        target_net = item.total_liquido
    else:
        target_net = (
            item.total_liquido * cumulative_quantity / item.quantidade
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    value = (target_net - previous_net_value).quantize(TWO_PLACES)
    if value < 0:
        raise ReversalOperationError(
            'O histórico de devoluções excede o líquido do item; encaminhe para revisão manual.',
        )
    return value


def _check_sale_replay(sale: Venda, actor_id: int, signature: str) -> None:
    if sale.responsavel_id != actor_id or sale.assinatura != signature:
        raise SaleOperationError(
            'Essa chave de venda já foi usada com outros dados. Abra uma nova venda.',
        )


def registrar_venda(
    *,
    actor,
    product_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
    discount: object = '0',
    payment_method: str,
    idempotency_key: str | None,
) -> SaleResult:
    _require_authenticated(actor)
    try:
        key = _parse_idempotency_key(idempotency_key)
    except StockOperationError as error:
        raise SaleOperationError(str(error)) from error

    lines = _parse_lines(product_ids, quantities)
    payment = (payment_method or '').strip().upper()
    if payment not in dict(Venda.FORMA_PAGAMENTO_CHOICES):
        raise SaleOperationError('Selecione uma forma de pagamento válida.')
    parsed_discount = _parse_sale_money(discount, 'desconto')
    signature = _sale_signature(actor.pk, payment, parsed_discount, lines)

    with transaction.atomic():
        existing = Venda.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            _check_sale_replay(existing, actor.pk, signature)
            return SaleResult(existing, created=False)

        _require_caixa_if_cutover()

        products = _locked_products(lines)
        item_totals = [
            (product_id, quantity, (products[product_id].preco_venda * quantity).quantize(TWO_PLACES))
            for product_id, quantity in lines
        ]
        subtotal = sum(
            (item_total for _, _, item_total in item_totals),
            Decimal('0.00'),
        ).quantize(TWO_PLACES)
        if parsed_discount > subtotal:
            raise SaleOperationError('O desconto não pode superar o subtotal da venda.')

        try:
            movement = criar_baixa_venda(actor, lines, products)
        except StockOperationError as error:
            raise SaleOperationError(str(error)) from error
        sale = Venda.objects.create(
            responsavel=actor,
            baixa=movement,
            forma_pagamento=payment,
            subtotal=subtotal,
            desconto=parsed_discount,
            total=(subtotal - parsed_discount).quantize(TWO_PLACES),
            chave_idempotencia=key,
            assinatura=signature,
        )
        vincular_referencia_venda(movement, sale.pk)

        for product_id, quantity, item_total in item_totals:
            product = products[product_id]
            ItemVenda.objects.create(
                venda=sale,
                produto=product,
                quantidade=quantity,
                preco_unitario=product.preco_venda,
                total=item_total,
                total_liquido=Decimal('0.00'),
            )
        sale_items = list(ItemVenda.objects.filter(venda=sale).order_by('pk'))
        discount_shares = _allocate_discount_by_largest_remainder(
            [(item.pk, item.total) for item in sale_items],
            parsed_discount,
        )
        for item in sale_items:
            item.total_liquido = (item.total - discount_shares[item.pk]).quantize(TWO_PLACES)
            item.save(update_fields=('total_liquido',))
    return SaleResult(sale, created=True)


def cancelar_venda(
    *,
    actor,
    sale_id: int,
    reason: str,
    idempotency_key: str | None,
) -> ReversalResult:
    _require_reversal_manager(actor)
    key = _reversal_key(idempotency_key)
    motivo = _reversal_reason(reason)
    signature = _reversal_signature(actor.pk, 'cancelamento-venda', sale_id, motivo)

    with transaction.atomic():
        existing = VendaCancelamento.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            _check_reversal_replay(existing, actor.pk, signature)
            return ReversalResult(existing, created=False)

        try:
            sale = Venda.objects.select_for_update().get(pk=sale_id)
        except Venda.DoesNotExist as error:
            raise ReversalOperationError('Venda não encontrada.') from error
        if VendaCancelamento.objects.filter(venda=sale).exists():
            raise ReversalOperationError('Essa venda já foi cancelada.')
        if VendaDevolucao.objects.filter(venda=sale).exists():
            raise ReversalOperationError(
                'Uma venda com devolução parcial não pode ser cancelada integralmente.',
            )
        if VendaTroca.objects.filter(venda=sale).exists():
            raise ReversalOperationError(
                'Uma venda com troca confirmada não pode ser cancelada integralmente.',
            )

        sale_items = list(
            ItemVenda.objects.select_for_update().filter(venda=sale).order_by('pk')
        )
        value_total = sum(
            (item.total_liquido for item in sale_items), Decimal('0.00'),
        ).quantize(TWO_PLACES)
        entry = registrar_retorno_venda(
            actor,
            [(item.produto_id, item.quantidade) for item in sale_items],
            f'Cancelamento da Venda #{sale.pk}',
        )
        cancellation = VendaCancelamento.objects.create(
            venda=sale,
            entrada=entry,
            responsavel=actor,
            motivo=motivo,
            chave_idempotencia=key,
            assinatura=signature,
            valor_total=value_total.quantize(TWO_PLACES),
        )
        try:
            from caixa.services import (
                CaixaOperationError,
                registrar_pendencia_cancelamento,
            )

            registrar_pendencia_cancelamento(
                actor=actor,
                cancelamento=cancellation,
            )
        except CaixaOperationError as error:
            raise SaleOperationError(str(error)) from error
    return ReversalResult(cancellation, created=True)


def devolver_venda(
    *,
    actor,
    sale_id: int,
    item_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
    reason: str,
    idempotency_key: str | None,
) -> ReversalResult:
    _require_reversal_manager(actor)
    key = _reversal_key(idempotency_key)
    motivo = _reversal_reason(reason)
    lines = _reversal_lines(item_ids, quantities)
    signature = _reversal_signature(actor.pk, 'devolucao-venda', sale_id, motivo, lines)

    with transaction.atomic():
        existing = VendaDevolucao.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            _check_reversal_replay(existing, actor.pk, signature)
            return ReversalResult(existing, created=False)

        try:
            sale = Venda.objects.select_for_update().get(pk=sale_id)
        except Venda.DoesNotExist as error:
            raise ReversalOperationError('Venda não encontrada.') from error
        if VendaCancelamento.objects.filter(venda=sale).exists():
            raise ReversalOperationError('Uma venda cancelada não aceita devolução adicional.')
        item_ids_set = {item_id for item_id, _ in lines}
        sale_items = {
            item.pk: item
            for item in ItemVenda.objects.select_for_update().filter(
                venda=sale,
                pk__in=item_ids_set,
            )
        }
        if len(sale_items) != len(item_ids_set):
            raise ReversalOperationError('Um dos itens não pertence à venda informada.')

        previous_returns = {
            item_id: (
                (
                    _sum_reversed_quantities(
                        ItemVendaDevolucao.objects.filter(
                            devolucao__venda=sale,
                            item_venda_id=item_id,
                        ).values_list('quantidade', flat=True),
                    )
                    + (
                        ItemTrocaOrigem.objects.filter(
                            troca__venda=sale,
                            item_venda_id=item_id,
                        ).aggregate(total=Sum('quantidade'))['total']
                        or Decimal('0.00')
                    )
                ).quantize(TWO_PLACES),
                (
                    ItemVendaDevolucao.objects.filter(
                        devolucao__venda=sale,
                        item_venda_id=item_id,
                    ).aggregate(total=Sum('total'))['total']
                    or Decimal('0.00')
                )
                + (
                    ItemTrocaOrigem.objects.filter(
                        troca__venda=sale,
                        item_venda_id=item_id,
                    ).aggregate(total=Sum('credito_total'))['total']
                    or Decimal('0.00')
                ),
            )
            for item_id in item_ids_set
        }
        for item_id, quantity in lines:
            item = sale_items[item_id]
            previous_quantity, _previous_net = previous_returns[item_id]
            eligible = item.quantidade - previous_quantity
            if quantity > eligible:
                raise ReversalOperationError(
                    f'A quantidade devolvida de {item.produto.nome} supera o saldo elegível.',
                )

        entry = registrar_retorno_venda(
            actor,
            [(sale_items[item_id].produto_id, quantity) for item_id, quantity in lines],
            f'Devolução da Venda #{sale.pk}',
        )
        value_total = Decimal('0.00')
        devolucao = VendaDevolucao.objects.create(
            venda=sale,
            entrada=entry,
            responsavel=actor,
            motivo=motivo,
            chave_idempotencia=key,
            assinatura=signature,
            valor_total=Decimal('0.00'),
        )
        for item_id, quantity in lines:
            item = sale_items[item_id]
            previous_quantity, previous_net = previous_returns[item_id]
            item_total = _returned_item_net_value(
                item=item,
                previous_quantity=previous_quantity,
                previous_net_value=previous_net,
                returned_quantity=quantity,
            )
            ItemVendaDevolucao.objects.create(
                devolucao=devolucao,
                item_venda=item,
                produto_id=item.produto_id,
                quantidade=quantity,
                preco_unitario=item.preco_unitario,
                total=item_total,
            )
            value_total += item_total
        devolucao.valor_total = value_total.quantize(TWO_PLACES)
        devolucao.save(update_fields=('valor_total',))
        try:
            from caixa.services import (
                CaixaOperationError,
                registrar_pendencia_devolucao,
            )

            registrar_pendencia_devolucao(
                actor=actor,
                devolucao=devolucao,
            )
        except CaixaOperationError as error:
            raise SaleOperationError(str(error)) from error
    return ReversalResult(devolucao, created=True)
