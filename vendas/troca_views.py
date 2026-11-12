from __future__ import annotations

import uuid
from itertools import zip_longest

from django.contrib import messages
from django.http import HttpResponseNotFound
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from caixa.models import PagamentoVenda, RecebimentoVenda, SessaoCaixa
from estoque.models import Produto
from estoque.views import gerente_required

from .models import Venda
from .trocas import (
    LEGACY_SALE_MESSAGE,
    TrocaOperationError,
    confirmar_troca,
    previsualizar_troca,
)


def _sale_context(pk: int):
    try:
        return Venda.objects.prefetch_related('itens__produto').get(pk=pk)
    except Venda.DoesNotExist:
        return None


def _posted_payment_lines(request) -> list[dict[str, str]]:
    forms = request.POST.getlist('forma_parcela')
    applied = request.POST.getlist('valor_aplicado')
    tendered = request.POST.getlist('valor_entregue')
    return [
        {
            'forma': str(form),
            'valor_aplicado': str(amount),
            'valor_entregue': str(received),
        }
        for form, amount, received in zip_longest(forms, applied, tendered, fillvalue='')
        if any(str(value).strip() for value in (form, amount, received))
    ]


def _posted_exchange_lines(request):
    source_ids = request.POST.getlist('item_venda')
    source_quantities = [
        request.POST.get(f'quantidade_origem_{item_id}', '')
        for item_id in source_ids
    ]
    destination_pairs = [
        (product_id, quantity)
        for product_id, quantity in zip_longest(
            request.POST.getlist('produto_substituto'),
            request.POST.getlist('quantidade_substituto'),
            fillvalue='',
        )
        if str(product_id).strip() or str(quantity).strip()
    ]
    destination_ids = [product_id for product_id, _ in destination_pairs]
    destination_quantities = [quantity for _, quantity in destination_pairs]
    return source_ids, source_quantities, destination_ids, destination_quantities


def _catalog_payload():
    return [
        {
            'id': product.pk,
            'nome': product.variacao_label,
            'codigo': product.codigo_barras or '',
            'preco': str(product.preco_venda),
            'estoque': str(product.quantidade),
        }
        for product in Produto.objects.filter(ativo=True).order_by('nome', 'pk')
    ]


def _context(
    *, sale, session, catalog, source_ids, source_quantities,
    destination_ids, destination_quantities, payments, reason, key,
    manual, preview=None, review_signature='', error_message='',
):
    source_quantity_map = dict(zip(source_ids, source_quantities))
    source_rows = [
        {
            'item': item,
            'selected': str(item.pk) in source_ids,
            'quantity': source_quantity_map.get(str(item.pk), ''),
        }
        for item in sale.itens.all()
    ]
    destination_rows = [
        {
            'product_id': product_id,
            'quantity': quantity,
        }
        for product_id, quantity in zip_longest(
            destination_ids, destination_quantities, fillvalue='',
        )
        if product_id or quantity
    ] or [{'product_id': '', 'quantity': ''}]
    payment_rows = payments or [{'forma': '', 'valor_aplicado': '', 'valor_entregue': ''}]
    return {
        'venda': sale,
        'sessao': session,
        'source_rows': source_rows,
        'destination_rows': destination_rows,
        'payment_rows': payment_rows,
        'catalog': catalog,
        'motivo': reason,
        'chave_idempotencia': key,
        'confirmacao_manual': manual,
        'payment_forms': PagamentoVenda.FORMAS_CHECKOUT,
        'preview': preview,
        'preview_difference_display': (
            abs(preview.difference) if preview is not None else None
        ),
        'review_signature': review_signature,
        'legacy_blocked': not RecebimentoVenda.objects.filter(venda=sale).exists(),
        'error_message': error_message,
    }


@gerente_required
@require_http_methods(['GET', 'POST'])
def trocar_venda(request, pk):
    sale = _sale_context(pk)
    if sale is None:
        return HttpResponseNotFound('Venda não encontrada.')
    session = SessaoCaixa.objects.filter(aberta=True).first()
    catalog = _catalog_payload()
    source_ids: list[str] = []
    source_quantities: list[str] = []
    destination_ids: list[str] = []
    destination_quantities: list[str] = []
    payments: list[dict[str, str]] = []
    reason = ''
    key = uuid.uuid4().hex
    manual = False
    preview = None
    review_signature = ''
    error_message = ''

    if request.method == 'POST':
        source_ids, source_quantities, destination_ids, destination_quantities = (
            _posted_exchange_lines(request)
        )
        payments = _posted_payment_lines(request)
        reason = request.POST.get('motivo', '')
        key = request.POST.get('chave_idempotencia') or key
        manual = request.POST.get('confirmacao_manual') == 'on'
        review_signature = request.POST.get('revisao_assinatura', '')
        action = request.POST.get('acao', 'revisar')
        try:
            if session is None:
                raise TrocaOperationError(
                    'Abra uma sessão de caixa antes de registrar a troca.',
                )
            if action == 'revisar':
                preview = previsualizar_troca(
                    actor=request.user,
                    sessao=session,
                    sale_id=sale.pk,
                    source_item_ids=source_ids,
                    source_quantities=source_quantities,
                    destination_product_ids=destination_ids,
                    destination_quantities=destination_quantities,
                    motivo=reason,
                    pagamentos=payments,
                    confirmacao_manual=manual,
                )
                review_signature = preview.review_signature
            elif action == 'confirmar':
                result = confirmar_troca(
                    actor=request.user,
                    sessao=session,
                    sale_id=sale.pk,
                    source_item_ids=source_ids,
                    source_quantities=source_quantities,
                    destination_product_ids=destination_ids,
                    destination_quantities=destination_quantities,
                    motivo=reason,
                    idempotency_key=key,
                    revisao_assinatura=review_signature,
                    pagamentos=payments,
                    confirmacao_manual=manual,
                )
                messages.success(
                    request,
                    'Troca registrada.' if result.created else 'Essa troca já foi registrada.',
                )
                return redirect('vendas:venda_comprovante', pk=sale.pk)
            else:
                raise TrocaOperationError('Ação de troca inválida.')
        except TrocaOperationError as error:
            error_message = str(error)
            messages.error(request, error_message)
            if error_message == LEGACY_SALE_MESSAGE:
                review_signature = ''

    context = _context(
        sale=sale,
        session=session,
        catalog=catalog,
        source_ids=source_ids,
        source_quantities=source_quantities,
        destination_ids=destination_ids,
        destination_quantities=destination_quantities,
        payments=payments,
        reason=reason,
        key=key,
        manual=manual,
        preview=preview,
        review_signature=review_signature,
        error_message=error_message,
    )
    return render(request, 'trocar_venda.html', context)
