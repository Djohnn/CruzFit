import json
import uuid
from itertools import zip_longest

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import (
    HttpResponseNotFound,
)
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from caixa.models import (
    PendenciaCancelamentoVenda,
    PendenciaDevolucaoVenda,
    RecebimentoVenda,
    RestituicaoVenda,
    SessaoCaixa,
)
from caixa.services import CaixaOperationError, previsualizar_recebimento
from caixa.services import finalizar_venda as finalizar_venda_service
from estoque.models import (
    Produto,
)
from estoque.services import (
    ReversalOperationError,
    StockOperationError,
)
from estoque.views import _selected_reversal_lines, gerente_required

from .models import Venda
from .services import SaleOperationError, previsualizar_venda
from .services import cancelar_venda as cancelar_venda_service
from .services import devolver_venda as devolver_venda_service


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def registrar_venda(request):
    session = SessaoCaixa.objects.filter(aberta=True).first()
    preview = None
    payment_preview = None
    payments = []
    previewed = False
    review_signature = ''
    if request.method == 'POST':
        try:
            action = request.POST.get('acao', '')
            raw_payments = request.POST.get('pagamentos_json')
            if raw_payments:
                try:
                    payments = json.loads(raw_payments)
                except json.JSONDecodeError as error:
                    raise CaixaOperationError('Revise as parcelas de pagamento informadas.') from error
                if not isinstance(payments, list):
                    raise CaixaOperationError('Informe as parcelas de pagamento da venda.')
            else:
                forms = request.POST.getlist('forma_parcela')
                applied_values = request.POST.getlist('valor_aplicado')
                tendered_values = request.POST.getlist('valor_entregue')
                if forms or applied_values or tendered_values:
                    payments = [
                        {
                            'forma': form,
                            'valor_aplicado': applied,
                            'valor_entregue': tendered,
                        }
                        for form, applied, tendered in zip_longest(
                            forms, applied_values, tendered_values, fillvalue='',
                        )
                        if any((form.strip(), applied.strip(), tendered.strip()))
                    ]
                else:
                    payments = None
            if payments is not None:
                payments = [
                    {
                        'forma': str(line.get('forma', '')),
                        'valor_aplicado': str(line.get('valor_aplicado', '')),
                        'valor_entregue': str(line.get('valor_entregue', '')),
                    }
                    for line in payments
                    if isinstance(line, dict) and any(
                        str(line.get(field, '')).strip()
                        for field in ('forma', 'valor_aplicado', 'valor_entregue')
                    )
                ]
            if action in {'revisar', 'confirmar'}:
                if not session:
                    raise CaixaOperationError('Abra uma sessão de caixa antes de registrar a venda.')
                preview = previsualizar_venda(
                    actor=request.user,
                    product_ids=request.POST.getlist('produto'),
                    quantities=request.POST.getlist('quantidade'),
                    discount=request.POST.get('desconto') or '0',
                )
                payments = payments if payments is not None else []
                payment_preview = previsualizar_recebimento(
                    payments=payments, total=preview.total,
                )
                previewed = True
                review_payload = {
                    'actor': request.user.pk,
                    'session': session.pk,
                    'session_version': session.versao,
                    'lines': [
                        [line.product_id, str(line.quantity), str(line.unit_price), str(line.total)]
                        for line in preview.lines
                    ],
                    'discount': str(preview.discount),
                    'total': str(preview.total),
                    'payments': [
                        [
                            line['forma'], str(line['valor_aplicado']),
                            str(line['valor_entregue']), str(line['troco']),
                        ]
                        for line in payment_preview.pagamentos
                    ],
                }
                if action == 'revisar':
                    review_signature = signing.dumps(
                        review_payload, salt='caixa.checkout-review',
                    )
                elif request.POST.get('revisao_confirmada') != '1':
                    raise CaixaOperationError('Revise os valores antes de confirmar a venda.')
                elif (
                    request.POST.get('total_confirmado', '').strip().replace(',', '.')
                    != format(preview.total, '.2f')
                ):
                    raise CaixaOperationError('O total da venda mudou. Revise os valores novamente.')
                else:
                    try:
                        signed_payload = signing.loads(
                            request.POST.get('assinatura_revisao', ''),
                            salt='caixa.checkout-review',
                        )
                    except signing.BadSignature as error:
                        raise CaixaOperationError(
                            'A revisão não é válida. Confira os valores novamente.',
                        ) from error
                    if signed_payload != review_payload:
                        raise CaixaOperationError(
                            'Os dados da venda mudaram após a revisão. Revise novamente.',
                        )
            raw_version = request.POST.get('sessao_versao')
            try:
                expected_version = int(raw_version) if raw_version else None
                if expected_version is not None and expected_version < 0:
                    raise ValueError
            except ValueError as error:
                raise CaixaOperationError('Atualize a sessão de caixa antes de confirmar.') from error

            if action not in {'revisar', 'confirmar'}:
                if not request.POST.get('forma_pagamento'):
                    raise CaixaOperationError(
                        'Revise a venda e o recebimento antes de confirmar.',
                    )
                result = finalizar_venda_service(
                    actor=request.user,
                    product_ids=request.POST.getlist('produto'),
                    quantities=request.POST.getlist('quantidade'),
                    discount=request.POST.get('desconto') or '0',
                    payment_method=request.POST.get('forma_pagamento') or '',
                    pagamentos=payments,
                    sessao=request.POST.get('sessao_id') or None,
                    versao_esperada=expected_version,
                    motivo='Venda registrada no Caixa',
                    idempotency_key=request.POST.get('chave_idempotencia'),
                )
            elif action == 'confirmar':
                result = finalizar_venda_service(
                    actor=request.user,
                    product_ids=request.POST.getlist('produto'),
                    quantities=request.POST.getlist('quantidade'),
                    discount=request.POST.get('desconto') or '0',
                    pagamentos=payments,
                    sessao=session.pk,
                    versao_esperada=expected_version,
                    motivo='Venda registrada no Caixa',
                    idempotency_key=request.POST.get('chave_idempotencia'),
                )
            else:
                result = None
        except (CaixaOperationError, SaleOperationError, StockOperationError) as error:
            messages.error(request, str(error))
        else:
            if result is not None:
                messages.success(
                    request,
                    'Venda registrada com sucesso.'
                    if result.created
                    else 'Essa venda já foi registrada.',
                )
                return redirect('vendas:venda_detalhe', pk=result.sale.pk)

    posted = request.POST if request.method == 'POST' else request.GET
    linhas = [
        {'produto': produto, 'quantidade': quantidade}
        for produto, quantidade in zip_longest(
            request.POST.getlist('produto'),
            request.POST.getlist('quantidade'),
            fillvalue='',
        )
    ] if request.method == 'POST' else [{'produto': '', 'quantidade': '1'}]
    produtos = list(Produto.objects.filter(ativo=True).order_by('nome', 'cor', 'tamanho', 'pk'))
    catalogo = [
        {
            'id': produto.pk,
            'nome': produto.nome,
            'cor': produto.cor,
            'tamanho': produto.get_tamanho_display() if produto.tamanho else '',
            'label': produto.variacao_label,
            'codigo_barras': produto.codigo_barras or '',
            'preco': str(produto.preco_venda),
            'estoque': str(produto.quantidade),
        }
        for produto in produtos
    ]
    if request.method == 'POST':
        forms = request.POST.getlist('forma_parcela')
        applied_values = request.POST.getlist('valor_aplicado')
        tendered_values = request.POST.getlist('valor_entregue')
        if not forms and request.POST.get('pagamentos_json'):
            try:
                payments = json.loads(request.POST['pagamentos_json'])
            except (json.JSONDecodeError, TypeError):
                payments = []
        elif forms:
            payments = [
                {'forma': form, 'valor_aplicado': applied, 'valor_entregue': tendered}
                for form, applied, tendered in zip_longest(
                    forms, applied_values, tendered_values, fillvalue='',
                )
            ]
        else:
            payments = []
    else:
        payments = [{'forma': '', 'valor_aplicado': '', 'valor_entregue': ''}]

    return render(
        request,
        'registrar_venda.html',
        {
            'produtos': produtos,
            'catalogo': catalogo,
            'linhas': linhas or [{'produto': '', 'quantidade': '1'}],
            'desconto': posted.get('desconto', '0'),
            'forma_pagamento': posted.get('forma_pagamento', ''),
            'formas_pagamento': Venda.MEIOS_PAGAMENTO_CHOICES,
            'chave_idempotencia': posted.get('chave_idempotencia') or uuid.uuid4().hex,
            'sessao_caixa': session,
            'preview': preview,
            'payment_preview': payment_preview,
            'pagamentos': payments or [{'forma': '', 'valor_aplicado': '', 'valor_entregue': ''}],
            'previewed': previewed and request.POST.get('acao') == 'revisar',
            'assinatura_revisao': review_signature,
        },
    )


