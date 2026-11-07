from django.urls import path

from . import views

app_name = 'financeiro'

urlpatterns = [
    path('', views.painel, name='painel'),
    path('fechamentos/', views.fechamento_mensal, name='fechamento_mensal'),
    path(
        'fechamentos/<int:pk>/reabrir/',
        views.fechamento_reabrir,
        name='fechamento_reabrir',
    ),
    path('contas/nova/', views.conta_nova, name='conta_nova'),
    path('contas/<int:pk>/', views.conta_detalhe, name='conta_detalhe'),
    path('aportes/', views.aporte, name='aporte'),
    path('transferencias/', views.transferencia, name='transferencia'),
    path('estornos/<int:pk>/', views.estorno, name='estorno'),
    path('recebimentos/pix/', views.pix_confirmacao, name='pix_confirmacao'),
    path('recebimentos/cartao/', views.cartao_liquidacao, name='cartao_liquidacao'),
    path('contas-pagar/nova/', views.conta_pagar_nova, name='conta_pagar_nova'),
    path('contas-pagar/compras/', views.conta_pagar_compra, name='conta_pagar_compra'),
    path(
        'contas-pagar/<int:pk>/pagar/',
        views.conta_pagar_pagamento,
        name='conta_pagar_pagamento',
    ),
    path(
        'contas-pagar/pagamentos/<int:pk>/estornar/',
        views.conta_pagar_estorno,
        name='conta_pagar_estorno',
    ),
]
