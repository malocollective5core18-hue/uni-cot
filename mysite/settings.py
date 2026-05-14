"""
Django settings wrapper for mysite project.
Imports from inner settings module and applies production configurations.
"""

# Import all settings from the inner config module.
# This file must remain a thin wrapper; the concrete configuration lives in
# `mysite/mysite/settings.py`.
from .mysite.settings import *  # noqa: F401,F403

# Keep app lists explicit here so the outer settings module and the inner
# settings module agree on shared-vs-tenant placement when Django imports this
# wrapper in production.
COMMON_APPS_CORRECTED = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.postgres',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'cloudinary_storage',
    'cloudinary',
]

LOCAL_APPS_CORRECTED = [
    'service',
    'customers',
    'core',
    *COMMON_APPS_CORRECTED,
]

SHARED_APPS_CORRECTED = [
    'django_tenants',
    'service',
    'customers',
    *COMMON_APPS_CORRECTED,
]

TENANT_APPS_CORRECTED = [
    'service',
    'core',
]

COMMON_APPS = COMMON_APPS_CORRECTED
LOCAL_APPS = LOCAL_APPS_CORRECTED
SHARED_APPS = SHARED_APPS_CORRECTED
TENANT_APPS = TENANT_APPS_CORRECTED


def build_installed_apps(*app_groups):
    installed_apps = []
    seen_apps = set()
    for app_group in app_groups:
        for app in app_group:
            if app not in seen_apps:
                installed_apps.append(app)
                seen_apps.add(app)
    return installed_apps


# Re-evaluate app groups with corrected app names.
if USE_TENANT_INFRA:
    INSTALLED_APPS = build_installed_apps(SHARED_APPS_CORRECTED, TENANT_APPS_CORRECTED)
else:
    INSTALLED_APPS = build_installed_apps(LOCAL_APPS_CORRECTED)

# Fix template context processor paths after the wildcard import above.
TEMPLATES[0]['OPTIONS']['context_processors'] = [
    'django.template.context_processors.request',
    'django.contrib.auth.context_processors.auth',
    'django.contrib.messages.context_processors.messages',
    'mysite.core.context_processors.cloudinary_urls',
]
