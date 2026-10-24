"""Verify live routes/assets and additive migration without altering user data."""
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlsplit
import hashlib
import json
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ORIGIN = 'http://127.0.0.1:8000'
responses = {}
for path in ('/ready', '/health/metrics', '/progress/recovery', '/dashboard/home', '/coach/today',
             '/activities/duplicates/summary', '/activities/duplicates/pending',
             '/activities/duplicates/review-history?limit=20', '/athlete/derived-profile',
             '/dashboard/training-response?lag=1', '/dashboard/training-response?lag=2'):
    with urlopen(ORIGIN + path, timeout=30) as response:
        assert response.status == 200
        responses[path] = json.load(response)
(HERE / 'runtime-responses.json').write_text(json.dumps(responses, indent=2), encoding='utf-8')
with urlopen(ORIGIN + '/app') as response:
    markup = response.read().decode('utf-8')
assets = []
for url in re.findall(r'(?:src|href)="([^"]+)"', markup):
    local = ROOT / 'web' / urlsplit(url).path.rsplit('/', 1)[-1]
    if not url.startswith('/web/') or not local.is_file():
        continue
    with urlopen(ORIGIN + url) as response:
        raw = response.read()
    assert raw == local.read_bytes(), url
    assets.append({'path': str(local.relative_to(ROOT)), 'sha256': hashlib.sha256(raw).hexdigest()})
assert any(row['path'].replace('\\','/') == 'web/duplicate-review.js' for row in assets)
before = sqlite3.connect((HERE / 'runtime-before-update.db').as_uri() + '?mode=ro', uri=True)
after = sqlite3.connect((ROOT / 'data/coach.db').as_uri() + '?mode=ro', uri=True)
names = lambda db: {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
old_names, new_names = names(before), names(after)
assert old_names <= new_names
assert new_names - old_names <= {'duplicate_reviews', 'sqlite_sequence'}
details = []
for name in sorted(old_names):
    quoted = '"' + name.replace('"','""') + '"'
    left = sorted(map(repr, before.execute('SELECT * FROM ' + quoted).fetchall()))
    right = sorted(map(repr, after.execute('SELECT * FROM ' + quoted).fetchall()))
    if name == 'iron_schema_versions':
        left_versions = dict(before.execute('SELECT version,applied_at FROM iron_schema_versions'))
        right_versions = dict(after.execute('SELECT version,applied_at FROM iron_schema_versions'))
        valid = all(right_versions[key] == value for key, value in left_versions.items()) and set(right_versions) == {1,2,3}
    else:
        valid = left == right
    assert valid, name
    details.append({'table': name, 'before': len(left), 'after': len(right), 'identical': left == right, 'valid': valid})
assert after.execute('SELECT COUNT(*) FROM duplicate_reviews').fetchone()[0] == 0
report = {'api_reads': len(responses), 'assets_verified': len(assets), 'assets': assets,
          'old_tables': len(details), 'new_tables': sorted(new_names-old_names), 'personal_data_preserved': True,
          'schema_versions': sorted(right_versions), 'details': details, 'ready': responses['/ready']}
before.close(); after.close()
(HERE / 'runtime-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({key: value for key, value in report.items() if key not in ('assets','details')}))
