from django.shortcuts import render
from rolepermissions.decorators import has_permission_decorator
from .models import Users
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.contrib import auth
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST



@has_permission_decorator('cadastrar_vendedor')
def cadastrar_vendedor(request):
    if request.method == 'GET':
        vendedores = Users.objects.filter(cargo='V')
        form_data = request.session.pop('vendedor_form_data', {})
        return render(
            request,
            'cadastrar_vendedor.html',
            {'vendedores': vendedores, 'form_data': form_data},
        )
    if request.method == 'POST':
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

        user = Users.objects.filter(email=email)
        
        if user.exists():
            request.session['vendedor_form_data'] = form_data
            messages.error(request, 'Já existe um vendedor com este e-mail.')
            return redirect(reverse('cadastrar_vendedor'))
        
        user = Users.objects.create_user(username=email,
                                         last_name=sobrenome,
                                         first_name=nome,
                                         email=email,
                                         password=senha,
                                         cargo='V')
       
        messages.add_message(request, messages.SUCCESS, 'Vendedor cadastrado com sucesso!')
        return redirect(reverse('cadastrar_vendedor'))


def login(request):
    if request.method == 'GET':
        if request.user.is_authenticated:
            return redirect(reverse('home'))
        nome = request.session.pop('login_name', '')
        return render(request, 'login.html', {'nome': nome})
    
    elif request.method == 'POST':
        nome = (request.POST.get('nome') or '').strip()
        senha = request.POST.get('senha') or ''

        user = auth.authenticate(username=nome, password=senha)
        if not user and nome:
            user_by_name = Users.objects.filter(first_name__iexact=nome).first()
            if user_by_name:
                user = auth.authenticate(username=user_by_name.username, password=senha)

        if not user:
            request.session['login_name'] = nome
            messages.error(request, 'Não foi possível entrar. Verifique seu nome e senha.')
            return redirect(reverse('login'))
        
        auth.login(request, user)
        return redirect(reverse('home'))


@login_required(login_url='login')
def home(request):
    return render(request, 'home.html')
    
def logout(request):
    request.session.flush()
    return redirect(reverse(login))

@has_permission_decorator('cadastrar_vendedor')
@require_POST
def excluir_usuario(request, id):
    vendedor = get_object_or_404(Users, id=id)
    vendedor.delete()
    messages.add_message(request, messages.SUCCESS, 'Vendedor Excluido com sucesso!')
    return redirect(reverse('cadastrar_vendedor'))
