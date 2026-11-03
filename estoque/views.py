import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps
from io import BytesIO
from itertools import zip_longest

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.http import HttpResponseForbidden, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods
from PIL import Image, ImageDraw, UnidentifiedImageError
from rolepermissions.decorators import has_permission_decorator

from .barcodes import generate_ean13, render_ean13_svg
from .forms import ModeloEtiquetaForm, ProdutoForm
from .fornecedores import fornecedores_ativos
from .models import (
    BaixaEstoque,
    Categoria,
    EntradaEstoque,
    Imagem,
    ModeloEtiqueta,
    Produto,
)
from .services import (
    StockOperationError,
)
from .services import (
    registrar_baixa as registrar_baixa_service,
)
from .services import (
    registrar_entrada as registrar_entrada_service,
)

MAX_PRODUCT_IMAGE_COUNT = 5
MAX_PRODUCT_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PRODUCT_IMAGE_DIMENSION = 4000
ALLOWED_PRODUCT_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}


def gerente_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f'{reverse("login")}?next={request.path}')
        if not (request.user.is_superuser or request.user.cargo == 'G'):
            return HttpResponseForbidden('Apenas o gerente pode administrar etiquetas.')
        return view_func(request, *args, **kwargs)

    return wrapped


def _validate_product_images(images):
    if len(images) > MAX_PRODUCT_IMAGE_COUNT:
        raise ValueError(f'Envie no máximo {MAX_PRODUCT_IMAGE_COUNT} imagens por produto.')

    for imagem in images:
        if imagem.size > MAX_PRODUCT_IMAGE_BYTES:
            raise ValueError('Cada imagem deve ter no máximo 5 MB.')
        try:
            with Image.open(imagem) as opened:
                if opened.format not in ALLOWED_PRODUCT_IMAGE_FORMATS:
                    raise ValueError('Envie imagens JPEG, PNG ou WEBP.')
                width, height = opened.size
                if (
                    width < 1
                    or height < 1
                    or width > MAX_PRODUCT_IMAGE_DIMENSION
                    or height > MAX_PRODUCT_IMAGE_DIMENSION
                ):
                    raise ValueError('As imagens devem ter no máximo 4000x4000 pixels.')
                opened.verify()
        except ValueError:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError('Envie apenas imagens válidas.') from error
        finally:
            imagem.seek(0)


def _existing_barcode_codes():
    return Produto.objects.values_list('codigo_barras', flat=True).exclude(
        codigo_barras__isnull=True
    ).exclude(codigo_barras='')



