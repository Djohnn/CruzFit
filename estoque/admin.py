from django.contrib import admin

from .models import Categoria, Produto

admin.site.register(Produto)
admin.site.register(Categoria)
