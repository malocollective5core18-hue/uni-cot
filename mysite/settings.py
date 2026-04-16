"""
Django settings wrapper for mysite project.
Imports from inner settings module and applies production configurations.
"""

# Import all settings from the inner config module
from .mysite.settings import *  # noqa: F401,F403

# Override INSTALLED_APPS to use full module paths
# (needed because manage.py adds mysite/ to sys.path, making short names inaccessible)

COMMON_APPS_CORRECTED = [
    'mysite.customers',
    'mysite.core',
    'mysite.service',
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

SHARED_APPS_CORRECTED = [
    'django_tenants',
    *COMMON_APPS_CORRECTED,
]

# Re-evaluate INSTALLED_APPS with corrected app names
if USE_TENANT_INFRA:
    INSTALLED_APPS = SHARED_APPS_CORRECTED
else:
    INSTALLED_APPS = COMMON_APPS_CORRECTED

# Fix template context processor paths
TEMPLATES[0]['OPTIONS']['context_processors'] = [
    'django.template.context_processors.request',
    'django.contrib.auth.context_processors.auth',
    'django.contrib.messages.context_processors.messages',
    'mysite.core.context_processors.cloudinary_urls',
]
