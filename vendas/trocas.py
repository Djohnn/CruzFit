from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Sequence

from django.core import signing
from django.db import OperationalError, transaction
from django.db.models import Sum
from django.utils.formats import number_format

from caixa.models import (
    PagamentoRestituicao,
    PagamentoRestituicaoTroca,
    PagamentoTroca,
    PagamentoVenda,
    RecebimentoTroca,
    RecebimentoVenda,
    RestituicaoTroca,
    SessaoCaixa,
)
from estoque.models import Produto
from estoque.services import TWO_PLACES

from .models import (
    ItemTrocaDestino,
    ItemTrocaOrigem,
    ItemVenda,
    ItemVendaDevolucao,
    Venda,
    VendaCancelamento,
    VendaTroca,
)

LEGACY_SALE_MESSAGE = (
    'Esta venda antiga não possui recebimento registrado no Caixa. '
    'A troca pelo sistema não está disponível.'
)


class TrocaOperationError(ValueError):
    """Validation, authorization, or consistency error in an exchange."""


@dataclass(frozen=True)
class TrocaPreviewLine:
    item_id: int
    product_id: int
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    credit_total: Decimal


@dataclass(frozen=True)
class TrocaDestinationLine:
    product_id: int
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal


@dataclass(frozen=True)
class TrocaPreview:
    sale_id: int
    session_id: int
    session_version: int
    credit_total: Decimal
    destination_total: Decimal
    difference: Decimal
    source_lines: tuple[TrocaPreviewLine, ...]
    destination_lines: tuple[TrocaDestinationLine, ...]
    payment_lines: tuple[dict[str, object], ...]
    review_signature: str


@dataclass(frozen=True)
class TrocaResult:
    troca: VendaTroca
    created: bool


def _require_manager(actor) -> None:
    if not (
        actor
        and actor.is_authenticated
        and actor.is_active
        and actor.pk
        and (actor.is_superuser or getattr(actor, 'cargo', '') == 'G')
    ):
        raise TrocaOperationError('Apenas gerente ativo pode registrar trocas.')


def _required_text(value: str | None, label: str, limit: int = 500) -> str:
    normalized = (value or '').strip()
    if not normalized or len(normalized) > limit:
        raise TrocaOperationError(f'Informe {label} com até {limit} caracteres.')
    return normalized


def _parse_ids_and_quantities(
    ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
    label: str,
) -> list[tuple[int, Decimal]]:
    raw_ids = list(ids)
    raw_quantities = list(quantities)
    if not raw_ids or len(raw_ids) != len(raw_quantities):
        raise TrocaOperationError(f'Adicione pelo menos um {label}.')

    result: list[tuple[int, Decimal]] = []
    selected: set[int] = set()
    for raw_id, raw_quantity in zip(raw_ids, raw_quantities):
        try:
            identifier = int(raw_id)
            quantity = Decimal(str(raw_quantity).replace(',', '.'))
            normalized = quantity.quantize(TWO_PLACES)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise TrocaOperationError(
                'Informe quantidades maiores que zero com no máximo duas casas decimais.',
            ) from error
        if (
            identifier <= 0
            or not quantity.is_finite()
            or quantity <= 0
            or quantity != normalized
        ):
            raise TrocaOperationError(
                'Informe quantidades maiores que zero com no máximo duas casas decimais.',
            )
        if identifier in selected:
            raise TrocaOperationError(f'Não repita o mesmo {label}.')
        selected.add(identifier)
        result.append((identifier, normalized))
    return result


def _sale_or_error(sale_id: int) -> Venda:
    try:
        sale = Venda.objects.get(pk=sale_id)
    except Venda.DoesNotExist as error:
        raise TrocaOperationError('Venda não encontrada.') from error
    if VendaCancelamento.objects.filter(venda_id=sale.pk).exists():
        raise TrocaOperationError('Uma venda cancelada não pode ser trocada.')
    if not RecebimentoVenda.objects.filter(venda_id=sale.pk).exists():
        raise TrocaOperationError(LEGACY_SALE_MESSAGE)
    return sale


def _session_or_error(sessao: SessaoCaixa | int | str) -> SessaoCaixa:
    try:
        session_id = sessao.pk if isinstance(sessao, SessaoCaixa) else int(sessao)
        session = SessaoCaixa.objects.get(pk=session_id)
    except (TypeError, ValueError, SessaoCaixa.DoesNotExist) as error:
        raise TrocaOperationError('Selecione uma sessão de caixa válida.') from error
    if not session.aberta:
        raise TrocaOperationError('A sessão de caixa está fechada.')
    return session


