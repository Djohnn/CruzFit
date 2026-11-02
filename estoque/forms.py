from django import forms

from .barcodes import validate_ean13
from .models import ModeloEtiqueta, Produto


class ProdutoForm(forms.ModelForm):
    codigo_barras = forms.CharField(max_length=17, required=False)

    class Meta:
        model = Produto
        fields = "__all__"

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
