from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from estoque.models import Fornecedor

from .forms import (
    PurchaseActionForm,
    PurchaseDraftForm,
    PurchaseItemFormSet,
    PurchaseReceiptForm,
    PurchaseReceiptItemFormSet,
)
from .models import PedidoCompra
from .queries import listar_pedidos, obter_pedido, saldos_pedido
from .services import (
    PurchaseOperationError,
    cancelar_pedido,
    confirmar_pedido,
    criar_rascunho,
    editar_rascunho,
    receber_pedido,
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
                'Apenas o gerente ativo pode operar Compras.',
            )
        return view(request, *args, **kwargs)

    return wrapped


def _purchase_or_404(identifier: int | str) -> PedidoCompra:
    try:
        return obter_pedido(identifier)
    except PedidoCompra.DoesNotExist as error:
        raise Http404('Pedido de compra não encontrado.') from error


def _summary(pedido: PedidoCompra) -> dict[str, object]:
    rows = saldos_pedido(pedido)
    def as_decimal(value: object) -> Decimal:
        return value if isinstance(value, Decimal) else Decimal(str(value))

    return {
        'pedida': sum((as_decimal(row['quantidade_pedida']) for row in rows), Decimal('0')),
        'recebida': sum((as_decimal(row['quantidade_recebida']) for row in rows), Decimal('0')),
        'cancelada': sum((as_decimal(row['quantidade_cancelada']) for row in rows), Decimal('0')),
        'recebivel': sum((as_decimal(row['quantidade_recebivel']) for row in rows), Decimal('0')),
        'pendente': sum((as_decimal(row['quantidade_pendente']) for row in rows), Decimal('0')),
    }


def _line_data(formset):
    return [
        {
            'produto_id': int(getattr(form.cleaned_data['produto_id'], 'pk')),
            'quantidade': form.cleaned_data['quantidade'],
            'custo_unitario': form.cleaned_data['custo_unitario'],
        }
        for form in formset
        if form.cleaned_data and not form.cleaned_data.get('DELETE')
        and form.cleaned_data.get('produto_id')
    ]


def _receipt_line_data(formset):
    return [
        {
            'item_id': form.cleaned_data['item_id'],
            'quantidade': form.cleaned_data['quantidade'],
        }
        for form in formset
        if form.cleaned_data and form.cleaned_data.get('quantidade')
    ]


def _draft_initial(pedido: PedidoCompra | None = None):
    if pedido is None:
        return {
            'chave_idempotencia': uuid.uuid4().hex,
        }, []
    return {
        'fornecedor_id': pedido.fornecedor_id,
        'observacao': pedido.observacao,
        'chave_idempotencia': uuid.uuid4().hex,
        'versao': pedido.versao,
    }, [
        {
            'produto_id': item.produto_id,
            'quantidade': item.quantidade,
            'custo_unitario': item.custo_unitario,
        }
        for item in pedido.itens.all()
    ]


def _render_draft(request, *, form, item_formset, pedido=None):
    return render(
        request,
        'compras/formulario.html',
        {
            'form': form,
            'item_formset': item_formset,
            'pedido': pedido,
            'editing': pedido is not None,
        },
    )


@login_required(login_url='login')
@require_GET
def lista(request: HttpRequest) -> HttpResponse:
    fornecedor_raw = request.GET.get('fornecedor', '')
    estado = request.GET.get('estado', '')
    data_inicio_raw = request.GET.get('data_inicio', '')
    data_fim_raw = request.GET.get('data_fim', '')
    try:
        fornecedor_id = int(fornecedor_raw) if fornecedor_raw else None
    except ValueError:
        fornecedor_id = None
    try:
        data_inicio = date.fromisoformat(data_inicio_raw) if data_inicio_raw else None
    except ValueError:
        data_inicio = None
    try:
        data_fim = date.fromisoformat(data_fim_raw) if data_fim_raw else None
    except ValueError:
        data_fim = None

    queryset = listar_pedidos(
        fornecedor_id=fornecedor_id,
        estado=estado,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )
    page = Paginator(queryset, 20).get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)
    pedidos = [
        {'pedido': pedido, 'resumo': _summary(pedido)}
        for pedido in page.object_list
    ]
    return render(
        request,
        'compras/lista.html',
        {
            'pedidos': pedidos,
            'pagina': page,
            'fornecedores': Fornecedor.objects.filter(ativo=True).order_by('nome'),
            'estados': PedidoCompra.Estado.choices,
            'filtros': {
                'fornecedor': fornecedor_raw,
                'estado': estado,
                'data_inicio': data_inicio_raw,
                'data_fim': data_fim_raw,
            },
            'querystring': query_params.urlencode(),
        },
    )


