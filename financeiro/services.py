from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from time import sleep
from typing import Any

from django.db import IntegrityError, OperationalError, connections, transaction
from django.db.models import Sum
from django.utils import timezone

from caixa.models import PagamentoRestituicao, PagamentoVenda

from .models import (
    AporteFinanceiro,
    ContaFinanceira,
    DepositoCartao,
    EstornoFinanceiro,
    EstornoRecebimentoCartao,
    EventoAuditoriaFinanceiro,
    LancamentoFinanceiro,
    LiquidacaoCartao,
    RecebimentoPixFinanceiro,
    RecebivelCartao,
    TransferenciaFinanceira,
)


class FinanceOperationError(ValueError):
    """Erro de negócio recuperável de uma operação financeira."""


@dataclass(frozen=True)
class ContaResult:
    conta: ContaFinanceira
    created: bool


@dataclass(frozen=True)
class AporteResult:
    aporte: AporteFinanceiro
    created: bool


@dataclass(frozen=True)
class TransferenciaResult:
    transferencia: TransferenciaFinanceira
    created: bool


@dataclass(frozen=True)
class EstornoResult:
    estorno: EstornoFinanceiro
    created: bool


@dataclass(frozen=True)
class PixResult:
    recebimento: RecebimentoPixFinanceiro
    created: bool


@dataclass(frozen=True)
class RecebivelCartaoResult:
    recebivel: RecebivelCartao
    created: bool


@dataclass(frozen=True)
class LiquidacaoCartaoResult:
    deposito: DepositoCartao
    created: bool


@dataclass(frozen=True)
class EstornoRecebimentoCartaoResult:
    estorno: EstornoRecebimentoCartao
    created: bool


ESTORNO_MAX_ATTEMPTS = 5
RECEBIMENTO_MAX_ATTEMPTS = 5


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
            return isinstance(name, str) and name.startswith(
                ('SQLITE_BUSY', 'SQLITE_LOCKED'),
            )
        cause = cause.__cause__
    return False


def _require_manager(actor: Any) -> None:
    if not (
        actor
        and actor.is_authenticated
        and actor.is_active
        and actor.pk
        and (actor.is_superuser or getattr(actor, 'cargo', '') == 'G')
    ):
        raise FinanceOperationError(
            'Apenas gerente ativo pode executar esta operação financeira.',
        )


def _required_text(value: object, label: str, limit: int) -> str:
    normalized = str(value or '').strip()
    if not normalized or len(normalized) > limit:
        raise FinanceOperationError(f'Informe {label} com até {limit} caracteres.')
    return normalized


def _money(value: object, label: str, *, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value).strip().replace(',', '.'))
        if not parsed.is_finite() or parsed < 0 or parsed > Decimal('999999999999.99'):
            raise ValueError
        rounded = parsed.quantize(Decimal('0.01'))
        if rounded != parsed or (positive and rounded <= 0):
            raise ValueError
    except (InvalidOperation, TypeError, ValueError) as error:
        qualifier = 'positivo' if positive else 'não negativo'
        raise FinanceOperationError(
            f'Informe {label} {qualifier} com até duas casas decimais.',
        ) from error
    return rounded if rounded else Decimal('0.00')


def _business_date(value: object, label: str = 'a data do movimento') -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as error:
        raise FinanceOperationError(f'Informe {label} válida no formato AAAA-MM-DD.') from error


def _key(value: object) -> str:
    return _required_text(value, 'uma chave de operação', 64)


def _signature(payload: list[object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':'), default=str).encode(
            'utf-8',
        ),
    ).hexdigest()


def _validate_movement_date(account: ContaFinanceira, movement_date: date) -> None:
    if movement_date < account.data_corte_financeiro:
        raise FinanceOperationError(
            'A data do movimento não pode ser anterior à data de corte financeiro.',
        )


def _account(value: ContaFinanceira | int) -> ContaFinanceira:
    if isinstance(value, ContaFinanceira):
        if not value.pk:
            raise FinanceOperationError('Selecione uma conta financeira válida.')
        return value
    try:
        return ContaFinanceira.objects.get(pk=int(value))
    except (ContaFinanceira.DoesNotExist, TypeError, ValueError) as error:
        raise FinanceOperationError('Selecione uma conta financeira válida.') from error


