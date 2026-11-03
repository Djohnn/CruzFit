from django.urls import path

from . import fornecedor_views, views

urlpatterns = [
    path('fornecedores/', fornecedor_views.listar, name='fornecedores'),
    path('fornecedores/novo/', fornecedor_views.formulario, name='fornecedor_novo'),
    path('fornecedores/<int:pk>/', fornecedor_views.detalhe, name='fornecedor_detalhe'),
    path('fornecedores/<int:pk>/editar/', fornecedor_views.formulario,
         name='fornecedor_editar'),
    path('fornecedores/<int:pk>/inativar/', fornecedor_views.inativar,
         name='fornecedor_inativar'),
    path('', views.estoque, name='estoque'),
    path('movimentacoes/', views.movimentacoes, name='movimentacoes'),
    path('relatorios/', views.relatorios, name='relatorios'),
    path('relatorios/exportar.csv', views.exportar_relatorio_csv,
         name='exportar_relatorio_csv'),
    path('consulta/', views.consulta_produto, name='consulta_produto'),
    path('baixa/', views.registrar_baixa, name='registrar_baixa'),
    path('entrada/', views.registrar_entrada, name='registrar_entrada'),
    path('add_produto/', views.add_produto, name='add_produto'),
    path('codigo-barras/gerar/', views.gerar_codigo_barras, name='gerar_codigo_barras'),
    path('etiquetas/modelos/', views.modelos_etiqueta, name='modelos_etiqueta'),
    path('produto/<slug:slug>/etiquetas/', views.imprimir_etiquetas, name='imprimir_etiquetas'),
    path('produto/<slug:slug>/editar/', views.editar_produto, name='editar_produto'),
    path('produto/<slug:slug>/inativar/', views.inativar_produto, name='inativar_produto'),
    path('produto/<slug:slug>', views.produto, name="produto"),
]
