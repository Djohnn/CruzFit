import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import (
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
)
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .models import (
    FechamentoCaixa,
    PendenciaCancelamentoVenda,
    PendenciaDevolucaoVenda,
    RecebimentoVenda,
    RestituicaoVenda,
    SangriaCaixa,
    SessaoCaixa,
    SuprimentoCaixa,
)
from .queries import CaixaQueryError, historico_sessao
from .services import (
    CaixaOperationError,
    abrir_sessao,
    calcular_saldo_dinheiro,
    fechar_sessao,
    previsualizar_fechamento,
    previsualizar_restituicao,
    registrar_restituicao,
    registrar_sangria,
    registrar_suprimento,
)


def _caixa_operator(user) -> bool:
    return bool(
        user.is_active and (
            user.is_superuser or getattr(user, 'cargo', '') in {'G', 'V', 'C'}
        )
    )


@login_required(login_url='login')
def painel(request):
    if not _caixa_operator(request.user):
        return HttpResponseForbidden('Usuário sem permissão para acessar o Caixa.')
    sessao = SessaoCaixa.objects.filter(aberta=True).first()
    fechamento_preview = previsualizar_fechamento(sessao) if sessao else None
    recebimentos = RecebimentoVenda.objects.filter(sessao=sessao).select_related(
        'venda', 'responsavel',
    ).prefetch_related('pagamentos')[:20] if sessao else []
    return render(request, 'caixa/painel.html', {
        'sessao': sessao,
        'is_manager': bool(
            request.user.is_superuser or getattr(request.user, 'cargo', '') == 'G'
        ),
        'recebimentos': recebimentos,
        'saldo_dinheiro': calcular_saldo_dinheiro(sessao) if sessao else None,
        'pendencias_pendentes': (
            fechamento_preview.pendencias_pendentes if fechamento_preview else 0
        ),
        'suprimentos_count': SuprimentoCaixa.objects.filter(sessao=sessao).count() if sessao else 0,
        'sangrias_count': SangriaCaixa.objects.filter(sessao=sessao).count() if sessao else 0,
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def abrir(request):
    if request.method == 'POST':
        try:
            result = abrir_sessao(
                actor=request.user,
                fundo_inicial=request.POST.get('fundo_inicial'),
                motivo=request.POST.get('motivo', ''),
                idempotency_key=request.POST.get('chave_idempotencia'),
            )
        except CaixaOperationError as error:
            if not request.user.is_active or not (
                request.user.is_superuser or getattr(request.user, 'cargo', '') == 'G'
            ):
                return HttpResponseForbidden(str(error))
            messages.error(request, str(error))
            return render(request, 'caixa/abrir.html', {
                'fundo_inicial': request.POST.get('fundo_inicial', ''),
                'motivo': request.POST.get('motivo', ''),
                'chave_idempotencia': request.POST.get('chave_idempotencia', ''),
            }, status=400)
        messages.success(
            request,
            'Sessão de caixa aberta.' if result.created else 'A sessão já estava registrada.',
        )
        return redirect('caixa:painel')
    return render(request, 'caixa/abrir.html', {
        'chave_idempotencia': uuid.uuid4().hex,
    })


def _cash_movement_form(request, *, kind, operation, heading, success_message):
    if not request.user.is_active or not (
        request.user.is_superuser or getattr(request.user, 'cargo', '') == 'G'
    ):
        return HttpResponseForbidden('Apenas gerente ativo pode registrar este movimento.')

    sessao = SessaoCaixa.objects.filter(aberta=True).first()
    context = {
        'sessao': sessao,
        'kind': kind,
        'heading': heading,
        'saldo_dinheiro': calcular_saldo_dinheiro(sessao) if sessao else None,
        'valor': '',
        'motivo': '',
        'chave_idempotencia': uuid.uuid4().hex,
    }
    if request.method == 'POST':
        context.update({
            'valor': request.POST.get('valor', ''),
            'motivo': request.POST.get('motivo', ''),
            'chave_idempotencia': request.POST.get('chave_idempotencia', ''),
        })
        if sessao is None:
            messages.error(request, 'Abra uma sessão de caixa antes de registrar o movimento.')
            return render(request, 'caixa/movimento.html', context, status=400)
        try:
            operation(
                actor=request.user,
                sessao=sessao,
                valor=context['valor'],
                motivo=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
            )
        except CaixaOperationError as error:
            messages.error(request, str(error))
            return render(request, 'caixa/movimento.html', context, status=400)
        messages.success(request, success_message)
        return redirect('caixa:painel')
    return render(request, 'caixa/movimento.html', context)


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def suprimento(request):
    return _cash_movement_form(
        request,
        kind='suprimento',
        operation=registrar_suprimento,
        heading='Registrar suprimento',
        success_message='Suprimento registrado na sessão de caixa.',
    )


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def sangria(request):
    return _cash_movement_form(
        request,
        kind='sangria',
        operation=registrar_sangria,
        heading='Registrar sangria',
        success_message='Sangria registrada na sessão de caixa.',
    )


@login_required(login_url='login')
@require_http_methods(['GET'])
def historico(request):
    if not _caixa_operator(request.user):
        return HttpResponseForbidden('Usuário sem permissão para acessar o Caixa.')
    sessions = SessaoCaixa.objects.order_by('-aberta_em', '-pk')
    session_id = request.GET.get('sessao', '').strip()
    if session_id:
        if not session_id.isdecimal():
            return HttpResponseBadRequest('Selecione uma sessão de caixa válida.')
        sessao = SessaoCaixa.objects.filter(pk=int(session_id)).first()
        if sessao is None:
            return HttpResponseBadRequest('Selecione uma sessão de caixa válida.')
    else:
        sessao = SessaoCaixa.objects.filter(aberta=True).first()

    try:
        page_obj, filters = historico_sessao(sessao, request.GET)
    except CaixaQueryError as error:
        messages.error(request, str(error))
        page_obj, filters = historico_sessao(None, {})
    return render(request, 'caixa/historico.html', {
        'sessao': sessao,
        'sessoes': sessions,
        'page_obj': page_obj,
        'movimentos': page_obj.object_list,
        'filtros': filters,
        'query_string': filters.as_query_string(),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def fechar(request):
    if not request.user.is_active or not (
        request.user.is_superuser or getattr(request.user, 'cargo', '') == 'G'
    ):
        return HttpResponseForbidden('Apenas gerente ativo pode fechar a sessão de caixa.')
    session = SessaoCaixa.objects.filter(aberta=True).first()
    context = {
        'sessao': session,
        'dinheiro_contado': '',
        'justificativa': '',
        'chave_idempotencia': uuid.uuid4().hex,
        'sessao_versao': session.versao if session else '',
        'resumo': previsualizar_fechamento(session) if session else None,
    }
    if request.method == 'POST':
        context.update({
            'dinheiro_contado': request.POST.get('dinheiro_contado', ''),
            'justificativa': request.POST.get('justificativa', ''),
            'chave_idempotencia': request.POST.get('chave_idempotencia', ''),
            'sessao_versao': request.POST.get('sessao_versao', ''),
        })
        if session is None:
            messages.error(request, 'Não existe sessão aberta para fechar.')
            return render(request, 'caixa/fechar.html', context, status=400)
        try:
            expected_version = int(context['sessao_versao'])
            if expected_version < 0:
                raise ValueError
            result = fechar_sessao(
                actor=request.user,
                sessao=session,
                versao_esperada=expected_version,
                dinheiro_contado=context['dinheiro_contado'],
                justificativa=context['justificativa'],
                idempotency_key=context['chave_idempotencia'],
            )
        except ValueError as error:
            message = (
                str(error) if isinstance(error, CaixaOperationError)
                else 'Atualize a sessão de caixa antes de fechar.'
            )
            messages.error(request, message)
            return render(request, 'caixa/fechar.html', context, status=400)
        messages.success(request, 'Fechamento de caixa registrado.')
        return redirect('caixa:fechamento_detalhe', pk=result.fechamento.pk)
    return render(request, 'caixa/fechar.html', context)


@login_required(login_url='login')
@require_http_methods(['GET'])
def fechamento_detalhe(request, pk):
    if not _caixa_operator(request.user):
        return HttpResponseForbidden('Usuário sem permissão para acessar o Caixa.')
    try:
        fechamento = FechamentoCaixa.objects.select_related(
            'sessao', 'responsavel',
        ).get(pk=pk)
    except FechamentoCaixa.DoesNotExist:
        return HttpResponseNotFound('Fechamento não encontrado.')
    return render(request, 'caixa/fechamento_detalhe.html', {
        'fechamento': fechamento,
        'totais_por_forma': fechamento.totais_por_forma.items(),
    })


def _restituicao_form(
    request, *, pending_id, model, source_field, is_cancelamento,
):
    if not request.user.is_active or not (
        request.user.is_superuser or getattr(request.user, 'cargo', '') == 'G'
    ):
        return HttpResponseForbidden('Apenas gerente ativo pode registrar restituições.')

    try:
        pending = model.objects.select_related('recebimento').get(pk=pending_id)
    except model.DoesNotExist:
        return HttpResponseNotFound('Pendência de restituição não encontrada.')

    refund = RestituicaoVenda.objects.filter(**{source_field: pending}).select_related(
        'sessao', 'responsavel',
    ).first()
    session = SessaoCaixa.objects.filter(aberta=True).first()
    payments = []
    preview_error = ''
    if refund:
        payments = [{
            'forma_display': payment.pagamento_original.get_forma_display(),
            'valor': payment.valor,
        } for payment in refund.pagamentos.select_related('pagamento_original')]
    else:
        try:
            payments = list(previsualizar_restituicao(**{source_field: pending}))
        except CaixaOperationError as error:
            preview_error = str(error)

    context = {
        'pendencia': pending,
        'cancelamento': is_cancelamento,
        'sessao': session,
        'saldo_dinheiro': calcular_saldo_dinheiro(session) if session else None,
        'pagamentos': payments,
        'ja_restituida': refund,
        'preview_error': preview_error,
        'motivo': request.POST.get('motivo', '') if request.method == 'POST' else '',
        'chave_idempotencia': (
            request.POST.get('chave_idempotencia', '')
            if request.method == 'POST'
            else uuid.uuid4().hex
        ),
        'confirmacao_manual': request.POST.get('confirmacao_manual') == '1',
        'sessao_versao': session.versao if session else '',
    }
    if request.method == 'POST':
        context['sessao_versao'] = request.POST.get('sessao_versao', '')
        if refund:
            messages.error(request, 'Essa pendência já foi restituída.')
            return render(request, 'caixa/restituicao.html', context, status=400)
        if session is None:
            messages.error(request, 'Abra uma sessão de caixa para registrar a restituição.')
            return render(request, 'caixa/restituicao.html', context, status=400)
        raw_version = request.POST.get('sessao_versao', '')
        try:
            expected_version = int(raw_version)
            if expected_version < 0:
                raise ValueError
            result = registrar_restituicao(
                actor=request.user,
                sessao=session,
                versao_esperada=expected_version,
                motivo=context['motivo'],
                idempotency_key=context['chave_idempotencia'],
                confirmacao_manual=context['confirmacao_manual'],
                **{source_field: pending},
            )
        except ValueError as error:
            message = (
                str(error) if isinstance(error, CaixaOperationError)
                else 'Atualize a sessão de caixa antes de confirmar.'
            )
            messages.error(request, message)
            return render(request, 'caixa/restituicao.html', context, status=400)
        messages.success(
            request,
            'Restituição registrada.' if result.created else 'Essa restituição já estava registrada.',
        )
        return redirect('vendas:venda_detalhe', pk=pending.recebimento.venda_id)
    return render(request, 'caixa/restituicao.html', context)


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def restituir_cancelamento(request, pending_id):
    return _restituicao_form(
        request,
        pending_id=pending_id,
        model=PendenciaCancelamentoVenda,
        source_field='pendencia_cancelamento',
        is_cancelamento=True,
    )


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def restituir_devolucao(request, pending_id):
    return _restituicao_form(
        request,
        pending_id=pending_id,
        model=PendenciaDevolucaoVenda,
        source_field='pendencia_devolucao',
        is_cancelamento=False,
    )
