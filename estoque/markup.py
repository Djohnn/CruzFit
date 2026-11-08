from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from django import forms
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

CENTAVO = Decimal('0.01')
PERCENTUAIS = ('impostos', 'cartao', 'comissao', 'fixo', 'margem')


def _percentual(label: str) -> forms.DecimalField:
    return forms.DecimalField(
        label=label, localize=True, max_digits=5, decimal_places=2,
        min_value=0, max_value=100, required=False, initial=0,
    )


class MarkupForm(forms.Form):
    custo = forms.DecimalField(
        label='Custo do produto (R$)', localize=True,
        max_digits=12, decimal_places=2, min_value=CENTAVO,
    )
    impostos = _percentual('Impostos (%)')
    cartao = _percentual('Taxa de cartão (%)')
    comissao = _percentual('Comissão (%)')
    fixo = _percentual('Custo fixo rateado (%)')
    margem = _percentual('Margem de lucro desejada (%)')
    arredondamento = forms.ChoiceField(
        label='Arredondar preço',
        choices=[('centavos', 'Manter centavos'), ('99', 'Terminar em ,99')],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            if isinstance(field, forms.DecimalField):
                field.widget.attrs['inputmode'] = 'decimal'

    def clean(self):
        dados = super().clean()
        taxas = {campo: dados.get(campo) or Decimal(0) for campo in PERCENTUAIS}
        divisor = 1 - sum(taxas.values()) / Decimal(100)
        if divisor <= 0:
            raise forms.ValidationError('A soma dos percentuais deve ser menor que 100%.')
        dados.update(taxas, divisor=divisor)
        return dados

    def calcular(self):
        dados = self.cleaned_data
        custo, divisor = dados['custo'], dados['divisor']
        preco_base = custo / divisor
        preco = preco_base.quantize(CENTAVO, rounding=ROUND_CEILING)
        if dados['arredondamento'] == '99':
            preco = preco.to_integral_value(rounding=ROUND_CEILING) - CENTAVO
            if preco < preco_base:
                preco += 1

        valores = {
            campo: (preco * dados[campo] / 100).quantize(CENTAVO, rounding=ROUND_HALF_UP)
            for campo in PERCENTUAIS
        }
        receita = preco - valores['impostos'] - valores['cartao'] - valores['comissao']
        return {
            'custo': custo, 'preco': preco, 'preco_base': preco_base, 'divisor': divisor,
            'multiplicador_base': 1 / divisor, 'multiplicador_final': preco / custo,
            'detalhes': [(self.fields[campo].label, dados[campo], valores[campo]) for campo in PERCENTUAIS],
            'receita': receita, 'lucro': receita - custo - valores['fixo'],
        }


@login_required
@require_http_methods(['GET', 'POST'])
def calculadora_markup(request: HttpRequest) -> HttpResponse:
    form = MarkupForm(request.POST if request.method == 'POST' else None)
    resultado = form.calcular() if form.is_bound and form.is_valid() else None
    return render(request, 'markup.html', {'form': form, 'resultado': resultado})
