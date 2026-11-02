from django.contrib.auth.models import AbstractUser
from django.db import models


class Users(AbstractUser):
    choices_cargo = (('V', 'Vendedor'),
                     ('G', 'Gerente'),
                     ('C', 'Caixa'))
    cargo = models.CharField(max_length=1, choices=choices_cargo)
