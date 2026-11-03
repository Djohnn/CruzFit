from django import forms

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

    def clean_codigo_barras(self):
        codigo_barras = self.cleaned_data.get("codigo_barras")
        if not codigo_barras:
            return None

        try:
            return validate_ean13(codigo_barras)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


class ModeloEtiquetaForm(forms.ModelForm):
    class Meta:
        model = ModeloEtiqueta
        fields = "__all__"
