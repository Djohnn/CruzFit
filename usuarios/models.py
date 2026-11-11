from django.contrib.auth.models import AbstractUser
from django.db import models


class Users(AbstractUser):
    choices_cargo = (('V', 'Vendedor'),
                     ('G', 'Gerente'),
                     ('C', 'Caixa'))
    cargo = models.CharField(max_length=1, choices=choices_cargo)


class LoginAttempt(models.Model):
    """Persisted failed-login bucket for one account identifier and origin."""

    identifier_key = models.CharField(max_length=64)
    source_key = models.CharField(max_length=64)
    failures = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('identifier_key', 'source_key'),
                name='usuarios_login_attempt_unique_origin',
            ),
        ]
