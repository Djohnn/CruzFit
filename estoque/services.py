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

from .barcodes import generate_ean13, validate_ean13
from .fornecedores import cadastrar_fornecedor as cadastrar_fornecedor
from .fornecedores import editar_fornecedor as editar_fornecedor
from .fornecedores import inativar_fornecedor as inativar_fornecedor
from .fornecedores import obter_fornecedor_ativo
from .models import (
    BaixaCorrecao,
    BaixaEstoque,
    DevolucaoFornecedor,
    EntradaEstoque,
    FichaProduto,
    FichaProdutoAlteracao,
    FichaProdutoCadastro,
    Imagem,
    ItemBaixaCorrecao,
    ItemBaixaEstoque,
    ItemDevolucaoFornecedor,
    ItemEntradaEstoque,
    Produto,
    ProdutoAlteracao,
    ProdutoCadastro,
    ProdutoInativacao,
    normalizar_variacao,
)

TWO_PLACES = Decimal('0.01')


class StockOperationError(ValueError):
    """Erro de validação de uma movimentação de estoque."""


class ReversalOperationError(ValueError):
    """Erro de validação ou conflito em uma reversão."""


class ProductOperationError(ValidationError):
    """Erro de validação de uma alteração de cadastro de produto."""


@dataclass(frozen=True)
class MovementResult:
    movement: BaixaEstoque | EntradaEstoque
    created: bool


@dataclass(frozen=True)
class ReversalResult:
    operation: object
    created: bool


@dataclass(frozen=True)
class ProductOperationResult:
    product: Produto
    created: bool


@dataclass(frozen=True)
class FichaOperationResult:
    ficha: FichaProduto
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

