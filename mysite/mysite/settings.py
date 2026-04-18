"""
Django settings for mysite project.

Multi-Tenant SaaS Configuration using django-tenants
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment variables from .env when available
load_dotenv()


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-%#x$$5qbu+g32@$*#1_yau@$m4%%=+%47kd5x2co$4&+hza545')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool('DJANGO_DEBUG', default=True)

ALLOWED_HOSTS = env_list(
    'DJANGO_ALLOWED_HOSTS',
    'localhost,.localhost,127.0.0.1,testserver,ring0.com,.ring0.com',
)
for local_host in ('localhost', '.localhost', '127.0.0.1', 'testserver'):
    if local_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(local_host)

CSRF_TRUSTED_ORIGINS = env_list(
    'DJANGO_CSRF_TRUSTED_ORIGINS',
    'http://localhost:8000,http://*.localhost:8000,https://ring0.com,https://*.ring0.com',
)

POSTGRES_SETTINGS = {
    'NAME': os.getenv('POSTGRES_DB'),
    'USER': os.getenv('POSTGRES_USER'),
    'PASSWORD': os.getenv('POSTGRES_PASSWORD'),
    'HOST': os.getenv('POSTGRES_HOST'),
    'PORT': os.getenv('POSTGRES_PORT', '5432'),
}
POSTGRES_CONFIGURED = all(POSTGRES_SETTINGS.values())
# Default to SQLite in debug/local mode so development does not depend on remote infra.
USE_TENANT_INFRA = env_bool(
    'DJANGO_USE_TENANT_INFRA',
    default=(not DEBUG and POSTGRES_CONFIGURED),
)


# ============================================================
# DJANGO-TENANTS CONFIGURATION
# ============================================================

COMMON_APPS = [
    'customers',
    'core',
    'service',
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

# SHARED APPS - These live in the public schema
SHARED_APPS = [
    'django_tenants',
    *COMMON_APPS,
]

# TENANT APPS - django-tenants requires this to be non-empty.
TENANT_APPS = [
    'service',
]

if USE_TENANT_INFRA:
    INSTALLED_APPS = SHARED_APPS
else:
    INSTALLED_APPS = COMMON_APPS


# ============================================================
# CLOUDINARY CONFIGURATION (For Static Images)
# ============================================================

import cloudinary
import cloudinary.uploader
import cloudinary.api

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET'),
)

# Use Cloudinary for media files (images)
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

# Cloudinary storage settings for static files
CLOUDINARY_STORAGE = {
    "CLOUDINARY_CLOUD_NAME": os.getenv('CLOUDINARY_CLOUD_NAME'),
    "CLOUDINARY_API_KEY": os.getenv('CLOUDINARY_API_KEY'),
    "CLOUDINARY_API_SECRET": os.getenv('CLOUDINARY_API_SECRET'),
    "STATIC_TAG": "ring0",
}


# ============================================================
# MIDDLEWARE - django-tenants middleware is REQUIRED
# ============================================================

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'mysite.middleware.SafeSessionMiddleware',
    'mysite.tenant_middleware.TenantMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if USE_TENANT_INFRA:
    MIDDLEWARE = [
        'django_tenants.middleware.main.TenantMainMiddleware',
        *MIDDLEWARE,
    ]


# ============================================================
# DATABASE - Must use django-tenants backend!
# ============================================================

if USE_TENANT_INFRA:
    DATABASES = {
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            **POSTGRES_SETTINGS,
            'OPTIONS': {
                'options': '-c client_encoding=UTF8',
                'sslmode': os.getenv('POSTGRES_SSLMODE', 'require'),
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'mysite' / 'db.sqlite3',
            'OPTIONS': {
                'timeout': 30,
            },
        }
    }


# ============================================================
# DJANGO-TENANTS ROUTERS - REQUIRED!
# ============================================================

DATABASE_ROUTERS = (
    ('django_tenants.routers.TenantSyncRouter',) if USE_TENANT_INFRA else ()
)


# ============================================================
# TENANT CONFIGURATION
# ============================================================

TENANT_MODEL = 'customers.CRTenant'


# ============================================================
# PRODUCTION SECURITY SETTINGS
# ============================================================

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
TENANT_DOMAIN_MODEL = 'customers.Domain'

if USE_TENANT_INFRA:
    # Schema that holds shared data (public schema)
    PUBLIC_SCHEMA_NAME = 'public'
    PUBLIC_SCHEMA_URLCONF = 'mysite.urls'
    SHOW_PUBLIC_IF_NO_TENANT_FOUND = True

    # Default tenant domain protocol
    DEFAULT_DOMAIN_PROTOCOL = 'https'

    # Auto-create schema when tenant is created
    TENANT_AUTO_CREATE_SCHEMA = True
    TENANT_AUTO_DROP_SCHEMA = False  # NEVER auto-drop in production!


# ============================================================
# OTHER SETTINGS
# ============================================================

ROOT_URLCONF = 'mysite.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
            BASE_DIR / 'mysite' / 'templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.csrf',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.cloudinary_urls',
            ],
        },
    },
]

WSGI_APPLICATION = 'mysite.wsgi.application'


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

STATICFILES_DIRS = [
    BASE_DIR / 'static',
]


# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Login URLs
LOGIN_URL = 'service:login'
LOGIN_REDIRECT_URL = 'service:owner_dashboard'
LOGOUT_REDIRECT_URL = 'service:welcome'


# Session settings
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7  # 1 week
SESSION_COOKIE_NAME = 'ring0_session'
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = 'Lax'


# CSRF settings
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_USE_SESSIONS = True
CSRF_COOKIE_SAMESITE = 'Lax'


# Security settings
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', default=not DEBUG)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = int(os.getenv('DJANGO_SECURE_HSTS_SECONDS', '31536000' if not DEBUG else '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', default=not DEBUG)
SECURE_HSTS_PRELOAD = env_bool('DJANGO_SECURE_HSTS_PRELOAD', default=False)


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'service': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'mysite': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
SECURE_HSTS_PRELOAD = env_bool('DJANGO_SECURE_HSTS_PRELOAD', default=False)
