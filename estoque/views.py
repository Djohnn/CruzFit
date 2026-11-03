from django.shortcuts import render, redirect
from django.db import transaction
from django.http import HttpResponseNotAllowed, HttpResponseNotFound, JsonResponse
from django.template.defaultfilters import slugify
from PIL import Image, ImageDraw, UnidentifiedImageError
from datetime import date
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
import sys
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from rolepermissions.decorators  import has_permission_decorator
from math import isfinite

from .models import (
    BaixaEstoque,
    Categoria,
    EntradaEstoque,
    Imagem,
    ItemBaixaEstoque,
    ItemEntradaEstoque,
    Produto,
)



@has_permission_decorator('cadastrar_produtos')
def add_produto(request):
    if request.method == "GET":
        categorias = Categoria.objects.all()
        form_data = request.session.pop('produto_form_data', {})
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
            quantidade = float(quantidade)
            preco_compra = float(preco_compra)
            preco_venda = float(preco_venda)
            if quantidade < 0 or preco_compra < 0 or preco_venda < 0:
                raise ValueError
        except (TypeError, ValueError):
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

        imagens = request.FILES.getlist('imagens')
        for imagem in imagens:
            try:
                Image.open(imagem).verify()
            except (UnidentifiedImageError, OSError):
                request.session['produto_form_data'] = form_data
                messages.error(request, 'Envie apenas imagens válidas.')
                return redirect(reverse('add_produto'))
            finally:
                imagem.seek(0)

        produto = Produto(nome=nome,
                          categoria_id=categoria or None,
                          tamanho=tamanho,
                          modelo=modelo,
                          cor=cor,
                          tecido=tecido,
                          genero=genero,
                          quantidade=quantidade,
                          preco_compra=preco_compra,
                          preco_venda=preco_venda)
        produto.save()
        
        for f in imagens:
            name = f'{date.today()}-{produto.id}.jpg'

            img = Image.open(f)
            img = img.convert('RGB')
            img = img.resize((300, 300))
            draw = ImageDraw.Draw(img)
            draw.text((20, 280), f"CRUZ FIT {date.today()}", (255, 255, 255))
            output = BytesIO()
            img.save(output, format="JPEG", quality=100)
            output.seek(0)
            img_final = InMemoryUploadedFile(
                output, 'ImageField', name, 'image/jpeg', sys.getsizeof(output), None
            )

            img_dj = Imagem(imagem=img_final, produto=produto)
            img_dj.save()
        messages.add_message(request, messages.SUCCESS, 'Produto cadastrado com sucesso')
        return redirect(reverse('add_produto'))


@login_required(login_url='login')
def estoque(request):
    produtos = Produto.objects.select_related('categoria').all()
    return render(
        request,
        'estoque.html',
        {'produtos': produtos},
    )


