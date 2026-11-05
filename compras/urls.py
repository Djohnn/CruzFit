from django.urls import path

from . import views

app_name = 'compras'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('novo/', views.novo, name='novo'),
    path('<int:pk>/', views.detalhe, name='detalhe'),
    path('<int:pk>/editar/', views.editar, name='editar'),
    path('<int:pk>/confirmar/', views.confirmar, name='confirmar'),
    path('<int:pk>/receber/', views.receber, name='receber'),
    path('<int:pk>/cancelar/', views.cancelar, name='cancelar'),
]