def _returned_quantity(item: ItemVenda) -> Decimal:
    sale_returns = ItemVendaDevolucao.objects.filter(
        item_venda=item,
    ).aggregate(total=Sum('quantidade'))['total'] or Decimal('0.00')
    exchange_returns = ItemTrocaOrigem.objects.filter(
        item_venda=item,
    ).aggregate(total=Sum('quantidade'))['total'] or Decimal('0.00')
    return (sale_returns + exchange_returns).quantize(TWO_PLACES)


def _returned_credit(item: ItemVenda) -> Decimal:
    sale_returns = ItemVendaDevolucao.objects.filter(
        item_venda=item,
    ).aggregate(total=Sum('total'))['total'] or Decimal('0.00')
    exchange_returns = ItemTrocaOrigem.objects.filter(
        item_venda=item,
    ).aggregate(total=Sum('credito_total'))['total'] or Decimal('0.00')
    return (sale_returns + exchange_returns).quantize(TWO_PLACES)


def _incremental_credit(
    item: ItemVenda,
    previous_quantity: Decimal,
    previous_credit: Decimal,
    quantity: Decimal,
) -> Decimal:
    cumulative = previous_quantity + quantity
    if cumulative == item.quantidade:
        target = item.total_liquido
    else:
        target = (
            item.total_liquido * cumulative / item.quantidade
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    credit = (target - previous_credit).quantize(TWO_PLACES)
    if credit < 0:
        raise TrocaOperationError(
            'O histórico de devoluções e trocas excede o líquido do item; revise manualmente.',
        )
    return credit


def _review_signature(payload: dict[str, object]) -> str:
    return signing.dumps(payload, salt='vendas.troca-review', compress=True)


def _decode_review_payload(token: str) -> dict[str, object]:
    try:
        payload = signing.loads(token, salt='vendas.troca-review')
    except (signing.BadSignature, TypeError) as error:
        raise TrocaOperationError(
            'A revisão não é válida. Confira os valores novamente.',
        ) from error
    if not isinstance(payload, dict):
        raise TrocaOperationError(
            'A revisão não é válida. Confira os valores novamente.',
        )
    return payload


def _review_payload_from_troca(troca: VendaTroca) -> dict[str, object]:
    source_lines = list(troca.itens_origem.order_by('pk'))
    destination_lines = list(troca.itens_destino.order_by('pk'))
    payment_lines: list[list[object]] = []
    manual_confirmation = False
    receipt = RecebimentoTroca.objects.filter(troca=troca).first()
    if receipt is not None:
        payment_lines = [
            [
                '',
                ('CARTAO_' + payment.modalidade_cartao) if payment.modalidade_cartao else payment.forma,
                str(payment.valor_aplicado),
                str(payment.valor_entregue),
                str(payment.troco),
            ]
            for payment in PagamentoTroca.objects.filter(
                recebimento=receipt,
            ).order_by('forma', 'pk')
        ]
    else:
        refund = RestituicaoTroca.objects.filter(troca=troca).first()
        if refund is not None:
            manual_confirmation = bool(refund.confirmacao_manual)
            payment_lines = [
                [
                    payment.pagamento_original_id,
                    payment.pagamento_original.forma,
                    str(payment.valor),
                    '',
                    '',
                ]
                for payment in PagamentoRestituicaoTroca.objects.select_related(
                    'pagamento_original',
                ).filter(restituicao=refund).order_by('pagamento_original_id', 'pk')
            ]
    return {
        'actor': troca.responsavel_id,
        'session': troca.sessao_id,
        'session_version': troca.versao_caixa,
        'sale': troca.venda_id,
        'reason': troca.motivo,
        'sources': [
            [line.item_venda_id, str(line.quantidade), str(line.credito_total)]
            for line in source_lines
        ],
        'destinations': [
            [
                line.produto_id,
                str(line.quantidade),
                str(line.preco_unitario),
                str(line.total),
            ]
            for line in destination_lines
        ],
        'credit_total': str(troca.credito_total),
        'destination_total': str(troca.valor_novos),
        'difference': str(troca.diferenca),
        'payments': payment_lines,
        'manual_confirmation': manual_confirmation,
    }


def _review_selection(payload: dict[str, object], key: str) -> list[tuple[int, str]]:
    raw_lines = payload.get(key)
    if not isinstance(raw_lines, list):
        return []
    try:
        return [(int(line[0]), str(line[1])) for line in raw_lines]
    except (IndexError, TypeError, ValueError):
        return []


def _submitted_payment_payload(
    payments: list[dict[str, object]], difference: Decimal,
) -> list[list[object]]:
    if difference > 0:
        from caixa.services import CaixaOperationError, previsualizar_recebimento

        try:
            preview = previsualizar_recebimento(payments=payments, total=difference)
        except (CaixaOperationError, ValueError) as error:
            raise TrocaOperationError(
                'A chave de troca já foi usada com outros dados.',
            ) from error
        return [
            [
                '',
                line.get('forma', ''),
                str(line.get('valor_aplicado', '')),
                str(line.get('valor_entregue', '')),
                str(line.get('troco', '')),
            ]
            for line in preview.pagamentos
        ]
    if payments:
        raise TrocaOperationError('A chave de troca já foi usada com outros dados.')
    return []


def _request_matches_review_payload(
    *,
    payload: dict[str, object],
    actor,
    sessao: SessaoCaixa | int | str,
    sale_id: int,
    source_item_ids: Sequence[str] | Iterable[str],
    source_quantities: Sequence[str] | Iterable[str],
    destination_product_ids: Sequence[str] | Iterable[str],
    destination_quantities: Sequence[str] | Iterable[str],
    motivo: str,
    pagamentos: list[dict[str, object]],
    confirmacao_manual: bool,
    difference: Decimal,
) -> bool:
    try:
        session_id = sessao.pk if isinstance(sessao, SessaoCaixa) else int(sessao)
        reason = _required_text(motivo, 'um motivo')
        source_lines = _parse_ids_and_quantities(
            source_item_ids, source_quantities, 'item original',
        )
        destination_lines = _parse_ids_and_quantities(
            destination_product_ids, destination_quantities, 'produto substituto',
        )
        submitted_payments = _submitted_payment_payload(pagamentos, difference)
    except (TrocaOperationError, TypeError, ValueError):
        return False
    return (
        payload.get('actor') == actor.pk
        and payload.get('session') == session_id
        and payload.get('sale') == sale_id
        and payload.get('reason') == reason
        and _review_selection(payload, 'sources') == [
            (item_id, str(quantity)) for item_id, quantity in source_lines
        ]
        and _review_selection(payload, 'destinations') == [
            (product_id, str(quantity))
            for product_id, quantity in destination_lines
        ]
        and payload.get('payments') == submitted_payments
        and payload.get('manual_confirmation') is bool(confirmacao_manual)
    )


def _review_payload_matches_troca(
    troca: VendaTroca,
    payload: dict[str, object],
) -> bool:
    expected = _review_payload_from_troca(troca)
    if troca.diferenca >= 0:
        expected['manual_confirmation'] = payload.get('manual_confirmation')
    return payload == expected


def _refund_preview_lines(sale_id: int, amount: Decimal):
    receipt = RecebimentoVenda.objects.filter(venda_id=sale_id).first()
    if receipt is None:
        raise TrocaOperationError(LEGACY_SALE_MESSAGE)
    payments = list(PagamentoVenda.objects.filter(recebimento=receipt).order_by('pk'))
    payment_ids = [payment.pk for payment in payments]
    refunded = {
        row['pagamento_original_id']: row['total'] or Decimal('0.00')
        for row in PagamentoRestituicao.objects.filter(
            pagamento_original_id__in=payment_ids,
        ).values('pagamento_original_id').annotate(total=Sum('valor'))
    }
    exchanged = {
        row['pagamento_original_id']: row['total'] or Decimal('0.00')
        for row in PagamentoRestituicaoTroca.objects.filter(
            pagamento_original_id__in=payment_ids,
        ).values('pagamento_original_id').annotate(total=Sum('valor'))
    }
    capacities = [
        (payment, int((
            payment.valor_aplicado
            - refunded.get(payment.pk, Decimal('0.00'))
            - exchanged.get(payment.pk, Decimal('0.00'))
        ) * 100))
        for payment in payments
        if payment.valor_aplicado > (
            refunded.get(payment.pk, Decimal('0.00'))
            + exchanged.get(payment.pk, Decimal('0.00'))
        )
    ]
    total_capacity = sum(capacity for _, capacity in capacities)
    amount_cents = int(amount * 100)
    if not capacities or amount_cents <= 0 or amount_cents > total_capacity:
        raise TrocaOperationError(
            'A restituição da troca supera o valor ainda disponível nas formas originais.',
        )
    shares: dict[int, int] = {}
    remainders: dict[int, int] = {}
    by_id = {payment.pk: payment for payment, _ in capacities}
    for payment, capacity in capacities:
        shares[payment.pk], remainders[payment.pk] = divmod(
            amount_cents * capacity, total_capacity,
        )
    cents_left = amount_cents - sum(shares.values())
    for payment_id in sorted(shares, key=lambda value: (-remainders[value], value))[:cents_left]:
        shares[payment_id] += 1
    return tuple({
        'payment_id': payment_id,
        'forma': by_id[payment_id].forma,
        'forma_display': by_id[payment_id].get_forma_display(),
        'valor': (Decimal(cents) / 100).quantize(TWO_PLACES),
    } for payment_id, cents in sorted(shares.items()) if cents > 0)


def previsualizar_troca(
    *,
    actor,
    sessao: SessaoCaixa | int | str,
    sale_id: int,
    source_item_ids: Sequence[str] | Iterable[str],
    source_quantities: Sequence[str] | Iterable[str],
    destination_product_ids: Sequence[str] | Iterable[str],
    destination_quantities: Sequence[str] | Iterable[str],
    motivo: str,
    pagamentos: Iterable[dict[str, object]] | None = None,
    confirmacao_manual: bool = False,
) -> TrocaPreview:
    _require_manager(actor)
    payment_payload = list(pagamentos or [])
    reason = _required_text(motivo, 'um motivo')
    source_lines = _parse_ids_and_quantities(
        source_item_ids, source_quantities, 'item original',
    )
    destination_lines = _parse_ids_and_quantities(
        destination_product_ids, destination_quantities, 'produto substituto',
    )
    session = _session_or_error(sessao)

    with transaction.atomic():
        sale = _sale_or_error(sale_id)
        items = {
            item.pk: item
            for item in ItemVenda.objects.select_related('produto').filter(
                venda=sale, pk__in=[item_id for item_id, _ in source_lines],
            )
        }
        if len(items) != len(source_lines):
            raise TrocaOperationError('Um dos itens não pertence à venda informada.')

        source_preview: list[TrocaPreviewLine] = []
        returned_by_product: dict[int, Decimal] = {}
        for item_id, quantity in source_lines:
            item = items[item_id]
            if not item.produto.ativo:
                raise TrocaOperationError(
                    f'O produto {item.produto.nome} não está apto a retornar ao estoque disponível.',
                )
            previous_quantity = _returned_quantity(item)
            eligible = item.quantidade - previous_quantity
            if quantity > eligible:
                raise TrocaOperationError(
                    f'A quantidade trocada de {item.produto.nome} supera o saldo elegível.',
                )
            credit = _incremental_credit(
                item, previous_quantity, _returned_credit(item), quantity,
            )
            source_preview.append(TrocaPreviewLine(
                item_id=item.pk,
                product_id=item.produto_id,
                product_name=item.produto.nome,
                quantity=quantity,
                unit_price=item.preco_unitario,
                credit_total=credit,
            ))
            returned_by_product[item.produto_id] = (
                returned_by_product.get(item.produto_id, Decimal('0.00')) + quantity
            )

        products = {
            product.pk: product
            for product in Produto.objects.select_for_update().filter(
                pk__in=[product_id for product_id, _ in destination_lines],
            )
        }
        if len(products) != len(destination_lines):
            raise TrocaOperationError('Um dos produtos substitutos não existe.')

        destination_preview: list[TrocaDestinationLine] = []
        for product_id, quantity in destination_lines:
            product = products[product_id]
            if not product.ativo:
                raise TrocaOperationError('Produto substituto inativo não pode ser usado.')
            available = product.quantidade + returned_by_product.get(product.pk, Decimal('0.00'))
            if quantity > available:
                raise TrocaOperationError(f'Estoque insuficiente para {product.nome}.')
            total = (product.preco_venda * quantity).quantize(TWO_PLACES)
            destination_preview.append(TrocaDestinationLine(
                product_id=product.pk,
                product_name=product.nome,
                quantity=quantity,
                unit_price=product.preco_venda,
                total=total,
            ))

    credit_total = sum(
        (line.credit_total for line in source_preview), Decimal('0.00'),
    ).quantize(TWO_PLACES)
    destination_total = sum(
        (line.total for line in destination_preview), Decimal('0.00'),
    ).quantize(TWO_PLACES)
    difference = (destination_total - credit_total).quantize(TWO_PLACES)
    payment_lines: tuple[dict[str, object], ...]
    if difference > 0:
        if not payment_payload:
            payment_lines = ()
        else:
            from caixa.services import CaixaOperationError, previsualizar_recebimento

            try:
                payment_preview = previsualizar_recebimento(
                    payments=payment_payload, total=difference,
                )
            except CaixaOperationError as error:
                amount = number_format(difference, decimal_pos=2, use_l10n=True)
                raise TrocaOperationError(
                    f'Diferença a pagar: R$ {amount}. {error}',
                ) from error
            payment_lines = payment_preview.pagamentos
    elif difference < 0:
        if payment_payload:
            raise TrocaOperationError(
                'A restituição da troca usa somente as formas originais da venda.',
            )
        payment_lines = _refund_preview_lines(sale_id, abs(difference))
    else:
        if payment_payload:
            raise TrocaOperationError('Uma troca sem diferença não possui pagamento.')
        payment_lines = ()
    payload = {
        'actor': actor.pk,
        'session': session.pk,
        'session_version': session.versao,
        'sale': sale_id,
        'reason': reason,
        'sources': [
            [line.item_id, str(line.quantity), str(line.credit_total)]
            for line in source_preview
        ],
        'destinations': [
            [line.product_id, str(line.quantity), str(line.unit_price), str(line.total)]
            for line in destination_preview
        ],
        'credit_total': str(credit_total),
        'destination_total': str(destination_total),
        'difference': str(difference),
        'payments': [
            [
                line.get('payment_id', ''), line.get('forma', ''),
                str(line.get('valor_aplicado', line.get('valor', ''))),
                str(line.get('valor_entregue', '')),
                str(line.get('troco', '')),
            ]
            for line in payment_lines
        ],
        'manual_confirmation': bool(confirmacao_manual),
    }
    return TrocaPreview(
        sale_id=sale_id,
        session_id=session.pk,
        session_version=session.versao,
        credit_total=credit_total,
        destination_total=destination_total,
        difference=difference,
        source_lines=tuple(source_preview),
        destination_lines=tuple(destination_preview),
        payment_lines=payment_lines,
        review_signature=_review_signature(payload),
    )


def confirmar_troca(
    *,
    actor,
    sessao: SessaoCaixa | int | str,
    sale_id: int,
    source_item_ids: Sequence[str] | Iterable[str],
    source_quantities: Sequence[str] | Iterable[str],
    destination_product_ids: Sequence[str] | Iterable[str],
    destination_quantities: Sequence[str] | Iterable[str],
    motivo: str,
    idempotency_key: str | None,
    revisao_assinatura: str,
    pagamentos: Iterable[dict[str, object]] | None,
    confirmacao_manual: bool = False,
):
    _require_manager(actor)
    key = _required_text(idempotency_key, 'uma chave de operação', 64)
    _sale_or_error(sale_id)
    payments = list(pagamentos or [])
    received_payload = _decode_review_payload(revisao_assinatura)
    signature = hashlib.sha256(revisao_assinatura.encode('utf-8')).hexdigest()

    with transaction.atomic():
        existing = VendaTroca.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            if not _request_matches_review_payload(
                payload=received_payload,
                actor=actor,
                sessao=sessao,
                sale_id=sale_id,
                source_item_ids=source_item_ids,
                source_quantities=source_quantities,
                destination_product_ids=destination_product_ids,
                destination_quantities=destination_quantities,
                motivo=motivo,
                pagamentos=payments,
                confirmacao_manual=confirmacao_manual,
                difference=existing.diferenca,
            ) or not _review_payload_matches_troca(existing, received_payload):
                raise TrocaOperationError(
                    'Essa chave de troca já foi usada com outros dados.',
                )
            return TrocaResult(existing, created=False)

        # Serializa a elegibilidade das unidades da venda antes de recalcular
        # o crédito. Isso impede duas confirmações concorrentes de aprovarem
        # a mesma unidade entre a revisão e a escrita dos efeitos.
        Venda.objects.select_for_update().get(pk=sale_id)
        preview = previsualizar_troca(
            actor=actor,
            sessao=sessao,
            sale_id=sale_id,
            source_item_ids=source_item_ids,
            source_quantities=source_quantities,
            destination_product_ids=destination_product_ids,
            destination_quantities=destination_quantities,
            motivo=motivo,
            pagamentos=payments,
            confirmacao_manual=confirmacao_manual,
        )
        expected_payload = _decode_review_payload(preview.review_signature)
        if received_payload != expected_payload:
            raise TrocaOperationError(
                'A revisão não é válida. Confira os valores novamente.',
            )
        if preview.difference > 0 and not preview.payment_lines:
            raise TrocaOperationError(
                'Informe o pagamento da diferença e revise a troca novamente.',
            )
        session = _session_or_error(sessao)
        if session.versao != preview.session_version:
            raise TrocaOperationError(
                'O caixa foi alterado. Atualize os valores e revise a troca novamente.',
            )

        from caixa.services import (
            CaixaOperationError,
            registrar_recebimento_troca,
            registrar_restituicao_troca,
            registrar_troca_sem_diferenca,
        )
        from estoque.services import (
            StockOperationError,
            registrar_troca_baixa,
            registrar_troca_entrada,
        )

        try:
            source_stock_lines = [
                (line.product_id, line.quantity) for line in preview.source_lines
            ]
            destination_stock_lines = [
                (line.product_id, line.quantity) for line in preview.destination_lines
            ]
            child_entry_key = hashlib.sha256(
                f'{key}:entrada'.encode('utf-8'),
            ).hexdigest()
            child_outflow_key = hashlib.sha256(
                f'{key}:baixa'.encode('utf-8'),
            ).hexdigest()
            entry_result = registrar_troca_entrada(
                actor=actor,
                lines=source_stock_lines,
                observation=f'Troca da Venda #{sale_id}',
                idempotency_key=child_entry_key,
            )
            outflow_result = registrar_troca_baixa(
                actor=actor,
                lines=destination_stock_lines,
                observation=f'Troca da Venda #{sale_id}',
                idempotency_key=child_outflow_key,
            )
            sale = Venda.objects.select_for_update().get(pk=sale_id)
            troca = VendaTroca.objects.create(
                venda=sale,
                sessao_id=session.pk,
                entrada=entry_result.movement,
                baixa=outflow_result.movement,
                responsavel=actor,
                motivo=_required_text(motivo, 'um motivo'),
                credito_total=preview.credit_total,
                valor_novos=preview.destination_total,
                diferenca=preview.difference,
                versao_caixa=preview.session_version,
                chave_idempotencia=key,
                assinatura=signature,
            )
            for source_line in preview.source_lines:
                item = ItemVenda.objects.get(pk=source_line.item_id)
                ItemTrocaOrigem.objects.create(
                    troca=troca,
                    item_venda=item,
                    produto_id=source_line.product_id,
                    quantidade=source_line.quantity,
                    preco_unitario=source_line.unit_price,
                    credito_total=source_line.credit_total,
                )
            for destination_line in preview.destination_lines:
                ItemTrocaDestino.objects.create(
                    troca=troca,
                    produto_id=destination_line.product_id,
                    quantidade=destination_line.quantity,
                    preco_unitario=destination_line.unit_price,
                    total=destination_line.total,
                )
            if preview.difference > 0:
                registrar_recebimento_troca(
                    actor=actor,
                    sessao=session,
                    troca=troca,
                    total=preview.difference,
                    pagamentos=payments,
                    motivo=motivo,
                    idempotency_key=key,
                    versao_esperada=session.versao,
                )
            elif preview.difference < 0:
                registrar_restituicao_troca(
                    actor=actor,
                    sessao=session,
                    troca=troca,
                    total=abs(preview.difference),
                    motivo=motivo,
                    idempotency_key=key,
                    confirmacao_manual=confirmacao_manual,
                    versao_esperada=session.versao,
                )
            else:
                registrar_troca_sem_diferenca(
                    sessao=session,
                    versao_esperada=session.versao,
                )
        except (CaixaOperationError, StockOperationError) as error:
            raise TrocaOperationError(str(error)) from error
        except OperationalError as error:
            from caixa.services import _is_recoverable_sqlite_lock

            if _is_recoverable_sqlite_lock(error):
                raise TrocaOperationError(
                    'O banco está ocupado. Atualize e tente novamente usando a mesma chave.',
                ) from error
            raise

    return TrocaResult(troca, created=True)