def _audit(
    *,
    action: str,
    actor: Any,
    reason: str,
    key: str,
    signature: str,
    data: dict[str, object],
) -> EventoAuditoriaFinanceiro:
    return EventoAuditoriaFinanceiro.objects.create(
        acao=action,
        responsavel=actor,
        motivo=reason,
        chave_idempotencia=key,
        assinatura=signature,
        dados=data,
    )


def _conflicting_key() -> FinanceOperationError:
    return FinanceOperationError('Essa chave de operação já foi usada com outros dados.')


def _assert_key_available(key: str, current_model: Any) -> None:
    for model in (
        ContaFinanceira,
        AporteFinanceiro,
        TransferenciaFinanceira,
        EstornoFinanceiro,
        RecebimentoPixFinanceiro,
        RecebivelCartao,
        DepositoCartao,
        EstornoRecebimentoCartao,
        EventoAuditoriaFinanceiro,
    ):
        if model is current_model:
            continue
        if model.objects.filter(chave_idempotencia=key).exists():
            raise _conflicting_key()


def _require_operational_actor(actor: Any) -> None:
    if not (
        actor
        and actor.is_authenticated
        and actor.is_active
        and actor.pk
    ):
        raise FinanceOperationError(
            'Apenas usuário ativo pode registrar o recebimento integrado.',
        )


def _payment(value: PagamentoVenda | int) -> PagamentoVenda:
    if isinstance(value, PagamentoVenda):
        if not value.pk:
            raise FinanceOperationError('Selecione um pagamento de venda válido.')
        return value
    try:
        return PagamentoVenda.objects.get(pk=int(value))
    except (PagamentoVenda.DoesNotExist, TypeError, ValueError) as error:
        raise FinanceOperationError('Selecione um pagamento de venda válido.') from error


def _approval_date(payment: PagamentoVenda) -> date:
    created_at = payment.recebimento.venda.criada_em
    if timezone.is_aware(created_at):
        return timezone.localtime(created_at).date()
    return created_at.date()


def registrar_recebiveis_cartao(
    *,
    actor: Any,
    pagamentos: Iterable[PagamentoVenda | int],
    reason: str = 'Aprovação de cartão no Caixa',
) -> tuple[RecebivelCartaoResult, ...]:
    """Criar, uma única vez, os recebíveis das parcelas aprovadas no Caixa."""
    _require_operational_actor(actor)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    results: list[RecebivelCartaoResult] = []
    with transaction.atomic():
        for raw_payment in pagamentos:
            payment = _payment(raw_payment)
            locked = PagamentoVenda.objects.select_for_update().select_related(
                'recebimento__venda',
            ).get(pk=payment.pk)
            if locked.forma != 'CARTAO':
                continue
            value = _money(locked.valor_aplicado, 'o valor bruto do cartão', positive=True)
            approval_date = _approval_date(locked)
            key = f'cartao-aprovado-{locked.pk}'
            signature = _signature([
                'CARTAO_APROVADO', locked.pk, str(value),
                approval_date.isoformat(), normalized_reason,
            ])
            existing = RecebivelCartao.objects.filter(
                chave_idempotencia=key,
            ).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key()
                results.append(RecebivelCartaoResult(existing, created=False))
                continue
            source_existing = RecebivelCartao.objects.filter(
                pagamento_origem_id=locked.pk,
            ).first()
            if source_existing:
                raise FinanceOperationError(
                    'Esse pagamento de cartão já está em A receber.',
                )
            _assert_key_available(key, RecebivelCartao)
            receivable = RecebivelCartao.objects.create(
                pagamento_origem=locked,
                valor_bruto=value,
                data_aprovacao=approval_date,
                responsavel=actor,
                motivo=normalized_reason,
                chave_idempotencia=key,
                assinatura=signature,
            )
            _audit(
                action='REGISTRAR_RECEBIVEL_CARTAO',
                actor=actor,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={
                    'recebivel_id': receivable.pk,
                    'pagamento_origem_id': locked.pk,
                    'valor_bruto': str(value),
                },
            )
            results.append(RecebivelCartaoResult(receivable, created=True))
    return tuple(results)