VARIATION_FIELDS = (
    'cor', 'tamanho', 'quantidade', 'preco_compra', 'preco_venda',
    'estoque_minimo', 'codigo_barras',
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


def _normalize_variation_data(data: Mapping[str, object], line_number: int) -> dict[str, object]:
    normalized = _normalize_product_data(data, VARIATION_FIELDS)
    normalized['cor'] = ' '.join(str(data.get('cor') or '').split())
    normalized['tamanho'] = str(data.get('tamanho') or '').strip().upper()
    normalized['cor_normalizada'] = normalizar_variacao(str(normalized['cor']))
    normalized['tamanho_normalizado'] = normalizar_variacao(str(normalized['tamanho']))[:5]
    if not normalized['cor_normalizada']:
        raise ProductOperationError(f'Linha {line_number}: informe a cor.')
    if not normalized['tamanho_normalizado']:
        raise ProductOperationError(f'Linha {line_number}: informe o tamanho.')
    return normalized


def _normalize_variation_lines(lines: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    normalized_lines = []
    seen = set()
    for line_number, raw_line in enumerate(lines, start=1):
        try:
            normalized = _normalize_variation_data(raw_line, line_number)
        except ProductOperationError:
            raise
        except (TypeError, AttributeError) as error:
            raise ProductOperationError(f'Linha {line_number}: dados inválidos.') from error
        identity = (
            normalized['cor_normalizada'],
            normalized['tamanho_normalizado'],
        )
        if identity in seen:
            raise ProductOperationError(
                f'Linha {line_number}: combinação de cor e tamanho duplicada.',
            )
        seen.add(identity)
        normalized_lines.append(normalized)
    if not normalized_lines:
        raise ProductOperationError('Adicione pelo menos uma variação.')
    return normalized_lines


def _ficha_signature(actor, ficha_id: int, reason: str, data: object) -> str:
    payload = json.dumps(
        [actor.pk, ficha_id, reason, data],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _preencher_eans_variacoes(lines: list[dict[str, object]]) -> list[dict[str, object]]:
    if all(line['codigo_barras'] for line in lines):
        return lines
    used_codes = set(Produto.objects.exclude(codigo_barras__isnull=True).exclude(
        codigo_barras='',
    ).values_list('codigo_barras', flat=True))
    for line in lines:
        if line['codigo_barras']:
            try:
                used_codes.add(validate_ean13(str(line['codigo_barras'])))
            except ValueError as error:
                raise ProductOperationError(str(error)) from error
    result = []
    for line in lines:
        if line['codigo_barras']:
            result.append(line)
        else:
            code = generate_ean13(used_codes)
            used_codes.add(code)
            result.append({**line, 'codigo_barras': code})
    return result


def _ficha_data(dados: Mapping[str, object]) -> dict[str, object]:
    normalized = _normalize_product_data(
        dados,
        ('nome', 'categoria', 'modelo', 'tecido', 'genero'),
    )
    normalized['nome'] = str(normalized['nome']).strip()
    if not normalized['nome']:
        raise ProductOperationError('Informe o nome da ficha.')
    return normalized


def _ficha_audit_payload(common: Mapping[str, object], lines: Sequence[Mapping[str, object]]) -> object:
    return _json_safe({'ficha': dict(common), 'variacoes': list(lines)})


def cadastrar_ficha_variacoes(
    *, actor, dados: Mapping[str, object], variacoes: Iterable[Mapping[str, object]],
    reason: str, idempotency_key: str | None,
) -> FichaOperationResult:
    _require_manager(actor)
    key = _product_key(idempotency_key)
    motivo = _product_reason(reason)
    common = _ficha_data(dados)
    lines = _normalize_variation_lines(variacoes)
    signature_data = _ficha_audit_payload(common, lines)
    signature = _ficha_signature(actor, 0, motivo, signature_data)

    with transaction.atomic():
        existing = FichaProdutoCadastro.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            if existing.assinatura != signature:
                raise ProductOperationError(
                    'Essa chave já foi usada com outros dados. Abra uma nova operação.',
                )
            return FichaOperationResult(existing.ficha, created=False)
        try:
            ficha = FichaProduto.objects.create(**common)
            saved_lines = _preencher_eans_variacoes(lines)
            for normalized in saved_lines:
                Produto.objects.create(
                    nome=ficha.nome,
                    ficha=ficha,
                    categoria=ficha.categoria,
                    modelo=ficha.modelo,
                    tecido=ficha.tecido,
                    genero=ficha.genero,
                    **normalized,
                )
            FichaProdutoCadastro.objects.create(
                ficha=ficha,
                responsavel=actor,
                motivo=motivo,
                chave_idempotencia=key,
                assinatura=signature,
                dados_novos=_ficha_audit_payload(common, saved_lines),
            )
        except ValidationError as error:
            raise ProductOperationError('; '.join(error.messages)) from error
        except IntegrityError as error:
            raise ProductOperationError(
                'A combinação ou o código de barras já está cadastrado.',
            ) from error
    return FichaOperationResult(ficha, created=True)


def adicionar_variacoes_ficha(
    *, actor, ficha_id: int, variacoes: Iterable[Mapping[str, object]],
    reason: str, idempotency_key: str | None,
) -> FichaOperationResult:
    _require_manager(actor)
    key = _product_key(idempotency_key)
    motivo = _product_reason(reason)
    lines = _normalize_variation_lines(variacoes)
    with transaction.atomic():
        try:
            ficha = FichaProduto.objects.select_for_update().get(pk=ficha_id)
        except FichaProduto.DoesNotExist as error:
            raise ProductOperationError('Ficha de produto não encontrada.') from error
        signature_data = _ficha_audit_payload(
            {'ficha_id': ficha.pk, 'nome': ficha.nome}, lines,
        )
        signature = _ficha_signature(actor, ficha.pk, motivo, signature_data)
        existing = FichaProdutoAlteracao.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            if existing.assinatura != signature:
                raise ProductOperationError(
                    'Essa chave já foi usada com outros dados. Abra uma nova operação.',
                )
            return FichaOperationResult(existing.ficha, created=False)
        try:
            saved_lines = _preencher_eans_variacoes(lines)
            for normalized in saved_lines:
                Produto.objects.create(
                    nome=ficha.nome,
                    ficha=ficha,
                    categoria=ficha.categoria,
                    modelo=ficha.modelo,
                    tecido=ficha.tecido,
                    genero=ficha.genero,
                    **normalized,
                )
            FichaProdutoAlteracao.objects.create(
                ficha=ficha,
                responsavel=actor,
                motivo=motivo,
                chave_idempotencia=key,
                assinatura=signature,
                dados_novos=_ficha_audit_payload(
                    {'ficha_id': ficha.pk, 'nome': ficha.nome}, saved_lines,
                ),
            )
        except ValidationError as error:
            raise ProductOperationError('; '.join(error.messages)) from error
        except IntegrityError as error:
            raise ProductOperationError(
                'A combinação ou o código de barras já está cadastrado.',
            ) from error
    return FichaOperationResult(ficha, created=True)


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
    if reason == 'DEVOLUCAO':
        raise StockOperationError(
            'Devoluções devem ser vinculadas à venda ou à compra original.',
        )
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
    if reason == 'DEVOLUCAO':
        raise StockOperationError(
            'Devoluções ao fornecedor devem ser vinculadas à compra original.',
        )

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


def _reversal_key(key: str | None) -> str:
    normalized = (key or '').strip()
    if not normalized:
        raise ReversalOperationError('A chave de idempotência é obrigatória.')
    if len(normalized) > 64:
        raise ReversalOperationError('Chave de idempotência inválida.')
    return normalized


def _reversal_reason(reason: str) -> str:
    normalized = (reason or '').strip()
    if not normalized or len(normalized) > 500:
        raise ReversalOperationError('Informe um motivo com até 500 caracteres.')
    return normalized


def _reversal_lines(
    item_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
) -> list[tuple[int, Decimal]]:
    raw_item_ids = list(item_ids)
    raw_quantities = list(quantities)
    if not raw_item_ids or len(raw_item_ids) != len(raw_quantities):
        raise ReversalOperationError('Adicione pelo menos um item à reversão.')

    lines: list[tuple[int, Decimal]] = []
    selected_ids: set[int] = set()
    for raw_item_id, raw_quantity in zip(raw_item_ids, raw_quantities):
        try:
            item_id = int(raw_item_id)
            quantity = Decimal(str(raw_quantity))
            normalized_quantity = quantity.quantize(TWO_PLACES)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ReversalOperationError(
                'Informe quantidades maiores que zero com no máximo duas casas decimais.',
            ) from error
        if (
            item_id <= 0
            or not quantity.is_finite()
            or quantity <= 0
            or quantity != normalized_quantity
        ):
            raise ReversalOperationError(
                'Informe quantidades maiores que zero com no máximo duas casas decimais.',
            )
        if item_id in selected_ids:
            raise ReversalOperationError('Não repita o mesmo item na reversão.')
        selected_ids.add(item_id)
        lines.append((item_id, normalized_quantity))
    return lines


def _reversal_signature(
    actor_id: int,
    operation: str,
    origin_id: int,
    reason: str,
    lines: Sequence[tuple[int, Decimal]] = (),
) -> str:
    payload = [
        actor_id,
        operation,
        origin_id,
        reason,
        [[item_id, str(quantity)] for item_id, quantity in sorted(lines)],
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
    ).hexdigest()


def _check_reversal_replay(operation, actor_id: int, signature: str) -> None:
    if operation.responsavel_id != actor_id or operation.assinatura != signature:
        raise ReversalOperationError(
            'Essa chave de reversão já foi usada com outros dados. Abra uma nova operação.',
        )


def _locked_reversal_products(product_ids: Iterable[int]) -> dict[int, Produto]:
    selected_ids = set(product_ids)
    products = {
        product.pk: product
        for product in Produto.objects.select_for_update().filter(pk__in=selected_ids)
    }
    if len(products) != len(selected_ids):
        raise ReversalOperationError('Um dos produtos da operação não existe.')
    return products


def _sum_reversed_quantities(queryset) -> Decimal:
    return sum(queryset, Decimal('0.00')).quantize(TWO_PLACES)


def corrigir_baixa(
    *,
    actor,
    baixa_id: int,
    reason: str,
    idempotency_key: str | None,
) -> ReversalResult:
    _require_reversal_manager(actor)
    key = _reversal_key(idempotency_key)
    motivo = _reversal_reason(reason)
    signature = _reversal_signature(actor.pk, 'correcao-baixa', baixa_id, motivo)

    with transaction.atomic():
        existing = BaixaCorrecao.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            _check_reversal_replay(existing, actor.pk, signature)
            return ReversalResult(existing, created=False)

        try:
            movement = BaixaEstoque.objects.select_for_update().get(pk=baixa_id)
        except BaixaEstoque.DoesNotExist as error:
            raise ReversalOperationError('Baixa não encontrada.') from error
        if hasattr(movement, 'venda'):
            raise ReversalOperationError('Baixas de venda devem ser revertidas pela venda original.')
        if hasattr(movement, 'devolucao_fornecedor'):
            raise ReversalOperationError('A devolução ao fornecedor não pode ser corrigida como baixa avulsa.')
        if BaixaCorrecao.objects.filter(baixa=movement).exists():
            raise ReversalOperationError('Essa baixa já foi corrigida.')

        original_items = list(
            ItemBaixaEstoque.objects.select_for_update().filter(baixa=movement).order_by('pk')
        )
        products = _locked_reversal_products(item.produto_id for item in original_items)
        entry = EntradaEstoque.objects.create(
            motivo='AJUSTE',
            observacao=f'Correção da Baixa #{movement.pk}',
            responsavel=actor,
        )
        correction = BaixaCorrecao.objects.create(
            baixa=movement,
            entrada=entry,
            responsavel=actor,
            motivo=motivo,
            chave_idempotencia=key,
            assinatura=signature,
        )
        for item in original_items:
            product = products[item.produto_id]
            product.quantidade += item.quantidade
            product.save(update_fields=('quantidade',))
            ItemEntradaEstoque.objects.create(
                entrada=entry,
                produto=product,
                quantidade=item.quantidade,
            )
            ItemBaixaCorrecao.objects.create(
                correcao=correction,
                item_baixa=item,
                produto=product,
                quantidade=item.quantidade,
            )
    return ReversalResult(correction, created=True)


def devolver_fornecedor(
    *,
    actor,
    entrada_id: int,
    item_ids: Sequence[str] | Iterable[str],
    quantities: Sequence[str] | Iterable[str],
    reason: str,
    idempotency_key: str | None,
) -> ReversalResult:
    _require_reversal_manager(actor)
    key = _reversal_key(idempotency_key)
    motivo = _reversal_reason(reason)
    lines = _reversal_lines(item_ids, quantities)
    signature = _reversal_signature(
        actor.pk,
        'devolucao-fornecedor',
        entrada_id,
        motivo,
        lines,
    )

    with transaction.atomic():
        existing = DevolucaoFornecedor.objects.select_for_update().filter(
            chave_idempotencia=key,
        ).first()
        if existing:
            _check_reversal_replay(existing, actor.pk, signature)
            return ReversalResult(existing, created=False)

        try:
            purchase = EntradaEstoque.objects.select_for_update().get(pk=entrada_id)
        except EntradaEstoque.DoesNotExist as error:
            raise ReversalOperationError('Entrada de compra não encontrada.') from error
        if purchase.motivo != 'COMPRA' or purchase.fornecedor_id is None:
            raise ReversalOperationError('A origem informada não é uma compra com fornecedor.')

        item_ids_set = {item_id for item_id, _ in lines}
        purchase_items = {
            item.pk: item
            for item in ItemEntradaEstoque.objects.select_for_update().filter(
                entrada=purchase,
                pk__in=item_ids_set,
            )
        }
        if len(purchase_items) != len(item_ids_set):
            raise ReversalOperationError('Um dos itens não pertence à compra informada.')

        previous_returns = {
            item_id: _sum_reversed_quantities(
                ItemDevolucaoFornecedor.objects.filter(
                    devolucao__entrada_compra=purchase,
                    item_entrada_id=item_id,
                ).values_list('quantidade', flat=True),
            )
            for item_id in item_ids_set
        }
        for item_id, quantity in lines:
            item = purchase_items[item_id]
            eligible = item.quantidade - previous_returns[item_id]
            if quantity > eligible:
                raise ReversalOperationError(
                    f'A quantidade devolvida de {item.produto.nome} supera o saldo comprado.',
                )

        products = _locked_reversal_products(item.produto_id for item in purchase_items.values())
        for item_id, quantity in lines:
            product = products[purchase_items[item_id].produto_id]
            if quantity > product.quantidade:
                raise ReversalOperationError(f'Estoque insuficiente para {product.nome}.')

        movement = BaixaEstoque.objects.create(
            motivo='DEVOLUCAO',
            observacao=f'Devolução ao fornecedor da Entrada #{purchase.pk}',
            responsavel=actor,
        )
        operation = DevolucaoFornecedor.objects.create(
            entrada_compra=purchase,
            baixa=movement,
            responsavel=actor,
            motivo=motivo,
            chave_idempotencia=key,
            assinatura=signature,
        )
        for item_id, quantity in lines:
            item = purchase_items[item_id]
            product = products[item.produto_id]
            product.quantidade -= quantity
            product.save(update_fields=('quantidade',))
            ItemBaixaEstoque.objects.create(
                baixa=movement,
                produto=product,
                quantidade=quantity,
            )
            ItemDevolucaoFornecedor.objects.create(
                devolucao=operation,
                item_entrada=item,
                produto=product,
                quantidade=quantity,
            )
    return ReversalResult(operation, created=True)


def _require_reversal_manager(actor) -> None:
    if not (
        actor
        and actor.is_authenticated
        and actor.is_active
        and (actor.is_superuser or actor.cargo == 'G')
    ):
        raise ReversalOperationError('Apenas o gerente pode registrar reversões.')



def criar_baixa_venda(
    actor, lines: Sequence[tuple[int, Decimal]], products: Mapping[int, Produto],
) -> BaixaEstoque:
    # O chamador mantém a transação e os bloqueios durante a venda.
    for product_id, quantity in lines:
        product = products[product_id]
        if quantity > product.quantidade:
            raise StockOperationError(f'Estoque insuficiente para {product.nome}.')
    movement = BaixaEstoque.objects.create(
        motivo='VENDA', observacao='Venda local', responsavel=actor,
    )
    for product_id, quantity in lines:
        product = products[product_id]
        product.quantidade -= quantity
        product.save(update_fields=('quantidade',))
        ItemBaixaEstoque.objects.create(baixa=movement, produto=product, quantidade=quantity)
    return movement


def vincular_referencia_venda(movement: BaixaEstoque, sale_id: int) -> None:
    movement.observacao = f'Venda #{sale_id}'
    movement.save(update_fields=('observacao',))


def registrar_retorno_venda(
    actor, lines: Sequence[tuple[int, Decimal]], observation: str,
) -> EntradaEstoque:
    # Devoluções podem repor itens inativados após a venda.
    products = _locked_reversal_products(product_id for product_id, _ in lines)
    entry = EntradaEstoque.objects.create(
        motivo='DEVOLUCAO', observacao=observation, responsavel=actor,
    )
    for product_id, quantity in lines:
        product = products[product_id]
        product.quantidade += quantity
        product.save(update_fields=('quantidade',))
        ItemEntradaEstoque.objects.create(entrada=entry, produto=product, quantidade=quantity)
    return entry
