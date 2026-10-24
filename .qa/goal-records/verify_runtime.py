"""Private read-only runtime verification; no scans or provider requests."""
from pathlib import Path
from urllib.request import urlopen
import hashlib
import json
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = 'http://127.0.0.1:8000'

def read(path):
    with urlopen(BASE + path, timeout=30) as response:
        return response.read()

html = read('/app')
assert html == (ROOT / 'web/index.html').read_bytes()
assets = []
for path in re.findall(r'(?:src|href)="(/web/[^"?]+)(?:\?[^" ]*)?"', html.decode()):
    if not path.endswith(('.css', '.js')):
        continue
    payload = read(path)
    assert payload == (ROOT / path.lstrip('/')).read_bytes(), path
    assets.append(dict(path=path, sha256=hashlib.sha256(payload).hexdigest()))
assert any(item['path'] == '/web/effort-records.js' for item in assets)
responses = {}
for path in ('/health', '/ready', '/athlete/effort-records',
             '/athlete/effort-records?period=last_90_days&sport=running',
             '/athlete/effort-records?period=last_28_days&sport=cycling',
             '/activities/diary?limit=50', '/athlete/derived-profile'):
    responses[path] = json.loads(read(path))
for path, value in responses.items():
    if not path.startswith('/athlete/effort-records'):
        continue
    assert len(value['records']) == 8
    assert value['active_scan'] is value['last_scan'] is None
    assert value['coverage']['indexed'] == 0
    assert value['coverage']['pending'] == value['coverage']['total']
with sqlite3.connect((HERE / 'runtime-before-restart.db').as_uri() + '?mode=ro', uri=True) as before, \
        sqlite3.connect((ROOT / 'data/coach.db').as_uri() + '?mode=ro', uri=True) as after:
    previous = [row[0] for row in before.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    current = {row[0] for row in after.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    changes = []
    for name in previous:
        quoted = '"' + name.replace('"', '""') + '"'
        left = sorted(map(repr, before.execute('SELECT * FROM ' + quoted).fetchall()))
        right = sorted(map(repr, after.execute('SELECT * FROM ' + quoted).fetchall()))
        if left != right:
            changes.append(dict(table=name, before=len(left), after=len(right)))
    assert not changes, changes
    assert current - set(previous) == {'effort_analyses', 'effort_scans'}
    assert after.execute('SELECT count(*) FROM effort_analyses').fetchone()[0] == 0
    assert after.execute('SELECT count(*) FROM effort_scans').fetchone()[0] == 0
report = dict(api_reads=len(responses), assets_verified=len(assets), assets=assets,
              previous_tables_compared=len(previous), changed_tables=changes,
              new_derived_tables_empty=True, personal_scan_started=False,
              visual_verification=False, browser_status='no_surfaces_available')
(HERE / 'runtime-responses.json').write_text(json.dumps(responses, indent=2), encoding='utf-8')
(HERE / 'runtime-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({key:value for key,value in report.items() if key != 'assets'}))
