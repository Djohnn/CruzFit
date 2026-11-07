from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connections, transaction
from django.db.models import F, Sum
from django.utils import timezone

from estoque.fornecedores import obter_fornecedor_ativo
from estoque.models import Produto
from estoque.services import registrar_entrada

from .models import (
    CancelamentoPedidoCompra,
    EventoPedidoCompra,
    ItemPedidoCompra,
    ItemRecebimentoCompra,
    PedidoCompra,
    RecebimentoCompra,
    authorized_purchase_append,
)

TWO_PLACES = Decimal('0.01')
OP_CREATE = 'CRIACAO'
OP_EDIT = 'EDICAO'
OP_CONFIRM = 'CONFIRMACAO'
OP_RECEIVE = 'RECEBIMENTO'
OP_CANCEL = 'CANCELAMENTO'


class PurchaseOperationError(ValueError):
    """Erro de validação, autorização ou concorrência em Compras."""


@dataclass(frozen=True)
class PurchaseResult:
    pedido: PedidoCompra
    created: bool


@dataclass(frozen=True)
class ReceiptResult:
    recebimento: RecebimentoCompra
    pedido: PedidoCompra
    created: bool


@dataclass(frozen=True)
class FinancialPayableSource:
    pedido_id: int
    public_id: str
    fornecedor_nome: str
    total_contratado: Decimal


def resumo_pedido_para_financeiro(*, pedido_id: int) -> FinancialPayableSource:
    """Retorna uma origem de obrigação somente quando o pedido foi recebido por inteiro."""
    try:
        pedido = PedidoCompra.objects.select_for_update().get(pk=int(pedido_id))
    except (PedidoCompra.DoesNotExist, TypeError, ValueError) as error:
        raise PurchaseOperationError('Pedido de compra não encontrado.') from error
    if pedido.estado != PedidoCompra.Estado.RECEBIDO:
        raise PurchaseOperationError(
            'O pedido precisa estar totalmente recebido para gerar uma conta a pagar.',
        )
    return FinancialPayableSource(
        pedido_id=pedido.pk,
        public_id=str(pedido.public_id),
        fornecedor_nome=pedido.fornecedor_nome,
        total_contratado=pedido.total_contratado,
    )


def _require_manager(actor) -> None:
    if not (
        actor
        and actor.is_authenticated
        and actor.is_active
        and (actor.is_superuser or getattr(actor, 'cargo', '') == 'G')
    ):
        raise PurchaseOperationError('Apenas o gerente ativo pode operar Compras.')


def _required_text(value: object, label: str, limit: int) -> str:
    normalized = str(value or '').strip()
    if not normalized or len(normalized) > limit:
        raise PurchaseOperationError(f'Informe {label} com até {limit} caracteres.')
    return normalized


def _optional_text(value: object, label: str, limit: int) -> str:
    normalized = str(value or '').strip()
    if len(normalized) > limit:
        raise PurchaseOperationError(f'{label} deve ter até {limit} caracteres.')
    return normalized


def _key(value: str | None) -> str:
    return _required_text(value, 'uma chave de idempotência', 64)


def _decimal(value: object, label: str, *, maximum: Decimal) -> Decimal:
    try:
        parsed = Decimal(str(value).strip().replace(',', '.'))
        rounded = parsed.quantize(TWO_PLACES)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PurchaseOperationError(
            f'Informe {label} positivo com até duas casas decimais.',
        ) from error
    if (
        not parsed.is_finite()
        or parsed <= 0
        or parsed != rounded
        or rounded > maximum
    ):
        raise PurchaseOperationError(
            f'Informe {label} positivo com até duas casas decimais.',
        )
    return rounded


def _quantity(value: object) -> Decimal:
    return _decimal(value, 'uma quantidade', maximum=Decimal('9999999999.99'))


def _cost(value: object) -> Decimal:
    return _decimal(value, 'um custo unitário', maximum=Decimal('99999999.99'))


