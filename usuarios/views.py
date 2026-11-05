from functools import wraps

from django.contrib import auth, messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .dashboard import painel_do_dia
from .models import Users
from .services import (
    AmbiguousIdentifierError,
    UserOperationError,
    autenticar_por_identificador,
    criar_vendedor,
)
from .services import (
    excluir_usuario as excluir_usuario_service,
)


def superuser_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden('Apenas o superusuário pode administrar contas.')
        return view_func(request, *args, **kwargs)

    return wrapped


@login_required(login_url='login')
@superuser_required
@require_http_methods(['GET', 'POST'])
def cadastrar_vendedor(request):
    if request.method == 'GET':
        vendedores = Users.objects.filter(cargo='V')
        form_data = request.session.pop('vendedor_form_data', {})
        return render(
            request,
            'cadastrar_vendedor.html',
            {'vendedores': vendedores, 'form_data': form_data},
        )
    form_data = {
        'nome': (request.POST.get('nome') or '').strip(),
        'sobrenome': (request.POST.get('sobrenome') or '').strip(),
        'email': (request.POST.get('email') or '').strip(),
    }
    nome = form_data['nome']
    sobrenome = form_data['sobrenome']
    email = form_data['email']
    senha = request.POST.get('senha') or ''

    if any(not value or not value.strip() for value in (nome, sobrenome, email, senha)):
        request.session['vendedor_form_data'] = form_data
        messages.error(request, 'Preencha todos os campos obrigatórios do vendedor.')
        return redirect(reverse('cadastrar_vendedor'))

    try:
        criar_vendedor(
            actor=request.user,
            nome=nome,
            sobrenome=sobrenome,
            email=email,
            senha=senha,
        )
    except UserOperationError as error:
        request.session['vendedor_form_data'] = form_data
        messages.error(request, str(error))
        return redirect(reverse('cadastrar_vendedor'))

    messages.add_message(request, messages.SUCCESS, 'Vendedor cadastrado com sucesso!')
    return redirect(reverse('cadastrar_vendedor'))


@require_http_methods(['GET', 'POST'])
def login(request):
    if request.method == 'GET':
        if request.user.is_authenticated:
            return redirect(reverse('home'))
        nome = request.session.pop('login_name', '')
        return render(request, 'login.html', {'nome': nome})
    
    nome = (request.POST.get('identificador') or request.POST.get('nome') or '').strip()
    senha = request.POST.get('senha') or ''

    try:
        user = autenticar_por_identificador(request, nome, senha)
    except AmbiguousIdentifierError as error:
        request.session['login_name'] = nome
        messages.error(request, str(error))
        return redirect(reverse('login'))

    if not user:
        request.session['login_name'] = nome
        messages.error(request, 'Não foi possível entrar. Verifique seu nome e senha.')
        return redirect(reverse('login'))

    auth.login(request, user)
    return redirect(reverse('home'))


@login_required(login_url='login')
@require_GET
def home(request):
    return render(request, 'home.html', {"painel": painel_do_dia()})


@require_POST
def logout(request):
    auth.logout(request)
    return redirect(reverse('login'))


@login_required(login_url='login')
@superuser_required
@require_POST
def excluir_usuario(request, id):
    try:
        excluir_usuario_service(actor=request.user, user_id=id)
    except UserOperationError as error:
        if str(error) == 'Usuário não encontrado.':
            raise Http404(str(error))
        return HttpResponseForbidden(str(error))
    messages.add_message(request, messages.SUCCESS, 'Vendedor Excluido com sucesso!')
    return redirect(reverse('cadastrar_vendedor'))