@login_required(login_url='login')
def registrar_baixa(request):
    if request.method == 'POST':
        produto_ids = request.POST.getlist('produto')
        quantidades = request.POST.getlist('quantidade')

        try:
            if not produto_ids or len(produto_ids) != len(quantidades):
                raise ValueError('Adicione pelo menos um produto à baixa.')

            linhas = []
            ids_selecionados = set()
            for produto_id, quantidade_bruta in zip(produto_ids, quantidades):
                produto_id = int(produto_id)
                quantidade = float(quantidade_bruta)
                if quantidade <= 0 or not isfinite(quantidade):
                    raise ValueError('Informe quantidades maiores que zero.')
                if produto_id in ids_selecionados:
                    raise ValueError('Selecione cada produto apenas uma vez.')
                ids_selecionados.add(produto_id)
                linhas.append((produto_id, quantidade))

            with transaction.atomic():
                produtos = {
                    produto.pk: produto
                    for produto in Produto.objects.select_for_update().filter(
                        pk__in=ids_selecionados,
                    )
                }
                if len(produtos) != len(ids_selecionados):
                    raise ValueError('Um dos produtos selecionados não existe.')

                for produto_id, quantidade in linhas:
                    produto = produtos[produto_id]
                    if quantidade > produto.quantidade:
                        raise ValueError(f'Estoque insuficiente para {produto.nome}.')

                baixa = BaixaEstoque.objects.create()
                for produto_id, quantidade in linhas:
                    produto = produtos[produto_id]
                    produto.quantidade -= quantidade
                    produto.save(update_fields=('quantidade',))
                    ItemBaixaEstoque.objects.create(
                        baixa=baixa,
                        produto=produto,
                        quantidade=quantidade,
                    )
        except (TypeError, ValueError, OverflowError) as error:
            messages.error(request, str(error))
            return redirect('registrar_baixa')

        messages.success(request, 'Baixa registrada com sucesso.')
        return redirect('registrar_baixa')

    return render(
        request,
        'registrar_baixa.html',
        {
            'produtos': Produto.objects.order_by('nome'),
            'historico': BaixaEstoque.objects.prefetch_related(
                'itens__produto',
            )[:30],
        },
    )


@login_required(login_url='login')
def registrar_entrada(request):
    if request.method == 'POST':
        produto_ids = request.POST.getlist('produto')
        quantidades = request.POST.getlist('quantidade')
        motivo = request.POST.get('motivo') or ''
        observacao = (request.POST.get('observacao') or '').strip()

        try:
            if not produto_ids or len(produto_ids) != len(quantidades):
                raise ValueError('Adicione pelo menos um produto à entrada.')
            if motivo not in dict(EntradaEstoque.MOTIVO_CHOICES):
                raise ValueError('Selecione um motivo válido para a entrada.')

            linhas = []
            ids_selecionados = set()
            for produto_id, quantidade_bruta in zip(produto_ids, quantidades):
                produto_id = int(produto_id)
                quantidade = float(quantidade_bruta)
                if quantidade <= 0 or not isfinite(quantidade):
                    raise ValueError('Informe quantidades maiores que zero.')
                if produto_id in ids_selecionados:
                    raise ValueError('Selecione cada produto apenas uma vez.')
                ids_selecionados.add(produto_id)
                linhas.append((produto_id, quantidade))

            with transaction.atomic():
                produtos = {
                    produto.pk: produto
                    for produto in Produto.objects.select_for_update().filter(
                        pk__in=ids_selecionados,
                    )
                }
                if len(produtos) != len(ids_selecionados):
                    raise ValueError('Um dos produtos selecionados não existe.')

                entrada = EntradaEstoque.objects.create(
                    motivo=motivo,
                    observacao=observacao,
                )
                for produto_id, quantidade in linhas:
                    produto = produtos[produto_id]
                    produto.quantidade += quantidade
                    produto.save(update_fields=('quantidade',))
                    ItemEntradaEstoque.objects.create(
                        entrada=entrada,
                        produto=produto,
                        quantidade=quantidade,
                    )
        except (TypeError, ValueError, OverflowError) as error:
            messages.error(request, str(error))
            return redirect('registrar_entrada')

        messages.success(request, 'Entrada registrada com sucesso.')
        return redirect('registrar_entrada')

    return render(
        request,
        'registrar_entrada.html',
        {
            'produtos': Produto.objects.order_by('nome'),
            'motivo_choices': EntradaEstoque.MOTIVO_CHOICES,
            'historico': EntradaEstoque.objects.prefetch_related(
                'itens__produto',
            )[:30],
        },
    )


@login_required(login_url='login')
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


def produto(request, slug):
    if request.method == "GET":
        try:
            produto = Produto.objects.select_related('categoria').get(slug=slug)
        except Produto.DoesNotExist:
            return HttpResponseNotFound('Produto não encontrado.')

        return render(request, 'produto.html', {'produto': produto})

    return HttpResponseNotAllowed(['GET'])
