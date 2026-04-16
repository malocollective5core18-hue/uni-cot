"""
WSGI config for mysite project.
Settings are loaded from mysite.settings which wraps mysite.mysite.settings.
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')

application = get_wsgi_application()