@gerente_required
def novo(request: HttpRequest) -> HttpResponse:
    if request.method == 'POST':
        form = PurchaseDraftForm(request.POST)
        item_formset = PurchaseItemFormSet(request.POST, prefix='itens')
    else:
        initial, item_initial = _draft_initial()
        form = PurchaseDraftForm(initial=initial)
        item_formset = PurchaseItemFormSet(
            initial=item_initial,
            prefix='itens',
        )

    if request.method == 'POST' and form.is_valid() and item_formset.is_valid():
        try:
            result = criar_rascunho(
                actor=request.user,
                fornecedor_id=form.cleaned_data['fornecedor_id'].pk,
                lines=_line_data(item_formset),
                observation=form.cleaned_data['observacao'],
                reason=form.cleaned_data['motivo'],
                idempotency_key=form.cleaned_data['chave_idempotencia'],
            )
        except PurchaseOperationError as error:
            form.add_error(None, str(error))
        else:
            messages.success(
                request,
                'Rascunho criado.' if result.created else 'Rascunho já estava criado.',
            )
            return redirect('compras:detalhe', pk=result.pedido.pk)

    return _render_draft(request, form=form, item_formset=item_formset)


@gerente_required
def editar(request: HttpRequest, pk: int) -> HttpResponse:
    pedido = _purchase_or_404(pk)
    if pedido.estado != PedidoCompra.Estado.RASCUNHO:
        return HttpResponseForbidden('Somente rascunhos podem ser alterados.')
    if request.method == 'POST':
        form = PurchaseDraftForm(request.POST)
        item_formset = PurchaseItemFormSet(request.POST, prefix='itens')
    else:
        initial, item_initial = _draft_initial(pedido)
        form = PurchaseDraftForm(initial=initial)
        item_formset = PurchaseItemFormSet(
            initial=item_initial,
            prefix='itens',
        )

    if request.method == 'POST' and form.is_valid() and item_formset.is_valid():
        try:
            result = editar_rascunho(
                actor=request.user,
                pedido_id=pedido.pk,
                fornecedor_id=form.cleaned_data['fornecedor_id'].pk,
                lines=_line_data(item_formset),
                observation=form.cleaned_data['observacao'],
                reason=form.cleaned_data['motivo'],
                idempotency_key=form.cleaned_data['chave_idempotencia'],
                expected_version=form.cleaned_data.get('versao'),
            )
        except PurchaseOperationError as error:
            form.add_error(None, str(error))
        else:
            messages.success(
                request,
                'Rascunho atualizado.' if result.created else 'Rascunho já estava atualizado.',
            )
            return redirect('compras:detalhe', pk=result.pedido.pk)

    return _render_draft(
        request,
        form=form,
        item_formset=item_formset,
        pedido=pedido,
    )


@login_required(login_url='login')
@require_GET
def detalhe(request: HttpRequest, pk: int) -> HttpResponse:
    pedido = _purchase_or_404(pk)
    rows = saldos_pedido(pedido)
    recebimentos = pedido.recebimentos.select_related(
        'entrada', 'responsavel',
    ).prefetch_related('itens__item_pedido')
    eventos = pedido.eventos.select_related('responsavel').all()
    return render(
        request,
        'compras/detalhe.html',
        {
            'pedido': pedido,
            'saldos': rows,
            'resumo': _summary(pedido),
            'recebimentos': recebimentos,
            'eventos': eventos,
            'pode_operar': request.user.is_superuser or getattr(request.user, 'cargo', '') == 'G',
            'chave_confirmacao': uuid.uuid4().hex,
            'chave_cancelamento': uuid.uuid4().hex,
        },
    )