@login_required(login_url='login')
@has_permission_decorator('cadastrar_produtos')
@require_http_methods(['GET', 'POST'])
def add_produto(request):
    if request.method == "GET":
        categorias = Categoria.objects.all()
        form_data = request.session.pop('produto_form_data', {})
        if not form_data.get('codigo_barras'):
            form_data['codigo_barras'] = generate_ean13(_existing_barcode_codes())
        return render(
            request,
            'add_produto.html',
            {
                'categorias': categorias,
                'form_data': form_data,
                'tamanho_choices': Produto.TAMANHO_CHOICES,
                'genero_choices': Produto.GENERO_CHOICES,
            },
        )
    elif request.method == 'POST':
        if request.POST.get('acao') == 'cadastrar_categoria':
            titulo = (request.POST.get('titulo_categoria') or '').strip()
            if not titulo:
                return JsonResponse(
                    {'error': 'Informe o nome da categoria.'},
                    status=400,
                )
            if len(titulo) > 40:
                return JsonResponse(
                    {'error': 'O nome da categoria deve ter no máximo 40 caracteres.'},
                    status=400,
                )
            if Categoria.objects.filter(titulo__iexact=titulo).exists():
                return JsonResponse(
                    {'error': 'Essa categoria já está cadastrada.'},
                    status=400,
                )

            categoria_nova = Categoria.objects.create(titulo=titulo)
            return JsonResponse(
                {'id': categoria_nova.pk, 'titulo': categoria_nova.titulo},
                status=201,
            )

        form_data = {
            'nome': (request.POST.get('nome') or '').strip(),
            'categoria': request.POST.get('categoria') or '',
            'tamanho': request.POST.get('tamanho') or '',
            'modelo': (request.POST.get('modelo') or '').strip(),
            'cor': (request.POST.get('cor') or '').strip(),
            'tecido': (request.POST.get('tecido') or '').strip(),
            'genero': request.POST.get('genero') or '',
            'quantidade': request.POST.get('quantidade') or '',
            'preco_compra': request.POST.get('preco_compra') or '',
            'preco_venda': request.POST.get('preco_venda') or '',
            'codigo_barras': (request.POST.get('codigo_barras') or '').strip(),
        }
        nome = form_data['nome']
        categoria = form_data['categoria']
        tamanho = form_data['tamanho']
        modelo = form_data['modelo']
        cor = form_data['cor']
        tecido = form_data['tecido']
        genero = form_data['genero']
        quantidade = form_data['quantidade']
        preco_compra = form_data['preco_compra']
        preco_venda = form_data['preco_venda']

        if any(value in (None, '') for value in (nome, quantidade, preco_compra, preco_venda)):
            request.session['produto_form_data'] = form_data
            messages.error(request, 'Preencha os campos obrigatórios do produto.')
            return redirect(reverse('add_produto'))

        try:
            quantidade = Decimal(str(quantidade))
            preco_compra = Decimal(str(preco_compra))
            preco_venda = Decimal(str(preco_venda))
            if (
                not quantidade.is_finite()
                or not preco_compra.is_finite()
                or not preco_venda.is_finite()
                or quantidade < 0
                or preco_compra < 0
                or preco_venda < 0
            ):
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            request.session['produto_form_data'] = form_data
            messages.error(request, 'Use valores numéricos não negativos no produto.')
            return redirect(reverse('add_produto'))

        if categoria and not Categoria.objects.filter(pk=categoria).exists():
            request.session['produto_form_data'] = form_data
            messages.error(request, 'Selecione uma categoria válida.')
            return redirect(reverse('add_produto'))

        tamanhos_validos = {value for value, _ in Produto.TAMANHO_CHOICES}
        generos_validos = {value for value, _ in Produto.GENERO_CHOICES}
        if (tamanho and tamanho not in tamanhos_validos) or (genero and genero not in generos_validos):
            request.session['produto_form_data'] = form_data
            messages.error(request, 'Selecione valores válidos para tamanho e gênero.')
            return redirect(reverse('add_produto'))

        if Produto.objects.filter(nome=nome).exists() or Produto.objects.filter(slug=slugify(nome)).exists():
            request.session['produto_form_data'] = form_data
            messages.error(request, 'Já existe um produto com esse nome.')
            return redirect(reverse('add_produto'))

        produto_form = ProdutoForm(data=form_data)
        if not produto_form.is_valid():
            request.session['produto_form_data'] = form_data
            codigo_errors = produto_form.errors.get('codigo_barras')
            if codigo_errors:
                messages.error(request, f'Código de barras: {codigo_errors[0]}')
            else:
                field_name, field_errors = next(iter(produto_form.errors.items()))
                field = produto_form.fields.get(field_name)
                field_label = field.label if field else 'Produto'
                messages.error(request, f'{field_label}: {field_errors[0]}')
            return redirect(reverse('add_produto'))

        codigo_barras = produto_form.cleaned_data.get('codigo_barras')

        imagens = request.FILES.getlist('imagens')
        try:
            _validate_product_images(imagens)
        except ValueError as error:
            request.session['produto_form_data'] = form_data
            messages.error(request, str(error))
            return redirect(reverse('add_produto'))

        if not codigo_barras:
            codigo_barras = generate_ean13(_existing_barcode_codes())

        produto = Produto(nome=nome,
                          categoria_id=categoria or None,
                          tamanho=tamanho,
                          modelo=modelo,
                          cor=cor,
                          tecido=tecido,
                          genero=genero,
                          quantidade=quantidade,
                          preco_compra=preco_compra,
                           preco_venda=preco_venda,
                           codigo_barras=codigo_barras)
        try:
            produto.save()
        except ValidationError as error:
            request.session['produto_form_data'] = form_data
            messages.error(request, str(error))
            return redirect(reverse('add_produto'))
        
        for f in imagens:
            name = f'{date.today()}-{produto.id}.jpg'

            with Image.open(f) as opened:
                img = opened.convert('RGB')
            img = img.resize((300, 300))
            draw = ImageDraw.Draw(img)
            draw.text((20, 280), f"CRUZ FIT {date.today()}", (255, 255, 255))
            output = BytesIO()
            img.save(output, format="JPEG", quality=100)
            output.seek(0)
            img_final = InMemoryUploadedFile(
                output, 'ImageField', name, 'image/jpeg', output.getbuffer().nbytes, None
            )

            img_dj = Imagem(imagem=img_final, produto=produto)
            img_dj.save()
        messages.add_message(request, messages.SUCCESS, 'Produto cadastrado com sucesso')
        return redirect(reverse('add_produto'))


