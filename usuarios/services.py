from __future__ import annotations

from django.contrib import auth
from django.db import transaction
from django.utils.text import slugify

from .models import Users


class UserOperationError(ValueError):
    """Erro de regra de negócio de contas locais."""


class AmbiguousIdentifierError(UserOperationError):
    """O identificador informado não aponta para uma única conta."""


@transaction.atomic
def criar_conta_principal(*, nome: str, senha: str) -> Users:
    nome = (nome or '').strip()
    if not nome:
        raise UserOperationError('Informe um nome para a conta.')
    if len(senha or '') < 6:
        raise UserOperationError('A senha deve ter pelo menos 6 caracteres.')
    if Users.objects.filter(first_name__iexact=nome).exists():
        raise UserOperationError('Já existe uma conta com este nome.')

    base_username = slugify(nome).replace('-', '_') or 'cruzfit'
    username = base_username
    suffix = 2
    while Users.objects.filter(username=username).exists():
        username = f'{base_username}_{suffix}'
        suffix += 1

    return Users.objects.create_user(
        username=username,
        first_name=nome,
        password=senha,
        cargo='G',
    )


def autenticar_por_identificador(request, identifier: str, password: str):
    normalized = (identifier or '').strip()
    if not normalized:
        return None

    username_matches = Users.objects.filter(username__iexact=normalized)
    if username_matches.count() > 1:
        raise AmbiguousIdentifierError('Use um identificador único para entrar.')
    if username_matches.exists():
        username = username_matches.get().username
        return auth.authenticate(request, username=username, password=password)

    name_matches = Users.objects.filter(first_name__iexact=normalized)
    name_count = name_matches.count()
    if name_count > 1:
        raise AmbiguousIdentifierError(
            'Use um identificador único (nome de usuário), pois esse nome não é único.'
        )
    if name_count == 0:
        return None
    return auth.authenticate(
        request,
        username=name_matches.get().username,
        password=password,
    )


@transaction.atomic
def criar_vendedor(*, actor, nome: str, sobrenome: str, email: str, senha: str) -> Users:
    if actor is None or not actor.is_authenticated or not actor.is_superuser:
        raise UserOperationError('Apenas o superusuário pode criar vendedores.')
    values = [nome.strip(), sobrenome.strip(), email.strip(), senha]
    if any(not value for value in values):
        raise UserOperationError('Preencha todos os campos obrigatórios do vendedor.')
    if Users.objects.filter(first_name__iexact=nome).exists():
        raise UserOperationError('Já existe uma conta com este nome.')
    if Users.objects.filter(email=email).exists():
        raise UserOperationError('Já existe um vendedor com este e-mail.')
    return Users.objects.create_user(
        username=email,
        last_name=sobrenome,
        first_name=nome,
        email=email,
        password=senha,
        cargo='V',
    )


@transaction.atomic
def excluir_usuario(*, actor, user_id: int) -> None:
    if actor is None or not actor.is_authenticated or not actor.is_superuser:
        raise UserOperationError('Apenas o superusuário pode excluir usuários.')
    try:
        user = Users.objects.select_for_update().get(pk=user_id)
    except Users.DoesNotExist as error:
        raise UserOperationError('Usuário não encontrado.') from error
    if user.pk == actor.pk:
        raise UserOperationError('A conta atual não pode ser excluída.')
    if user.is_staff or user.is_superuser:
        raise UserOperationError('Contas administrativas não podem ser excluídas.')
    if user.cargo == 'G' and Users.objects.filter(cargo='G').count() <= 1:
        raise UserOperationError('A última conta gerente não pode ser excluída.')
    user.delete()
