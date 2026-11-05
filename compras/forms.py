from __future__ import annotations

from decimal import Decimal

from django import forms
from django.forms import formset_factory

from estoque.models import Fornecedor, Produto


class PurchaseDraftForm(forms.Form):
    fornecedor_id = forms.ModelChoiceField(
        label='Fornecedor',
        queryset=Fornecedor.objects.none(),
        empty_label='Selecione um fornecedor',
    )
    observacao = forms.CharField(
        label='Observação',
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    motivo = forms.CharField(
        label='Motivo da operação',
        max_length=500,
        error_messages={'required': 'Informe um motivo.'},
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'autocomplete': 'off',
        }),
    )
    chave_idempotencia = forms.CharField(widget=forms.HiddenInput())
    versao = forms.IntegerField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fornecedor_id'].queryset = Fornecedor.objects.filter(
            ativo=True,
        ).order_by('nome')
        self.fields['fornecedor_id'].widget.attrs.update({
            'class': 'form-control',
            'data-testid': 'purchase-supplier',
        })
        self.fields['observacao'].widget.attrs.update({
            'class': 'form-control',
            'data-testid': 'purchase-observation',
        })
        self.fields['motivo'].widget.attrs.update({
            'data-testid': 'purchase-reason',
        })


class PurchaseItemForm(forms.Form):
    produto_id = forms.ModelChoiceField(
        label='Produto',
        queryset=Produto.objects.none(),
        empty_label='Selecione um produto',
    )
    quantidade = forms.DecimalField(
        label='Quantidade pedida',
        min_value=Decimal('0.01'),
        max_digits=12,
        decimal_places=2,
    )
    custo_unitario = forms.DecimalField(
        label='Custo unitário',
        min_value=Decimal('0.01'),
        max_digits=10,
        decimal_places=2,
    )
    DELETE = forms.BooleanField(required=False, label='Remover item')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['produto_id'].queryset = Produto.objects.filter(
            ativo=True,
        ).order_by('nome')
        self.fields['produto_id'].widget.attrs.update({'data-testid': 'purchase-product'})
        self.fields['quantidade'].widget.attrs.update({'data-testid': 'purchase-quantity'})
        self.fields['custo_unitario'].widget.attrs.update({'data-testid': 'purchase-cost'})
        for field in ('produto_id', 'quantidade', 'custo_unitario'):
            self.fields[field].widget.attrs['class'] = 'form-control'


PurchaseItemFormSet = formset_factory(
    PurchaseItemForm,
    extra=1,
    can_delete=True,
    max_num=100,
    validate_max=True,
)


class PurchaseReceiptForm(forms.Form):
    referencia_documental = forms.CharField(
        label='Referência documental',
        required=False,
        max_length=160,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'data-testid': 'receipt-reference',
        }),
    )
    motivo = forms.CharField(
        label='Motivo do recebimento',
        max_length=500,
        error_messages={'required': 'Informe um motivo.'},
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'data-testid': 'receipt-reason',
        }),
    )
    chave_idempotencia = forms.CharField(widget=forms.HiddenInput())
    versao = forms.IntegerField(required=False, widget=forms.HiddenInput())


class PurchaseReceiptItemForm(forms.Form):
    item_id = forms.IntegerField(widget=forms.HiddenInput())
    quantidade = forms.DecimalField(
        label='Quantidade a receber',
        required=False,
        min_value=Decimal('0.01'),
        max_digits=12,
        decimal_places=2,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['quantidade'].widget.attrs['class'] = 'form-control'


PurchaseReceiptItemFormSet = formset_factory(
    PurchaseReceiptItemForm,
    extra=0,
    max_num=100,
    validate_max=True,
)


class PurchaseActionForm(forms.Form):
    motivo = forms.CharField(
        label='Motivo da operação',
        max_length=500,
        error_messages={'required': 'Informe um motivo.'},
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'data-testid': 'purchase-action-reason',
        }),
    )
    chave_idempotencia = forms.CharField(widget=forms.HiddenInput())
    versao = forms.IntegerField(required=False, widget=forms.HiddenInput())
