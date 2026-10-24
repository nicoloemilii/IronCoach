"""Read-only checks of the restarted local server and retained database."""
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urljoin, urlsplit
import hashlib
import json
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent
ORIGIN = 'http://127.0.0.1:8000'
responses = {}
for path in ('/ready', '/health/metrics', '/progress/recovery', '/dashboard/home',
             '/coach/today', '/activities/duplicates/summary', '/athlete/derived-profile',
             '/dashboard/training-response?lag=1', '/dashboard/training-response?lag=2'):
    with urlopen(ORIGIN + path, timeout=30) as response:
        assert response.status == 200
        responses[path] = json.load(response)
(QA / 'runtime-responses.json').write_text(json.dumps(responses, indent=2, default=str), encoding='utf-8')
for lag in (1, 2):
    body = responses[f'/dashboard/training-response?lag={lag}']
    assert body['version'] == 'observed-training-response-1' and body['lag_days'] == lag
    for metric in body['metrics']:
        for source in metric['series']:
            for row in source['rows']:
                assert row['response_date'] <= body['period']['as_of']
                if row['eligible']:
                    assert row['before_source'] == row['after_source'] == source['source']
                    assert source['source'] != 'local_estimate'

with urlopen(ORIGIN + '/app', timeout=10) as response:
    markup = response.read().decode('utf-8')
assets = []
for url in re.findall(r'(?:src|href)="([^"]+)"', markup):
    if not url.startswith('/'):
        continue
    local = ROOT / 'web' / urlsplit(url).path.rsplit('/', 1)[-1]
    if not local.is_file():
        continue
    with urlopen(urljoin(ORIGIN, url), timeout=10) as response:
        content = response.read()
    assert content == local.read_bytes(), url
    assets.append({'file': str(local.relative_to(ROOT)), 'sha256': hashlib.sha256(content).hexdigest()})

before = sqlite3.connect((QA / 'runtime-before-update.db').as_uri() + '?mode=ro', uri=True)
after = sqlite3.connect((ROOT / 'data/coach.db').as_uri() + '?mode=ro', uri=True)
tables = [row[0] for row in before.execute('SELECT name FROM sqlite_master WHERE type=?', ('table',))]
details = []
for name in tables:
    quoted = '"' + name.replace('"', '""') + '"'
    left = sorted(map(repr, before.execute('SELECT * FROM ' + quoted).fetchall()))
    right = sorted(map(repr, after.execute('SELECT * FROM ' + quoted).fetchall()))
    details.append({'table': name, 'before': len(left), 'after': len(right), 'identical': left == right})
before.close()
after.close()
preservation = {'tables': len(details), 'unchanged': all(row['identical'] for row in details), 'details': details}
(QA / 'runtime-data-preservation.json').write_text(json.dumps(preservation, indent=2), encoding='utf-8')
report = {'api_reads': len(responses), 'assets_verified': len(assets), 'assets': assets,
          'data_unchanged': preservation['unchanged'], 'database_tables': len(details),
          'ready': responses['/ready']}
(QA / 'runtime-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
assert report['data_unchanged']
print(json.dumps({key: value for key, value in report.items() if key != 'assets'}))
