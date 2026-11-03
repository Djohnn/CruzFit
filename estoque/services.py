from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any, cast
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from PIL import Image, ImageDraw

from .fornecedores import cadastrar_fornecedor as cadastrar_fornecedor
from .fornecedores import editar_fornecedor as editar_fornecedor
from .fornecedores import inativar_fornecedor as inativar_fornecedor
from .fornecedores import obter_fornecedor_ativo
from .models import (
    BaixaEstoque,
    EntradaEstoque,
    Imagem,
    ItemBaixaEstoque,
    ItemEntradaEstoque,
    Produto,
    ProdutoAlteracao,
    ProdutoCadastro,
    ProdutoInativacao,
)

TWO_PLACES = Decimal('0.01')


class StockOperationError(ValueError):
    """Erro de validação de uma movimentação de estoque."""


class ProductOperationError(ValidationError):
    """Erro de validação de uma alteração de cadastro de produto."""


@dataclass(frozen=True)
class MovementResult:
    movement: BaixaEstoque | EntradaEstoque
    created: bool


@dataclass(frozen=True)
class ProductOperationResult:
    product: Produto
    created: bool


PRODUCT_EDIT_FIELDS = (
    'nome',
    'categoria',
    'tamanho',
    'modelo',
    'cor',
    'tecido',
    'genero',
    'preco_compra',
    'preco_venda',
    'estoque_minimo',
    'codigo_barras',
)
PRODUCT_CREATE_FIELDS = (
    'nome',
    'categoria',
    'tamanho',
    'modelo',
    'cor',
    'tecido',
    'genero',
    'quantidade',
    'preco_compra',
    'preco_venda',
    'estoque_minimo',
    'codigo_barras',
)


def _require_authenticated(actor):
    if actor is None or not actor.is_authenticated:
        raise StockOperationError('Usuário autenticado é obrigatório.')


def _require_manager(actor) -> None:
    if not (
        actor
        and actor.is_authenticated
        and actor.is_active
        and (actor.is_superuser or actor.cargo == 'G')
    ):
        raise ProductOperationError('Apenas o gerente pode administrar produtos.')


def _product_key(key: str | None) -> str:
    normalized = (key or '').strip()
    if not normalized or len(normalized) > 64:
        raise ProductOperationError('Informe uma chave de operação válida.')
    return normalized


def _product_reason(reason: str) -> str:
    normalized = (reason or '').strip()
    if not normalized or len(normalized) > 500:
        raise ProductOperationError('Informe um motivo com até 500 caracteres.')
    return normalized


def _normalize_product_data(
    dados: Mapping[str, object],
    fields: Sequence[str] = PRODUCT_EDIT_FIELDS,
) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for field in fields:
        value = dados.get(field)
        if field == 'categoria':
            category_id = getattr(value, 'pk', value) or None
            if category_id is not None:
                try:
                    category_id = int(cast(Any, category_id))
                except (TypeError, ValueError, OverflowError) as error:
                    raise ProductOperationError('Selecione uma categoria válida.') from error
            normalized['categoria_id'] = category_id
        elif field in ('preco_compra', 'preco_venda', 'estoque_minimo', 'quantidade'):
            if value in (None, '') and field == 'estoque_minimo':
                normalized[field] = None
                continue
            try:
                parsed = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise ProductOperationError('Informe valores decimais válidos.') from error
            if not parsed.is_finite():
                raise ProductOperationError('Informe valores decimais válidos.')
            normalized[field] = parsed
        elif field == 'codigo_barras':
            normalized[field] = str(value or '').strip() or None
        else:
            normalized[field] = str(value or '').strip()
    return normalized


def _product_snapshot(product: Produto) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for field in PRODUCT_EDIT_FIELDS:
        if field == 'categoria':
            value = product.categoria_id
        else:
            value = getattr(product, field)
        snapshot[field] = str(value) if isinstance(value, Decimal) else value
    snapshot['quantidade'] = str(product.quantidade)
    snapshot['ativo'] = product.ativo
    return snapshot


