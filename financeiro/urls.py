from django.urls import path

from . import views

app_name = 'financeiro'

urlpatterns = [
    path('', views.painel, name='painel'),
    path('contas/nova/', views.conta_nova, name='conta_nova'),
    path('contas/<int:pk>/', views.conta_detalhe, name='conta_detalhe'),
    path('aportes/', views.aporte, name='aporte'),
    path('transferencias/', views.transferencia, name='transferencia'),
    path('estornos/<int:pk>/', views.estorno, name='estorno'),
    path('recebimentos/pix/', views.pix_confirmacao, name='pix_confirmacao'),
    path('recebimentos/cartao/', views.cartao_liquidacao, name='cartao_liquidacao'),
]
