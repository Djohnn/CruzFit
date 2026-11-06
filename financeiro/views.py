from __future__ import annotations

import uuid
from datetime import date
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .models import ContaFinanceira, LancamentoFinanceiro
from .queries import historico_conta, listar_contas, saldo_consolidado
from .services import (
    FinanceOperationError,
    calcular_saldo,
    criar_conta_financeira,
    estornar_lancamento,
    registrar_aporte,
    transferir_entre_contas,
)


def gerente_required(view):
    @login_required(login_url='login')
    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs):
        user = request.user
        if not (
            user.is_active
            and (user.is_superuser or getattr(user, 'cargo', '') == 'G')
        ):
            return HttpResponseForbidden(
                'Apenas o gerente ativo pode operar o Financeiro.',
            )
        return view(request, *args, **kwargs)

    return wrapped


def _account_or_404(pk: int) -> ContaFinanceira:
    try:
        return ContaFinanceira.objects.get(pk=pk)
    except ContaFinanceira.DoesNotExist as error:
        raise Http404('Conta financeira não encontrada.') from error


def _ledger_or_404(pk: int) -> LancamentoFinanceiro:
    try:
        return LancamentoFinanceiro.objects.select_related('conta').get(pk=pk)
    except LancamentoFinanceiro.DoesNotExist as error:
        raise Http404('Lançamento financeiro não encontrado.') from error


def _base_context(request: HttpRequest) -> dict[str, object]:
    return {
        'contas': ContaFinanceira.objects.filter(ativo=True).order_by('nome', 'pk'),
        'today': date.today().isoformat(),
        'chave_idempotencia': request.POST.get('chave_idempotencia') or uuid.uuid4().hex,
    }


@gerente_required
@require_http_methods(['GET'])
def painel(request: HttpRequest) -> HttpResponse:
    return render(request, 'financeiro/painel.html', {
        'contas_resumo': listar_contas(),
        'saldo_consolidado': saldo_consolidado(),
        'receita_operacional': '0.00',
    })


@gerente_required
@require_http_methods(['GET', 'POST'])
def conta_nova(request: HttpRequest) -> HttpResponse:
    context = {
        'nome': request.POST.get('nome', ''),
        'saldo_inicial': request.POST.get('saldo_inicial', ''),
        'data_corte_financeiro': request.POST.get(
            'data_corte_financeiro', date.today().isoformat(),
        ),
        'motivo': request.POST.get('motivo', ''),
        'chave_idempotencia': request.POST.get('chave_idempotencia', uuid.uuid4().hex),
    }
    if request.method == 'POST':
        try:
            result = criar_conta_financeira(
                actor=request.user,
                name=context['nome'],
                initial_balance=context['saldo_inicial'],
                cutover_date=context['data_corte_financeiro'],
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/conta_form.html', context, status=400)
        messages.success(
            request,
            'Conta financeira criada.' if result.created else 'A conta já estava cadastrada.',
        )
        return redirect('financeiro:conta_detalhe', pk=result.conta.pk)
    return render(request, 'financeiro/conta_form.html', context)


@gerente_required
@require_http_methods(['GET'])
def conta_detalhe(request: HttpRequest, pk: int) -> HttpResponse:
    conta = _account_or_404(pk)
    return render(request, 'financeiro/detalhe.html', {
        **_base_context(request),
        'conta': conta,
        'saldo': calcular_saldo(conta),
        'historico': historico_conta(conta),
    })


@gerente_required
@require_http_methods(['GET', 'POST'])
def aporte(request: HttpRequest) -> HttpResponse:
    context = {
        **_base_context(request),
        'titulo': 'Registrar aporte',
        'acao': 'aporte',
        'conta_id': request.POST.get('conta_id', request.GET.get('conta_id', '')),
        'valor': request.POST.get('valor', ''),
        'data_movimento': request.POST.get('data_movimento', date.today().isoformat()),
        'motivo': request.POST.get('motivo', ''),
    }
    if request.method == 'POST':
        try:
            result = registrar_aporte(
                actor=request.user,
                account=context['conta_id'],
                amount=context['valor'],
                movement_date=context['data_movimento'],
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/movimento_form.html', context, status=400)
        messages.success(
            request,
            'Aporte registrado.' if result.created else 'Esse aporte já estava registrado.',
        )
        return redirect('financeiro:conta_detalhe', pk=result.aporte.conta_id)
    return render(request, 'financeiro/movimento_form.html', context)


@gerente_required
@require_http_methods(['GET', 'POST'])
def transferencia(request: HttpRequest) -> HttpResponse:
    context = {
        **_base_context(request),
        'titulo': 'Transferir entre contas',
        'acao': 'transferencia',
        'origem_id': request.POST.get('origem_id', ''),
        'destino_id': request.POST.get('destino_id', ''),
        'valor': request.POST.get('valor', ''),
        'data_movimento': request.POST.get('data_movimento', date.today().isoformat()),
        'motivo': request.POST.get('motivo', ''),
    }
    if request.method == 'POST':
        try:
            result = transferir_entre_contas(
                actor=request.user,
                source=context['origem_id'],
                target=context['destino_id'],
                amount=context['valor'],
                movement_date=context['data_movimento'],
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/movimento_form.html', context, status=400)
        messages.success(
            request,
            'Transferência registrada.' if result.created
            else 'Essa transferência já estava registrada.',
        )
        return redirect('financeiro:conta_detalhe', pk=result.transferencia.origem_id)
    return render(request, 'financeiro/movimento_form.html', context)


@gerente_required
@require_http_methods(['GET', 'POST'])
def estorno(request: HttpRequest, pk: int) -> HttpResponse:
    lancamento = _ledger_or_404(pk)
    context = {
        **_base_context(request),
        'titulo': 'Estornar lançamento',
        'acao': 'estorno',
        'lancamento': lancamento,
        'data_movimento': request.POST.get('data_movimento', date.today().isoformat()),
        'motivo': request.POST.get('motivo', ''),
    }
    if request.method == 'POST':
        try:
            result = estornar_lancamento(
                actor=request.user,
                lancamento=lancamento,
                movement_date=context['data_movimento'],
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/movimento_form.html', context, status=400)
        messages.success(
            request,
            'Estorno registrado.' if result.created else 'Esse estorno já estava registrado.',
        )
        return redirect('financeiro:conta_detalhe', pk=lancamento.conta_id)
    return render(request, 'financeiro/movimento_form.html', context)
