import os
import secrets

from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, JsonResponse


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def readyz(request):
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    if os.getenv("REDIS_URL"):
        probe_key = f"readyz:{secrets.token_urlsafe(12)}"
        try:
            cache.set(probe_key, "ok", timeout=10)
            if cache.get(probe_key) != "ok":
                raise RuntimeError("cache probe returned an unexpected value")
            cache.delete(probe_key)
            checks["cache"] = "ok"
        except Exception:
            checks["cache"] = "error"

    ready = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"ready": ready, "checks": checks},
        status=200 if ready else 503,
    )