def confirmar_pix(
    *,
    actor: Any,
    pagamento: PagamentoVenda | int,
    account: ContaFinanceira | int,
    confirmation_date: object,
    reason: object,
    idempotency_key: object,
) -> PixResult:
    """Confirmar um Pix e creditar sua conta de destino uma única vez."""
    _require_manager(actor)
    source = _payment(pagamento)
    resolved = _account(account)
    movement = _business_date(confirmation_date)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    value = _money(source.valor_aplicado, 'o valor do Pix', positive=True)
    signature = _signature([
        'CONFIRMAR_PIX', actor.pk, source.pk, resolved.pk, str(value),
        movement.isoformat(), normalized_reason,
    ])

    for attempt in range(RECEBIMENTO_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                existing = RecebimentoPixFinanceiro.objects.filter(
                    chave_idempotencia=key,
                ).first()
                if existing:
                    if existing.assinatura != signature:
                        raise _conflicting_key()
                    return PixResult(existing, created=False)
                _assert_key_available(key, RecebimentoPixFinanceiro)
                locked_payment = PagamentoVenda.objects.select_for_update().select_related(
                    'recebimento__venda',
                ).get(pk=source.pk)
                if locked_payment.forma != 'PIX':
                    raise FinanceOperationError(
                        'O pagamento de origem não é um Pix confirmado.',
                    )
                source_existing = RecebimentoPixFinanceiro.objects.filter(
                    pagamento_origem_id=locked_payment.pk,
                ).first()
                if source_existing:
                    raise FinanceOperationError('Esse pagamento Pix já foi creditado.')
                locked_account = ContaFinanceira.objects.select_for_update().get(
                    pk=resolved.pk,
                )
                if not locked_account.ativo:
                    raise FinanceOperationError('A conta financeira está inativa.')
                _validate_movement_date(locked_account, movement)
                ledger = LancamentoFinanceiro.objects.create(
                    conta=locked_account,
                    direcao=LancamentoFinanceiro.Direcao.CREDITO,
                    valor=value,
                    data_movimento=movement,
                    descricao='Recebimento Pix',
                    motivo=normalized_reason,
                    responsavel=actor,
                )
                receipt = RecebimentoPixFinanceiro.objects.create(
                    pagamento_origem=locked_payment,
                    conta=locked_account,
                    lancamento=ledger,
                    valor=value,
                    data_confirmacao=movement,
                    responsavel=actor,
                    motivo=normalized_reason,
                    chave_idempotencia=key,
                    assinatura=signature,
                )
                _audit(
                    action='CONFIRMAR_PIX',
                    actor=actor,
                    reason=normalized_reason,
                    key=key,
                    signature=signature,
                    data={
                        'recebimento_pix_id': receipt.pk,
                        'pagamento_origem_id': locked_payment.pk,
                        'lancamento_id': ledger.pk,
                    },
                )
                return PixResult(receipt, created=True)
        except IntegrityError as error:
            existing = RecebimentoPixFinanceiro.objects.filter(
                chave_idempotencia=key,
            ).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key() from error
                return PixResult(existing, created=False)
            if RecebimentoPixFinanceiro.objects.filter(
                pagamento_origem_id=source.pk,
            ).exists():
                raise FinanceOperationError(
                    'Esse pagamento Pix já foi creditado.',
                ) from error
            _assert_key_available(key, RecebimentoPixFinanceiro)
            raise
        except OperationalError as error:
            if (
                not _is_recoverable_sqlite_lock(error)
                or attempt + 1 >= RECEBIMENTO_MAX_ATTEMPTS
            ):
                raise
            sleep(0.01 * (attempt + 1))
    raise AssertionError('A confirmação Pix terminou sem resultado.')


def _normalize_card_installments(
    installments: Iterable[Mapping[str, object] | int],
) -> list[tuple[int, Decimal]]:
    normalized: list[tuple[int, Decimal]] = []
    seen: set[int] = set()
    for raw in installments:
        if isinstance(raw, Mapping):
            raw_id = raw.get('recebivel_id', raw.get('id'))
            raw_fee = raw.get('taxas', '0.00')
        else:
            raw_id = raw
            raw_fee = '0.00'
        try:
            receivable_id = int(str(raw_id))
        except (TypeError, ValueError) as error:
            raise FinanceOperationError('Informe parcelas de cartão válidas.') from error
        if receivable_id <= 0 or receivable_id in seen:
            raise FinanceOperationError('Informe cada parcela de cartão uma única vez.')
        fee = _money(raw_fee, 'as taxas do cartão')
        seen.add(receivable_id)
        normalized.append((receivable_id, fee))
    if not normalized:
        raise FinanceOperationError('Selecione pelo menos uma parcela de cartão.')
    return sorted(normalized)


def liquidar_cartao(
    *,
    actor: Any,
    account: ContaFinanceira | int,
    deposit_date: object,
    operator_reference: object,
    installments: Iterable[Mapping[str, object] | int],
    reason: object,
    idempotency_key: object,
) -> LiquidacaoCartaoResult:
    """Liquidar em lote parcelas de cartão e creditar somente o líquido."""
    _require_manager(actor)
    resolved = _account(account)
    movement = _business_date(deposit_date)
    reference = _required_text(operator_reference, 'a referência da operadora', 120)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    normalized = _normalize_card_installments(installments)
    signature = _signature([
        'LIQUIDAR_CARTAO', actor.pk, resolved.pk, movement.isoformat(),
        reference, normalized_reason, [(pk, str(fee)) for pk, fee in normalized],
    ])

    for attempt in range(RECEBIMENTO_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                existing = DepositoCartao.objects.filter(
                    chave_idempotencia=key,
                ).first()
                if existing:
                    if existing.assinatura != signature:
                        raise _conflicting_key()
                    return LiquidacaoCartaoResult(existing, created=False)
                _assert_key_available(key, DepositoCartao)
                locked_account = ContaFinanceira.objects.select_for_update().get(
                    pk=resolved.pk,
                )
                if not locked_account.ativo:
                    raise FinanceOperationError('A conta financeira está inativa.')
                _validate_movement_date(locked_account, movement)
                ids = [receivable_id for receivable_id, _fee in normalized]
                locked_receivables = list(
                    RecebivelCartao.objects.select_for_update().filter(
                        pk__in=ids,
                    ).order_by('pk')
                )
                if len(locked_receivables) != len(ids):
                    raise FinanceOperationError('Uma parcela de cartão não foi encontrada.')
                by_id = {item.pk: item for item in locked_receivables}
                if LiquidacaoCartao.objects.filter(recebivel_id__in=ids).exists():
                    raise FinanceOperationError('Essa parcela de cartão já foi liquidada.')
                if any(item.cancelado for item in locked_receivables):
                    raise FinanceOperationError('Esse recebível de cartão foi cancelado.')
                gross = sum(
                    (by_id[receivable_id].valor_bruto for receivable_id, _fee in normalized),
                    Decimal('0.00'),
                ).quantize(Decimal('0.01'))
                fees = sum((fee for _receivable_id, fee in normalized), Decimal('0.00'))
                fees = fees.quantize(Decimal('0.01'))
                if fees > gross:
                    raise FinanceOperationError('As taxas não podem superar o valor bruto.')
                net = (gross - fees).quantize(Decimal('0.01'))
                if net <= 0:
                    raise FinanceOperationError('A liquidação precisa gerar valor líquido positivo.')
                ledger = LancamentoFinanceiro.objects.create(
                    conta=locked_account,
                    direcao=LancamentoFinanceiro.Direcao.CREDITO,
                    valor=net,
                    data_movimento=movement,
                    descricao='Liquidação de cartão',
                    motivo=normalized_reason,
                    responsavel=actor,
                )
                deposit = DepositoCartao.objects.create(
                    conta=locked_account,
                    lancamento=ledger,
                    data_deposito=movement,
                    valor_bruto=gross,
                    taxas=fees,
                    valor_liquido=net,
                    referencia_operadora=reference,
                    responsavel=actor,
                    motivo=normalized_reason,
                    chave_idempotencia=key,
                    assinatura=signature,
                )
                for receivable_id, fee in normalized:
                    item = by_id[receivable_id]
                    LiquidacaoCartao.objects.create(
                        deposito=deposit,
                        recebivel=item,
                        valor_bruto=item.valor_bruto,
                        taxas=fee,
                        valor_liquido=(item.valor_bruto - fee).quantize(Decimal('0.01')),
                    )
                _audit(
                    action='LIQUIDAR_CARTAO',
                    actor=actor,
                    reason=normalized_reason,
                    key=key,
                    signature=signature,
                    data={
                        'deposito_cartao_id': deposit.pk,
                        'lancamento_id': ledger.pk,
                        'recebiveis_ids': ids,
                        'valor_bruto': str(gross),
                        'taxas': str(fees),
                        'valor_liquido': str(net),
                    },
                )
                return LiquidacaoCartaoResult(deposit, created=True)
        except IntegrityError as error:
            existing = DepositoCartao.objects.filter(chave_idempotencia=key).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key() from error
                return LiquidacaoCartaoResult(existing, created=False)
            if LiquidacaoCartao.objects.filter(
                recebivel_id__in=[receivable_id for receivable_id, _fee in normalized],
            ).exists():
                raise FinanceOperationError(
                    'Essa parcela de cartão já foi liquidada.',
                ) from error
            _assert_key_available(key, DepositoCartao)
            raise
        except OperationalError as error:
            if (
                not _is_recoverable_sqlite_lock(error)
                or attempt + 1 >= RECEBIMENTO_MAX_ATTEMPTS
            ):
                raise
            sleep(0.01 * (attempt + 1))
    raise AssertionError('A liquidação de cartão terminou sem resultado.')


def cancelar_recebiveis_cartao_por_reversao(
    *, actor: Any, source: Any, source_kind: str,
) -> int:
    """Cancelar parcelas ainda não liquidadas quando Venda é revertida."""
    _require_manager(actor)
    if source.responsavel_id != actor.pk:
        raise FinanceOperationError('A autoria da reversão não corresponde ao gerente informado.')
    today = timezone.localdate()
    cancelled = 0
    with transaction.atomic():
        receivables = RecebivelCartao.objects.select_for_update().filter(
            pagamento_origem__recebimento__venda_id=source.venda_id,
            pagamento_origem__forma='CARTAO',
        )
        for receivable in receivables:
            if receivable.cancelado or LiquidacaoCartao.objects.filter(
                recebivel_id=receivable.pk,
            ).exists():
                continue
            receivable.cancelado = True
            receivable.cancelado_em = today
            receivable.save(update_fields=('cancelado', 'cancelado_em'))
            key = f'cancelar-cartao-{source_kind}-{source.pk}-{receivable.pk}'
            signature = _signature([
                'CANCELAR_RECEBIVEL_CARTAO', actor.pk, source_kind,
                source.pk, receivable.pk, source.motivo,
            ])
            _assert_key_available(key, RecebivelCartao)
            _audit(
                action='CANCELAR_RECEBIVEL_CARTAO',
                actor=actor,
                reason=source.motivo,
                key=key,
                signature=signature,
                data={
                    'recebivel_id': receivable.pk,
                    'origem_reversao_id': source.pk,
                    'origem_reversao_tipo': source_kind,
                },
            )
            cancelled += 1
    return cancelled


def registrar_estorno_cartao(
    *, actor: Any, pagamento_restituicao: PagamentoRestituicao | int,
    movement_date: object | None = None, reason: object | None = None,
) -> EstornoRecebimentoCartaoResult | None:
    """Vincular uma restituição de cartão ao cancelamento ou débito financeiro."""
    _require_manager(actor)
    try:
        refund = (
            pagamento_restituicao
            if isinstance(pagamento_restituicao, PagamentoRestituicao)
            else PagamentoRestituicao.objects.select_related(
                'restituicao', 'pagamento_original',
            ).get(pk=int(pagamento_restituicao))
        )
    except (PagamentoRestituicao.DoesNotExist, TypeError, ValueError) as error:
        raise FinanceOperationError('Selecione uma restituição de cartão válida.') from error
    receivable = RecebivelCartao.objects.filter(
        pagamento_origem_id=refund.pagamento_original_id,
    ).first()
    if receivable is None:
        return None
    movement = _business_date(
        movement_date
        if movement_date is not None
        else timezone.localdate(refund.restituicao.criada_em),
    )
    normalized_reason = _required_text(
        reason if reason is not None else refund.restituicao.motivo,
        'um motivo', 500,
    )
    key = f'estorno-cartao-{refund.pk}'
    signature = _signature([
        'ESTORNAR_RECEBIMENTO_CARTAO', actor.pk, refund.pk, receivable.pk,
        str(refund.valor), movement.isoformat(), normalized_reason,
    ])
    try:
        with transaction.atomic():
            existing = EstornoRecebimentoCartao.objects.filter(
                chave_idempotencia=key,
            ).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key()
                return EstornoRecebimentoCartaoResult(existing, created=False)
            _assert_key_available(key, EstornoRecebimentoCartao)
            locked_receivable = RecebivelCartao.objects.select_for_update().get(
                pk=receivable.pk,
            )
            liquidation = LiquidacaoCartao.objects.select_related(
                'deposito__conta',
            ).filter(recebivel_id=locked_receivable.pk).first()
            ledger = None
            if liquidation is None:
                if not locked_receivable.cancelado:
                    locked_receivable.cancelado = True
                    locked_receivable.cancelado_em = movement
                    locked_receivable.save(update_fields=('cancelado', 'cancelado_em'))
            else:
                account = liquidation.deposito.conta
                _validate_movement_date(account, movement)
                ledger = LancamentoFinanceiro.objects.create(
                    conta=account,
                    direcao=LancamentoFinanceiro.Direcao.DEBITO,
                    valor=_money(refund.valor, 'o valor da restituição', positive=True),
                    data_movimento=movement,
                    descricao='Estorno de recebimento cartão',
                    motivo=normalized_reason,
                    responsavel=actor,
                )
            reversal = EstornoRecebimentoCartao.objects.create(
                pagamento_restituicao=refund,
                recebivel=locked_receivable,
                lancamento=ledger,
                valor=_money(refund.valor, 'o valor da restituição', positive=True),
                data_movimento=movement,
                responsavel=actor,
                motivo=normalized_reason,
                chave_idempotencia=key,
                assinatura=signature,
            )
            _audit(
                action='ESTORNAR_RECEBIMENTO_CARTAO',
                actor=actor,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={
                    'estorno_recebimento_id': reversal.pk,
                    'pagamento_restituicao_id': refund.pk,
                    'recebivel_id': locked_receivable.pk,
                    'lancamento_id': ledger.pk if ledger else None,
                },
            )
            return EstornoRecebimentoCartaoResult(reversal, created=True)
    except IntegrityError as error:
        existing = EstornoRecebimentoCartao.objects.filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            if existing.assinatura != signature:
                raise _conflicting_key() from error
            return EstornoRecebimentoCartaoResult(existing, created=False)
        source_existing = EstornoRecebimentoCartao.objects.filter(
            pagamento_restituicao_id=refund.pk,
        ).first()
        if source_existing:
            raise FinanceOperationError(
                'Essa restituição de cartão já foi processada.',
            ) from error
        _assert_key_available(key, EstornoRecebimentoCartao)
        raise


def criar_conta_financeira(
    *,
    actor: Any,
    name: object,
    initial_balance: object,
    cutover_date: object,
    reason: object,
    idempotency_key: object,
) -> ContaResult:
    _require_manager(actor)
    normalized_name = _required_text(name, 'o nome da conta', 120)
    balance = _money(initial_balance, 'o saldo inicial')
    cutover = _business_date(cutover_date, 'a data de corte financeiro')
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    signature = _signature([
        actor.pk,
        normalized_name,
        str(balance),
        cutover.isoformat(),
        normalized_reason,
    ])

    try:
        with transaction.atomic():
            existing = ContaFinanceira.objects.filter(chave_idempotencia=key).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key()
                return ContaResult(existing, created=False)
            _assert_key_available(key, ContaFinanceira)
            account = ContaFinanceira.objects.create(
                nome=normalized_name,
                saldo_inicial=balance,
                data_corte_financeiro=cutover,
                responsavel_criacao=actor,
                motivo_criacao=normalized_reason,
                chave_idempotencia=key,
                assinatura=signature,
            )
            _audit(
                action='CRIAR_CONTA',
                actor=actor,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={
                    'conta_id': account.pk,
                    'nome': normalized_name,
                    'saldo_inicial': str(balance),
                    'data_corte_financeiro': cutover.isoformat(),
                },
            )
            return ContaResult(account, created=True)
    except IntegrityError as error:
        existing = ContaFinanceira.objects.filter(chave_idempotencia=key).first()
        if existing:
            if existing.assinatura != signature:
                raise _conflicting_key() from error
            return ContaResult(existing, created=False)
        _assert_key_available(key, ContaFinanceira)
        raise


def calcular_saldo(account: ContaFinanceira | int) -> Decimal:
    resolved = _account(account)
    credits = resolved.lancamentos.filter(
        direcao=LancamentoFinanceiro.Direcao.CREDITO,
    ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
    debits = resolved.lancamentos.filter(
        direcao=LancamentoFinanceiro.Direcao.DEBITO,
    ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
    return (resolved.saldo_inicial + credits - debits).quantize(Decimal('0.01'))


def registrar_aporte(
    *,
    actor: Any,
    account: ContaFinanceira | int,
    amount: object,
    movement_date: object,
    reason: object,
    idempotency_key: object,
) -> AporteResult:
    _require_manager(actor)
    resolved = _account(account)
    if not resolved.ativo:
        raise FinanceOperationError('A conta financeira está inativa.')
    value = _money(amount, 'o valor do aporte', positive=True)
    movement = _business_date(movement_date)
    _validate_movement_date(resolved, movement)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    signature = _signature([
        actor.pk,
        resolved.pk,
        str(value),
        movement.isoformat(),
        normalized_reason,
    ])

    try:
        with transaction.atomic():
            existing = AporteFinanceiro.objects.filter(chave_idempotencia=key).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key()
                return AporteResult(existing, created=False)
            _assert_key_available(key, AporteFinanceiro)
            locked = ContaFinanceira.objects.select_for_update().get(pk=resolved.pk)
            ledger = LancamentoFinanceiro.objects.create(
                conta=locked,
                direcao=LancamentoFinanceiro.Direcao.CREDITO,
                valor=value,
                data_movimento=movement,
                descricao='Aporte financeiro',
                motivo=normalized_reason,
                responsavel=actor,
            )
            aporte = AporteFinanceiro.objects.create(
                conta=locked,
                lancamento=ledger,
                responsavel=actor,
                motivo=normalized_reason,
                chave_idempotencia=key,
                assinatura=signature,
            )
            _audit(
                action='REGISTRAR_APORTE',
                actor=actor,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={'aporte_id': aporte.pk, 'lancamento_id': ledger.pk},
            )
            return AporteResult(aporte, created=True)
    except IntegrityError as error:
        existing = AporteFinanceiro.objects.filter(chave_idempotencia=key).first()
        if existing:
            if existing.assinatura != signature:
                raise _conflicting_key() from error
            return AporteResult(existing, created=False)
        _assert_key_available(key, AporteFinanceiro)
        raise


def transferir_entre_contas(
    *,
    actor: Any,
    source: ContaFinanceira | int,
    target: ContaFinanceira | int,
    amount: object,
    movement_date: object,
    reason: object,
    idempotency_key: object,
) -> TransferenciaResult:
    _require_manager(actor)
    source_account = _account(source)
    target_account = _account(target)
    if source_account.pk == target_account.pk:
        raise FinanceOperationError('Escolha contas de origem e destino diferentes.')
    if not source_account.ativo or not target_account.ativo:
        raise FinanceOperationError('As contas de origem e destino devem estar ativas.')
    value = _money(amount, 'o valor da transferência', positive=True)
    movement = _business_date(movement_date)
    _validate_movement_date(source_account, movement)
    _validate_movement_date(target_account, movement)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    signature = _signature([
        actor.pk,
        source_account.pk,
        target_account.pk,
        str(value),
        movement.isoformat(),
        normalized_reason,
    ])

    try:
        with transaction.atomic():
            existing = TransferenciaFinanceira.objects.filter(
                chave_idempotencia=key,
            ).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key()
                return TransferenciaResult(existing, created=False)
            _assert_key_available(key, TransferenciaFinanceira)

            account_ids = sorted((source_account.pk, target_account.pk))
            locked_accounts = {
                account.pk: account
                for account in ContaFinanceira.objects.select_for_update().filter(
                    pk__in=account_ids,
                )
            }
            locked_source = locked_accounts[source_account.pk]
            locked_target = locked_accounts[target_account.pk]
            debit = LancamentoFinanceiro.objects.create(
                conta=locked_source,
                direcao=LancamentoFinanceiro.Direcao.DEBITO,
                valor=value,
                data_movimento=movement,
                descricao='Transferência financeira — débito',
                motivo=normalized_reason,
                responsavel=actor,
            )
            credit = LancamentoFinanceiro.objects.create(
                conta=locked_target,
                direcao=LancamentoFinanceiro.Direcao.CREDITO,
                valor=value,
                data_movimento=movement,
                descricao='Transferência financeira — crédito',
                motivo=normalized_reason,
                responsavel=actor,
            )
            transferencia = TransferenciaFinanceira.objects.create(
                origem=locked_source,
                destino=locked_target,
                valor=value,
                data_movimento=movement,
                debito=debit,
                credito=credit,
                responsavel=actor,
                motivo=normalized_reason,
                chave_idempotencia=key,
                assinatura=signature,
            )
            _audit(
                action='TRANSFERIR_CONTAS',
                actor=actor,
                reason=normalized_reason,
                key=key,
                signature=signature,
                data={
                    'transferencia_id': transferencia.pk,
                    'debito_id': debit.pk,
                    'credito_id': credit.pk,
                },
            )
            return TransferenciaResult(transferencia, created=True)
    except IntegrityError as error:
        existing = TransferenciaFinanceira.objects.filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            if existing.assinatura != signature:
                raise _conflicting_key() from error
            return TransferenciaResult(existing, created=False)
        _assert_key_available(key, TransferenciaFinanceira)
        raise


def estornar_lancamento(
    *,
    actor: Any,
    lancamento: LancamentoFinanceiro | int,
    movement_date: object,
    reason: object,
    idempotency_key: object,
) -> EstornoResult:
    _require_manager(actor)
    try:
        original = (
            lancamento
            if isinstance(lancamento, LancamentoFinanceiro)
            else LancamentoFinanceiro.objects.get(pk=int(lancamento))
        )
    except (LancamentoFinanceiro.DoesNotExist, TypeError, ValueError) as error:
        raise FinanceOperationError('Selecione um lançamento financeiro válido.') from error
    if not original.pk:
        raise FinanceOperationError('Selecione um lançamento financeiro válido.')
    movement = _business_date(movement_date)
    _validate_movement_date(original.conta, movement)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    signature = _signature([
        actor.pk,
        original.pk,
        movement.isoformat(),
        normalized_reason,
    ])

    for attempt in range(ESTORNO_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                existing = EstornoFinanceiro.objects.filter(
                    chave_idempotencia=key,
                ).first()
                if existing:
                    if existing.assinatura != signature:
                        raise _conflicting_key()
                    return EstornoResult(existing, created=False)
                _assert_key_available(key, EstornoFinanceiro)
                locked_original = LancamentoFinanceiro.objects.select_for_update().select_related(
                    'conta',
                ).get(pk=original.pk)
                if locked_original.estorno_de_id:
                    raise FinanceOperationError('Um estorno não pode ser estornado novamente.')
                if locked_original.estorno_operacoes.exists():
                    raise FinanceOperationError('Esse lançamento já possui um estorno.')
                inverse = (
                    LancamentoFinanceiro.Direcao.DEBITO
                    if locked_original.direcao == LancamentoFinanceiro.Direcao.CREDITO
                    else LancamentoFinanceiro.Direcao.CREDITO
                )
                reversal_ledger = LancamentoFinanceiro.objects.create(
                    conta=locked_original.conta,
                    direcao=inverse,
                    valor=locked_original.valor,
                    data_movimento=movement,
                    descricao='Estorno financeiro',
                    motivo=normalized_reason,
                    responsavel=actor,
                    estorno_de=locked_original,
                )
                estorno = EstornoFinanceiro.objects.create(
                    lancamento_original=locked_original,
                    lancamento=reversal_ledger,
                    responsavel=actor,
                    motivo=normalized_reason,
                    chave_idempotencia=key,
                    assinatura=signature,
                )
                _audit(
                    action='ESTORNAR_LANCAMENTO',
                    actor=actor,
                    reason=normalized_reason,
                    key=key,
                    signature=signature,
                    data={
                        'estorno_id': estorno.pk,
                        'lancamento_original_id': locked_original.pk,
                        'lancamento_estorno_id': reversal_ledger.pk,
                    },
                )
                return EstornoResult(estorno, created=True)
        except IntegrityError as error:
            existing = EstornoFinanceiro.objects.filter(
                chave_idempotencia=key,
            ).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key() from error
                return EstornoResult(existing, created=False)
            _assert_key_available(key, EstornoFinanceiro)
            raise
        except OperationalError as error:
            if (
                not _is_recoverable_sqlite_lock(error)
                or attempt + 1 >= ESTORNO_MAX_ATTEMPTS
            ):
                raise
            sleep(0.01 * (attempt + 1))
            continue

    raise AssertionError('A rotina de estorno terminou sem resultado.')
