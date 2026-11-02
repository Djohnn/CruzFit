from django.urls import path

from . import views

urlpatterns = [
    path('', views.estoque, name='estoque'),
    path('consulta/', views.consulta_produto, name='consulta_produto'),
    path('baixa/', views.registrar_baixa, name='registrar_baixa'),
    path('entrada/', views.registrar_entrada, name='registrar_entrada'),
    path('add_produto/', views.add_produto, name='add_produto'),
    path('codigo-barras/gerar/', views.gerar_codigo_barras, name='gerar_codigo_barras'),
    path('etiquetas/modelos/', views.modelos_etiqueta, name='modelos_etiqueta'),
    path('produto/<slug:slug>/etiquetas/', views.imprimir_etiquetas, name='imprimir_etiquetas'),
    path('produto/<slug:slug>', views.produto, name="produto"),
]