@login_required(login_url='login')
@has_permission_decorator('cadastrar_produtos')
@require_GET
def gerar_codigo_barras(request):
    code = generate_ean13(_existing_barcode_codes())
    return JsonResponse({'codigo_barras': code})


@login_required(login_url='login')
@gerente_required
@require_http_methods(['GET', 'POST'])
def modelos_etiqueta(request):
    form = ModeloEtiquetaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Modelo de etiqueta salvo com sucesso.')
        return redirect('modelos_etiqueta')

    return render(
        request,
        'modelos_etiqueta.html',
        {
            'form': form,
            'modelos': ModeloEtiqueta.objects.all(),
        },
    )


@login_required(login_url='login')
@require_GET
def imprimir_etiquetas(request, slug):
    try:
        produto = Produto.objects.select_related('categoria').get(slug=slug)
    except Produto.DoesNotExist:
        return HttpResponseNotFound('Produto não encontrado.')

    modelos = ModeloEtiqueta.objects.filter(ativo=True)
    modelo_param = (request.GET.get('modelo') or '').strip()
    if modelo_param:
        try:
            modelo = modelos.filter(pk=int(modelo_param)).first()
        except (TypeError, ValueError):
            modelo = None
    else:
        modelo = modelos.first()

    quantidade_param = request.GET.get('quantidade')
    quantidade = None
    if quantidade_param not in (None, ''):
        try:
            quantidade = max(1, min(500, int(quantidade_param)))
        except (TypeError, ValueError):
            quantidade = None

    etiquetas = []
    if produto.codigo_barras and modelo and quantidade:
        etiquetas = list(range(1, quantidade + 1))

    return render(
        request,
        'imprimir_etiquetas.html',
        {
            'produto': produto,
            'modelos': modelos,
            'modelo': modelo,
            'quantidade': quantidade,
            'etiquetas': etiquetas,
            'barcode_svg': render_ean13_svg(produto.codigo_barras)
            if produto.codigo_barras
            else '',
        },
    )


