from __future__ import annotations

import ipaddress
import sqlite3
import time
from datetime import timedelta

from django.conf import settings
from django.contrib import auth
from django.db import OperationalError, connections, models, transaction
from django.db.models import F, Value, When
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.text import slugify

from .models import LoginAttempt, Users

LOGIN_FAILURE_LIMIT = 5
LOCKOUT_DURATION = timedelta(minutes=10)
LOGIN_FAILURE_WINDOW = LOCKOUT_DURATION


class UserOperationError(ValueError):
    """Erro de regra de negócio de contas locais."""


class AmbiguousIdentifierError(UserOperationError):
    """O identificador informado não aponta para uma única conta."""


def _digest(value: str) -> str:
    return salted_hmac(
        'usuarios.login-rate-limit',
        value,
        secret=settings.SECRET_KEY,
        algorithm='sha256',
    ).hexdigest()


def _identifier_key(identifier: str) -> str:
    normalized = (identifier or '').strip()
    if not normalized:
        return _digest('identifier:')

    username_matches = Users.objects.filter(username__iexact=normalized)
    if username_matches.count() == 1:
        user = username_matches.only('pk').first()
        return _digest(f'user:{user.pk}')
    if username_matches.count() == 0:
        name_matches = Users.objects.filter(first_name__iexact=normalized)
        if name_matches.count() == 1:
            user = name_matches.only('pk').first()
            return _digest(f'user:{user.pk}')
    return _digest(f'identifier:{normalized.casefold()}')


def _source_key(request) -> str:
    # X-Forwarded-For is intentionally ignored without a trusted-proxy contract.
    remote_addr = (request.META.get('REMOTE_ADDR') or 'unknown').strip()
    try:
        remote_addr = str(ipaddress.ip_address(remote_addr))
    except ValueError:
        pass
    return _digest(f'origin:{remote_addr}')


def _attempt_filter(request, identifier: str) -> dict[str, str]:
    return {
        'identifier_key': _identifier_key(identifier),
        'source_key': _source_key(request),
    }


def login_bloqueado(request, identifier: str) -> bool:
    attempt = LoginAttempt.objects.filter(**_attempt_filter(request, identifier)).only(
        'locked_until'
    ).first()
    return bool(attempt and attempt.locked_until and attempt.locked_until > timezone.now())


def _registrar_falha_login_atomically(request, identifier: str) -> None:
    keys = _attempt_filter(request, identifier)
    now = timezone.now()
    window_start = now - LOGIN_FAILURE_WINDOW
    with transaction.atomic():
        attempt, _ = LoginAttempt.objects.get_or_create(**keys)
        LoginAttempt.objects.filter(pk=attempt.pk).update(
            failures=models.Case(
                When(locked_until__gt=now, then=F('failures')),
                When(locked_until__lte=now, then=Value(1)),
                When(updated_at__lte=window_start, then=Value(1)),
                default=F('failures') + Value(1),
                output_field=models.PositiveIntegerField(),
            ),
            locked_until=models.Case(
                When(locked_until__gt=now, then=F('locked_until')),
                When(locked_until__lte=now, then=Value(None)),
                When(updated_at__lte=window_start, then=Value(None)),
                When(failures__gte=LOGIN_FAILURE_LIMIT - 1, then=Value(now + LOCKOUT_DURATION)),
                default=F('locked_until'),
                output_field=models.DateTimeField(),
            ),
            updated_at=now,
        )
        LoginAttempt.objects.filter(updated_at__lte=window_start).delete()


def _is_recoverable_sqlite_lock(error: OperationalError) -> bool:
    if connections['default'].vendor != 'sqlite':
        return False
    cause = error.__cause__
    while cause is not None:
        if isinstance(cause, sqlite3.Error):
            code = getattr(cause, 'sqlite_errorcode', None)
            if isinstance(code, int):
                return code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
            name = getattr(cause, 'sqlite_errorname', None)
            return isinstance(name, str) and name.startswith(('SQLITE_BUSY', 'SQLITE_LOCKED'))
        cause = cause.__cause__
    return False


def registrar_falha_login(request, identifier: str) -> None:
    # SQLite can report BUSY while separate local workers update this bucket.
    for attempt_number in range(5):
        try:
            _registrar_falha_login_atomically(request, identifier)
            return
        except OperationalError as error:
            if not _is_recoverable_sqlite_lock(error) or attempt_number == 4:
                raise
            time.sleep(0.01 * (attempt_number + 1))

@transaction.atomic
def limpar_falhas_login(request, identifier: str) -> None:
    LoginAttempt.objects.filter(**_attempt_filter(request, identifier)).delete()


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
