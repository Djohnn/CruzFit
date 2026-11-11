
from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.views import serve as serve_static
from django.http import HttpRequest, HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve as serve_media

from usuarios import views as usuario_views

urlpatterns = [
    path('', usuario_views.home, name='home'),
    path('admin/', admin.site.urls),
    path('auth/', include('usuarios.urls')),
    path('caixa/', include(('caixa.urls', 'caixa'), namespace='caixa')),
    path('vendas/', include(('vendas.urls', 'vendas'))),
    path('compras/', include(('compras.urls', 'compras'), namespace='compras')),
    path('financeiro/', include(('financeiro.urls', 'financeiro'), namespace='financeiro')),
    path('estoque/', include('estoque.urls'))
    
] 


def serve_local_media(request: HttpRequest, path: str) -> HttpResponse:
    """Serve a requested media file without enabling Django's debug mode."""
    return serve_media(
        request,
        path,
        document_root=settings.MEDIA_ROOT,
        show_indexes=False,
    )


urlpatterns += [
    re_path(r'^static/(?P<path>.*)$', serve_static, {'insecure': True}),
    re_path(r'^media/(?P<path>.*)$', serve_local_media),
]
