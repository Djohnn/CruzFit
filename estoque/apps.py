import copy
import sys

import django
from django.apps import AppConfig
from django.template.context import BaseContext


def _copy_base_context(context):
    """Use the Python 3.14-compatible implementation from Django 5.2."""
    duplicate = BaseContext()
    duplicate.__class__ = context.__class__
    duplicate.__dict__ = copy.copy(context.__dict__)
    duplicate.dicts = context.dicts[:]
    return duplicate


class EstoqueConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'estoque'

    def ready(self):
        if sys.version_info >= (3, 14) and django.VERSION < (5, 2):
            BaseContext.__copy__ = _copy_base_context
