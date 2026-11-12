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

from caixa.models import (
    PagamentoRestituicao,
    PagamentoRestituicaoTroca,
    PagamentoTroca,
    PagamentoVenda,
)
from compras.services import PurchaseOperationError, resumo_pedido_para_financeiro

from .models import (
    AporteFinanceiro,
    ContaFinanceira,
    ContaPagar,
    DepositoCartao,
    EstornoFinanceiro,
    EstornoPagamentoContaPagar,
    EstornoPixTroca,
    EstornoRecebimentoCartao,
    EventoAuditoriaFinanceiro,
    FechamentoMensal,
    FechamentoMensalConta,
    LancamentoFinanceiro,
    LiquidacaoCartao,
    PagamentoContaPagar,
    ReaberturaFechamentoMensal,
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


@dataclass(frozen=True)
class ContaPagarResult:
    conta: ContaPagar
    created: bool


@dataclass(frozen=True)
class PagamentoContaPagarResult:
    pagamento: PagamentoContaPagar
    created: bool


@dataclass(frozen=True)
class EstornoPagamentoContaPagarResult:
    estorno: EstornoPagamentoContaPagar
    created: bool


@dataclass(frozen=True)
class FechamentoContaPreview:
    conta_id: int
    nome: str
    saldo_inicial: Decimal
    saldo_final: Decimal


@dataclass(frozen=True)
class FechamentoObrigacaoPreview:
    conta_pagar_id: int
    descricao: str
    valor: Decimal
    data_vencimento: date
    situacao: str


@dataclass(frozen=True)
class FechamentoMensalPreview:
    mes_referencia: date
    margem: Decimal
    entradas_operacionais_liquidas: Decimal
    obrigacoes_vencimento_mes: Decimal
    pagamentos_efetivos: Decimal
    obrigacoes_pagas: Decimal
    obrigacoes_abertas: Decimal
    vencidas_anteriores: Decimal
    cartao_a_receber: Decimal
    cobertura: Decimal
    classificacao: str
    contas: tuple[FechamentoContaPreview, ...]
    obrigacoes: tuple[FechamentoObrigacaoPreview, ...]
    assinatura: str


@dataclass(frozen=True)
class FechamentoMensalResult:
    fechamento: FechamentoMensal
    created: bool


@dataclass(frozen=True)
class ReaberturaFechamentoMensalResult:
    reabertura: ReaberturaFechamentoMensal
    created: bool


ESTORNO_MAX_ATTEMPTS = 5
RECEBIMENTO_MAX_ATTEMPTS = 5
CONTA_PAGAR_MAX_ATTEMPTS = 5
FECHAMENTO_MAX_ATTEMPTS = 5


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


def _assert_month_open_for_correction(movement_date: date) -> None:
    month = date(movement_date.year, movement_date.month, 1)
    if FechamentoMensal.objects.filter(
        mes_referencia=month,
        vigente=True,
    ).exists():
        raise FinanceOperationError(
            f'O mês {month:%Y-%m} está fechado; reabra-o antes de registrar uma correção retroativa.',
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


def _payable(value: ContaPagar | int) -> ContaPagar:
    if isinstance(value, ContaPagar):
        if not value.pk:
            raise FinanceOperationError('Selecione uma conta a pagar válida.')
        return value
    try:
        return ContaPagar.objects.get(pk=int(value))
    except (ContaPagar.DoesNotExist, TypeError, ValueError) as error:
        raise FinanceOperationError('Selecione uma conta a pagar válida.') from error


def _payable_payment(value: PagamentoContaPagar | int) -> PagamentoContaPagar:
    if isinstance(value, PagamentoContaPagar):
        if not value.pk:
            raise FinanceOperationError('Selecione um pagamento de conta a pagar válido.')
        return value
    try:
        return PagamentoContaPagar.objects.get(pk=int(value))
    except (PagamentoContaPagar.DoesNotExist, TypeError, ValueError) as error:
        raise FinanceOperationError(
            'Selecione um pagamento de conta a pagar válido.',
        ) from error


def _monthly_closing(value: FechamentoMensal | int) -> FechamentoMensal:
    if isinstance(value, FechamentoMensal):
        if not value.pk:
            raise FinanceOperationError('Selecione um fechamento mensal válido.')
        return value
    try:
        return FechamentoMensal.objects.get(pk=int(value))
    except (FechamentoMensal.DoesNotExist, TypeError, ValueError) as error:
        raise FinanceOperationError('Selecione um fechamento mensal válido.') from error


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
        ContaPagar,
        PagamentoContaPagar,
        EstornoPagamentoContaPagar,
        EstornoPixTroca,
        AporteFinanceiro,
        TransferenciaFinanceira,
        EstornoFinanceiro,
        RecebimentoPixFinanceiro,
        RecebivelCartao,
        DepositoCartao,
        EstornoRecebimentoCartao,
        FechamentoMensal,
        ReaberturaFechamentoMensal,
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


def _payment(value: PagamentoVenda | PagamentoTroca | int | str) -> PagamentoVenda | PagamentoTroca:
    if isinstance(value, (PagamentoVenda, PagamentoTroca)):
        if not value.pk:
            raise FinanceOperationError('Selecione um pagamento válido.')
        return value
    model = PagamentoVenda
    identifier = str(value)
    if identifier.startswith('troca-'):
        model = PagamentoTroca
        identifier = identifier.removeprefix('troca-')
    try:
        return model.objects.get(pk=int(identifier))
    except (PagamentoVenda.DoesNotExist, PagamentoTroca.DoesNotExist, TypeError, ValueError) as error:
        raise FinanceOperationError('Selecione um pagamento válido.') from error


def _payment_source(payment):
    field = 'pagamento_troca' if isinstance(payment, PagamentoTroca) else 'pagamento_origem'
    return {field: payment}


def _locked_payment(payment):
    return type(payment).objects.select_for_update().select_related('recebimento').get(pk=payment.pk)


def _approval_date(payment: PagamentoVenda | PagamentoTroca) -> date:
    created_at = (
        payment.recebimento.criada_em if isinstance(payment, PagamentoTroca)
        else payment.recebimento.venda.criada_em
    )
    if timezone.is_aware(created_at):
        return timezone.localtime(created_at).date()
    return created_at.date()


def registrar_recebiveis_cartao(
    *,
    actor: Any,
    pagamentos: Iterable[PagamentoVenda | PagamentoTroca | int],
    reason: str = 'Aprovação de cartão no Caixa',
) -> tuple[RecebivelCartaoResult, ...]:
    """Criar, uma única vez, os recebíveis das parcelas aprovadas no Caixa."""
    _require_operational_actor(actor)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    results: list[RecebivelCartaoResult] = []
    with transaction.atomic():
        for raw_payment in pagamentos:
            payment = _payment(raw_payment)
            locked = _locked_payment(payment)
            if locked.forma != 'CARTAO':
                continue
            value = _money(locked.valor_aplicado, 'o valor bruto do cartão', positive=True)
            approval_date = _approval_date(locked)
            key = f'cartao-aprovado-{"troca-" if isinstance(locked, PagamentoTroca) else ""}{locked.pk}'
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
                **_payment_source(locked),
            ).first()
            if source_existing:
                raise FinanceOperationError(
                    'Esse pagamento de cartão já está em A receber.',
                )
            _assert_month_open_for_correction(approval_date)
            _assert_key_available(key, RecebivelCartao)
            receivable = RecebivelCartao.objects.create(
                **_payment_source(locked),
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
    pagamento: PagamentoVenda | PagamentoTroca | int | str,
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
        'CONFIRMAR_PIX_TROCA' if isinstance(source, PagamentoTroca) else 'CONFIRMAR_PIX',
        actor.pk, source.pk, resolved.pk, str(value),
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
                locked_payment = _locked_payment(source)
                if locked_payment.forma != 'PIX':
                    raise FinanceOperationError(
                        'O pagamento de origem não é um Pix confirmado.',
                    )
                source_existing = RecebimentoPixFinanceiro.objects.filter(
                    **_payment_source(locked_payment),
                ).first()
                if source_existing:
                    raise FinanceOperationError('Esse pagamento Pix já foi creditado.')
                _assert_month_open_for_correction(movement)
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
                    **_payment_source(locked_payment),
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
                if isinstance(locked_payment, PagamentoVenda):
                    for refund in PagamentoRestituicaoTroca.objects.filter(pagamento_original=locked_payment):
                        registrar_estorno_pix_troca(actor=actor, pagamento_restituicao=refund)
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
                **_payment_source(source),
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
                _assert_month_open_for_correction(movement)
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
                if any(fee > by_id[pk].valor_pendente for pk, fee in normalized):
                    raise FinanceOperationError('A taxa não pode superar o saldo da parcela.')
                gross = sum(
                    (by_id[receivable_id].valor_pendente for receivable_id, _fee in normalized),
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
                        valor_bruto=item.valor_pendente,
                        taxas=fee,
                        valor_liquido=(item.valor_pendente - fee).quantize(Decimal('0.01')),
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
            _assert_month_open_for_correction(today)
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
    *, actor: Any, pagamento_restituicao: PagamentoRestituicao | PagamentoRestituicaoTroca | int,
    movement_date: object | None = None, reason: object | None = None,
) -> EstornoRecebimentoCartaoResult | None:
    """Vincular uma restituição de cartão ao cancelamento ou débito financeiro."""
    _require_manager(actor)
    try:
        refund = (
            pagamento_restituicao
            if isinstance(pagamento_restituicao, (PagamentoRestituicao, PagamentoRestituicaoTroca))
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
    key = f'estorno-cartao-{"troca-" if isinstance(refund, PagamentoRestituicaoTroca) else ""}{refund.pk}'
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
            _assert_month_open_for_correction(movement)
            locked_receivable = RecebivelCartao.objects.select_for_update().get(
                pk=receivable.pk,
            )
            liquidation = LiquidacaoCartao.objects.select_related(
                'deposito__conta',
            ).filter(recebivel_id=locked_receivable.pk).first()
            ledger = None
            if liquidation is None:
                if refund.valor >= locked_receivable.valor_pendente and not locked_receivable.cancelado:
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
                **({'pagamento_restituicao_troca': refund} if isinstance(refund, PagamentoRestituicaoTroca) else {'pagamento_restituicao': refund}),
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
            **({'pagamento_restituicao_troca': refund} if isinstance(refund, PagamentoRestituicaoTroca) else {'pagamento_restituicao': refund}),
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
            _assert_month_open_for_correction(cutover)
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


def periodo_mensal(month: object) -> tuple[date, date]:
    """Normalizar mês para o primeiro e o último dia, ambos inclusivos."""
    if isinstance(month, date):
        reference = month
    else:
        raw = str(month or '').strip()
        try:
            reference = date.fromisoformat(
                f'{raw}-01' if len(raw) == 7 else raw,
            )
        except ValueError as error:
            raise FinanceOperationError(
                'Informe o mês de referência no formato AAAA-MM.',
            ) from error
    try:
        first = date(reference.year, reference.month, 1)
        if first.month == 12:
            next_month = date(first.year + 1, 1, 1)
        else:
            next_month = date(first.year, first.month + 1, 1)
    except ValueError as error:
        raise FinanceOperationError(
            'Informe um mês de referência válido.',
        ) from error
    return first, next_month.fromordinal(next_month.toordinal() - 1)


def _round_money(value: Decimal | None) -> Decimal:
    return (value or Decimal('0.00')).quantize(Decimal('0.01'))


def _payable_state_as_of(
    payable: ContaPagar,
    end_date: date,
) -> tuple[str, list[tuple[date, object, int, str]]]:
    events: list[tuple[date, object, int, str]] = []
    payments = PagamentoContaPagar.objects.filter(
        conta_pagar_id=payable.pk,
    ).select_related('conta').order_by('pk')
    for payment in payments:
        if payment.data_pagamento > end_date:
            continue
        if payment.data_pagamento < payment.conta.data_corte_financeiro:
            continue
        events.append((
            payment.data_pagamento,
            payment.criada_em,
            payment.pk,
            'PAGA',
        ))
        refund = EstornoPagamentoContaPagar.objects.filter(
            pagamento_id=payment.pk,
        ).first()
        if refund is not None and refund.data_movimento <= end_date:
            events.append((
                refund.data_movimento,
                refund.criada_em,
                refund.pk,
                'ABERTA',
            ))
    events.sort(key=lambda event: (event[0], event[1], event[2]))
    state = 'ABERTA'
    for _event_date, _created_at, _event_id, state_change in events:
        state = state_change
    return state, events


def _fechamento_classificacao(coverage: Decimal, margin: Decimal) -> str:
    if coverage < 0:
        return 'MAL'
    if coverage > 0 and coverage >= margin:
        return 'BEM'
    return 'NO_LIMITE'


def previsualizar_fechamento(
    *,
    month: object,
    margin: object,
) -> FechamentoMensalPreview:
    """Reconstruir a posição do mês pelas datas efetivas dos eventos."""
    start_date, end_date = periodo_mensal(month)
    normalized_margin = _money(margin, 'a margem')
    accounts = list(
        ContaFinanceira.objects.filter(ativo=True).order_by('nome', 'pk'),
    )
    account_ids = [account.pk for account in accounts]
    cutover = min(
        (account.data_corte_financeiro for account in accounts),
        default=end_date,
    )
    ledgers = list(
        LancamentoFinanceiro.objects.filter(
            conta_id__in=account_ids,
            data_movimento__lte=end_date,
        ).select_related('conta').order_by('pk'),
    )
    ledgers = [
        ledger for ledger in ledgers
        if ledger.data_movimento >= ledger.conta.data_corte_financeiro
    ]
    ledger_ids = [ledger.pk for ledger in ledgers]
    pix_ledger_ids = set(
        RecebimentoPixFinanceiro.objects.filter(
            lancamento_id__in=ledger_ids,
        ).values_list('lancamento_id', flat=True),
    )
    deposit_ledger_ids = set(
        DepositoCartao.objects.filter(
            lancamento_id__in=ledger_ids,
        ).values_list('lancamento_id', flat=True),
    )
    refund_ledger_ids = set(
        EstornoRecebimentoCartao.objects.filter(
            lancamento_id__in=ledger_ids,
        ).values_list('lancamento_id', flat=True),
    )

    refund_ledger_ids.update(EstornoPixTroca.objects.filter(
        lancamento_id__in=ledger_ids,
    ).values_list('lancamento_id', flat=True))

    account_previews: list[FechamentoContaPreview] = []
    for account in accounts:
        opening = account.saldo_inicial
        ending = account.saldo_inicial
        for ledger in ledgers:
            if ledger.conta_id != account.pk:
                continue
            if ledger.data_movimento < start_date:
                opening += (
                    ledger.valor
                    if ledger.direcao == LancamentoFinanceiro.Direcao.CREDITO
                    else -ledger.valor
                )
            if ledger.data_movimento <= end_date:
                ending += (
                    ledger.valor
                    if ledger.direcao == LancamentoFinanceiro.Direcao.CREDITO
                    else -ledger.valor
                )
        account_previews.append(FechamentoContaPreview(
            conta_id=account.pk,
            nome=account.nome,
            saldo_inicial=_round_money(opening),
            saldo_final=_round_money(ending),
        ))

    month_ledgers = [
        ledger for ledger in ledgers
        if start_date <= ledger.data_movimento <= end_date
    ]
    operational_credits = sum(
        (
            ledger.valor for ledger in month_ledgers
            if ledger.direcao == LancamentoFinanceiro.Direcao.CREDITO
            and ledger.pk in pix_ledger_ids | deposit_ledger_ids
        ),
        Decimal('0.00'),
    )
    operational_refunds = sum(
        (
            ledger.valor for ledger in month_ledgers
            if ledger.direcao == LancamentoFinanceiro.Direcao.DEBITO
            and ledger.pk in refund_ledger_ids
        ),
        Decimal('0.00'),
    )
    operational_net = _round_money(operational_credits - operational_refunds)

    payables = list(
        ContaPagar.objects.filter(
            data_vencimento__lte=end_date,
        ).order_by('data_vencimento', 'pk'),
    )
    obligation_previews: list[FechamentoObrigacaoPreview] = []
    obligations_due_month = Decimal('0.00')
    effective_payments = Decimal('0.00')
    paid_obligations = Decimal('0.00')
    open_obligations = Decimal('0.00')
    previous_overdue = Decimal('0.00')
    payable_signature_rows: list[list[object]] = []
    for payable in payables:
        state, events = _payable_state_as_of(payable, end_date)
        if start_date <= payable.data_vencimento <= end_date:
            obligations_due_month += payable.valor
        if state == 'PAGA':
            paid_obligations += payable.valor
        else:
            open_obligations += payable.valor
            if payable.data_vencimento < start_date:
                previous_overdue += payable.valor
        for event_date, _created_at, event_id, state_change in events:
            if state_change == 'PAGA':
                payment = PagamentoContaPagar.objects.get(pk=event_id)
                if start_date <= event_date <= end_date:
                    effective_payments += payment.valor
        obligation_previews.append(FechamentoObrigacaoPreview(
            conta_pagar_id=payable.pk,
            descricao=payable.descricao,
            valor=_round_money(payable.valor),
            data_vencimento=payable.data_vencimento,
            situacao=state,
        ))
        payable_signature_rows.append([
            payable.pk,
            payable.descricao,
            payable.categoria,
            str(payable.valor),
            payable.data_vencimento.isoformat(),
            state,
            [
                [event_date.isoformat(), event_id, state_change]
                for event_date, _created_at, event_id, state_change in events
            ],
        ])

    card_receivables = list(
        RecebivelCartao.objects.filter(
            data_aprovacao__lte=end_date,
        ).order_by('pk'),
    )
    card_a_receber = Decimal('0.00')
    card_signature_rows: list[list[object]] = []
    for receivable in card_receivables:
        if receivable.data_aprovacao < cutover:
            continue
        liquidation = LiquidacaoCartao.objects.filter(
            recebivel_id=receivable.pk,
        ).select_related('deposito').first()
        deposit_date = liquidation.deposito.data_deposito if liquidation else None
        cancellation_date = receivable.cancelado_em if receivable.cancelado else None
        if (
            (cancellation_date is not None and cancellation_date <= end_date)
            or (deposit_date is not None and deposit_date <= end_date)
        ):
            continue
        prior_refunds = receivable.estornos_recebimento.filter(
            lancamento__isnull=True, data_movimento__lte=end_date,
        ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
        pending_value = (receivable.valor_bruto - prior_refunds).quantize(Decimal('0.01'))
        card_a_receber += pending_value
        card_signature_rows.append([
            receivable.pk,
            str(pending_value),
            receivable.data_aprovacao.isoformat(),
            cancellation_date.isoformat() if cancellation_date else None,
            deposit_date.isoformat() if deposit_date else None,
        ])

    obligations_due_month = _round_money(obligations_due_month)
    effective_payments = _round_money(effective_payments)
    paid_obligations = _round_money(paid_obligations)
    open_obligations = _round_money(open_obligations)
    previous_overdue = _round_money(previous_overdue)
    card_a_receber = _round_money(card_a_receber)
    coverage = _round_money(operational_net - obligations_due_month)
    classification = _fechamento_classificacao(coverage, normalized_margin)

    ledger_signature_rows = [
        [
            ledger.pk,
            ledger.conta_id,
            ledger.direcao,
            str(ledger.valor),
            ledger.data_movimento.isoformat(),
            ledger.descricao,
            ledger.motivo,
            ledger.estorno_de_id,
        ]
        for ledger in ledgers
    ]
    signature = _signature([
        'FECHAMENTO_MENSAL',
        start_date.isoformat(),
        end_date.isoformat(),
        str(normalized_margin),
        [
            [item.conta_id, item.nome, str(item.saldo_inicial), str(item.saldo_final)]
            for item in account_previews
        ],
        ledger_signature_rows,
        payable_signature_rows,
        card_signature_rows,
        str(operational_net),
        str(obligations_due_month),
        str(effective_payments),
        str(paid_obligations),
        str(open_obligations),
        str(previous_overdue),
        str(card_a_receber),
        str(coverage),
        classification,
    ])
    return FechamentoMensalPreview(
        mes_referencia=start_date,
        margem=normalized_margin,
        entradas_operacionais_liquidas=operational_net,
        obrigacoes_vencimento_mes=obligations_due_month,
        pagamentos_efetivos=effective_payments,
        obrigacoes_pagas=paid_obligations,
        obrigacoes_abertas=open_obligations,
        vencidas_anteriores=previous_overdue,
        cartao_a_receber=card_a_receber,
        cobertura=coverage,
        classificacao=classification,
        contas=tuple(account_previews),
        obrigacoes=tuple(obligation_previews),
        assinatura=signature,
    )


def previsualizar_fechamento_mensal(
    *,
    month: object,
    margin: object,
) -> FechamentoMensalPreview:
    return previsualizar_fechamento(month=month, margin=margin)


def confirmar_fechamento_mensal(
    *,
    actor: Any,
    month: object,
    margin: object,
    preview_signature: object,
    reason: object,
    idempotency_key: object,
) -> FechamentoMensalResult:
    """Persistir uma versão mensal somente após recalcular a prévia na transação."""
    _require_manager(actor)
    start_date, _end_date = periodo_mensal(month)
    normalized_margin = _money(margin, 'a margem')
    normalized_preview_signature = _required_text(
        preview_signature,
        'a assinatura da prévia',
        64,
    )
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    signature = _signature([
        'CONFIRMAR_FECHAMENTO_MENSAL',
        actor.pk,
        start_date.isoformat(),
        str(normalized_margin),
        normalized_preview_signature,
        normalized_reason,
    ])

    for attempt in range(FECHAMENTO_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                existing = FechamentoMensal.objects.filter(
                    chave_idempotencia=key,
                ).first()
                if existing:
                    if existing.assinatura != signature:
                        raise _conflicting_key()
                    return FechamentoMensalResult(existing, created=False)
                _assert_key_available(key, FechamentoMensal)

                versions = list(
                    FechamentoMensal.objects.select_for_update().filter(
                        mes_referencia=start_date,
                    ).order_by('-versao', '-pk'),
                )
                if any(version.vigente for version in versions):
                    raise FinanceOperationError(
                        'Esse mês já possui um fechamento vigente; reabra-o antes de corrigir.',
                    )
                preview = previsualizar_fechamento(
                    month=start_date,
                    margin=normalized_margin,
                )
                if preview.assinatura != normalized_preview_signature:
                    raise FinanceOperationError(
                        'A prévia mudou; confira os dados novamente antes de confirmar.',
                    )
                version = versions[0].versao + 1 if versions else 1
                fechamento = FechamentoMensal.objects.create(
                    mes_referencia=start_date,
                    versao=version,
                    situacao=FechamentoMensal.Situacao.CONFIRMADO,
                    vigente=True,
                    margem=preview.margem,
                    entradas_operacionais_liquidas=preview.entradas_operacionais_liquidas,
                    obrigacoes_vencimento_mes=preview.obrigacoes_vencimento_mes,
                    pagamentos_efetivos=preview.pagamentos_efetivos,
                    obrigacoes_pagas=preview.obrigacoes_pagas,
                    obrigacoes_abertas=preview.obrigacoes_abertas,
                    vencidas_anteriores=preview.vencidas_anteriores,
                    cartao_a_receber=preview.cartao_a_receber,
                    cobertura=preview.cobertura,
                    classificacao=preview.classificacao,
                    assinatura_previa=preview.assinatura,
                    responsavel=actor,
                    motivo=normalized_reason,
                    chave_idempotencia=key,
                    assinatura=signature,
                )
                FechamentoMensalConta.objects.bulk_create([
                    FechamentoMensalConta(
                        fechamento=fechamento,
                        conta_id=account_preview.conta_id,
                        saldo_inicial=account_preview.saldo_inicial,
                        saldo_final=account_preview.saldo_final,
                    )
                    for account_preview in preview.contas
                ])
                _audit(
                    action='CONFIRMAR_FECHAMENTO_MENSAL',
                    actor=actor,
                    reason=normalized_reason,
                    key=key,
                    signature=signature,
                    data={
                        'fechamento_id': fechamento.pk,
                        'mes_referencia': start_date.isoformat(),
                        'versao': version,
                        'assinatura_previa': preview.assinatura,
                        'classificacao': preview.classificacao,
                    },
                )
                return FechamentoMensalResult(fechamento, created=True)
        except IntegrityError as error:
            existing = FechamentoMensal.objects.filter(
                chave_idempotencia=key,
            ).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key() from error
                return FechamentoMensalResult(existing, created=False)
            if FechamentoMensal.objects.filter(
                mes_referencia=start_date,
                vigente=True,
            ).exists():
                raise FinanceOperationError(
                    'Esse mês já possui um fechamento vigente; reabra-o antes de corrigir.',
                ) from error
            raise
        except OperationalError as error:
            if (
                not _is_recoverable_sqlite_lock(error)
                or attempt + 1 >= FECHAMENTO_MAX_ATTEMPTS
            ):
                raise
            sleep(0.01 * (attempt + 1))
    raise AssertionError('A confirmação do fechamento mensal terminou sem resultado.')


def reabrir_fechamento_mensal(
    *,
    actor: Any,
    fechamento: FechamentoMensal | int,
    reason: object,
    idempotency_key: object,
) -> ReaberturaFechamentoMensalResult:
    """Encerrar a vigência de um fechamento e registrar o motivo em auditoria."""
    _require_manager(actor)
    resolved = _monthly_closing(fechamento)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    signature = _signature([
        'REABRIR_FECHAMENTO_MENSAL',
        actor.pk,
        resolved.pk,
        normalized_reason,
    ])

    for attempt in range(FECHAMENTO_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                existing = ReaberturaFechamentoMensal.objects.filter(
                    chave_idempotencia=key,
                ).first()
                if existing:
                    if existing.assinatura != signature:
                        raise _conflicting_key()
                    return ReaberturaFechamentoMensalResult(existing, created=False)
                _assert_key_available(key, ReaberturaFechamentoMensal)
                locked = FechamentoMensal.objects.select_for_update().get(
                    pk=resolved.pk,
                )
                existing_for_closing = ReaberturaFechamentoMensal.objects.filter(
                    fechamento_id=locked.pk,
                ).first()
                if existing_for_closing is not None or not locked.vigente:
                    raise FinanceOperationError('Esse fechamento já foi reaberto.')
                if locked.situacao != FechamentoMensal.Situacao.CONFIRMADO:
                    raise FinanceOperationError('Esse fechamento não está confirmado.')
                locked.vigente = False
                locked.situacao = FechamentoMensal.Situacao.REABERTO
                locked.save(update_fields=('vigente', 'situacao'))
                reabertura = ReaberturaFechamentoMensal.objects.create(
                    fechamento=locked,
                    responsavel=actor,
                    motivo=normalized_reason,
                    chave_idempotencia=key,
                    assinatura=signature,
                )
                _audit(
                    action='REABRIR_FECHAMENTO_MENSAL',
                    actor=actor,
                    reason=normalized_reason,
                    key=key,
                    signature=signature,
                    data={
                        'fechamento_id': locked.pk,
                        'mes_referencia': locked.mes_referencia.isoformat(),
                        'versao': locked.versao,
                    },
                )
                return ReaberturaFechamentoMensalResult(reabertura, created=True)
        except FechamentoMensal.DoesNotExist as error:
            raise FinanceOperationError('Selecione um fechamento mensal válido.') from error
        except IntegrityError as error:
            existing = ReaberturaFechamentoMensal.objects.filter(
                chave_idempotencia=key,
            ).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key() from error
                return ReaberturaFechamentoMensalResult(existing, created=False)
            if ReaberturaFechamentoMensal.objects.filter(
                fechamento_id=resolved.pk,
            ).exists():
                raise FinanceOperationError('Esse fechamento já foi reaberto.') from error
            raise
        except OperationalError as error:
            if (
                not _is_recoverable_sqlite_lock(error)
                or attempt + 1 >= FECHAMENTO_MAX_ATTEMPTS
            ):
                raise
            sleep(0.01 * (attempt + 1))
    raise AssertionError('A reabertura do fechamento mensal terminou sem resultado.')


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
            _assert_month_open_for_correction(movement)
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
            _assert_month_open_for_correction(movement)

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
                _assert_month_open_for_correction(movement)
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


def _payable_signature(
    *,
    operation: str,
    actor: Any,
    source_id: object,
    description: str,
    category: str,
    value: Decimal,
    due_date: date,
    reason: str,
) -> str:
    return _signature([
        operation,
        actor.pk,
        source_id,
        description,
        category,
        str(value),
        due_date.isoformat(),
        reason,
    ])


def criar_conta_pagar(
    *,
    actor: Any,
    description: object,
    category: object,
    amount: object,
    due_date: object,
    reason: object,
    idempotency_key: object,
) -> ContaPagarResult:
    """Registra uma obrigação avulsa sem criar movimento de saldo."""
    _require_manager(actor)
    normalized_description = _required_text(description, 'uma descrição', 160)
    normalized_category = _required_text(category, 'uma categoria', 80)
    value = _money(amount, 'o valor da conta a pagar', positive=True)
    due = _business_date(due_date, 'a data de vencimento')
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    signature = _payable_signature(
        operation='CRIAR_CONTA_PAGAR',
        actor=actor,
        source_id=None,
        description=normalized_description,
        category=normalized_category,
        value=value,
        due_date=due,
        reason=normalized_reason,
    )

    for attempt in range(CONTA_PAGAR_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                existing = ContaPagar.objects.filter(
                    chave_idempotencia=key,
                ).first()
                if existing:
                    if existing.assinatura != signature:
                        raise _conflicting_key()
                    return ContaPagarResult(existing, created=False)
                _assert_key_available(key, ContaPagar)
                _assert_month_open_for_correction(due)
                payable = ContaPagar.objects.create(
                    descricao=normalized_description,
                    categoria=normalized_category,
                    valor=value,
                    data_vencimento=due,
                    responsavel=actor,
                    motivo=normalized_reason,
                    chave_idempotencia=key,
                    assinatura=signature,
                )
                _audit(
                    action='CRIAR_CONTA_PAGAR',
                    actor=actor,
                    reason=normalized_reason,
                    key=key,
                    signature=signature,
                    data={'conta_pagar_id': payable.pk, 'valor': str(value)},
                )
                return ContaPagarResult(payable, created=True)
        except IntegrityError as error:
            existing = ContaPagar.objects.filter(chave_idempotencia=key).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key() from error
                return ContaPagarResult(existing, created=False)
            _assert_key_available(key, ContaPagar)
            raise
        except OperationalError as error:
            if (
                not _is_recoverable_sqlite_lock(error)
                or attempt + 1 >= CONTA_PAGAR_MAX_ATTEMPTS
            ):
                raise
            sleep(0.01 * (attempt + 1))
    raise AssertionError('A rotina de criação de conta a pagar terminou sem resultado.')


def criar_conta_pagar_de_compra(
    *,
    actor: Any,
    pedido: object,
    amount: object,
    due_date: object,
    reason: object,
    idempotency_key: object,
) -> ContaPagarResult:
    """Gera uma obrigação única para um pedido totalmente recebido."""
    _require_manager(actor)
    try:
        pedido_id = int(str(getattr(pedido, 'pk', pedido)))
    except (TypeError, ValueError) as error:
        raise FinanceOperationError('Selecione um pedido de compra válido.') from error
    normalized_category = 'Compras'
    normalized_description = 'Compra recebida'
    value = _money(amount, 'o valor da conta a pagar', positive=True)
    due = _business_date(due_date, 'a data de vencimento')
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)

    for attempt in range(CONTA_PAGAR_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                existing = ContaPagar.objects.filter(
                    chave_idempotencia=key,
                ).first()
                signature = _payable_signature(
                    operation='CRIAR_CONTA_PAGAR_COMPRA',
                    actor=actor,
                    source_id=pedido_id,
                    description=normalized_description,
                    category=normalized_category,
                    value=value,
                    due_date=due,
                    reason=normalized_reason,
                )
                if existing:
                    if existing.assinatura != signature:
                        raise _conflicting_key()
                    return ContaPagarResult(existing, created=False)
                _assert_key_available(key, ContaPagar)
                try:
                    source = resumo_pedido_para_financeiro(pedido_id=pedido_id)
                except PurchaseOperationError as error:
                    raise FinanceOperationError(str(error)) from error
                if value != source.total_contratado:
                    raise FinanceOperationError(
                        'O valor deve corresponder ao total contratado da compra.',
                    )
                existing_origin = ContaPagar.objects.filter(
                    origem_pedido_id=source.pedido_id,
                ).first()
                if existing_origin:
                    raise FinanceOperationError(
                        'Esse pedido já possui uma conta a pagar.',
                    )
                _assert_month_open_for_correction(due)
                payable = ContaPagar.objects.create(
                    descricao=f'{normalized_description} #{source.public_id[:8]}',
                    categoria=normalized_category,
                    valor=value,
                    data_vencimento=due,
                    origem_pedido_id=source.pedido_id,
                    responsavel=actor,
                    motivo=normalized_reason,
                    chave_idempotencia=key,
                    assinatura=signature,
                )
                _audit(
                    action='CRIAR_CONTA_PAGAR_COMPRA',
                    actor=actor,
                    reason=normalized_reason,
                    key=key,
                    signature=signature,
                    data={
                        'conta_pagar_id': payable.pk,
                        'pedido_id': source.pedido_id,
                        'valor': str(value),
                    },
                )
                return ContaPagarResult(payable, created=True)
        except IntegrityError as error:
            existing = ContaPagar.objects.filter(chave_idempotencia=key).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key() from error
                return ContaPagarResult(existing, created=False)
            if ContaPagar.objects.filter(origem_pedido_id=pedido_id).exists():
                raise FinanceOperationError(
                    'Esse pedido já possui uma conta a pagar.',
                ) from error
            _assert_key_available(key, ContaPagar)
            raise
        except OperationalError as error:
            if (
                not _is_recoverable_sqlite_lock(error)
                or attempt + 1 >= CONTA_PAGAR_MAX_ATTEMPTS
            ):
                raise
            sleep(0.01 * (attempt + 1))
    raise AssertionError('A rotina de geração de conta a pagar terminou sem resultado.')


def listar_contas_pagar(*, as_of: object) -> dict[str, Any]:
    """Agrupa obrigações abertas, vencidas e pagas para o painel."""
    reference = _business_date(as_of, 'a data de referência')
    queryset = ContaPagar.objects.select_related('origem_pedido').all()
    return {
        'abertas': queryset.filter(
            situacao=ContaPagar.Situacao.ABERTA,
            data_vencimento__gte=reference,
        ),
        'vencidas': queryset.filter(
            situacao=ContaPagar.Situacao.ABERTA,
            data_vencimento__lt=reference,
        ),
        'pagas': queryset.filter(situacao=ContaPagar.Situacao.PAGA),
    }


def listar_pedidos_elegiveis_conta_pagar():
    """Lista pedidos completos ainda sem obrigação financeira vinculada."""
    from compras.models import PedidoCompra

    return PedidoCompra.objects.filter(
        estado=PedidoCompra.Estado.RECEBIDO,
        conta_pagar__isnull=True,
    ).order_by('-confirmado_em', '-pk')


def pagar_conta_pagar(
    *,
    actor: Any,
    conta_pagar: ContaPagar | int,
    account: ContaFinanceira | int,
    payment_date: object,
    amount: object,
    reason: object,
    idempotency_key: object,
) -> PagamentoContaPagarResult:
    """Paga integralmente uma obrigação e registra o débito na mesma transação."""
    _require_manager(actor)
    payable = _payable(conta_pagar)
    resolved_account = _account(account)
    movement = _business_date(payment_date)
    value = _money(amount, 'o valor do pagamento', positive=True)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    signature = _signature([
        'PAGAR_CONTA_PAGAR', actor.pk, payable.pk, resolved_account.pk,
        str(value), movement.isoformat(), normalized_reason,
    ])

    for attempt in range(CONTA_PAGAR_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                existing = PagamentoContaPagar.objects.filter(
                    chave_idempotencia=key,
                ).first()
                if existing:
                    if existing.assinatura != signature:
                        raise _conflicting_key()
                    return PagamentoContaPagarResult(existing, created=False)
                _assert_key_available(key, PagamentoContaPagar)
                locked_payable = ContaPagar.objects.select_for_update().get(
                    pk=payable.pk,
                )
                current_payment = PagamentoContaPagar.objects.filter(
                    conta_pagar=locked_payable,
                    vigente=True,
                ).first()
                if (
                    locked_payable.situacao != ContaPagar.Situacao.ABERTA
                    or current_payment is not None
                ):
                    raise FinanceOperationError('Essa conta a pagar já foi paga.')
                if value != locked_payable.valor:
                    raise FinanceOperationError(
                        'O valor do pagamento deve ser igual ao valor da conta a pagar.',
                    )
                _assert_month_open_for_correction(movement)
                locked_account = ContaFinanceira.objects.select_for_update().get(
                    pk=resolved_account.pk,
                )
                if not locked_account.ativo:
                    raise FinanceOperationError('A conta financeira está inativa.')
                _validate_movement_date(locked_account, movement)
                debit = LancamentoFinanceiro.objects.create(
                    conta=locked_account,
                    direcao=LancamentoFinanceiro.Direcao.DEBITO,
                    valor=value,
                    data_movimento=movement,
                    descricao='Pagamento de conta a pagar',
                    motivo=normalized_reason,
                    responsavel=actor,
                )
                payment = PagamentoContaPagar.objects.create(
                    conta_pagar=locked_payable,
                    conta=locked_account,
                    lancamento=debit,
                    data_pagamento=movement,
                    valor=value,
                    responsavel=actor,
                    motivo=normalized_reason,
                    chave_idempotencia=key,
                    assinatura=signature,
                    vigente=True,
                )
                locked_payable.situacao = ContaPagar.Situacao.PAGA
                locked_payable.save(update_fields=('situacao',))
                _audit(
                    action='PAGAR_CONTA_PAGAR',
                    actor=actor,
                    reason=normalized_reason,
                    key=key,
                    signature=signature,
                    data={
                        'pagamento_id': payment.pk,
                        'conta_pagar_id': locked_payable.pk,
                        'lancamento_id': debit.pk,
                    },
                )
                return PagamentoContaPagarResult(payment, created=True)
        except IntegrityError as error:
            existing = PagamentoContaPagar.objects.filter(
                chave_idempotencia=key,
            ).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key() from error
                return PagamentoContaPagarResult(existing, created=False)
            if PagamentoContaPagar.objects.filter(
                conta_pagar_id=payable.pk,
                vigente=True,
            ).exists():
                raise FinanceOperationError('Essa conta a pagar já foi paga.') from error
            _assert_key_available(key, PagamentoContaPagar)
            raise
        except OperationalError as error:
            if (
                not _is_recoverable_sqlite_lock(error)
                or attempt + 1 >= CONTA_PAGAR_MAX_ATTEMPTS
            ):
                raise
            sleep(0.01 * (attempt + 1))
    raise AssertionError('A rotina de pagamento de conta a pagar terminou sem resultado.')


def estornar_pagamento_conta_pagar(
    *,
    actor: Any,
    pagamento: PagamentoContaPagar | int,
    movement_date: object,
    reason: object,
    idempotency_key: object,
) -> EstornoPagamentoContaPagarResult:
    """Estorna o débito e reabre a obrigação em uma única transação."""
    _require_manager(actor)
    payment = _payable_payment(pagamento)
    movement = _business_date(movement_date)
    normalized_reason = _required_text(reason, 'um motivo', 500)
    key = _key(idempotency_key)
    signature = _signature([
        'ESTORNAR_PAGAMENTO_CONTA_PAGAR', actor.pk, payment.pk,
        movement.isoformat(), normalized_reason,
    ])

    for attempt in range(CONTA_PAGAR_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                existing = EstornoPagamentoContaPagar.objects.filter(
                    chave_idempotencia=key,
                ).first()
                if existing:
                    if existing.assinatura != signature:
                        raise _conflicting_key()
                    return EstornoPagamentoContaPagarResult(existing, created=False)
                _assert_key_available(key, EstornoPagamentoContaPagar)
                locked_payment = PagamentoContaPagar.objects.select_for_update().get(
                    pk=payment.pk,
                )
                locked_payable = ContaPagar.objects.select_for_update().get(
                    pk=locked_payment.conta_pagar_id,
                )
                if EstornoPagamentoContaPagar.objects.filter(
                    pagamento_id=locked_payment.pk,
                ).exists():
                    raise FinanceOperationError('Esse pagamento já possui um estorno.')
                _assert_month_open_for_correction(movement)
                original = LancamentoFinanceiro.objects.select_for_update().get(
                    pk=locked_payment.lancamento_id,
                )
                locked_account = ContaFinanceira.objects.select_for_update().get(
                    pk=locked_payment.conta_id,
                )
                if not locked_account.ativo:
                    raise FinanceOperationError('A conta financeira está inativa.')
                _validate_movement_date(locked_account, movement)
                reversal = LancamentoFinanceiro.objects.create(
                    conta=locked_account,
                    direcao=LancamentoFinanceiro.Direcao.CREDITO,
                    valor=locked_payment.valor,
                    data_movimento=movement,
                    descricao='Estorno de pagamento de conta a pagar',
                    motivo=normalized_reason,
                    responsavel=actor,
                    estorno_de=original,
                )
                estorno = EstornoPagamentoContaPagar.objects.create(
                    pagamento=locked_payment,
                    lancamento=reversal,
                    data_movimento=movement,
                    responsavel=actor,
                    motivo=normalized_reason,
                    chave_idempotencia=key,
                    assinatura=signature,
                )
                locked_payment.vigente = False
                locked_payment.save(update_fields=('vigente',))
                locked_payable.situacao = ContaPagar.Situacao.ABERTA
                locked_payable.save(update_fields=('situacao',))
                _audit(
                    action='ESTORNAR_PAGAMENTO_CONTA_PAGAR',
                    actor=actor,
                    reason=normalized_reason,
                    key=key,
                    signature=signature,
                    data={
                        'estorno_id': estorno.pk,
                        'pagamento_id': locked_payment.pk,
                        'lancamento_original_id': original.pk,
                        'lancamento_estorno_id': reversal.pk,
                    },
                )
                return EstornoPagamentoContaPagarResult(estorno, created=True)
        except IntegrityError as error:
            existing = EstornoPagamentoContaPagar.objects.filter(
                chave_idempotencia=key,
            ).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key() from error
                return EstornoPagamentoContaPagarResult(existing, created=False)
            if EstornoPagamentoContaPagar.objects.filter(
                pagamento_id=payment.pk,
            ).exists():
                raise FinanceOperationError('Esse pagamento já possui um estorno.') from error
            _assert_key_available(key, EstornoPagamentoContaPagar)
            raise
        except OperationalError as error:
            if (
                not _is_recoverable_sqlite_lock(error)
                or attempt + 1 >= CONTA_PAGAR_MAX_ATTEMPTS
            ):
                raise
            sleep(0.01 * (attempt + 1))
    raise AssertionError('A rotina de estorno de pagamento terminou sem resultado.')


def registrar_estorno_pix_troca(*, actor: Any, pagamento_restituicao: PagamentoRestituicaoTroca):
    """Debitar a conta do Pix de origem uma única vez após restituição de troca."""
    _require_manager(actor)
    refund = pagamento_restituicao
    if refund.pagamento_original.forma != 'PIX':
        return None
    with transaction.atomic():
        receipt = RecebimentoPixFinanceiro.objects.select_for_update().filter(
            pagamento_origem_id=refund.pagamento_original_id,
        ).first()
        if receipt is None:
            return None
        key = f'estorno-pix-troca-{refund.pk}'
        existing = EstornoPixTroca.objects.filter(pagamento_restituicao=refund).first()
        if existing:
            return existing
        movement = max(timezone.localdate(refund.restituicao.criada_em), receipt.data_confirmacao)
        _assert_month_open_for_correction(movement)
        _validate_movement_date(receipt.conta, movement)
        signature = _signature(['ESTORNAR_PIX_TROCA', refund.pk, receipt.pk, str(refund.valor)])
        _assert_key_available(key, EstornoPixTroca)
        ledger = LancamentoFinanceiro.objects.create(
            conta=receipt.conta, direcao=LancamentoFinanceiro.Direcao.DEBITO,
            valor=refund.valor, data_movimento=movement,
            descricao='Restituição Pix de troca', motivo=refund.restituicao.motivo, responsavel=actor,
        )
        reversal = EstornoPixTroca.objects.create(
            pagamento_restituicao=refund, recebimento_pix=receipt, lancamento=ledger,
            valor=refund.valor, data_movimento=movement, responsavel=actor,
            motivo=refund.restituicao.motivo, chave_idempotencia=key, assinatura=signature,
        )
        _audit(action='ESTORNAR_PIX_TROCA', actor=actor, reason=refund.restituicao.motivo,
               key=key, signature=signature, data={'pagamento_restituicao_troca_id': refund.pk, 'lancamento_id': ledger.pk})
        return reversal
