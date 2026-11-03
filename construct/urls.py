
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from usuarios import views as usuario_views

urlpatterns = [
    path('', usuario_views.home, name='home'),
    path('admin/', admin.site.urls),
    path('auth/', include('usuarios.urls')),
    path('vendas/', include(('vendas.urls', 'vendas'))),
    path('estoque/', include('estoque.urls'))
    
] 

urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
