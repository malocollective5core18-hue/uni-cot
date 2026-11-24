"""Test-only settings overrides for isolated local test execution."""

import os
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F401,F403


# Never let Django's test runner create/drop a test database on the runtime
# DATABASE_URL (which may be the production Supabase project). Tests must use
# the dedicated local PostgreSQL sandbox only.
_test_database_url = os.getenv("TEST_DATABASE_URL", "").strip()
if not _test_database_url:
    raise ImproperlyConfigured(
        "TEST_DATABASE_URL is required for manage.py test. Configure a dedicated "
        "PostgreSQL test database; the runtime DATABASE_URL is never used for tests."
    )

_runtime_database = DATABASES["default"]
_test_database = dj_database_url.parse(
    _test_database_url,
    engine="django_tenants.postgresql_backend",
    conn_max_age=0,
    ssl_require=(urlsplit(_test_database_url).hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"},
)
_runtime_target = (
    str(_runtime_database.get("HOST") or "").lower(),
    str(_runtime_database.get("PORT") or "5432"),
    str(_runtime_database.get("NAME") or ""),
)
_test_target = (
    str(_test_database.get("HOST") or "").lower(),
    str(_test_database.get("PORT") or "5432"),
    str(_test_database.get("NAME") or ""),
)
_test_host = _test_target[0]
if _test_host not in {"localhost", "127.0.0.1", "::1"}:
    raise ImproperlyConfigured(
        "TEST_DATABASE_URL must point to the local PostgreSQL sandbox; remote database hosts are rejected."
    )
if _test_target[2].lower() != "unicott_test":
    raise ImproperlyConfigured(
        "TEST_DATABASE_URL must use the dedicated 'unicott_test' database name."
    )
if _test_database.get("ENGINE") != "django_tenants.postgresql_backend":
    raise ImproperlyConfigured("TEST_DATABASE_URL must use the PostgreSQL django-tenants backend.")
if _test_target == _runtime_target:
    raise ImproperlyConfigured(
        "TEST_DATABASE_URL points to the runtime database target. Use an isolated PostgreSQL test server."
    )
_test_database.setdefault("OPTIONS", {}).setdefault("options", "-c client_encoding=UTF8")

DATABASES = {"default": _test_database}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tests-isolated",
    },
}

CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
}

# Tests render templates directly without a preceding collectstatic step.
# Production retains the manifest-backed WhiteNoise configuration in settings.py.
STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