def _product_signature(actor, product_id: int, reason: str, data: object) -> str:
    payload = json.dumps(
        [actor.pk, product_id, reason, data],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _check_product_replay(operation, signature: str) -> None:
    if operation.assinatura != signature:
        raise ProductOperationError(
            'Essa chave já foi usada com outros dados. Abra uma nova operação.',
        )


def _image_descriptors(images: Sequence[object]) -> list[dict[str, object]]:
    return [
        {
            'nome': str(getattr(image, 'name', '')),
            'tamanho': int(cast(Any, getattr(image, 'size', 0))),
            'sha256': _image_content_digest(image),
        }
        for image in images
    ]


def _image_content_digest(image: object) -> str:
    digest = hashlib.sha256()
    seek = getattr(image, 'seek', None)
    tell = getattr(image, 'tell', None)
    original_position = 0
    if callable(tell):
        position = tell()
        if isinstance(position, int):
            original_position = position
    if callable(seek):
        seek(0)
    try:
        chunks = getattr(image, 'chunks', None)
        if callable(chunks):
            for chunk in chunks():
                digest.update(chunk)
        else:
            read = getattr(image, 'read', None)
            if not callable(read):
                raise ProductOperationError('Arquivo de imagem inválido.')
            while chunk := read(1024 * 1024):
                digest.update(chunk)
    finally:
        if callable(seek):
            seek(original_position)
    return digest.hexdigest()


def _delete_storage_files(names: Sequence[str]) -> None:
    for name in names:
        if name and default_storage.exists(name):
            default_storage.delete(name)


def _processed_image_payloads(
    product: Produto,
    images: Sequence[object],
) -> list[tuple[str, bytes]]:
    payloads: list[tuple[str, bytes]] = []
    for index, uploaded in enumerate(images, start=1):
        try:
            seek = getattr(uploaded, 'seek', None)
            if callable(seek):
                seek(0)
            with Image.open(cast(Any, uploaded)) as opened:
                image = opened.convert('RGB')
            image = image.resize((300, 300))
            draw = ImageDraw.Draw(image)
            draw.text((20, 280), f'CRUZ FIT {date.today()}', (255, 255, 255))
            output = BytesIO()
            image.save(output, format='JPEG', quality=100)
            name = f'{date.today()}-{product.pk}-{uuid4().hex}-{index}.jpg'
            payloads.append((name, output.getvalue()))
        except (Image.DecompressionBombError, OSError, ValueError) as error:
            raise ProductOperationError('Envie apenas imagens válidas.') from error
    return payloads


def _replace_product_images(product: Produto, images: Sequence[object]) -> list[str]:
    payloads = _processed_image_payloads(product, images)
    current_images = list(product.imagem_set.all())
    old_names = [str(current.imagem.name) for current in current_images]
    created_names: list[str] = []

    try:
        created_names = _persist_product_images(product, payloads)
        for current in current_images:
            current.delete()
        transaction.on_commit(
            lambda names=tuple(old_names): _delete_storage_files(names),
            robust=True,
        )
    except Exception:
        _delete_storage_files(created_names)
        raise
    return created_names


def _persist_product_images(
    product: Produto,
    payloads: Sequence[tuple[str, bytes]],
) -> list[str]:
    created_names: list[str] = []
    try:
        for name, content in payloads:
            image = Imagem(produto=product)
            image.imagem.save(
                name,
                ContentFile(content, name=name),
                save=False,
            )
            created_names.append(str(image.imagem.name))
            image.save(force_insert=True)
    except Exception:
        _delete_storage_files(created_names)
        raise
    return created_names


def cadastrar_produto(
    *,
    actor,
    dados: Mapping[str, object],
    reason: str,
    idempotency_key: str | None,
    images: Sequence[object] | None = None,
) -> ProductOperationResult:
    _require_manager(actor)
    key = _product_key(idempotency_key)
    motivo = _product_reason(reason)
    normalized = _normalize_product_data(dados, PRODUCT_CREATE_FIELDS)
    image_list = tuple(images or ())
    signature_data = {
        **normalized,
        'imagens': _image_descriptors(image_list),
    }
    audit_data = _json_safe(signature_data)
    signature = _product_signature(actor, 0, motivo, signature_data)

    with transaction.atomic():
        existing = ProdutoCadastro.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            _check_product_replay(existing, signature)
            return ProductOperationResult(existing.produto, created=False)

        created_image_names: list[str] = []
        try:
            product = Produto(**normalized)
            product.save()
            if image_list:
                created_image_names = _persist_product_images(
                    product,
                    _processed_image_payloads(product, image_list),
                )
            ProdutoCadastro.objects.create(
                produto=product,
                responsavel=actor,
                motivo=motivo,
                chave_idempotencia=key,
                assinatura=signature,
                dados_novos=audit_data,
            )
        except ValidationError as error:
            _delete_storage_files(created_image_names)
            raise ProductOperationError('; '.join(error.messages)) from error
        except IntegrityError as error:
            _delete_storage_files(created_image_names)
            raise ProductOperationError(
                'Produto ou operação já registrada. Confira os dados.',
            ) from error
        except Exception:
            _delete_storage_files(created_image_names)
            raise
    return ProductOperationResult(product, created=True)


def editar_produto(
    *,
    actor,
    product_id: int,
    dados: Mapping[str, object],
    reason: str,
    idempotency_key: str | None,
    images: Sequence[object] | None = None,
) -> ProductOperationResult:
    _require_manager(actor)
    key = _product_key(idempotency_key)
    motivo = _product_reason(reason)
    normalized = _normalize_product_data(dados)
    image_list = tuple(images or ())
    signature_data = {
        **normalized,
        'imagens': _image_descriptors(image_list),
    }
    audit_data = _json_safe(signature_data)
    signature = _product_signature(actor, product_id, motivo, signature_data)

    with transaction.atomic():
        existing = ProdutoAlteracao.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            _check_product_replay(existing, signature)
            return ProductOperationResult(existing.produto, created=False)

        try:
            product = Produto.objects.select_for_update().get(pk=product_id)
        except Produto.DoesNotExist as error:
            raise ProductOperationError('Produto não encontrado.') from error

        created_image_names: list[str] = []
        try:
            before = _product_snapshot(product)
            for field, value in normalized.items():
                setattr(product, field, value)
            try:
                product.save()
            except ValidationError as error:
                raise ProductOperationError('; '.join(error.messages)) from error

            if image_list:
                created_image_names = _replace_product_images(product, image_list)

            ProdutoAlteracao.objects.create(
                produto=product,
                responsavel=actor,
                motivo=motivo,
                chave_idempotencia=key,
                assinatura=signature,
                dados_anteriores=before,
                dados_novos=audit_data,
            )
        except Exception:
            _delete_storage_files(created_image_names)
            raise
    return ProductOperationResult(product, created=True)


def inativar_produto(
    *,
    actor,
    product_id: int,
    reason: str,
    idempotency_key: str | None,
) -> ProductOperationResult:
    _require_manager(actor)
    key = _product_key(idempotency_key)
    motivo = _product_reason(reason)
    signature = _product_signature(actor, product_id, motivo, {'acao': 'inativar'})

    with transaction.atomic():
        existing = ProdutoInativacao.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            _check_product_replay(existing, signature)
            return ProductOperationResult(existing.produto, created=False)

        try:
            product = Produto.objects.select_for_update().get(pk=product_id)
        except Produto.DoesNotExist as error:
            raise ProductOperationError('Produto não encontrado.') from error
        if not product.ativo:
            raise ProductOperationError('Este produto já está inativo.')
        if product.quantidade != 0:
            raise ProductOperationError(
                'Regularize o estoque até o saldo zero antes de inativar o produto.',
            )

        product.ativo = False
        product.save(update_fields=('ativo',))
        ProdutoInativacao.objects.create(
            produto=product,
            responsavel=actor,
            motivo=motivo,
            chave_idempotencia=key,
            assinatura=signature,
        )
    return ProductOperationResult(product, created=True)


def _parse_idempotency_key(key: str | None) -> str:
    normalized = (key or '').strip()
    if not normalized:
        raise StockOperationError('A chave de idempotência é obrigatória.')
    if len(normalized) > 64:
        raise StockOperationError('Chave de idempotência inválida.')
    return normalized


def _parse_lines(
    product_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
) -> list[tuple[int, Decimal]]:
    product_ids = list(product_ids)
    quantities = list(quantities)
    if not product_ids or len(product_ids) != len(quantities):
        raise StockOperationError('Adicione pelo menos um produto à operação.')

    lines = []
    selected_ids = set()
    for raw_product_id, raw_quantity in zip(product_ids, quantities):
        try:
            product_id = int(raw_product_id)
            quantity = Decimal(str(raw_quantity))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise StockOperationError('Informe quantidades maiores que zero.') from error

        try:
            normalized_quantity = quantity.quantize(TWO_PLACES)
        except InvalidOperation as error:
            raise StockOperationError(
                'Informe quantidades maiores que zero com no máximo duas casas decimais.'
            ) from error
        if (
            not quantity.is_finite()
            or quantity <= 0
            or quantity != normalized_quantity
        ):
            raise StockOperationError(
                'Informe quantidades maiores que zero com no máximo duas casas decimais.'
            )
        if product_id in selected_ids:
            raise StockOperationError('Selecione cada produto apenas uma vez.')
        selected_ids.add(product_id)
        lines.append((product_id, quantity))
    return lines


def _locked_products(lines: list[tuple[int, Decimal]]) -> dict[int, Produto]:
    selected_ids = {product_id for product_id, _ in lines}
    products = {
        product.pk: product
        for product in Produto.objects.select_for_update().filter(pk__in=selected_ids)
    }
    if len(products) != len(selected_ids):
        raise StockOperationError('Um dos produtos selecionados não existe.')
    if any(not product.ativo for product in products.values()):
        raise StockOperationError('Produto inativo não pode receber movimentações.')
    return products


def registrar_entrada(
    *,
    actor,
    product_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
    reason: str,
    observation: str = '',
    idempotency_key: str | None,
    supplier_id: str | int | None = None,
) -> MovementResult:
    _require_authenticated(actor)
    key = _parse_idempotency_key(idempotency_key)
    lines = _parse_lines(product_ids, quantities)
    if reason not in dict(EntradaEstoque.MOTIVO_CHOICES):
        raise StockOperationError('Selecione um motivo válido para a entrada.')
    try:
        supplier_pk = int(supplier_id) if supplier_id not in (None, '') else None
        if supplier_pk is not None and supplier_pk <= 0:
            raise ValueError('Identificador deve ser positivo.')
    except (TypeError, ValueError, OverflowError) as error:
        raise StockOperationError('Selecione um fornecedor válido.') from error

    with transaction.atomic():
        existing = EntradaEstoque.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            existing_lines = sorted(
                (item.produto_id, item.quantidade) for item in existing.itens.all()
            )
            if (
                existing.responsavel_id != actor.pk
                or existing.motivo != reason
                or existing.observacao != observation.strip()
                or existing.fornecedor_id != supplier_pk
                or existing_lines != sorted(lines)
            ):
                raise StockOperationError(
                    'Essa chave de entrada já foi usada com outros dados. '
                    'Abra uma nova entrada.',
                )
            return MovementResult(existing, created=False)

        if reason == 'COMPRA' and supplier_pk is None:
            raise StockOperationError('Selecione o fornecedor para registrar uma compra.')
        try:
            supplier = obter_fornecedor_ativo(supplier_pk) if supplier_pk is not None else None
        except ValidationError as error:
            raise StockOperationError(' '.join(error.messages)) from error
        products = _locked_products(lines)
        movement = EntradaEstoque.objects.create(
            motivo=reason,
            observacao=observation.strip(),
            responsavel=actor,
            chave_idempotencia=key,
            fornecedor=supplier,
            fornecedor_nome=supplier.nome if supplier else '',
            fornecedor_documento=(supplier.documento or '') if supplier else '',
        )
        for product_id, quantity in lines:
            product = products[product_id]
            product.quantidade += quantity
            product.save(update_fields=('quantidade',))
            ItemEntradaEstoque.objects.create(
                entrada=movement,
                produto=product,
                quantidade=quantity,
            )
    return MovementResult(movement, created=True)


def registrar_baixa(
    *,
    actor,
    product_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
    reason: str = 'VENDA',
    observation: str = '',
    idempotency_key: str | None,
) -> MovementResult:
    _require_authenticated(actor)
    key = _parse_idempotency_key(idempotency_key)
    lines = _parse_lines(product_ids, quantities)
    if reason not in dict(BaixaEstoque.MOTIVO_CHOICES):
        raise StockOperationError('Selecione um motivo válido para a baixa.')

    with transaction.atomic():
        existing = BaixaEstoque.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            existing_lines = sorted(
                (item.produto_id, item.quantidade) for item in existing.itens.all()
            )
            if (
                existing.responsavel_id != actor.pk
                or existing.motivo != reason
                or existing.observacao != observation.strip()
                or existing_lines != sorted(lines)
            ):
                raise StockOperationError(
                    'Essa chave de baixa já foi usada com outros dados. '
                    'Abra uma nova baixa.',
                )
            return MovementResult(existing, created=False)

        products = _locked_products(lines)
        for product_id, quantity in lines:
            product = products[product_id]
            if quantity > product.quantidade:
                raise StockOperationError(f'Estoque insuficiente para {product.nome}.')

        movement = BaixaEstoque.objects.create(
            motivo=reason,
            observacao=observation.strip(),
            responsavel=actor,
            chave_idempotencia=key,
        )
        for product_id, quantity in lines:
            product = products[product_id]
            product.quantidade -= quantity
            product.save(update_fields=('quantidade',))
            ItemBaixaEstoque.objects.create(
                baixa=movement,
                produto=product,
                quantidade=quantity,
            )
    return MovementResult(movement, created=True)
