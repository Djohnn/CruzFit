"""Serviços do cadastro de fornecedores; escritas auditadas e idempotentes."""

import hashlib
import json
from collections.abc import Mapping

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .documentos import validar_documento
from .models import Fornecedor, FornecedorAlteracao, FornecedorInativacao

CAMPOS = (
    "nome",
    "nome_fantasia",
    "documento",
    "contato",
    "telefone",
    "email",
    "endereco",
    "observacoes",
)


def _autor_e_chave(actor, key: str | None) -> str:
    if not (
        actor
        and actor.is_authenticated
        and actor.is_active
        and (actor.is_superuser or actor.cargo == "G")
    ):
        raise ValidationError("Apenas o gerente pode administrar fornecedores.")
    chave = (key or "").strip()
    if not chave or len(chave) > 64:
        raise ValidationError("Informe uma chave de operação válida.")
    return chave


def _motivo(reason: str) -> str:
    motivo = reason.strip()
    if not motivo or len(motivo) > 500:
        raise ValidationError({"motivo": "Informe um motivo com até 500 caracteres."})
    return motivo


def _dados(dados: Mapping[str, str]) -> dict[str, str | None]:
    resultado: dict[str, str | None] = {
        campo: str(dados.get(campo) or "").strip() for campo in CAMPOS
    }
    try:
        resultado["documento"] = validar_documento(resultado["documento"] or "")
    except ValidationError as error:
        raise ValidationError({"documento": error.messages}) from error
    return resultado


def _assinatura(actor, dados) -> str:
    payload = json.dumps([actor.pk, dados], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _conferir(operacao, assinatura: str):
    if operacao.assinatura != assinatura:
        raise ValidationError(
            "Essa chave já foi usada com outros dados. Abra um novo formulário.",
        )


def cadastrar_fornecedor(
    *, actor, dados: Mapping[str, str], idempotency_key: str | None
) -> Fornecedor:
    chave = _autor_e_chave(actor, idempotency_key)
    valores = _dados(dados)
    assinatura = _assinatura(actor, valores)
    try:
        with transaction.atomic():
            existente = Fornecedor.objects.filter(chave_idempotencia=chave).first()
            if existente:
                _conferir(existente, assinatura)
                return existente
            fornecedor = Fornecedor(
                **valores,
                responsavel=actor,
                chave_idempotencia=chave,
                assinatura=assinatura,
            )
            fornecedor.full_clean()
            fornecedor.save()
            return fornecedor
    except IntegrityError as error:
        raise ValidationError(
            "Cadastro já registrado. Confira o documento e tente novamente."
        ) from error


def editar_fornecedor(
    *,
    actor,
    fornecedor_id: int,
    dados: Mapping[str, str],
    reason: str,
    idempotency_key: str | None,
) -> Fornecedor:
    chave = _autor_e_chave(actor, idempotency_key)
    motivo = _motivo(reason)
    valores = _dados(dados)
    assinatura = _assinatura(actor, [fornecedor_id, valores, motivo])
    try:
        with transaction.atomic():
            existente = FornecedorAlteracao.objects.filter(
                chave_idempotencia=chave
            ).first()
            if existente:
                _conferir(existente, assinatura)
                return existente.fornecedor
            fornecedor = Fornecedor.objects.select_for_update().get(pk=fornecedor_id)
            anteriores = {campo: getattr(fornecedor, campo) for campo in CAMPOS}
            for campo, valor in valores.items():
                setattr(fornecedor, campo, valor)
            fornecedor.full_clean()
            fornecedor.save(update_fields=(*CAMPOS, "atualizado_em"))
            FornecedorAlteracao.objects.create(
                fornecedor=fornecedor,
                responsavel=actor,
                motivo=motivo,
                chave_idempotencia=chave,
                assinatura=assinatura,
                dados_anteriores=anteriores,
                dados_novos=valores,
            )
            return fornecedor
    except Fornecedor.DoesNotExist as error:
        raise ValidationError("Fornecedor não encontrado.") from error
    except IntegrityError as error:
        raise ValidationError(
            "Documento ou operação já registrado. Confira os dados."
        ) from error


def inativar_fornecedor(
    *, actor, fornecedor_id: int, reason: str, idempotency_key: str | None
) -> Fornecedor:
    chave = _autor_e_chave(actor, idempotency_key)
    motivo = _motivo(reason)
    assinatura = _assinatura(actor, [fornecedor_id, motivo])
    try:
        with transaction.atomic():
            existente = FornecedorInativacao.objects.filter(
                chave_idempotencia=chave
            ).first()
            if existente:
                _conferir(existente, assinatura)
                return existente.fornecedor
            fornecedor = Fornecedor.objects.select_for_update().get(pk=fornecedor_id)
            if not fornecedor.ativo:
                raise ValidationError("Este fornecedor já está inativo.")
            fornecedor.ativo = False
            fornecedor.save(update_fields=("ativo", "atualizado_em"))
            FornecedorInativacao.objects.create(
                fornecedor=fornecedor,
                responsavel=actor,
                motivo=motivo,
                chave_idempotencia=chave,
                assinatura=assinatura,
            )
            return fornecedor
    except Fornecedor.DoesNotExist as error:
        raise ValidationError("Fornecedor não encontrado.") from error
    except IntegrityError as error:
        raise ValidationError("Operação já registrada. Atualize a página.") from error


def fornecedores_ativos():
    return Fornecedor.objects.filter(ativo=True).order_by("nome", "pk")


def obter_fornecedor_ativo(fornecedor_id):
    try:
        return fornecedores_ativos().select_for_update().get(pk=int(fornecedor_id))
    except (TypeError, ValueError, OverflowError, Fornecedor.DoesNotExist) as error:
        raise ValidationError(
            "Selecione um fornecedor ativo para esta entrada."
        ) from error
