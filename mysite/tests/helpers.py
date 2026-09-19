from django.core.cache import cache


class CacheIsolationMixin:
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        super().setUp()
