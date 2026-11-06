from django import forms
from django.forms import formset_factory

from .barcodes import validate_ean13
from .models import ModeloEtiqueta, Produto


class ProdutoForm(forms.ModelForm):
    codigo_barras = forms.CharField(max_length=17, required=False)

    class Meta:
        model = Produto
        fields = (
            "nome",
            "categoria",
            "tamanho",
            "modelo",
            "cor",
            "tecido",
            "genero",
            "quantidade",
            "preco_compra",
            "preco_venda",
            "estoque_minimo",
            "codigo_barras",
        )

    def clean_codigo_barras(self):
        codigo_barras = self.cleaned_data.get("codigo_barras")
        if not codigo_barras:
            return None

        try:
            return validate_ean13(codigo_barras)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


class ProdutoEdicaoForm(forms.ModelForm):
    codigo_barras = forms.CharField(max_length=17, required=False)

    class Meta:
        model = Produto
        fields = (
            "nome",
            "categoria",
            "tamanho",
            "modelo",
            "cor",
            "tecido",
            "genero",
            "preco_compra",
            "preco_venda",
            "estoque_minimo",
            "codigo_barras",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field_ids = {
            "nome": "edit-product-name",
            "categoria": "edit-product-category",
            "codigo_barras": "edit-product-barcode",
            "genero": "edit-product-gender",
            "tamanho": "edit-product-size",
            "modelo": "edit-product-model",
            "cor": "edit-product-color",
            "tecido": "edit-product-fabric",
            "preco_compra": "edit-product-cost",
            "preco_venda": "edit-product-price",
            "estoque_minimo": "edit-product-minimum",
        }
        for field_name, field_id in field_ids.items():
            self.fields[field_name].widget.attrs["id"] = field_id
            self.fields[field_name].widget.attrs["class"] = "form-control"

    def clean_codigo_barras(self):
        codigo_barras = self.cleaned_data.get("codigo_barras")
        if not codigo_barras:
            return None

        try:
            return validate_ean13(codigo_barras)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


class FichaProdutoForm(forms.Form):
    nome = forms.CharField(max_length=40, label='Nome base')
    categoria = forms.ModelChoiceField(
        queryset=Produto._meta.get_field('categoria').remote_field.model.objects.all(),
        required=False,
        empty_label='Sem categoria',
    )
    modelo = forms.CharField(max_length=80, required=False)
    tecido = forms.CharField(max_length=80, required=False)
    genero = forms.ChoiceField(
        choices=(('', 'Não informado'), *Produto.GENERO_CHOICES),
        required=False,
    )
    preco_compra = forms.DecimalField(
        min_value=0.01, max_digits=10, decimal_places=2,
        label='Preço de compra padrão',
    )
    preco_venda = forms.DecimalField(
        min_value=0, max_digits=10, decimal_places=2,
        label='Preço de venda padrão',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        self.fields['nome'].widget.attrs['id'] = 'variation-base-name'


class VariacaoProdutoForm(forms.Form):
    cor = forms.CharField(max_length=40, label='Cor')
    tamanho = forms.ChoiceField(choices=Produto.TAMANHO_CHOICES, label='Tamanho')
    quantidade = forms.DecimalField(
        min_value=0, max_digits=12, decimal_places=2, label='Saldo inicial',
    )
    preco_compra = forms.DecimalField(
        required=False, min_value=0.01, max_digits=10, decimal_places=2,
    )
    preco_venda = forms.DecimalField(
        required=False, min_value=0, max_digits=10, decimal_places=2,
    )
    estoque_minimo = forms.DecimalField(
        required=False, min_value=0, max_digits=12, decimal_places=2,
    )
    codigo_barras = forms.CharField(
        max_length=17, required=False, label='Código de barras (EAN-13)',
        help_text='Deixe em branco para gerar automaticamente.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.setdefault('class', 'form-control')
            field.widget.attrs.setdefault('data-variation-field', name)

    def clean_codigo_barras(self):
        value = self.cleaned_data.get('codigo_barras')
        if not value:
            return None
        try:
            return validate_ean13(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


VariacaoProdutoFormSet = formset_factory(
    VariacaoProdutoForm,
    extra=1,
    max_num=50,
    validate_max=True,
)


class ModeloEtiquetaForm(forms.ModelForm):
    class Meta:
        model = ModeloEtiqueta
        fields = "__all__"
