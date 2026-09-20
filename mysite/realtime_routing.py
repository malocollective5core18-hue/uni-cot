from django.urls import path

from mysite.realtime import RealtimeConsumer


websocket_urlpatterns = [
    path(
        't/<slug:tenant_slug>/<int:tenant_id>/<str:tenant_key>/ws/realtime/',
        RealtimeConsumer.as_asgi(),
    ),
]
