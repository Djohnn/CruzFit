import uuid
from functools import wraps
from itertools import zip_longest

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import BooleanField, Case, F, Value, When
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotFound,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from PIL import Image, UnidentifiedImageError
from rolepermissions.decorators import has_permission_decorator

from .barcodes import generate_ean13, render_ean13_svg
from .forms import ModeloEtiquetaForm, ProdutoEdicaoForm, ProdutoForm
from .fornecedores import fornecedores_ativos
from .models import (
    BaixaEstoque,
    Categoria,
    EntradaEstoque,
    Fornecedor,
    ModeloEtiqueta,
    Produto,
    ProdutoCadastro,
)
from .relatorios import (
    MovementFilters,
    ReportFilterError,
    movement_report,
    paginate_movements,
    parse_movement_filters,
    report_csv,
    sales_ranking,
    stock_report,
)
from .services import (
    ProductOperationError,
    ReversalOperationError,
    StockOperationError,
)
from .services import (
    cadastrar_produto as cadastrar_produto_service,
)
from .services import (
    corrigir_baixa as corrigir_baixa_service,
)
from .services import (
    devolver_fornecedor as devolver_fornecedor_service,
)
from .services import (
    editar_produto as editar_produto_service,
)
from .services import (
    inativar_produto as inativar_produto_service,
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
            return HttpResponseForbidden('Apenas o gerente pode administrar produtos.')
        return view_func(request, *args, **kwargs)

    return wrapped


def _selected_reversal_lines(request, item_field):
    """Keep optional zero rows out of the strict reversal service contract."""
    selected = []
    for item_id, quantity in zip_longest(
        request.POST.getlist(item_field),
        request.POST.getlist('quantidade'),
        fillvalue='',
    ):
        normalized = str(quantity).strip().replace(',', '.')
        if normalized in {'', '0', '0.0', '0.00'}:
            continue
        selected.append((item_id, quantity))
    return [item_id for item_id, _ in selected], [quantity for _, quantity in selected]


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
    if request.method == 'GET':
        form_data = request.session.pop('produto_form_data', {})
        form_data.setdefault('codigo_barras', generate_ean13(_existing_barcode_codes()))
        form_data.setdefault('chave_idempotencia', uuid.uuid4().hex)
        return render(
            request,
            'add_produto.html',
            {
                'categorias': Categoria.objects.all(),
                'form_data': form_data,
                'tamanho_choices': Produto.TAMANHO_CHOICES,
                'genero_choices': Produto.GENERO_CHOICES,
            },
        )

    if request.POST.get('acao') == 'cadastrar_categoria':
        titulo = (request.POST.get('titulo_categoria') or '').strip()
        if not titulo:
            return JsonResponse({'error': 'Informe o nome da categoria.'}, status=400)
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
        field: (request.POST.get(field) or '').strip()
        for field in (
            'nome',
            'modelo',
            'cor',
            'tecido',
            'codigo_barras',
            'motivo',
            'chave_idempotencia',
        )
    }
    form_data.update({
        field: request.POST.get(field) or ''
        for field in (
            'categoria',
            'tamanho',
            'genero',
            'quantidade',
            'preco_compra',
            'preco_venda',
            'estoque_minimo',
        )
    })
    previous_registration = None
    operation_key = form_data['chave_idempotencia']
    if operation_key:
        previous_registration = ProdutoCadastro.objects.filter(
            chave_idempotencia=operation_key,
        ).first()
        if previous_registration and not form_data['codigo_barras']:
            previous_data = previous_registration.dados_novos or {}
            previous_barcode = previous_data.get('codigo_barras')
            if previous_barcode:
                form_data['codigo_barras'] = str(previous_barcode)
    if not all(form_data.get(field) for field in ('nome', 'quantidade', 'preco_compra', 'preco_venda')):
        request.session['produto_form_data'] = form_data
        messages.error(request, 'Preencha os campos obrigatórios do produto.')
        return redirect(reverse('add_produto'))

    if not previous_registration and Produto.objects.filter(nome=form_data['nome']).exists():
        request.session['produto_form_data'] = form_data
        messages.error(request, 'Já existe um produto com esse nome.')
        return redirect(reverse('add_produto'))

    produto_form = ProdutoForm(
        data=form_data,
        instance=previous_registration.produto if previous_registration else None,
    )
    if not produto_form.is_valid():
        request.session['produto_form_data'] = form_data
        codigo_errors = produto_form.errors.get('codigo_barras')
        if codigo_errors:
            messages.error(request, f'Código de barras: {codigo_errors[0]}')
        else:
            field_name, field_errors = next(iter(produto_form.errors.items()))
            if field_name in {'tamanho', 'genero'}:
                messages.error(request, 'Selecione valores válidos para tamanho e gênero.')
            else:
                field = produto_form.fields.get(field_name)
                field_label = field.label if field else 'Produto'
                messages.error(request, f'{field_label}: {field_errors[0]}')
        return redirect(reverse('add_produto'))

    imagens = request.FILES.getlist('imagens')
    try:
        _validate_product_images(imagens)
        codigo_barras = produto_form.cleaned_data.get('codigo_barras')
        dados = dict(produto_form.cleaned_data)
        dados['codigo_barras'] = codigo_barras or generate_ean13(_existing_barcode_codes())
        result = cadastrar_produto_service(
            actor=request.user,
            dados=dados,
            reason=form_data['motivo'],
            idempotency_key=form_data['chave_idempotencia'],
            images=imagens,
        )
    except (ProductOperationError, ValueError) as error:
        request.session['produto_form_data'] = form_data
        messages.error(request, str(error))
        return redirect(reverse('add_produto'))

    messages.success(
        request,
        'Produto cadastrado com sucesso.' if result.created
        else 'Esse cadastro já foi registrado.',
    )
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
    reposicao = request.GET.get('reposicao') == '1'
    produtos = Produto.objects.select_related('categoria').annotate(
        reposicao_necessaria=Case(
            When(
                ativo=True,
                estoque_minimo__isnull=False,
                quantidade__lte=F('estoque_minimo'),
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
        tem_estoque_minimo=Case(
            When(estoque_minimo__isnull=False, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )
    if reposicao:
        produtos = produtos.filter(reposicao_necessaria=True)
    return render(
        request,
        'estoque.html',
        {'produtos': produtos, 'reposicao': reposicao},
    )


def _report_filter_context(filters: MovementFilters) -> dict[str, object]:
    return {
        'filtros': {
            'data_inicio': filters.data_inicio.isoformat() if filters.data_inicio else '',
            'data_fim': filters.data_fim.isoformat() if filters.data_fim else '',
            'produto': str(filters.produto_id or ''),
            'responsavel': str(filters.responsavel_id or ''),
            'motivo': filters.motivo,
            'fornecedor': str(filters.fornecedor_id or ''),
            'tipo': filters.tipo,
        },
        'query_string': filters.as_query_string(),
        'produtos_filtro': Produto.objects.order_by('nome', 'pk'),
        'responsaveis_filtro': get_user_model().objects.order_by(
            'first_name', 'username', 'pk'
        ),
        'fornecedores_filtro': Fornecedor.objects.order_by('nome', 'pk'),
    }


@login_required(login_url='login')
@require_GET
def movimentacoes(request):
    try:
        page_obj, filters = paginate_movements(request.GET)
    except ReportFilterError as error:
        messages.error(request, str(error))
        filters = MovementFilters()
        page_obj = Paginator([], 20).get_page(1)

    context = {
        **_report_filter_context(filters),
        'page_obj': page_obj,
        'movements': page_obj.object_list,
    }
    return render(request, 'movimentacoes.html', context)


@login_required(login_url='login')
@require_GET
def relatorios(request):
    report_type = request.GET.get('relatorio', 'saldo')
    if report_type not in {'saldo', 'reposicao', 'movimentacoes', 'ranking'}:
        messages.error(request, 'Selecione um relatório válido.')
        report_type = 'saldo'
    try:
        filters = parse_movement_filters(request.GET)
    except ReportFilterError as error:
        messages.error(request, str(error))
        filters = MovementFilters()

    context = {
        **_report_filter_context(filters),
        'relatorio': report_type,
        'saldo_report': stock_report(only_reorder=report_type == 'reposicao'),
        'movement_report': (
            movement_report(filters) if report_type == 'movimentacoes' else []
        ),
        'ranking_report': sales_ranking() if report_type == 'ranking' else [],
    }
    return render(request, 'relatorios.html', context)


@login_required(login_url='login')
@require_GET
def exportar_relatorio_csv(request):
    report_type = request.GET.get('relatorio', 'saldo')
    if report_type not in {'saldo', 'reposicao', 'movimentacoes', 'ranking'}:
        return HttpResponse('Relatório inválido.', status=400)
    try:
        filters = parse_movement_filters(request.GET)
    except ReportFilterError as error:
        return HttpResponse(str(error), status=400)

    response = HttpResponse(
        report_csv(report_type, filters),
        content_type='text/csv; charset=utf-8',
    )
    response['Content-Disposition'] = (
        f'attachment; filename="cruz-fit-{report_type}.csv"'
    )
    return response


@gerente_required
@require_http_methods(['GET', 'POST'])
def corrigir_baixa(request, pk):
    try:
        movement = BaixaEstoque.objects.prefetch_related('itens__produto').get(pk=pk)
    except BaixaEstoque.DoesNotExist:
        return HttpResponseNotFound('Baixa não encontrada.')

    if request.method == 'POST':
        try:
            result = corrigir_baixa_service(
                actor=request.user,
                baixa_id=movement.pk,
                reason=request.POST.get('motivo') or '',
                idempotency_key=request.POST.get('chave_idempotencia'),
            )
        except ReversalOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                'Baixa corrigida.' if result.created else 'Essa correção já foi registrada.',
            )
            return redirect('movimentacoes')
    return render(
        request,
        'corrigir_baixa.html',
        {'baixa': movement, 'chave_idempotencia': uuid.uuid4().hex},
    )


@gerente_required
@require_http_methods(['GET', 'POST'])
def devolver_fornecedor(request, pk):
    try:
        purchase = EntradaEstoque.objects.select_related('fornecedor').prefetch_related(
            'itens__produto',
        ).get(pk=pk)
    except EntradaEstoque.DoesNotExist:
        return HttpResponseNotFound('Entrada de compra não encontrada.')

    if request.method == 'POST':
        item_ids, quantities = _selected_reversal_lines(request, 'item_entrada')
        try:
            result = devolver_fornecedor_service(
                actor=request.user,
                entrada_id=purchase.pk,
                item_ids=item_ids,
                quantities=quantities,
                reason=request.POST.get('motivo') or '',
                idempotency_key=request.POST.get('chave_idempotencia'),
            )
        except ReversalOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                'Devolução ao fornecedor registrada.'
                if result.created
                else 'Essa devolução já foi registrada.',
            )
            return redirect('movimentacoes')
    return render(
        request,
        'devolver_fornecedor.html',
        {'entrada': purchase, 'chave_idempotencia': uuid.uuid4().hex},
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
            'produtos': Produto.objects.filter(ativo=True).order_by('nome'),
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
            'produtos': Produto.objects.filter(ativo=True).order_by('nome'),
            'fornecedores': fornecedores_ativos(),
            'fornecedor_selecionado': posted.get('fornecedor', ''),
            'motivo_selecionado': posted.get('motivo', 'COMPRA' if posted.get('fornecedor') else ''),
            'observacao': posted.get('observacao', ''),
            'linhas': linhas or [{'produto': '', 'quantidade': '1'}],
            'motivo_choices': EntradaEstoque.MOTIVO_CHOICES,
            'chave_idempotencia': posted.get('chave_idempotencia') or uuid.uuid4().hex,
            'historico': EntradaEstoque.objects.select_related('fornecedor').prefetch_related(
                'itens__produto',
                'devolucoes_fornecedor',
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


@gerente_required
@require_http_methods(['GET', 'POST'])
def editar_produto(request, slug):
    try:
        produto = Produto.objects.select_related('categoria').get(slug=slug)
    except Produto.DoesNotExist:
        return HttpResponseNotFound('Produto não encontrado.')

    if request.method == 'POST':
        form = ProdutoEdicaoForm(request.POST, request.FILES, instance=produto)
        operation_key = (request.POST.get('chave_idempotencia') or '').strip()
        reason = request.POST.get('motivo') or ''
        images = request.FILES.getlist('imagens')
        if form.is_valid():
            try:
                _validate_product_images(images)
                result = editar_produto_service(
                    actor=request.user,
                    product_id=produto.pk,
                    dados=form.cleaned_data,
                    reason=reason,
                    idempotency_key=operation_key,
                    images=images,
                )
            except (ProductOperationError, ValueError) as error:
                form.add_error(None, str(error))
            else:
                message = (
                    'Cadastro do produto atualizado.'
                    if result.created
                    else 'Essa edição já foi registrada.'
                )
                messages.success(request, message)
                return redirect('produto', slug=result.product.slug)
    else:
        form = ProdutoEdicaoForm(instance=produto)
        operation_key = uuid.uuid4().hex
        reason = ''

    return render(
        request,
        'editar_produto.html',
        {
            'produto': produto,
            'form': form,
            'motivo': reason,
            'chave_idempotencia': operation_key,
        },
    )


@gerente_required
@require_POST
def inativar_produto(request, slug):
    try:
        produto = Produto.objects.get(slug=slug)
    except Produto.DoesNotExist:
        return HttpResponseNotFound('Produto não encontrado.')

    try:
        result = inativar_produto_service(
            actor=request.user,
            product_id=produto.pk,
            reason=request.POST.get('motivo') or '',
            idempotency_key=request.POST.get('chave_idempotencia'),
        )
    except ProductOperationError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            'Produto inativado.' if result.created else 'Essa inativação já foi registrada.',
        )
    return redirect('produto', slug=produto.slug)


@login_required(login_url='login')
@require_GET
def produto(request, slug):
    try:
        produto = Produto.objects.select_related('categoria').get(slug=slug)
    except Produto.DoesNotExist:
        return HttpResponseNotFound('Produto não encontrado.')

    return render(
        request,
        'produto.html',
        {
            'produto': produto,
            'inativacao_chave': uuid.uuid4().hex,
        },
    )
