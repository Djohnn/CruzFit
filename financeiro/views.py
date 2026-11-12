from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .models import (
    ContaFinanceira,
    ContaPagar,
    FechamentoMensal,
    LancamentoFinanceiro,
    PagamentoContaPagar,
)
from .queries import (
    historico_conta,
    listar_contas,
    pix_pendentes,
    pix_trocas_pendentes,
    recebiveis_cartao_pendentes,
    receita_operacional_consolidada,
    saldo_consolidado,
)
from .services import (
    FinanceOperationError,
    calcular_saldo,
    confirmar_fechamento_mensal,
    confirmar_pix,
    criar_conta_financeira,
    criar_conta_pagar,
    criar_conta_pagar_de_compra,
    estornar_lancamento,
    estornar_pagamento_conta_pagar,
    liquidar_cartao,
    listar_contas_pagar,
    listar_pedidos_elegiveis_conta_pagar,
    pagar_conta_pagar,
    periodo_mensal,
    previsualizar_fechamento,
    reabrir_fechamento_mensal,
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


def _payable_or_404(pk: int) -> ContaPagar:
    try:
        return ContaPagar.objects.select_related('origem_pedido').get(pk=pk)
    except ContaPagar.DoesNotExist as error:
        raise Http404('Conta a pagar não encontrada.') from error


def _payable_payment_or_404(pk: int) -> PagamentoContaPagar:
    try:
        return PagamentoContaPagar.objects.select_related(
            'conta_pagar', 'conta', 'lancamento',
        ).get(pk=pk)
    except PagamentoContaPagar.DoesNotExist as error:
        raise Http404('Pagamento de conta a pagar não encontrado.') from error


def _base_context(request: HttpRequest) -> dict[str, object]:
    return {
        'contas': ContaFinanceira.objects.filter(ativo=True).order_by('nome', 'pk'),
        'pix_pendentes': pix_pendentes(),
        'pix_trocas_pendentes': pix_trocas_pendentes(),
        'recebiveis_cartao': recebiveis_cartao_pendentes(),
        'today': date.today().isoformat(),
        'chave_idempotencia': request.POST.get('chave_idempotencia') or uuid.uuid4().hex,
    }


@gerente_required
@require_http_methods(['GET'])
def painel(request: HttpRequest) -> HttpResponse:
    payables = listar_contas_pagar(as_of=date.today())
    return render(request, 'financeiro/painel.html', {
        'contas_resumo': listar_contas(),
        'saldo_consolidado': saldo_consolidado(),
        'receita_operacional': receita_operacional_consolidada(),
        'pix_pendentes': pix_pendentes(),
        'pix_trocas_pendentes': pix_trocas_pendentes(),
        'recebiveis_cartao': recebiveis_cartao_pendentes(),
        'contas_pagar_abertas': payables['abertas'],
        'contas_pagar_vencidas': payables['vencidas'],
        'contas_pagar_pagas': payables['pagas'],
        'pedidos_contas_pagar': listar_pedidos_elegiveis_conta_pagar(),
    })


@gerente_required
@require_http_methods(['GET', 'POST'])
def fechamento_mensal(request: HttpRequest) -> HttpResponse:
    """Prévia e confirmação do fechamento mensal para gerentes."""
    requested_month = request.POST.get('mes') or request.GET.get('mes')
    if not requested_month:
        requested_month = date.today().replace(day=1).isoformat()[:7]
    requested_margin = request.POST.get('margem') or request.GET.get('margem') or '0.00'
    try:
        preview = previsualizar_fechamento(
            month=requested_month,
            margin=requested_margin,
        )
        if request.method == 'POST':
            result = confirmar_fechamento_mensal(
                actor=request.user,
                month=requested_month,
                margin=requested_margin,
                preview_signature=request.POST.get('assinatura_previa', ''),
                reason=request.POST.get('motivo', ''),
                idempotency_key=request.POST.get('chave_idempotencia', ''),
            )
            messages.success(
                request,
                'Fechamento mensal confirmado.' if result.created
                else 'Esse fechamento mensal já estava confirmado.',
            )
            return redirect(
                f"{reverse('financeiro:fechamento_mensal')}?mes={requested_month}&margem={requested_margin}",
            )
    except FinanceOperationError as error:
        messages.error(request, str(error))
        return render(request, 'financeiro/fechamento_form.html', {
            'preview': None,
            'month_value': requested_month,
            'margin_value': requested_margin,
            'history': FechamentoMensal.objects.filter(
                mes_referencia=periodo_mensal(requested_month)[0],
            ).order_by('-versao'),
            'chave_idempotencia': request.POST.get('chave_idempotencia', uuid.uuid4().hex),
        }, status=400)

    month_date = periodo_mensal(requested_month)[0]
    return render(request, 'financeiro/fechamento_form.html', {
        'preview': preview,
        'month_value': month_date.strftime('%Y-%m'),
        'margin_value': preview.margem,
        'saldo_disponivel': sum(
            (account.saldo_final for account in preview.contas),
            Decimal('0.00'),
        ),
        'history': FechamentoMensal.objects.filter(
            mes_referencia=month_date,
        ).prefetch_related('contas__conta').order_by('-versao'),
        'chave_idempotencia': request.POST.get('chave_idempotencia', uuid.uuid4().hex),
    })


@gerente_required
@require_http_methods(['POST'])
def fechamento_reabrir(request: HttpRequest, pk: int) -> HttpResponse:
    fechamento = get_object_or_404(FechamentoMensal, pk=pk)
    try:
        result = reabrir_fechamento_mensal(
            actor=request.user,
            fechamento=fechamento,
            reason=request.POST.get('motivo', ''),
            idempotency_key=request.POST.get('chave_idempotencia', ''),
        )
    except FinanceOperationError as error:
        messages.error(request, str(error))
        return redirect(
            f"{reverse('financeiro:fechamento_mensal')}?mes={fechamento.mes_referencia:%Y-%m}",
        )
    messages.success(
        request,
        'Fechamento reaberto.' if result.created else 'Essa reabertura já estava registrada.',
    )
    return redirect(
        f"{reverse('financeiro:fechamento_mensal')}?mes={fechamento.mes_referencia:%Y-%m}",
    )


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
        'receita_operacional': conta.receita_operacional(),
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


@gerente_required
@require_http_methods(['GET', 'POST'])
def pix_confirmacao(request: HttpRequest) -> HttpResponse:
    context = {
        **_base_context(request),
        'titulo': 'Confirmar Pix',
        'pix_id': request.POST.get('pix_id', request.GET.get('pix_id', '')),
        'conta_id': request.POST.get('conta_id', request.GET.get('conta_id', '')),
        'data_confirmacao': request.POST.get(
            'data_confirmacao', date.today().isoformat(),
        ),
        'motivo': request.POST.get('motivo', ''),
    }
    if request.method == 'POST':
        try:
            result = confirmar_pix(
                actor=request.user,
                pagamento=context['pix_id'],
                account=context['conta_id'],
                confirmation_date=context['data_confirmacao'],
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/pix_form.html', context, status=400)
        messages.success(
            request,
            'Pix confirmado.' if result.created else 'Esse Pix já estava confirmado.',
        )
        return redirect('financeiro:conta_detalhe', pk=result.recebimento.conta_id)
    return render(request, 'financeiro/pix_form.html', context)


def _parse_card_installments(raw_value: str) -> list[dict[str, str]]:
    installments: list[dict[str, str]] = []
    for line in raw_value.replace(',', '\n').splitlines():
        value = line.strip()
        if not value:
            continue
        parts = value.replace(';', ':').split(':', 1)
        if len(parts) != 2:
            raise FinanceOperationError(
                'Informe cada parcela no formato ID:taxa.',
            )
        installments.append({
            'recebivel_id': parts[0].strip(),
            'taxas': parts[1].strip(),
        })
    if not installments:
        raise FinanceOperationError('Selecione pelo menos uma parcela de cartão.')
    return installments


@gerente_required
@require_http_methods(['GET', 'POST'])
def cartao_liquidacao(request: HttpRequest) -> HttpResponse:
    context = {
        **_base_context(request),
        'titulo': 'Liquidar cartão',
        'parcelas': request.POST.get('parcelas', ''),
        'conta_id': request.POST.get('conta_id', request.GET.get('conta_id', '')),
        'data_deposito': request.POST.get('data_deposito', date.today().isoformat()),
        'referencia_operadora': request.POST.get('referencia_operadora', ''),
        'motivo': request.POST.get('motivo', ''),
    }
    if request.method == 'POST':
        try:
            installments = _parse_card_installments(context['parcelas'])
            result = liquidar_cartao(
                actor=request.user,
                account=context['conta_id'],
                deposit_date=context['data_deposito'],
                operator_reference=context['referencia_operadora'],
                installments=installments,
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/cartao_form.html', context, status=400)
        messages.success(
            request,
            'Liquidação registrada.' if result.created
            else 'Essa liquidação já estava registrada.',
        )
        return redirect('financeiro:conta_detalhe', pk=result.deposito.conta_id)
    return render(request, 'financeiro/cartao_form.html', context)


@gerente_required
@require_http_methods(['GET', 'POST'])
def conta_pagar_nova(request: HttpRequest) -> HttpResponse:
    context = {
        **_base_context(request),
        'titulo': 'Nova conta a pagar',
        'acao': 'conta_pagar_nova',
        'descricao': request.POST.get('descricao', ''),
        'categoria': request.POST.get('categoria', ''),
        'valor': request.POST.get('valor', ''),
        'data_vencimento': request.POST.get(
            'data_vencimento', date.today().isoformat(),
        ),
        'motivo': request.POST.get('motivo', ''),
    }
    if request.method == 'POST':
        try:
            result = criar_conta_pagar(
                actor=request.user,
                description=context['descricao'],
                category=context['categoria'],
                amount=context['valor'],
                due_date=context['data_vencimento'],
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/conta_pagar_form.html', context, status=400)
        messages.success(
            request,
            'Conta a pagar registrada.' if result.created
            else 'A conta a pagar já estava registrada.',
        )
        return redirect('financeiro:painel')
    return render(request, 'financeiro/conta_pagar_form.html', context)


@gerente_required
@require_http_methods(['GET', 'POST'])
def conta_pagar_compra(request: HttpRequest) -> HttpResponse:
    context = {
        **_base_context(request),
        'titulo': 'Gerar conta de compra recebida',
        'acao': 'conta_pagar_compra',
        'pedidos': listar_pedidos_elegiveis_conta_pagar(),
        'pedido_id': request.POST.get('pedido_id', request.GET.get('pedido_id', '')),
        'valor': request.POST.get('valor', ''),
        'data_vencimento': request.POST.get(
            'data_vencimento', date.today().isoformat(),
        ),
        'motivo': request.POST.get('motivo', ''),
    }
    if request.method == 'POST':
        try:
            result = criar_conta_pagar_de_compra(
                actor=request.user,
                pedido=context['pedido_id'],
                amount=context['valor'],
                due_date=context['data_vencimento'],
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/conta_pagar_form.html', context, status=400)
        messages.success(
            request,
            'Conta da compra registrada.' if result.created
            else 'A conta da compra já estava registrada.',
        )
        return redirect('financeiro:painel')
    return render(request, 'financeiro/conta_pagar_form.html', context)


@gerente_required
@require_http_methods(['GET', 'POST'])
def conta_pagar_pagamento(request: HttpRequest, pk: int) -> HttpResponse:
    payable = _payable_or_404(pk)
    context = {
        **_base_context(request),
        'titulo': 'Pagar conta a pagar',
        'acao': 'conta_pagar_pagamento',
        'conta_pagar': payable,
        'conta_id': request.POST.get('conta_id', ''),
        'valor': request.POST.get('valor', str(payable.valor)),
        'data_pagamento': request.POST.get(
            'data_pagamento', date.today().isoformat(),
        ),
        'motivo': request.POST.get('motivo', ''),
    }
    if request.method == 'POST':
        try:
            result = pagar_conta_pagar(
                actor=request.user,
                conta_pagar=payable,
                account=context['conta_id'],
                payment_date=context['data_pagamento'],
                amount=context['valor'],
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/conta_pagar_form.html', context, status=400)
        messages.success(
            request,
            'Pagamento registrado.' if result.created
            else 'Esse pagamento já estava registrado.',
        )
        return redirect('financeiro:painel')
    return render(request, 'financeiro/conta_pagar_form.html', context)


@gerente_required
@require_http_methods(['GET', 'POST'])
def conta_pagar_estorno(request: HttpRequest, pk: int) -> HttpResponse:
    payment = _payable_payment_or_404(pk)
    context = {
        **_base_context(request),
        'titulo': 'Estornar pagamento de conta',
        'acao': 'conta_pagar_estorno',
        'pagamento': payment,
        'data_movimento': request.POST.get(
            'data_movimento', date.today().isoformat(),
        ),
        'motivo': request.POST.get('motivo', ''),
    }
    if request.method == 'POST':
        try:
            result = estornar_pagamento_conta_pagar(
                actor=request.user,
                pagamento=payment,
                movement_date=context['data_movimento'],
                reason=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except FinanceOperationError as error:
            messages.error(request, str(error))
            return render(request, 'financeiro/conta_pagar_form.html', context, status=400)
        messages.success(
            request,
            'Estorno do pagamento registrado.' if result.created
            else 'Esse estorno já estava registrado.',
        )
        return redirect('financeiro:painel')
    return render(request, 'financeiro/conta_pagar_form.html', context)
