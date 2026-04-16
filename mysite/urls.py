"""
URL configuration wrapper for mysite project.
Imports from inner URLs module and applies path corrections.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from service.views import system_demo

urlpatterns = [
    path('admin/', admin.site.urls),
    path('t/<slug:tenant_slug>/<int:tenant_id>/<str:tenant_key>/', include(('service.urls', 'service'), namespace='tenant_service')),
    path('t/<slug:tenant_slug>/<int:tenant_id>/<str:tenant_key>/', include('core.urls')),
    path('', include('core.urls')),
    path('system/', system_demo, name='system_demo'),
    path('service/', include(('service.urls', 'service'), namespace='service')),
]

# Serve static files during development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'static')