@login_required(login_url='login')
@require_GET
def vendas(request):
    vendas_qs = Venda.objects.select_related('responsavel', 'baixa').prefetch_related(
        'itens__produto',
    )
    historico = request.GET.get('periodo') == 'historico'
    hoje = timezone.localdate()
    if historico:
        vendas_qs = vendas_qs[:100]
    else:
        vendas_qs = vendas_qs.filter(
            criada_em__date=hoje,
            recebimento_caixa__isnull=False,
        )
    return render(request, 'vendas.html', {
        'vendas': vendas_qs, 'historico': historico, 'hoje': hoje,
    })


@login_required(login_url='login')
@require_GET
def venda_detalhe(request, pk):
    try:
        venda = Venda.objects.select_related(
            'responsavel', 'baixa'
        ).prefetch_related('itens__produto').get(pk=pk)
    except Venda.DoesNotExist:
        return HttpResponseNotFound('Venda não encontrada.')
    recebimento = RecebimentoVenda.objects.filter(venda=venda).prefetch_related(
        'pagamentos',
    ).first()
    cancelamento_pendente = PendenciaCancelamentoVenda.objects.filter(
        cancelamento__venda=venda,
    ).select_related('sessao_origem').first()
    cancelamento_restituicao = (
        RestituicaoVenda.objects.filter(
            pendencia_cancelamento=cancelamento_pendente,
        ).select_related('sessao', 'responsavel').first()
        if cancelamento_pendente else None
    )
    devolucoes_pendentes = []
    for pending in PendenciaDevolucaoVenda.objects.filter(
        devolucao__venda=venda,
    ).select_related('sessao_origem'):
        devolucoes_pendentes.append({
            'pendencia': pending,
            'restituicao': RestituicaoVenda.objects.filter(
                pendencia_devolucao=pending,
            ).select_related('sessao', 'responsavel').first(),
            'url_name': 'caixa:restituir_devolucao',
        })
    return render(
        request, 'venda_detalhe.html', {
            'venda': venda,
            'recebimento': recebimento,
            'is_manager': bool(
                request.user.is_active and (
                    request.user.is_superuser or getattr(request.user, 'cargo', '') == 'G'
                )
            ),
            'cancelamento_pendente': cancelamento_pendente,
            'cancelamento_restituicao': cancelamento_restituicao,
            'devolucoes_pendentes': devolucoes_pendentes,
        },
    )


