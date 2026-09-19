"""Test-only settings overrides for isolated local test execution."""

from .settings import *  # noqa: F401,F403

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tests-isolated",
    },
}
