"""Escritas de Caixa: permissão, normalização, auditoria e idempotência."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, OperationalError, connections, transaction
from django.db.models import F, Sum

from vendas.models import Venda, VendaCancelamento, VendaDevolucao

from .models import (
    CorteCaixa,
    FechamentoCaixa,
    PagamentoRestituicao,
    PagamentoVenda,
    PendenciaCancelamentoVenda,
    PendenciaDevolucaoVenda,
    RecebimentoVenda,
    RestituicaoVenda,
    SangriaCaixa,
    SessaoCaixa,
    SuprimentoCaixa,
)


class CaixaOperationError(ValueError):
    """Pedido inválido ou conflito de operação recuperável pelo operador."""


@dataclass(frozen=True)
class SessaoResult:
    sessao: SessaoCaixa
    created: bool


@dataclass(frozen=True)
class RecebimentoResult:
    recebimento: RecebimentoVenda
    created: bool


@dataclass(frozen=True)
class FinalizacaoResult:
    sale: Venda
    recebimento: RecebimentoVenda
    created: bool


@dataclass(frozen=True)
class MovimentoCaixaResult:
    evento: SuprimentoCaixa | SangriaCaixa
    created: bool


@dataclass(frozen=True)
class RestituicaoResult:
    restituicao: RestituicaoVenda
    created: bool


@dataclass(frozen=True)
class FechamentoResult:
    fechamento: FechamentoCaixa
    created: bool


@dataclass(frozen=True)
class CorteCaixaResult:
    corte: CorteCaixa
    created: bool


@dataclass(frozen=True)
class FechamentoPreview:
    dinheiro_esperado: Decimal
    totais_por_forma: dict[str, str]
    pendencias_pendentes: int


@dataclass(frozen=True)
class RecebimentoPreview:
    pagamentos: tuple[dict[str, object], ...]
    valor_aplicado: Decimal
    restante: Decimal
    valor_entregue: Decimal
    troco: Decimal


def _require_manager(actor) -> None:
    if not (
        actor and actor.is_authenticated and actor.is_active and actor.pk
        and (actor.is_superuser or getattr(actor, 'cargo', '') == 'G')
    ):
        raise CaixaOperationError('Apenas gerente ativo pode executar esta operação de caixa.')


def _is_recoverable_sqlite_lock(error: OperationalError) -> bool:
    """Recognize only SQLite BUSY/LOCKED errors, not lookalike messages."""
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


def _money(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip().replace(',', '.'))
        if not parsed.is_finite() or not Decimal('0') <= parsed <= Decimal('9999999999.99'):
            raise ValueError
        rounded = parsed.quantize(Decimal('0.01'))
        if rounded != parsed:
            raise ValueError
    except (InvalidOperation, TypeError, ValueError) as error:
        raise CaixaOperationError(
            f'Informe {label} não negativo com até duas casas decimais.',
        ) from error
    # Decimal('-0') e Decimal('0') representam o mesmo pedido.
    return rounded if rounded else Decimal('0.00')


def _required_text(value: str | None, label: str, limit: int) -> str:
    normalized = (value or '').strip()
    if not normalized or len(normalized) > limit:
        raise CaixaOperationError(f'Informe {label} com até {limit} caracteres.')
    return normalized


def _cutover_signature(actor_id: int, reason: str) -> str:
    payload = [actor_id, reason]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def ativar_corte_caixa(*, actor, reason: str, idempotency_key: str | None) -> CorteCaixaResult:
    """Persist the one-way cutover; new sales then require Caixa orchestration."""
    _require_manager(actor)
    normalized_reason = _required_text(reason, 'um motivo para o corte', 500)
    key = _required_text(idempotency_key, 'uma chave de operação', 64)
    signature = _cutover_signature(actor.pk, normalized_reason)

    try:
        with transaction.atomic():
            existing = CorteCaixa.objects.filter(chave_idempotencia=key).first()
            if existing:
                if existing.responsavel_id != actor.pk or existing.assinatura != signature:
                    raise CaixaOperationError(
                        'Essa chave de corte já foi usada com outros dados.',
                    )
                return CorteCaixaResult(existing, created=False)
            if CorteCaixa.objects.exists():
                raise CaixaOperationError('O controle Caixa já foi ativado nesta instalação.')

            try:
                with transaction.atomic():
                    cutover = CorteCaixa.objects.create(
                        responsavel=actor,
                        motivo=normalized_reason,
                        chave_idempotencia=key,
                        assinatura=signature,
                    )
            except IntegrityError:
                existing = CorteCaixa.objects.filter(chave_idempotencia=key).first()
                if existing and existing.responsavel_id == actor.pk and existing.assinatura == signature:
                    return CorteCaixaResult(existing, created=False)
                raise CaixaOperationError(
                    'O controle Caixa já foi ativado nesta instalação.',
                ) from None
            return CorteCaixaResult(cutover, created=True)
    except OperationalError as error:
        if _is_recoverable_sqlite_lock(error):
            raise CaixaOperationError(
                'O corte do Caixa está ocupado. Atualize e tente novamente usando a mesma chave.',
            ) from error
        raise


def _require_sales_operator(actor) -> None:
    if not (
        actor and actor.is_authenticated and actor.is_active and actor.pk
        and (actor.is_superuser or getattr(actor, 'cargo', '') in {'G', 'V', 'C'})
    ):
        raise CaixaOperationError('Usuário ativo autorizado para vendas é obrigatório.')


def _normalize_payments(
    payments: object, total: Decimal,
) -> list[tuple[str, Decimal, Decimal, Decimal]]:
    if isinstance(payments, (str, bytes)) or not isinstance(payments, Iterable):
        raise CaixaOperationError('Informe as parcelas de pagamento da venda.')

    allowed_forms = dict(PagamentoVenda.FORMA_PAGAMENTO_CHOICES)
    grouped: dict[str, list[Decimal]] = {}
    for line in payments:
        if not isinstance(line, Mapping):
            raise CaixaOperationError('Cada parcela deve informar forma e valor aplicado.')
        form = str(line.get('forma') or '').strip().upper()
        if form not in allowed_forms:
            raise CaixaOperationError('Selecione uma forma de pagamento válida.')
        applied = _money(line.get('valor_aplicado'), 'o valor aplicado')
        if applied <= 0:
            raise CaixaOperationError('Cada parcela deve ter valor aplicado positivo.')

        tendered_input = line.get('valor_entregue')
        if form == 'DINHEIRO':
            if tendered_input in (None, ''):
                raise CaixaOperationError('Informe quanto foi entregue em dinheiro.')
            tendered = _money(tendered_input, 'o valor entregue em dinheiro')
            if tendered < applied:
                raise CaixaOperationError('O valor entregue em dinheiro não cobre a parcela.')
        else:
            tendered = (
                applied if tendered_input in (None, '')
                else _money(tendered_input, 'o valor entregue')
            )
            if tendered != applied:
                raise CaixaOperationError(
                    'Nas formas Pix e cartão, o valor entregue deve ser igual ao aplicado.',
                )

        amounts = grouped.setdefault(form, [Decimal('0.00'), Decimal('0.00')])
        amounts[0] += applied
        amounts[1] += tendered

    normalized = [
        (form, values[0], values[1], values[1] - values[0] if form == 'DINHEIRO' else Decimal('0.00'))
        for form, values in sorted(grouped.items())
    ]
    applied_total = sum((item[1] for item in normalized), Decimal('0.00'))
    if applied_total != total:
        raise CaixaOperationError(
            'A soma dos pagamentos deve corresponder exatamente ao total líquido da venda.',
        )
    if total == 0 and normalized:
        raise CaixaOperationError('Venda sem saldo não deve criar parcelas de pagamento.')
    return normalized


def previsualizar_recebimento(*, payments: object, total: object) -> RecebimentoPreview:
    """Validar parcelas e calcular aplicado, restante e troco sem persistir dados."""
    parsed_total = _money(total, 'o total da venda')
    normalized = _normalize_payments(payments, parsed_total)
    applied = sum((line[1] for line in normalized), Decimal('0.00'))
    tendered = sum((line[2] for line in normalized), Decimal('0.00'))
    change = sum((line[3] for line in normalized), Decimal('0.00'))
    return RecebimentoPreview(
        pagamentos=tuple({
            'forma': form,
            'forma_label': dict(PagamentoVenda.FORMA_PAGAMENTO_CHOICES)[form],
            'valor_aplicado': amount,
            'valor_entregue': amount_tendered,
            'troco': amount_change,
        } for form, amount, amount_tendered, amount_change in normalized),
        valor_aplicado=applied,
        restante=parsed_total - applied,
        valor_entregue=tendered,
        troco=change,
    )


def _receipt_signature(
    *, actor_id: int, session_id: int, sale_id: int, total: Decimal,
    reason: str, payments: list[tuple[str, Decimal, Decimal, Decimal]],
) -> str:
    payload = [
        actor_id,
        session_id,
        sale_id,
        str(total),
        reason,
        [[form, str(applied), str(tendered), str(change)]
         for form, applied, tendered, change in payments],
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
    ).hexdigest()


def _receipt_replay(receipt: RecebimentoVenda, signature: str) -> RecebimentoResult:
    if receipt.assinatura != signature:
        raise CaixaOperationError(
            'Essa chave de recebimento já foi usada com outros dados.',
        )
    return RecebimentoResult(receipt, created=False)


def _opening_replay(session: SessaoCaixa, signature: str) -> SessaoResult:
    if session.assinatura != signature:
        raise CaixaOperationError('Essa chave de abertura já foi usada com outros dados.')
    return SessaoResult(session, created=False)


def abrir_sessao(
    *, actor, fundo_inicial: object, motivo: str, idempotency_key: str | None,
) -> SessaoResult:
    """Abrir uma única sessão; replays consultam o evento mesmo após fechar."""
    _require_manager(actor)
    amount = _money(fundo_inicial, 'fundo inicial')
    reason = _required_text(motivo, 'um motivo', 500)
    key = _required_text(idempotency_key, 'uma chave de operação', 64)
    signature = hashlib.sha256(json.dumps(
        [actor.pk, str(amount), reason], ensure_ascii=False, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()

    try:
        with transaction.atomic():
            existing = SessaoCaixa.objects.filter(chave_idempotencia=key).first()
            if existing:
                return _opening_replay(existing, signature)
            if SessaoCaixa.objects.filter(aberta=True).exists():
                raise CaixaOperationError(
                    'Já existe uma sessão de caixa aberta. '
                    'Atualize e tente novamente usando a mesma chave.',
                )
            session = SessaoCaixa.objects.create(
                responsavel=actor, fundo_inicial=amount, motivo=reason,
                chave_idempotencia=key, assinatura=signature,
            )
            return SessaoResult(session, created=True)
    except IntegrityError as error:
        # Outra conexão pode vencer entre consulta e INSERT. A constraint,
        # e não o select_for_update ineficaz no SQLite, garante exclusividade.
        existing = SessaoCaixa.objects.filter(chave_idempotencia=key).first()
        if existing:
            return _opening_replay(existing, signature)
        if SessaoCaixa.objects.filter(aberta=True).exists():
            raise CaixaOperationError(
                'Já existe uma sessão de caixa aberta. '
                'Atualize e tente novamente usando a mesma chave.',
            ) from error
        raise
    except OperationalError as error:
        if _is_recoverable_sqlite_lock(error):
            raise CaixaOperationError(
                'O caixa está ocupado. Atualize e tente novamente usando a mesma chave.',
            ) from error
        raise


def registrar_recebimento(
    *, actor, sessao: SessaoCaixa, venda: Venda,
    pagamentos: Iterable[Mapping[str, object]], motivo: str,
    idempotency_key: str | None, versao_esperada: int | None = None,
) -> RecebimentoResult:
    """Persistir parcelas exatas e serializar o recebimento pela versão da sessão."""
    _require_sales_operator(actor)
    key = _required_text(idempotency_key, 'uma chave de operação', 64)
    reason = _required_text(motivo, 'um motivo', 500)
    if not isinstance(sessao, SessaoCaixa) or not sessao.pk:
        raise CaixaOperationError('Selecione uma sessão de caixa válida.')
    if not isinstance(venda, Venda) or not venda.pk:
        raise CaixaOperationError('Selecione uma venda registrada.')

    try:
        sale_record = Venda.objects.get(pk=venda.pk)
    except Venda.DoesNotExist as error:
        raise CaixaOperationError('A venda informada não existe.') from error
    total = _money(sale_record.total, 'o total líquido da venda')
    normalized = _normalize_payments(pagamentos, total)
    signature = _receipt_signature(
        actor_id=actor.pk,
        session_id=sessao.pk,
        sale_id=sale_record.pk,
        total=total,
        reason=reason,
        payments=normalized,
    )
    expected_version = sessao.versao if versao_esperada is None else versao_esperada

    try:
        with transaction.atomic():
            existing = RecebimentoVenda.objects.select_related(
                'sessao', 'venda', 'responsavel',
            ).filter(chave_idempotencia=key).first()
            if existing:
                return _receipt_replay(existing, signature)

            if RecebimentoVenda.objects.filter(venda=sale_record).exists():
                raise CaixaOperationError('Essa venda já possui um recebimento.')

            claimed = SessaoCaixa.objects.filter(
                pk=sessao.pk, aberta=True, versao=expected_version,
            ).update(versao=F('versao') + 1)
            if claimed != 1:
                raise CaixaOperationError(
                    'O caixa foi alterado ou está fechado. Atualize e tente novamente.',
                )

            receipt = RecebimentoVenda.objects.create(
                sessao_id=sessao.pk,
                venda=sale_record,
                valor_total=total,
                responsavel=actor,
                motivo=reason,
                chave_idempotencia=key,
                assinatura=signature,
            )
            PagamentoVenda.objects.bulk_create([
                PagamentoVenda(
                    recebimento=receipt,
                    forma=form,
                    valor_aplicado=applied,
                    valor_entregue=tendered,
                    troco=change,
                )
                for form, applied, tendered, change in normalized
            ])
    except IntegrityError as error:
        existing = RecebimentoVenda.objects.filter(chave_idempotencia=key).first()
        if existing:
            return _receipt_replay(existing, signature)
        if RecebimentoVenda.objects.filter(venda_id=venda.pk).exists():
            raise CaixaOperationError('Essa venda já possui um recebimento.') from error
        raise CaixaOperationError(
            'Não foi possível confirmar o recebimento. Atualize o caixa e tente novamente.',
        ) from error
    except OperationalError as error:
        if _is_recoverable_sqlite_lock(error):
            raise CaixaOperationError(
                'O caixa está ocupado. Atualize e tente novamente usando a mesma chave.',
            ) from error
        raise

    sessao.versao = expected_version + 1
    return RecebimentoResult(receipt, created=True)


def calcular_saldo_dinheiro(sessao: SessaoCaixa | int) -> Decimal:
    """Apurar dinheiro físico por parcelas líquidas, sem contar troco ou não dinheiro."""
    session_id = sessao.pk if isinstance(sessao, SessaoCaixa) else int(sessao)
    try:
        session = SessaoCaixa.objects.get(pk=session_id)
    except SessaoCaixa.DoesNotExist as error:
        raise CaixaOperationError('Selecione uma sessão de caixa válida.') from error

    cash_applied = PagamentoVenda.objects.filter(
        recebimento__sessao_id=session.pk,
        forma='DINHEIRO',
    ).aggregate(total=Sum('valor_aplicado'))['total'] or Decimal('0.00')
    supplies = SuprimentoCaixa.objects.filter(
        sessao_id=session.pk,
    ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
    withdrawals = SangriaCaixa.objects.filter(
        sessao_id=session.pk,
    ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
    cash_refunds = PagamentoRestituicao.objects.filter(
        restituicao__sessao_id=session.pk,
        pagamento_original__forma='DINHEIRO',
    ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
    return session.fundo_inicial + cash_applied + supplies - withdrawals - cash_refunds


def _pending_refunds_for_session(session_id: int) -> int:
    pending_cancellations = PendenciaCancelamentoVenda.objects.filter(
        sessao_origem_id=session_id,
        restituicao__isnull=True,
    ).count()
    pending_returns = PendenciaDevolucaoVenda.objects.filter(
        sessao_origem_id=session_id,
        restituicao__isnull=True,
    ).count()
    return pending_cancellations + pending_returns


def _payment_form_totals(session_id: int) -> dict[str, str]:
    totals: dict[str, str] = {}
    for form, _label in PagamentoVenda.FORMA_PAGAMENTO_CHOICES:
        received = PagamentoVenda.objects.filter(
            recebimento__sessao_id=session_id,
            forma=form,
        ).aggregate(total=Sum('valor_aplicado'))['total'] or Decimal('0.00')
        returned = PagamentoRestituicao.objects.filter(
            restituicao__sessao_id=session_id,
            pagamento_original__forma=form,
        ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')
        totals[form] = str((received - returned).quantize(Decimal('0.01')))
    return totals


def previsualizar_fechamento(
    sessao: SessaoCaixa | int | str,
) -> FechamentoPreview:
    if isinstance(sessao, SessaoCaixa):
        session_id = sessao.pk
    else:
        try:
            session_id = int(sessao)
        except (TypeError, ValueError) as error:
            raise CaixaOperationError('Selecione uma sessão de caixa válida.') from error
    try:
        session = SessaoCaixa.objects.get(pk=session_id)
    except SessaoCaixa.DoesNotExist as error:
        raise CaixaOperationError('Selecione uma sessão de caixa válida.') from error
    return FechamentoPreview(
        dinheiro_esperado=calcular_saldo_dinheiro(session.pk),
        totais_por_forma=_payment_form_totals(session.pk),
        pendencias_pendentes=_pending_refunds_for_session(session.pk),
    )


def _closing_signature(
    *, actor_id: int, session_id: int, counted: Decimal, reason: str,
) -> str:
    payload = ['FECHAMENTO', actor_id, session_id, str(counted), reason]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
    ).hexdigest()


def _closing_replay(
    closing: FechamentoCaixa, signature: str,
) -> FechamentoResult:
    if closing.assinatura != signature:
        raise CaixaOperationError(
            'Essa chave de fechamento já foi usada com outros dados.',
        )
    return FechamentoResult(closing, created=False)


def fechar_sessao(
    *, actor, sessao: SessaoCaixa | int | str,
    dinheiro_contado: object, justificativa: str = '',
    idempotency_key: str | None, versao_esperada: int | None = None,
) -> FechamentoResult:
    """Freeze cash count and payment-form totals in one versioned transaction."""
    _require_manager(actor)
    counted = _money(dinheiro_contado, 'o dinheiro contado')
    reason = (justificativa or '').strip()
    if len(reason) > 500:
        raise CaixaOperationError('A justificativa deve ter até 500 caracteres.')
    key = _required_text(idempotency_key, 'uma chave de operação', 64)
    if isinstance(sessao, SessaoCaixa):
        session_id = sessao.pk
        request_version = sessao.versao if versao_esperada is None else versao_esperada
    else:
        try:
            session_id = int(sessao)
        except (TypeError, ValueError) as error:
            raise CaixaOperationError('Selecione uma sessão de caixa válida.') from error
        request_version = versao_esperada

    signature = _closing_signature(
        actor_id=actor.pk,
        session_id=session_id,
        counted=counted,
        reason=reason,
    )
    try:
        with transaction.atomic():
            existing = FechamentoCaixa.objects.filter(chave_idempotencia=key).first()
            if existing:
                return _closing_replay(existing, signature)

            try:
                session = SessaoCaixa.objects.select_for_update().get(pk=session_id)
            except SessaoCaixa.DoesNotExist as error:
                raise CaixaOperationError('Selecione uma sessão de caixa válida.') from error
            if FechamentoCaixa.objects.filter(sessao_id=session.pk).exists():
                raise CaixaOperationError('Essa sessão já foi fechada.')
            if not session.aberta:
                raise CaixaOperationError('A sessão de caixa já está fechada.')

            expected_version = (
                session.versao if request_version is None else request_version
            )
            preview = previsualizar_fechamento(session.pk)
            if preview.pendencias_pendentes:
                raise CaixaOperationError(
                    'Existem restituições pendentes originadas nesta sessão; conclua-as antes de fechar.',
                )
            difference = (counted - preview.dinheiro_esperado).quantize(Decimal('0.01'))
            if difference and not reason:
                raise CaixaOperationError(
                    'Informe uma justificativa para a diferença de caixa.',
                )

            claimed = SessaoCaixa.objects.filter(
                pk=session.pk,
                aberta=True,
                versao=expected_version,
            ).update(aberta=False, versao=F('versao') + 1)
            if claimed != 1:
                raise CaixaOperationError(
                    'O caixa foi alterado ou está fechado. Atualize e confira novamente.',
                )

            closing = FechamentoCaixa.objects.create(
                sessao_id=session.pk,
                dinheiro_esperado=preview.dinheiro_esperado,
                dinheiro_contado=counted,
                diferenca=difference,
                totais_por_forma=preview.totais_por_forma,
                responsavel=actor,
                justificativa=reason,
                chave_idempotencia=key,
                assinatura=signature,
            )
    except IntegrityError as error:
        existing = FechamentoCaixa.objects.filter(chave_idempotencia=key).first()
        if existing:
            return _closing_replay(existing, signature)
        if FechamentoCaixa.objects.filter(sessao_id=session_id).exists():
            raise CaixaOperationError('Essa sessão já foi fechada.') from error
        raise CaixaOperationError(
            'Não foi possível fechar a sessão. Atualize e confira novamente.',
        ) from error
    except OperationalError as error:
        if _is_recoverable_sqlite_lock(error):
            raise CaixaOperationError(
                'O caixa está ocupado. Atualize e tente novamente usando a mesma chave.',
            ) from error
        raise

    session.aberta = False
    session.versao = expected_version + 1
    if isinstance(sessao, SessaoCaixa):
        sessao.aberta = False
        sessao.versao = session.versao
    return FechamentoResult(closing, created=True)


def _pending_key(operation: str, source_key: str) -> str:
    return hashlib.sha256(f'{operation}:{source_key}'.encode('utf-8')).hexdigest()


def _pending_signature(
    *, operation: str, actor_id: int, source_id: int, receipt_id: int,
    session_id: int, amount: Decimal, reason: str,
) -> str:
    payload = [
        operation, actor_id, source_id, receipt_id, session_id, str(amount), reason,
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
    ).hexdigest()


def _refund_signature(
    *, operation: str, actor_id: int, pending_id: int, receipt_id: int,
    session_id: int, amount: Decimal, reason: str, manual_confirmation: bool,
) -> str:
    payload = [
        operation, actor_id, pending_id, receipt_id, session_id,
        str(amount), reason, manual_confirmation,
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
    ).hexdigest()


def _remaining_payment_lines(receipt: RecebimentoVenda, *, for_update: bool = False):
    query = PagamentoVenda.objects.filter(recebimento_id=receipt.pk).order_by('pk')
    if for_update:
        query = query.select_for_update()
    payments = list(query)
    refunded_rows = PagamentoRestituicao.objects.filter(
        pagamento_original_id__in=[payment.pk for payment in payments],
    ).values('pagamento_original_id').annotate(total=Sum('valor'))
    refunded = {
        row['pagamento_original_id']: row['total'] or Decimal('0.00')
        for row in refunded_rows
    }
    return [
        (payment, (payment.valor_aplicado - refunded.get(payment.pk, Decimal('0.00'))).quantize(Decimal('0.01')))
        for payment in payments
        if payment.valor_aplicado > refunded.get(payment.pk, Decimal('0.00'))
    ]


def _refund_allocations(pending, *, for_update: bool = False):
    lines = _remaining_payment_lines(pending.recebimento, for_update=for_update)
    amount = pending.valor_total.quantize(Decimal('0.01'))
    capacities = [(payment, int(remaining * 100)) for payment, remaining in lines]
    total_capacity = sum(cents for _, cents in capacities)
    amount_cents = int(amount * 100)
    if amount_cents <= 0 or amount_cents > total_capacity:
        raise CaixaOperationError(
            'A restituição supera o valor líquido ainda disponível nas parcelas originais.',
        )
    if not capacities:
        raise CaixaOperationError('O recebimento original não possui parcelas restituíveis.')

    shares: dict[int, int] = {}
    remainders: dict[int, int] = {}
    by_id = {payment.pk: payment for payment, _ in capacities}
    for payment, capacity in capacities:
        numerator = amount_cents * capacity
        shares[payment.pk], remainders[payment.pk] = divmod(numerator, total_capacity)
    cents_left = amount_cents - sum(shares.values())
    for payment_id in sorted(shares, key=lambda value: (-remainders[value], value))[:cents_left]:
        shares[payment_id] += 1
    return [
        (by_id[payment_id], (Decimal(cents) / 100).quantize(Decimal('0.01')))
        for payment_id, cents in sorted(shares.items())
        if cents > 0
    ]


def _register_refund_pending(
    *, actor, source: VendaCancelamento | VendaDevolucao,
    source_kind: str,
):
    _require_manager(actor)
    if source.responsavel_id != actor.pk:
        raise CaixaOperationError('A autoria da reversão não corresponde ao gerente informado.')
    receipt = RecebimentoVenda.objects.select_related('sessao').filter(
        venda_id=source.venda_id,
    ).first()
    # Historical sales without a Caixa receipt retain their legacy reversal only.
    if receipt is None:
        return None

    amount = source.valor_total.quantize(Decimal('0.01'))
    if amount <= 0:
        return None
    payment_total = sum(
        PagamentoVenda.objects.filter(recebimento_id=receipt.pk).values_list(
            'valor_aplicado', flat=True,
        ),
        Decimal('0.00'),
    ).quantize(Decimal('0.01'))
    if payment_total != receipt.valor_total:
        raise CaixaOperationError(
            'O recebimento original não fecha com as parcelas registradas; revise manualmente.',
        )
    if receipt.valor_total != source.venda.total:
        raise CaixaOperationError(
            'O recebimento original diverge do líquido da venda; revise manualmente.',
        )
    remaining_total = sum(
        (remaining for _, remaining in _remaining_payment_lines(receipt)),
        Decimal('0.00'),
    ).quantize(Decimal('0.01'))
    if amount > remaining_total:
        raise CaixaOperationError(
            'O valor da reversão supera o saldo ainda não restituído do recebimento.',
        )
    if source_kind == 'cancelamento' and amount != remaining_total:
        raise CaixaOperationError(
            'O cancelamento integral não corresponde ao saldo líquido ainda restituível.',
        )

    operation = f'pendencia-{source_kind}'
    key = _pending_key(operation, source.chave_idempotencia)
    signature = _pending_signature(
        operation=operation,
        actor_id=actor.pk,
        source_id=source.pk,
        receipt_id=receipt.pk,
        session_id=receipt.sessao_id,
        amount=amount,
        reason=source.motivo,
    )
    model = (
        PendenciaCancelamentoVenda
        if source_kind == 'cancelamento'
        else PendenciaDevolucaoVenda
    )
    source_field = 'cancelamento' if source_kind == 'cancelamento' else 'devolucao'
    existing = model.objects.filter(**{f'{source_field}_id': source.pk}).first()
    if existing:
        if existing.assinatura != signature:
            raise CaixaOperationError('A pendência existente diverge da reversão registrada.')
        return existing
    values = {
        source_field: source,
        'recebimento': receipt,
        'sessao_origem': receipt.sessao,
        'valor_total': amount,
        'responsavel': actor,
        'motivo': source.motivo,
        'chave_idempotencia': key,
        'assinatura': signature,
    }
    return model.objects.create(**values)


def registrar_pendencia_cancelamento(
    *, actor, cancelamento: VendaCancelamento,
) -> PendenciaCancelamentoVenda | None:
    """Record the refund obligation alongside a received sale cancellation."""
    return _register_refund_pending(
        actor=actor,
        source=cancelamento,
        source_kind='cancelamento',
    )


def registrar_pendencia_devolucao(
    *, actor, devolucao: VendaDevolucao,
) -> PendenciaDevolucaoVenda | None:
    """Record a net refund obligation alongside a received sale return."""
    return _register_refund_pending(
        actor=actor,
        source=devolucao,
        source_kind='devolucao',
    )


def previsualizar_restituicao(
    *,
    pendencia_cancelamento: PendenciaCancelamentoVenda | None = None,
    pendencia_devolucao: PendenciaDevolucaoVenda | None = None,
):
    if (pendencia_cancelamento is None) == (pendencia_devolucao is None):
        raise CaixaOperationError('Selecione uma única pendência de restituição.')
    pending = pendencia_cancelamento or pendencia_devolucao
    allocations = _refund_allocations(pending)
    return tuple({
        'forma': payment.forma,
        'forma_display': payment.get_forma_display(),
        'valor': value,
    } for payment, value in allocations)


def registrar_restituicao(
    *, actor, sessao: SessaoCaixa | int | str,
    pendencia_cancelamento: PendenciaCancelamentoVenda | None = None,
    pendencia_devolucao: PendenciaDevolucaoVenda | None = None,
    motivo: str, idempotency_key: str | None,
    confirmacao_manual: bool = False,
    versao_esperada: int | None = None,
) -> RestituicaoResult:
    """Pay one pending refund in an open session, preserving original forms."""
    _require_manager(actor)
    if (pendencia_cancelamento is None) == (pendencia_devolucao is None):
        raise CaixaOperationError('Selecione uma única pendência de restituição.')
    reason = _required_text(motivo, 'um motivo', 500)
    key = _required_text(idempotency_key, 'uma chave de operação', 64)
    if isinstance(sessao, SessaoCaixa):
        session_id = sessao.pk
        expected_version = sessao.versao if versao_esperada is None else versao_esperada
    else:
        try:
            session_id = int(sessao)
        except (TypeError, ValueError) as error:
            raise CaixaOperationError('Selecione uma sessão de caixa válida.') from error
        expected_version = versao_esperada

    if pendencia_cancelamento is not None:
        pending_model = PendenciaCancelamentoVenda
        pending_id = pendencia_cancelamento.pk
        source_field = 'pendencia_cancelamento'
        operation = 'restituicao-cancelamento'
    elif pendencia_devolucao is not None:
        pending_model = PendenciaDevolucaoVenda
        pending_id = pendencia_devolucao.pk
        source_field = 'pendencia_devolucao'
        operation = 'restituicao-devolucao'
    else:
        raise CaixaOperationError('Selecione uma única pendência de restituição.')

    try:
        with transaction.atomic():
            try:
                pending = pending_model.objects.select_for_update().select_related(
                    'recebimento', 'recebimento__sessao',
                ).get(pk=pending_id)
            except pending_model.DoesNotExist as error:
                raise CaixaOperationError('Pendência de restituição não encontrada.') from error

            signature = _refund_signature(
                operation=operation,
                actor_id=actor.pk,
                pending_id=pending.pk,
                receipt_id=pending.recebimento_id,
                session_id=session_id,
                amount=pending.valor_total,
                reason=reason,
                manual_confirmation=bool(confirmacao_manual),
            )
            existing = RestituicaoVenda.objects.filter(chave_idempotencia=key).first()
            if existing:
                if existing.assinatura != signature:
                    raise CaixaOperationError(
                        'Essa chave de operação já foi usada com outros dados.',
                    )
                return RestituicaoResult(existing, created=False)
            if RestituicaoVenda.objects.filter(**{source_field: pending}).exists():
                raise CaixaOperationError('Essa pendência já foi restituída.')

            session = SessaoCaixa.objects.select_for_update().filter(pk=session_id).first()
            if session is None:
                raise CaixaOperationError('Selecione uma sessão de caixa válida.')
            if expected_version is None:
                expected_version = session.versao
            if not session.aberta:
                raise CaixaOperationError('A sessão de caixa está fechada.')

            allocations = _refund_allocations(pending, for_update=True)
            cash_amount = sum(
                (value for payment, value in allocations if payment.forma == 'DINHEIRO'),
                Decimal('0.00'),
            ).quantize(Decimal('0.01'))
            has_non_cash = any(payment.forma != 'DINHEIRO' for payment, _ in allocations)
            if has_non_cash and confirmacao_manual is not True:
                raise CaixaOperationError(
                    'A confirmação manual da restituição por Pix ou cartão é obrigatória.',
                )

            claimed = SessaoCaixa.objects.filter(
                pk=session.pk,
                aberta=True,
                versao=expected_version,
            ).update(versao=F('versao') + 1)
            if claimed != 1:
                raise CaixaOperationError(
                    'O caixa foi alterado ou está fechado. Atualize e tente novamente.',
                )
            if cash_amount > calcular_saldo_dinheiro(session.pk):
                raise CaixaOperationError(
                    'A restituição em dinheiro supera o saldo físico esperado do caixa.',
                )

            signature = _refund_signature(
                operation=operation,
                actor_id=actor.pk,
                pending_id=pending.pk,
                receipt_id=pending.recebimento_id,
                session_id=session.pk,
                amount=pending.valor_total,
                reason=reason,
                manual_confirmation=bool(confirmacao_manual),
            )
            values = {
                source_field: pending,
                'sessao': session,
                'valor_total': pending.valor_total,
                'responsavel': actor,
                'motivo': reason,
                'confirmacao_manual': bool(confirmacao_manual),
                'chave_idempotencia': key,
                'assinatura': signature,
            }
            refund = RestituicaoVenda.objects.create(**values)
            PagamentoRestituicao.objects.bulk_create([
                PagamentoRestituicao(
                    restituicao=refund,
                    pagamento_original=payment,
                    valor=value,
                )
                for payment, value in allocations
            ])
    except IntegrityError as error:
        existing = RestituicaoVenda.objects.filter(chave_idempotencia=key).first()
        if existing and existing.assinatura == signature:
            return RestituicaoResult(existing, created=False)
        raise CaixaOperationError(
            'Não foi possível registrar a restituição. Atualize e tente novamente.',
        ) from error
    except OperationalError as error:
        if _is_recoverable_sqlite_lock(error):
            raise CaixaOperationError(
                'O caixa está ocupado. Atualize e tente novamente usando a mesma chave.',
            ) from error
        raise

    session.versao = expected_version + 1
    if isinstance(sessao, SessaoCaixa):
        sessao.versao = session.versao
    return RestituicaoResult(refund, created=True)


def _movement_signature(
    *, operation: str, actor_id: int, session_id: int,
    amount: Decimal, reason: str,
) -> str:
    payload = [operation, actor_id, session_id, str(amount), reason]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
    ).hexdigest()


def _movement_replay(
    event: SuprimentoCaixa | SangriaCaixa, signature: str,
) -> MovimentoCaixaResult:
    if event.assinatura != signature:
        raise CaixaOperationError('Essa chave de operação já foi usada com outros dados.')
    return MovimentoCaixaResult(evento=event, created=False)


def _register_cash_movement(
    *, model: type[SuprimentoCaixa] | type[SangriaCaixa],
    operation: str, actor, sessao: SessaoCaixa, valor: object,
    motivo: str, idempotency_key: str | None,
    versao_esperada: int | None = None,
) -> MovimentoCaixaResult:
    _require_manager(actor)
    amount = _money(valor, 'um valor positivo')
    if amount <= 0:
        raise CaixaOperationError('O valor do movimento deve ser positivo.')
    reason = _required_text(motivo, 'um motivo', 500)
    key = _required_text(idempotency_key, 'uma chave de operação', 64)
    if not isinstance(sessao, SessaoCaixa) or not sessao.pk:
        raise CaixaOperationError('Selecione uma sessão de caixa válida.')

    signature = _movement_signature(
        operation=operation,
        actor_id=actor.pk,
        session_id=sessao.pk,
        amount=amount,
        reason=reason,
    )
    expected_version = sessao.versao if versao_esperada is None else versao_esperada

    try:
        with transaction.atomic():
            existing = model.objects.filter(chave_idempotencia=key).first()
            if existing:
                return _movement_replay(existing, signature)

            claimed = SessaoCaixa.objects.filter(
                pk=sessao.pk,
                aberta=True,
                versao=expected_version,
            ).update(versao=F('versao') + 1)
            if claimed != 1:
                raise CaixaOperationError(
                    'O caixa foi alterado ou está fechado. Atualize e tente novamente.',
                )

            if model is SangriaCaixa and amount > calcular_saldo_dinheiro(sessao.pk):
                raise CaixaOperationError(
                    'A sangria não pode superar o saldo físico esperado do caixa.',
                )

            event = model.objects.create(
                sessao_id=sessao.pk,
                valor=amount,
                responsavel=actor,
                motivo=reason,
                chave_idempotencia=key,
                assinatura=signature,
            )
    except IntegrityError as error:
        existing = model.objects.filter(chave_idempotencia=key).first()
        if existing:
            return _movement_replay(existing, signature)
        raise CaixaOperationError(
            'Não foi possível registrar o movimento. Atualize o caixa e tente novamente.',
        ) from error
    except OperationalError as error:
        if _is_recoverable_sqlite_lock(error):
            raise CaixaOperationError(
                'O caixa está ocupado. Atualize e tente novamente usando a mesma chave.',
            ) from error
        raise

    sessao.versao = expected_version + 1
    return MovimentoCaixaResult(evento=event, created=True)


def registrar_suprimento(
    *, actor, sessao: SessaoCaixa, valor: object, motivo: str,
    idempotency_key: str | None, versao_esperada: int | None = None,
) -> MovimentoCaixaResult:
    """Registrar entrada adicional de dinheiro com gerente, motivo e replay seguro."""
    return _register_cash_movement(
        model=SuprimentoCaixa,
        operation='SUPRIMENTO',
        actor=actor,
        sessao=sessao,
        valor=valor,
        motivo=motivo,
        idempotency_key=idempotency_key,
        versao_esperada=versao_esperada,
    )


def registrar_sangria(
    *, actor, sessao: SessaoCaixa, valor: object, motivo: str,
    idempotency_key: str | None, versao_esperada: int | None = None,
) -> MovimentoCaixaResult:
    """Registrar retirada física sem permitir que o caixa fique com saldo negativo."""
    return _register_cash_movement(
        model=SangriaCaixa,
        operation='SANGRIA',
        actor=actor,
        sessao=sessao,
        valor=valor,
        motivo=motivo,
        idempotency_key=idempotency_key,
        versao_esperada=versao_esperada,
    )


def _sale_payment_summary(
    payments: list[Mapping[str, object]] | None,
    legacy_payment_method: str | None,
) -> str:
    allowed_forms = dict(PagamentoVenda.FORMA_PAGAMENTO_CHOICES)
    if payments is None:
        method = (legacy_payment_method or '').strip().upper()
        if method and method not in allowed_forms:
            raise CaixaOperationError('Selecione uma forma de pagamento válida.')
        return method or 'SEM_PAGAMENTO'

    forms: set[str] = set()
    for line in payments:
        if not isinstance(line, Mapping):
            raise CaixaOperationError('Cada parcela deve informar uma forma de pagamento.')
        form = str(line.get('forma') or '').strip().upper()
        if form not in allowed_forms:
            raise CaixaOperationError('Selecione uma forma de pagamento válida.')
        forms.add(form)
    if len(forms) == 1:
        return next(iter(forms))
    return 'MISTO' if forms else 'SEM_PAGAMENTO'


def _resolve_finalization_session(
    *, sessao: SessaoCaixa | int | str | None, idempotency_key: str,
) -> SessaoCaixa:
    if isinstance(sessao, SessaoCaixa):
        return sessao
    if sessao is not None:
        try:
            session_id = int(sessao)
            return SessaoCaixa.objects.get(pk=session_id)
        except (TypeError, ValueError, SessaoCaixa.DoesNotExist) as error:
            raise CaixaOperationError('Selecione uma sessão de caixa válida.') from error

    existing = RecebimentoVenda.objects.select_related('sessao').filter(
        chave_idempotencia=idempotency_key,
    ).first()
    if existing:
        return existing.sessao
    session = SessaoCaixa.objects.filter(aberta=True).first()
    if session is None:
        raise CaixaOperationError('Abra uma sessão de caixa antes de confirmar a venda.')
    return session


def finalizar_venda(
    *, actor, product_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str], discount: object = '0',
    pagamentos: Iterable[Mapping[str, object]] | None = None,
    payment_method: str | None = None,
    motivo: str, idempotency_key: str | None,
    sessao: SessaoCaixa | int | str | None = None,
    versao_esperada: int | None = None,
) -> FinalizacaoResult:
    """Coordenar venda, baixa e recebimento no mesmo commit."""
    key = _required_text(idempotency_key, 'uma chave de operação', 64)
    reason = _required_text(motivo, 'um motivo', 500)
    payment_lines = None if pagamentos is None else list(pagamentos)
    summary = _sale_payment_summary(payment_lines, payment_method)
    session = _resolve_finalization_session(sessao=sessao, idempotency_key=key)

    # Imports locais mantêm as dependências entre módulos em uma única direção:
    # Caixa orquestra serviços de Vendas e Vendas continua chamando Estoque.
    from estoque.services import StockOperationError
    from vendas.services import (
        SaleOperationError,
        _caixa_finalization_context,
        registrar_venda,
    )

    try:
        with transaction.atomic():
            with _caixa_finalization_context():
                sale_result = registrar_venda(
                    actor=actor,
                    product_ids=product_ids,
                    quantities=quantities,
                    discount=discount,
                    payment_method=summary,
                    idempotency_key=key,
                )
            receipt_payments = payment_lines
            if receipt_payments is None:
                if sale_result.sale.total > 0 and payment_method:
                    method = payment_method.strip().upper()
                    receipt_payments = [{
                        'forma': method,
                        'valor_aplicado': sale_result.sale.total,
                        'valor_entregue': sale_result.sale.total,
                    }]
                else:
                    receipt_payments = []
            receipt_result = registrar_recebimento(
                actor=actor,
                sessao=session,
                versao_esperada=versao_esperada,
                venda=sale_result.sale,
                pagamentos=receipt_payments,
                motivo=reason,
                idempotency_key=key,
            )
    except (SaleOperationError, StockOperationError) as error:
        raise CaixaOperationError(str(error)) from error
    except OperationalError as error:
        if _is_recoverable_sqlite_lock(error):
            raise CaixaOperationError(
                'O caixa está ocupado. Atualize e tente novamente usando a mesma chave.',
            ) from error
        raise

    return FinalizacaoResult(
        sale=sale_result.sale,
        recebimento=receipt_result.recebimento,
        created=sale_result.created or receipt_result.created,
    )