@login_required(login_url='login')
@require_GET
def venda_comprovante(request, pk):
    try:
        venda = Venda.objects.select_related(
            'responsavel', 'baixa'
        ).prefetch_related('itens__produto').get(pk=pk)
    except Venda.DoesNotExist:
        return HttpResponseNotFound('Venda não encontrada.')
    recebimento = RecebimentoVenda.objects.filter(venda=venda).prefetch_related(
        'pagamentos',
    ).first()
    return render(
        request,
        'venda_comprovante.html',
        {'venda': venda, 'recebimento': recebimento},
    )


@gerente_required
@require_http_methods(['GET', 'POST'])
def cancelar_venda(request, pk):
    try:
        sale = Venda.objects.select_related('baixa').get(pk=pk)
    except Venda.DoesNotExist:
        return HttpResponseNotFound('Venda não encontrada.')

    if request.method == 'POST':
        try:
            result = cancelar_venda_service(
                actor=request.user,
                sale_id=sale.pk,
                reason=request.POST.get('motivo') or '',
                idempotency_key=request.POST.get('chave_idempotencia'),
            )
        except ReversalOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                'Venda cancelada.' if result.created else 'Esse cancelamento já foi registrado.',
            )
            return redirect('vendas:venda_detalhe', pk=sale.pk)
    return render(
        request,
        'cancelar_venda.html',
        {'venda': sale, 'chave_idempotencia': uuid.uuid4().hex},
    )


@gerente_required
@require_http_methods(['GET', 'POST'])
def devolver_venda(request, pk):
    try:
        sale = Venda.objects.prefetch_related('itens__produto').get(pk=pk)
    except Venda.DoesNotExist:
        return HttpResponseNotFound('Venda não encontrada.')

    if request.method == 'POST':
        item_ids, quantities = _selected_reversal_lines(request, 'item_venda')
        try:
            result = devolver_venda_service(
                actor=request.user,
                sale_id=sale.pk,
                item_ids=item_ids,
                quantities=quantities,
                reason=request.POST.get('motivo') or '',
                idempotency_key=request.POST.get('chave_idempotencia'),
            )
        except ReversalOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                'Devolução registrada.' if result.created else 'Essa devolução já foi registrada.',
            )
            return redirect('vendas:venda_detalhe', pk=sale.pk)
    return render(
        request,
        'devolver_venda.html',
        {'venda': sale, 'chave_idempotencia': uuid.uuid4().hex},
    )