@login_required(login_url='login')
@require_GET
def estoque(request):
    produtos = Produto.objects.select_related('categoria').all()
    return render(
        request,
        'estoque.html',
        {'produtos': produtos},
    )


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def registrar_baixa(request):
    if request.method == 'POST':
        try:
            result = registrar_baixa_service(
                actor=request.user,
                product_ids=request.POST.getlist('produto'),
                quantities=request.POST.getlist('quantidade'),
                reason=(request.POST.get('motivo') or 'VENDA').strip(),
                observation=request.POST.get('observacao') or '',
                idempotency_key=request.POST.get('chave_idempotencia'),
            )
        except StockOperationError as error:
            messages.error(request, str(error))
            return redirect('registrar_baixa')

        if result.created:
            messages.success(request, 'Baixa registrada com sucesso.')
        else:
            messages.info(request, 'Essa baixa já foi registrada.')
        return redirect('registrar_baixa')

    return render(
        request,
        'registrar_baixa.html',
        {
            'produtos': Produto.objects.order_by('nome'),
            'motivo_choices': BaixaEstoque.MOTIVO_CHOICES,
            'chave_idempotencia': uuid.uuid4().hex,
            'historico': BaixaEstoque.objects.prefetch_related(
                'itens__produto',
            )[:30],
        },
    )


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def registrar_entrada(request):
    if request.method == 'POST':
        try:
            result = registrar_entrada_service(
                actor=request.user,
                product_ids=request.POST.getlist('produto'),
                quantities=request.POST.getlist('quantidade'),
                reason=request.POST.get('motivo') or '',
                observation=request.POST.get('observacao') or '',
                idempotency_key=request.POST.get('chave_idempotencia'),
                supplier_id=request.POST.get('fornecedor'),
            )
        except StockOperationError as error:
            messages.error(request, str(error))
        else:
            if result.created:
                messages.success(request, 'Entrada registrada com sucesso.')
            else:
                messages.info(request, 'Essa entrada já foi registrada.')
            return redirect('registrar_entrada')

    posted = request.POST if request.method == 'POST' else request.GET
    linhas = [
        {'produto': produto, 'quantidade': quantidade}
        for produto, quantidade in zip_longest(
            request.POST.getlist('produto'), request.POST.getlist('quantidade'),
            fillvalue='',
        )
    ] if request.method == 'POST' else [{'produto': '', 'quantidade': '1'}]

    return render(
        request,
        'registrar_entrada.html',
        {
            'produtos': Produto.objects.order_by('nome'),
            'fornecedores': fornecedores_ativos(),
            'fornecedor_selecionado': posted.get('fornecedor', ''),
            'motivo_selecionado': posted.get('motivo', 'COMPRA' if posted.get('fornecedor') else ''),
            'observacao': posted.get('observacao', ''),
            'linhas': linhas or [{'produto': '', 'quantidade': '1'}],
            'motivo_choices': EntradaEstoque.MOTIVO_CHOICES,
            'chave_idempotencia': posted.get('chave_idempotencia') or uuid.uuid4().hex,
            'historico': EntradaEstoque.objects.select_related('fornecedor').prefetch_related(
                'itens__produto',
            )[:30],
        },
    )


@login_required(login_url='login')
@require_GET
def consulta_produto(request):
    filtros = {
        'nome': request.GET.get('nome', ''),
        'categoria': request.GET.get('categoria', ''),
        'preco_min': request.GET.get('preco_min', ''),
        'preco_max': request.GET.get('preco_max', ''),
    }
    nome = filtros['nome']
    categoria = filtros['categoria']
    preco_min = filtros['preco_min']
    preco_max = filtros['preco_max']
    produtos = Produto.objects.none()
    consultando = bool(nome or categoria or preco_min or preco_max)

    if consultando:
        produtos = Produto.objects.select_related('categoria').all()
        try:
            preco_min = float(preco_min) if preco_min else 0
            preco_max = float(preco_max) if preco_max else 9999999
            if preco_min < 0 or preco_max < 0 or preco_min > preco_max:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, 'Informe uma faixa de preços válida.')
            return redirect(reverse('consulta_produto'))

        if nome:
            produtos = produtos.filter(nome__icontains=nome)

        if categoria:
            produtos = produtos.filter(categoria=categoria)

        produtos = produtos.filter(
            preco_venda__gte=preco_min,
            preco_venda__lte=preco_max,
        )

    return render(
        request,
        'consulta_produto.html',
        {
            'categorias': Categoria.objects.all(),
            'produtos': produtos,
            'filtros': filtros,
            'consultando': consultando,
        },
    )


@login_required(login_url='login')
@require_GET
def produto(request, slug):
    try:
        produto = Produto.objects.select_related('categoria').get(slug=slug)
    except Produto.DoesNotExist:
        return HttpResponseNotFound('Produto não encontrado.')

    return render(request, 'produto.html', {'produto': produto})
