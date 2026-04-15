from pathlib import Path
import sys

project_root = Path(__file__).resolve().parent.parent
django_root = project_root / 'mysite'
if str(django_root) not in sys.path:
    sys.path.insert(0, str(django_root))

from .mysite.wsgi import application
