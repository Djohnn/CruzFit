from django.urls import path
from . import views


urlpatterns = [
    path('', views.estoque, name='estoque'),
    path('consulta/', views.consulta_produto, name='consulta_produto'),
    path('baixa/', views.registrar_baixa, name='registrar_baixa'),
    path('entrada/', views.registrar_entrada, name='registrar_entrada'),
    path('add_produto/', views.add_produto, name='add_produto'),
    path('produto/<slug:slug>', views.produto, name="produto"),
]
