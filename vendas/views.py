import uuid
from itertools import zip_longest

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import (
    HttpResponseNotFound,
)
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from estoque.models import (
    Produto,
)
from estoque.services import (
    ReversalOperationError,
    StockOperationError,
)
from estoque.views import _selected_reversal_lines, gerente_required

from .models import Venda
from .services import SaleOperationError
from .services import cancelar_venda as cancelar_venda_service
from .services import devolver_venda as devolver_venda_service
from .services import registrar_venda as registrar_venda_service


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def registrar_venda(request):
    if request.method == 'POST':
        try:
            result = registrar_venda_service(
                actor=request.user,
                product_ids=request.POST.getlist('produto'),
                quantities=request.POST.getlist('quantidade'),
                discount=request.POST.get('desconto') or '0',
                payment_method=request.POST.get('forma_pagamento') or '',
                idempotency_key=request.POST.get('chave_idempotencia'),
            )
        except (SaleOperationError, StockOperationError) as error:
            messages.error(request, str(error))
        else:
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
    produtos = list(Produto.objects.filter(ativo=True).order_by('nome', 'pk'))
    catalogo = [
        {
            'id': produto.pk,
            'nome': produto.nome,
            'codigo_barras': produto.codigo_barras or '',
            'preco': str(produto.preco_venda),
            'estoque': str(produto.quantidade),
        }
        for produto in produtos
    ]
    return render(
        request,
        'registrar_venda.html',
        {
            'produtos': produtos,
            'catalogo': catalogo,
            'linhas': linhas or [{'produto': '', 'quantidade': '1'}],
            'desconto': posted.get('desconto', '0'),
            'forma_pagamento': posted.get('forma_pagamento', ''),
            'formas_pagamento': Venda.FORMA_PAGAMENTO_CHOICES,
            'chave_idempotencia': posted.get('chave_idempotencia') or uuid.uuid4().hex,
        },
    )


@login_required(login_url='login')
@require_GET
def vendas(request):
    vendas_qs = Venda.objects.select_related('responsavel', 'baixa').prefetch_related(
        'itens__produto',
    )
    return render(request, 'vendas.html', {'vendas': vendas_qs[:100]})


@login_required(login_url='login')
@require_GET
def venda_detalhe(request, pk):
    try:
        venda = Venda.objects.select_related(
            'responsavel', 'baixa'
        ).prefetch_related('itens__produto').get(pk=pk)
    except Venda.DoesNotExist:
        return HttpResponseNotFound('Venda não encontrada.')
    return render(request, 'venda_detalhe.html', {'venda': venda})


@login_required(login_url='login')
@require_GET
def venda_comprovante(request, pk):
    try:
        venda = Venda.objects.select_related(
            'responsavel', 'baixa'
        ).prefetch_related('itens__produto').get(pk=pk)
    except Venda.DoesNotExist:
        return HttpResponseNotFound('Venda não encontrada.')
    return render(request, 'venda_comprovante.html', {'venda': venda})


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
