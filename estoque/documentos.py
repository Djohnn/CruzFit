"""Normalização e dígitos verificadores de CPF/CNPJ, sem consulta externa.

CNPJ alfanumérico: Receita Federal, Manual de cálculo do DV do CNPJ.
https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/documentos-tecnicos/cnpj
"""

import re

from django.core.exceptions import ValidationError


def normalizar_documento(value: str) -> str:
    return re.sub(r"[.\s/\-]", "", value).upper()


def validar_documento(value: str) -> str | None:
    documento = normalizar_documento(value)
    if not documento:
        return None
    if len(set(documento)) == 1:
        raise ValidationError("Informe um CPF ou CNPJ válido.")
    if re.fullmatch(r"[0-9]{11}", documento):
        base = documento[:9]
        for tamanho in (10, 11):
            total = sum(
                int(char) * peso
                for char, peso in zip(
                    base,
                    range(tamanho, 1, -1),
                )
            )
            resto = total % 11
            base += str(0 if resto < 2 else 11 - resto)
        valido = base == documento
    elif re.fullmatch(r"[A-Z0-9]{12}[0-9]{2}", documento):
        base = documento[:12]
        for pesos in (
            (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2),
            (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2),
        ):
            total = sum((ord(char) - 48) * peso for char, peso in zip(base, pesos))
            resto = total % 11
            base += str(0 if resto < 2 else 11 - resto)
        valido = base == documento
    else:
        valido = False
    if not valido:
        raise ValidationError("Informe um CPF ou CNPJ válido, ou deixe em branco.")
    return documento
