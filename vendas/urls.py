from django.urls import path

from . import troca_views, views

urlpatterns = [
    path('', views.vendas, name='vendas'),
    path('nova/', views.registrar_venda, name='registrar_venda'),
    path('<int:pk>/', views.venda_detalhe, name='venda_detalhe'),
    path('<int:pk>/comprovante/', views.venda_comprovante,
         name='venda_comprovante'),
    path('<int:pk>/cancelar/', views.cancelar_venda, name='cancelar_venda'),
    path('<int:pk>/devolver/', views.devolver_venda, name='devolver_venda'),
    path('<int:pk>/trocar/', troca_views.trocar_venda, name='trocar_venda'),
]