@gerente_required
@require_POST
def confirmar(request: HttpRequest, pk: int) -> HttpResponse:
    pedido = _purchase_or_404(pk)
    form = PurchaseActionForm(request.POST)
    if form.is_valid():
        try:
            result = confirmar_pedido(
                actor=request.user,
                pedido_id=pedido.pk,
                reason=form.cleaned_data['motivo'],
                idempotency_key=form.cleaned_data['chave_idempotencia'],
                expected_version=form.cleaned_data.get('versao'),
            )
        except PurchaseOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                'Pedido confirmado.' if result.created else 'Confirmação já registrada.',
            )
            return redirect('compras:detalhe', pk=pedido.pk)
    else:
        messages.error(request, 'Informe um motivo para confirmar o pedido.')
    return redirect('compras:detalhe', pk=pedido.pk)


def _receipt_context(request, pedido, form, item_formset):
    saldo_rows = [
        row for row in saldos_pedido(pedido)
        if row['quantidade_recebivel']
    ]
    return {
        'pedido': pedido,
        'saldos': saldo_rows,
        'receipt_rows': list(zip(saldo_rows, item_formset.forms)),
        'form': form,
        'item_formset': item_formset,
    }


@gerente_required
def receber(request: HttpRequest, pk: int) -> HttpResponse:
    pedido = _purchase_or_404(pk)
    initial_rows = [
        {'item_id': int(getattr(row['item'], 'pk'))}
        for row in saldos_pedido(pedido)
        if row['quantidade_recebivel']
    ]
    if not initial_rows:
        return HttpResponseForbidden('O pedido não possui saldo recebível.')
    if request.method == 'POST':
        form = PurchaseReceiptForm(request.POST)
        item_formset = PurchaseReceiptItemFormSet(request.POST, prefix='itens')
    else:
        form = PurchaseReceiptForm(initial={
            'chave_idempotencia': uuid.uuid4().hex,
            'versao': pedido.versao,
        })
        item_formset = PurchaseReceiptItemFormSet(
            initial=initial_rows,
            prefix='itens',
        )

    if request.method == 'POST' and form.is_valid() and item_formset.is_valid():
        try:
            result = receber_pedido(
                actor=request.user,
                pedido_id=pedido.pk,
                lines=_receipt_line_data(item_formset),
                referencia_documental=form.cleaned_data['referencia_documental'],
                reason=form.cleaned_data['motivo'],
                idempotency_key=form.cleaned_data['chave_idempotencia'],
                expected_version=form.cleaned_data.get('versao'),
            )
        except PurchaseOperationError as error:
            form.add_error(None, str(error))
        else:
            messages.success(
                request,
                'Recebimento registrado.' if result.created else 'Recebimento já estava registrado.',
            )
            return redirect('compras:detalhe', pk=pedido.pk)

    return render(
        request,
        'compras/recebimento.html',
        _receipt_context(request, pedido, form, item_formset),
    )


@gerente_required
@require_POST
def cancelar(request: HttpRequest, pk: int) -> HttpResponse:
    pedido = _purchase_or_404(pk)
    form = PurchaseActionForm(request.POST)
    if form.is_valid():
        try:
            result = cancelar_pedido(
                actor=request.user,
                pedido_id=pedido.pk,
                reason=form.cleaned_data['motivo'],
                idempotency_key=form.cleaned_data['chave_idempotencia'],
                expected_version=form.cleaned_data.get('versao'),
            )
        except PurchaseOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                'Pedido cancelado.' if result.created else 'Cancelamento já registrado.',
            )
            return redirect('compras:detalhe', pk=pedido.pk)
    else:
        messages.error(request, 'Informe um motivo para cancelar o pedido.')
    return redirect('compras:detalhe', pk=pedido.pk)