def _normalize_lines(lines: Iterable[Mapping[str, object] | Sequence[object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    seen: set[int] = set()
    for raw_line in lines:
        if isinstance(raw_line, Mapping):
            raw_product_id = raw_line.get('produto_id', raw_line.get('product_id'))
            raw_quantity = raw_line.get('quantidade')
            raw_cost = raw_line.get('custo_unitario', raw_line.get('cost'))
        else:
            values = list(raw_line)
            if len(values) != 3:
                raise PurchaseOperationError('Cada item precisa de produto, quantidade e custo.')
            raw_product_id, raw_quantity, raw_cost = values
        try:
            product_id = int(str(raw_product_id))
        except (TypeError, ValueError, OverflowError) as error:
            raise PurchaseOperationError('Selecione um produto válido.') from error
        if product_id <= 0 or product_id in seen:
            raise PurchaseOperationError('Selecione cada produto apenas uma vez.')
        seen.add(product_id)
        normalized.append({
            'produto_id': product_id,
            'quantidade': _quantity(raw_quantity),
            'custo_unitario': _cost(raw_cost),
        })
    return sorted(normalized, key=lambda line: int(str(line['produto_id'])))


def _normalize_receipt_lines(
    lines: Iterable[Mapping[str, object] | Sequence[object]],
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    seen: set[int] = set()
    for raw_line in lines:
        if isinstance(raw_line, Mapping):
            raw_item_id = raw_line.get('item_id')
            raw_quantity = raw_line.get('quantidade')
        else:
            values = list(raw_line)
            if len(values) != 2:
                raise PurchaseOperationError('Cada recebimento precisa de item e quantidade.')
            raw_item_id, raw_quantity = values
        try:
            item_id = int(str(raw_item_id))
        except (TypeError, ValueError, OverflowError) as error:
            raise PurchaseOperationError('Selecione um item de pedido válido.') from error
        if item_id <= 0 or item_id in seen:
            raise PurchaseOperationError('Selecione cada item do pedido apenas uma vez.')
        seen.add(item_id)
        normalized.append({'item_id': item_id, 'quantidade': _quantity(raw_quantity)})
    if not normalized:
        raise PurchaseOperationError('Adicione pelo menos um item ao recebimento.')
    return sorted(normalized, key=lambda line: int(str(line['item_id'])))


def _line_id(line: Mapping[str, object], key: str) -> int:
    return int(str(line[key]))


def _line_decimal(line: Mapping[str, object], key: str) -> Decimal:
    value = line[key]
    if not isinstance(value, Decimal):
        raise PurchaseOperationError('Linha normalizada inválida.')
    return value


def _serializable_lines(lines: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in line.items()
        }
        for line in lines
    ]


def _signature(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _active_supplier(supplier_id: object):
    try:
        return obter_fornecedor_ativo(supplier_id)
    except ValidationError as error:
        raise PurchaseOperationError('Selecione um fornecedor ativo.') from error


def _locked_products(product_ids: Iterable[int]) -> dict[int, Produto]:
    ids = set(product_ids)
    products = {
        product.pk: product
        for product in Produto.objects.select_for_update().filter(pk__in=ids)
    }
    if len(products) != len(ids):
        raise PurchaseOperationError('Um dos produtos selecionados não existe.')
    if any(not product.ativo for product in products.values()):
        raise PurchaseOperationError('Produto inativo não pode entrar em pedido.')
    return products


def _append_items(pedido: PedidoCompra, lines: Sequence[Mapping[str, object]], products: dict[int, Produto]) -> None:
    ItemPedidoCompra.objects.bulk_create([
        ItemPedidoCompra(
            pedido=pedido,
            produto=products[_line_id(line, 'produto_id')],
            produto_nome=products[_line_id(line, 'produto_id')].nome,
            produto_cor=products[_line_id(line, 'produto_id')].cor,
            produto_tamanho=products[_line_id(line, 'produto_id')].get_tamanho_display()
            if products[_line_id(line, 'produto_id')].tamanho else '',
            produto_codigo_barras=products[_line_id(line, 'produto_id')].codigo_barras or '',
            quantidade=_line_decimal(line, 'quantidade'),
            custo_unitario=_line_decimal(line, 'custo_unitario'),
        )
        for line in lines
    ])


def _create_event(
    *, pedido: PedidoCompra, actor, operation: str, reason: str,
    key: str, signature: str, data: dict[str, object],
) -> EventoPedidoCompra:
    with authorized_purchase_append():
        return EventoPedidoCompra.objects.create(
            pedido=pedido,
            responsavel=actor,
            operacao=operation,
            motivo=reason,
            chave_idempotencia=key,
            assinatura=signature,
            dados=data,
        )


def _event_replay(event: EventoPedidoCompra, signature: str, operation: str) -> PedidoCompra:
    if event.operacao != operation or event.assinatura != signature:
        raise PurchaseOperationError('Essa chave de idempotência já foi usada com outros dados.')
    try:
        return PedidoCompra.objects.get(pk=event.dados['pedido_id'])
    except (KeyError, TypeError, ValueError, PedidoCompra.DoesNotExist) as error:
        raise PurchaseOperationError('O histórico de Compras está inconsistente.') from error


def _claim_order_version(pedido_id: int, version: int) -> bool:
    return (
        PedidoCompra.objects.filter(pk=pedido_id, versao=version)
        .update(versao=F('versao') + 1)
        == 1
    )


def _is_recoverable_sqlite_lock(error: OperationalError) -> bool:
    if connections['default'].vendor != 'sqlite':
        return False
    cause: BaseException | None = error.__cause__
    while cause is not None:
        if isinstance(cause, sqlite3.Error):
            code = getattr(cause, 'sqlite_errorcode', None)
            if isinstance(code, int):
                return code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
            name = getattr(cause, 'sqlite_errorname', None)
            return isinstance(name, str) and name.startswith(('SQLITE_BUSY', 'SQLITE_LOCKED'))
        cause = cause.__cause__
    return False


def _raise_lock_as_business_error(error: OperationalError) -> None:
    if _is_recoverable_sqlite_lock(error):
        raise PurchaseOperationError(
            'O banco está ocupado. Atualize e tente novamente usando a mesma chave.',
        ) from error
    raise error


def criar_rascunho(
    *, actor, fornecedor_id: object, lines: Iterable[Mapping[str, object] | Sequence[object]],
    observation: str = '', reason: str, idempotency_key: str | None,
) -> PurchaseResult:
    _require_manager(actor)
    key = _key(idempotency_key)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    normalized_observation = _optional_text(observation, 'A observação', 2000)
    normalized_lines = _normalize_lines(lines)
    signature = _signature([
        OP_CREATE,
        actor.pk,
        str(fornecedor_id),
        normalized_observation,
        normalized_reason,
        _serializable_lines(normalized_lines),
    ])
    try:
        with transaction.atomic():
            existing = EventoPedidoCompra.objects.filter(chave_idempotencia=key).first()
            if existing:
                return PurchaseResult(
                    _event_replay(existing, signature, OP_CREATE),
                    False,
                )
            supplier = _active_supplier(fornecedor_id)
            products = _locked_products(_line_id(line, 'produto_id') for line in normalized_lines)
            pedido = PedidoCompra.objects.create(
                fornecedor=supplier,
                fornecedor_nome=supplier.nome,
                fornecedor_documento=supplier.documento or '',
                responsavel=actor,
                observacao=normalized_observation,
            )
            _append_items(pedido, normalized_lines, products)
            _create_event(
                pedido=pedido,
                actor=actor,
                operation=OP_CREATE,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={'pedido_id': pedido.pk, 'lines': _serializable_lines(normalized_lines)},
            )
    except OperationalError as error:
        _raise_lock_as_business_error(error)
    return PurchaseResult(pedido, True)


def editar_rascunho(
    *, actor, pedido_id: int, fornecedor_id: object,
    lines: Iterable[Mapping[str, object] | Sequence[object]], observation: str = '',
    reason: str, idempotency_key: str | None, expected_version: int | None = None,
) -> PurchaseResult:
    _require_manager(actor)
    key = _key(idempotency_key)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    normalized_observation = _optional_text(observation, 'A observação', 2000)
    normalized_lines = _normalize_lines(lines)
    signature = _signature([
        OP_EDIT,
        actor.pk,
        pedido_id,
        str(fornecedor_id),
        normalized_observation,
        normalized_reason,
        _serializable_lines(normalized_lines),
    ])
    try:
        with transaction.atomic():
            existing = EventoPedidoCompra.objects.filter(chave_idempotencia=key).first()
            if existing:
                return PurchaseResult(
                    _event_replay(existing, signature, OP_EDIT),
                    False,
                )
            try:
                pedido = PedidoCompra.objects.get(pk=pedido_id)
            except PedidoCompra.DoesNotExist as error:
                raise PurchaseOperationError('Pedido de compra não encontrado.') from error
            if pedido.estado != PedidoCompra.Estado.RASCUNHO:
                raise PurchaseOperationError('Somente rascunhos podem ser alterados.')
            version = pedido.versao if expected_version is None else expected_version
            supplier = _active_supplier(fornecedor_id)
            products = _locked_products(_line_id(line, 'produto_id') for line in normalized_lines)
            if not _claim_order_version(pedido.pk, version):
                raise PurchaseOperationError('O pedido foi alterado. Atualize e tente novamente.')
            pedido.fornecedor = supplier
            pedido.fornecedor_nome = supplier.nome
            pedido.fornecedor_documento = supplier.documento or ''
            pedido.observacao = normalized_observation
            pedido.versao = version + 1
            pedido.save(update_fields=(
                'fornecedor', 'fornecedor_nome', 'fornecedor_documento',
                'observacao', 'versao', 'atualizado_em',
            ))
            pedido.itens.all().delete()
            _append_items(pedido, normalized_lines, products)
            _create_event(
                pedido=pedido,
                actor=actor,
                operation=OP_EDIT,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={'pedido_id': pedido.pk, 'lines': _serializable_lines(normalized_lines)},
            )
    except OperationalError as error:
        _raise_lock_as_business_error(error)
    return PurchaseResult(pedido, True)


def confirmar_pedido(
    *, actor, pedido_id: int, reason: str, idempotency_key: str | None,
    expected_version: int | None = None,
) -> PurchaseResult:
    _require_manager(actor)
    key = _key(idempotency_key)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    signature = _signature([
        OP_CONFIRM,
        actor.pk,
        pedido_id,
        normalized_reason,
    ])
    try:
        with transaction.atomic():
            existing = EventoPedidoCompra.objects.filter(chave_idempotencia=key).first()
            if existing:
                return PurchaseResult(
                    _event_replay(existing, signature, OP_CONFIRM),
                    False,
                )
            try:
                pedido = PedidoCompra.objects.get(pk=pedido_id)
            except PedidoCompra.DoesNotExist as error:
                raise PurchaseOperationError('Pedido de compra não encontrado.') from error
            if pedido.estado != PedidoCompra.Estado.RASCUNHO:
                raise PurchaseOperationError('Somente rascunhos podem ser confirmados.')
            if not pedido.itens.exists():
                raise PurchaseOperationError('Adicione pelo menos um item antes de confirmar.')
            _active_supplier(pedido.fornecedor_id)
            _locked_products(pedido.itens.values_list('produto_id', flat=True))
            version = pedido.versao if expected_version is None else expected_version
            if not _claim_order_version(pedido.pk, version):
                raise PurchaseOperationError('O pedido foi alterado. Atualize e tente novamente.')
            pedido.estado = PedidoCompra.Estado.CONFIRMADO
            pedido.confirmado_em = timezone.now()
            pedido.versao = version + 1
            pedido.save(update_fields=('estado', 'confirmado_em', 'versao', 'atualizado_em'))
            _create_event(
                pedido=pedido,
                actor=actor,
                operation=OP_CONFIRM,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={'pedido_id': pedido.pk},
            )
    except OperationalError as error:
        _raise_lock_as_business_error(error)
    return PurchaseResult(pedido, True)


def _received_totals(item_ids: Iterable[int]) -> dict[int, Decimal]:
    rows = (
        ItemRecebimentoCompra.objects.filter(item_pedido_id__in=set(item_ids))
        .values('item_pedido_id')
        .annotate(total=Sum('quantidade'))
    )
    return {row['item_pedido_id']: row['total'] for row in rows}


def _receipt_replay(receipt: RecebimentoCompra, signature: str) -> ReceiptResult:
    if receipt.assinatura != signature:
        raise PurchaseOperationError('Essa chave de recebimento já foi usada com outros dados.')
    return ReceiptResult(receipt, receipt.pedido, False)


def receber_pedido(
    *, actor, pedido_id: int,
    lines: Iterable[Mapping[str, object] | Sequence[object]],
    referencia_documental: str = '', reason: str, idempotency_key: str | None,
    expected_version: int | None = None,
) -> ReceiptResult:
    _require_manager(actor)
    key = _key(idempotency_key)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    reference = _optional_text(referencia_documental, 'A referência documental', 160)
    normalized_lines = _normalize_receipt_lines(lines)
    signature = _signature([
        OP_RECEIVE,
        actor.pk,
        pedido_id,
        reference,
        normalized_reason,
        _serializable_lines(normalized_lines),
    ])
    try:
        with transaction.atomic():
            event = EventoPedidoCompra.objects.filter(chave_idempotencia=key).first()
            if event:
                if event.operacao != OP_RECEIVE or event.assinatura != signature:
                    raise PurchaseOperationError('Essa chave de idempotência já foi usada com outros dados.')
                try:
                    receipt = RecebimentoCompra.objects.get(chave_idempotencia=key)
                except RecebimentoCompra.DoesNotExist as error:
                    raise PurchaseOperationError('O histórico de recebimento está inconsistente.') from error
                return _receipt_replay(receipt, signature)
            existing = RecebimentoCompra.objects.filter(chave_idempotencia=key).first()
            if existing:
                return _receipt_replay(existing, signature)
            try:
                pedido = PedidoCompra.objects.get(pk=pedido_id)
            except PedidoCompra.DoesNotExist as error:
                raise PurchaseOperationError('Pedido de compra não encontrado.') from error
            if pedido.estado not in (
                PedidoCompra.Estado.CONFIRMADO,
                PedidoCompra.Estado.PARCIALMENTE_RECEBIDO,
            ):
                raise PurchaseOperationError('O pedido não possui saldo recebível.')
            supplier = _active_supplier(pedido.fornecedor_id)
            item_ids = [_line_id(line, 'item_id') for line in normalized_lines]
            items = {
                item.pk: item
                for item in ItemPedidoCompra.objects.select_related('produto').filter(
                    pedido=pedido,
                    pk__in=item_ids,
                )
            }
            if len(items) != len(item_ids):
                raise PurchaseOperationError('Um dos itens não pertence ao pedido informado.')
            products = _locked_products(item.produto_id for item in items.values())
            version = pedido.versao if expected_version is None else expected_version
            if not _claim_order_version(pedido.pk, version):
                existing = RecebimentoCompra.objects.filter(chave_idempotencia=key).first()
                if existing:
                    return _receipt_replay(existing, signature)
                raise PurchaseOperationError('O pedido foi alterado. Atualize e tente novamente.')
            totals = _received_totals(item_ids)
            for line in normalized_lines:
                item = items[_line_id(line, 'item_id')]
                received = totals.get(item.pk, Decimal('0.00'))
                if received + _line_decimal(line, 'quantidade') > item.quantidade:
                    raise PurchaseOperationError(
                        f'A quantidade recebida de {item.produto_nome} supera o saldo pendente.',
                    )
            stock_result = registrar_entrada(
                actor=actor,
                product_ids=[str(products[items[_line_id(line, 'item_id')].produto_id].pk) for line in normalized_lines],
                quantities=[str(_line_decimal(line, 'quantidade')) for line in normalized_lines],
                reason='COMPRA',
                observation=f'Pedido de compra {pedido.public_id}' + (
                    f' — {reference}' if reference else ''
                ),
                idempotency_key=key,
                supplier_id=supplier.pk,
            )
            with authorized_purchase_append():
                receipt = RecebimentoCompra.objects.create(
                    pedido=pedido,
                    entrada=stock_result.movement,
                    responsavel=actor,
                    referencia_documental=reference,
                    chave_idempotencia=key,
                    assinatura=signature,
                )
                for line in normalized_lines:
                    item = items[_line_id(line, 'item_id')]
                    ItemRecebimentoCompra.objects.create(
                        recebimento=receipt,
                        item_pedido=item,
                        quantidade=_line_decimal(line, 'quantidade'),
                        custo_unitario=item.custo_unitario,
                    )
            all_items = list(pedido.itens.all())
            received_after = _received_totals(item.pk for item in all_items)
            received_after.update({
                _line_id(line, 'item_id'): totals.get(_line_id(line, 'item_id'), Decimal('0.00'))
                + _line_decimal(line, 'quantidade')
                for line in normalized_lines
            })
            pedido.estado = (
                PedidoCompra.Estado.RECEBIDO
                if all(received_after.get(item.pk, Decimal('0.00')) == item.quantidade for item in all_items)
                else PedidoCompra.Estado.PARCIALMENTE_RECEBIDO
            )
            pedido.versao = version + 1
            pedido.save(update_fields=('estado', 'versao', 'atualizado_em'))
            _create_event(
                pedido=pedido,
                actor=actor,
                operation=OP_RECEIVE,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={'pedido_id': pedido.pk, 'recebimento_id': receipt.pk},
            )
    except IntegrityError as error:
        existing = RecebimentoCompra.objects.filter(chave_idempotencia=key).first()
        if existing:
            return _receipt_replay(existing, signature)
        raise PurchaseOperationError('Não foi possível registrar o recebimento.') from error
    except OperationalError as error:
        _raise_lock_as_business_error(error)
    return ReceiptResult(receipt, pedido, True)


def cancelar_pedido(
    *, actor, pedido_id: int, reason: str, idempotency_key: str | None,
    expected_version: int | None = None,
) -> PurchaseResult:
    _require_manager(actor)
    key = _key(idempotency_key)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    signature = _signature([
        OP_CANCEL,
        actor.pk,
        pedido_id,
        normalized_reason,
    ])
    try:
        with transaction.atomic():
            existing = EventoPedidoCompra.objects.filter(chave_idempotencia=key).first()
            if existing:
                return PurchaseResult(
                    _event_replay(existing, signature, OP_CANCEL),
                    False,
                )
            try:
                pedido = PedidoCompra.objects.get(pk=pedido_id)
            except PedidoCompra.DoesNotExist as error:
                raise PurchaseOperationError('Pedido de compra não encontrado.') from error
            if pedido.estado not in (
                PedidoCompra.Estado.CONFIRMADO,
                PedidoCompra.Estado.PARCIALMENTE_RECEBIDO,
            ):
                raise PurchaseOperationError('Somente pedidos confirmados com saldo pendente podem ser cancelados.')
            version = pedido.versao if expected_version is None else expected_version
            if not _claim_order_version(pedido.pk, version):
                raise PurchaseOperationError('O pedido foi alterado. Atualize e tente novamente.')
            CancelamentoPedidoCompra.objects.create(
                pedido=pedido,
                responsavel=actor,
                motivo=normalized_reason,
                chave_idempotencia=key,
                assinatura=signature,
            )
            pedido.estado = PedidoCompra.Estado.CANCELADO
            pedido.versao = version + 1
            pedido.save(update_fields=('estado', 'versao', 'atualizado_em'))
            _create_event(
                pedido=pedido,
                actor=actor,
                operation=OP_CANCEL,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={'pedido_id': pedido.pk},
            )
    except IntegrityError as error:
        existing = CancelamentoPedidoCompra.objects.filter(chave_idempotencia=key).first()
        if existing:
            return PurchaseResult(existing.pedido, False)
        raise PurchaseOperationError('Não foi possível cancelar o pedido.') from error
    except OperationalError as error:
        _raise_lock_as_business_error(error)
    return PurchaseResult(pedido, True)
