from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Sum

from .models import (
    AporteFinanceiro,
    ContaFinanceira,
    EstornoFinanceiro,
    EventoAuditoriaFinanceiro,
    LancamentoFinanceiro,
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
        EventoAuditoriaFinanceiro,
    ):
        if model is current_model:
            continue
        if model.objects.filter(chave_idempotencia=key).exists():
            raise _conflicting_key()


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
    if original.estorno_de_id:
        raise FinanceOperationError('Um estorno não pode ser estornado novamente.')
    if original.estorno_operacoes.exists():
        raise FinanceOperationError('Esse lançamento já possui um estorno.')
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

    try:
        with transaction.atomic():
            existing = EstornoFinanceiro.objects.filter(chave_idempotencia=key).first()
            if existing:
                if existing.assinatura != signature:
                    raise _conflicting_key()
                return EstornoResult(existing, created=False)
            _assert_key_available(key, EstornoFinanceiro)
            locked_original = LancamentoFinanceiro.objects.select_for_update().select_related(
                'conta',
            ).get(pk=original.pk)
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
        existing = EstornoFinanceiro.objects.filter(chave_idempotencia=key).first()
        if existing:
            if existing.assinatura != signature:
                raise _conflicting_key() from error
            return EstornoResult(existing, created=False)
        _assert_key_available(key, EstornoFinanceiro)
        raise
