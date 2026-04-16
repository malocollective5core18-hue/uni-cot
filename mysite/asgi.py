"""
ASGI config for mysite project.
Settings are loaded from mysite.settings which wraps mysite.mysite.settings.
"""

import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
mysite_root = project_root / 'mysite'
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(mysite_root) not in sys.path:
    sys.path.insert(1, str(mysite_root))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')

from django.core.asgi import get_asgi_application

application = get_asgi_application()
