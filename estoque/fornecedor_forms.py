from django import forms

from .documentos import validar_documento


class FornecedorForm(forms.Form):
    nome = forms.CharField(label="Nome / razão social", max_length=160)
    nome_fantasia = forms.CharField(
        label="Nome fantasia", max_length=160, required=False
    )
    documento = forms.CharField(
        label="CPF / CNPJ",
        max_length=32,
        required=False,
        help_text="Opcional. Aceita CNPJ com letras.",
    )
    contato = forms.CharField(label="Pessoa de contato", max_length=120, required=False)
    telefone = forms.CharField(max_length=40, required=False)
    email = forms.EmailField(label="E-mail", required=False)
    endereco = forms.CharField(
        label="Endereço",
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    observacoes = forms.CharField(
        label="Observações",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    chave_idempotencia = forms.CharField(max_length=64, widget=forms.HiddenInput)
    motivo = forms.CharField(
        max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing:
            self.fields["motivo"].required = True
            self.fields["motivo"].label = "Motivo da alteração"
        else:
            del self.fields["motivo"]
        for name, field in self.fields.items():
            field.widget.attrs["class"] = "form-control"
            field.widget.attrs["data-testid"] = f"fornecedor-{name}"
            if field.help_text:
                field.widget.attrs["aria-describedby"] = f"id_{name}_help"
            if self.is_bound and name in self.errors:
                field.widget.attrs["aria-invalid"] = "true"
                field.widget.attrs["aria-describedby"] = f"id_{name}_errors"

    def clean_documento(self):
        return validar_documento(self.cleaned_data["documento"]) or ""


class InativacaoFornecedorForm(forms.Form):
    motivo = forms.CharField(
        label="Motivo da inativação",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )
    chave_idempotencia = forms.CharField(max_length=64, widget=forms.HiddenInput)


def adicionar_erros(form, error):
    if hasattr(error, "message_dict"):
        for field, messages in error.message_dict.items():
            form.add_error(field if field in form.fields else None, messages)
    else:
        form.add_error(None, error)
