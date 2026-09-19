from django.core.cache import cache
from django.test import Client


class SecureClient(Client):
    def generic(self, method, path, data="", content_type="application/octet-stream", secure=False, **extra):
        """Exercise production HTTPS redirects without weakening settings."""
        return super().generic(
            method,
            path,
            data=data,
            content_type=content_type,
            secure=True,
            **extra,
        )


class CacheIsolationMixin:
    client_class = SecureClient

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        super().setUp()
