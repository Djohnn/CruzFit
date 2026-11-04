from django.urls import path

from . import views

app_name = 'caixa'

urlpatterns = [
    path('', views.painel, name='painel'),
    path('abrir/', views.abrir, name='abrir'),
    path('suprimento/', views.suprimento, name='suprimento'),
    path('sangria/', views.sangria, name='sangria'),
    path('historico/', views.historico, name='historico'),
    path('fechar/', views.fechar, name='fechar'),
    path(
        'fechamentos/<int:pk>/',
        views.fechamento_detalhe,
        name='fechamento_detalhe',
    ),
    path(
        'restituicoes/cancelamento/<int:pending_id>/',
        views.restituir_cancelamento,
        name='restituir_cancelamento',
    ),
    path(
        'restituicoes/devolucao/<int:pending_id>/',
        views.restituir_devolucao,
        name='restituir_devolucao',
    ),
]
