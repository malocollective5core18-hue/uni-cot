"""Syntax-check inline timetable scripts rendered by Django using Node.js."""

from html.parser import HTMLParser
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(1, str(PROJECT_ROOT / 'mysite'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')

import django
from django.test import RequestFactory


class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag != 'script':
            return
        attributes = dict(attrs)
        if 'src' in attributes:
            self._current = None
            return
        script_type = attributes.get('type', '').lower()
        if script_type and script_type not in {'text/javascript', 'application/javascript', 'module'}:
            self._current = None
            return
        self._current = []

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self._current is not None:
            self.scripts.append(''.join(self._current))
            self._current = None


def main():
    django.setup()
    from core.views import timetable

    response = timetable(RequestFactory().get('/timetable/?demo=1'))
    parser = InlineScripts()
    parser.feed(response.content.decode('utf-8'))
    if not parser.scripts:
        raise SystemExit('No inline timetable JavaScript was rendered.')
    for index, script in enumerate(parser.scripts, start=1):
        result = subprocess.run(
            ['node', '--check'], input=script, text=True, capture_output=True,
        )
        if result.returncode:
            sys.stderr.write(f'Inline timetable script {index} failed syntax check:\n{result.stderr}')
            raise SystemExit(result.returncode)
    print(f'Node syntax check passed for {len(parser.scripts)} rendered inline timetable script(s).')


if __name__ == '__main__':
    main()
