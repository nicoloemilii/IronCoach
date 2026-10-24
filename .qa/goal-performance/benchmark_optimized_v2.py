"""Compare current code to cached synthetic baseline bytes without rerunning it."""
from datetime import datetime, timedelta
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.environ['IRON_DATA_DIR'] = str(QA / 'isolated-optimized-data')
os.environ['IRON_AUTO_SYNC'] = '0'

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from app.db.database import Base
from app.db.iron_models import HealthMetric
from app.services import activity_rating

NOW = datetime(2026, 10, 22, 12)
SOURCES = ('app/services/activity_rating.py', 'app/services/rating_history.py')
SOURCE_SHA = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
BASELINE = json.loads((QA / 'baseline_timings.json').read_text(encoding='utf-8'))
BASELINE_FILE_SHA = hashlib.sha256((QA / 'baseline_timings.json').read_bytes()).hexdigest()


def fixtures(count, density):
    # This fixture is copied verbatim from the baseline script; that script is
    # intentionally neither imported nor executed by this verification.
    end = NOW - timedelta(hours=1)
    span = 35 * 86400 if density == 'dense_35_days' else (count - 1) * 43200
    sports = ('cycling', 'running', 'swimming', 'weighttraining')
    rows = [SimpleNamespace(id=index + 1, sport=sports[index % 4],
        start_time=end - timedelta(seconds=(count - index - 1) * span // max(1, count - 1)),
        duration_seconds=1800 + index % 11 * 120, avg_hr=130 + index % 9,
        avg_power=120 + index % 17, normalized_power=150 + index % 13) for index in range(count)]
    loads = {row.id: 30 + index % 17 * 2.5 for index, row in enumerate(rows)}
    first = rows[0].start_time.date() - timedelta(days=29)
    last = end.date() - timedelta(days=1)
    metrics = []
    day = first
    while day <= last:
        for kind, value in [('tsb', -5), ('sleep_hours', 8), ('hrv_ms', 50), ('resting_hr_bpm', 55)]:
            metrics.append(dict(date=str(day), kind=kind, value=value, source='intervals', unit='synthetic',
                observed_at=str(day), synced_at=str(day)))
        day += timedelta(days=1)
    return rows, loads, metrics


def first_diff(expected, actual, path='$'):
    if type(expected) is not type(actual):
        return dict(path=path, expected_type=type(expected).__name__, actual_type=type(actual).__name__)
    if isinstance(expected, dict):
        if set(expected) != set(actual):
            return dict(path=path, expected_keys=sorted(expected), actual_keys=sorted(actual))
        for key in sorted(expected):
            changed = first_diff(expected[key], actual[key], path + '.' + str(key))
            if changed is not None:
                return changed
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return dict(path=path, expected_length=len(expected), actual_length=len(actual))
        for index, (left, right) in enumerate(zip(expected, actual)):
            changed = first_diff(left, right, path + '[' + str(index) + ']')
            if changed is not None:
                return changed
    elif expected != actual:
        return dict(path=path, expected=str(expected)[:300], actual=str(actual)[:300])
    return None


metadata = dict(source_sha256=SOURCE_SHA, baseline_metadata_sha256=BASELINE_FILE_SHA,
    python=platform.python_version(), platform=platform.platform(), now=NOW.isoformat(), scenarios=[],
    notes=['Same isolated fixtures and timer scope as baseline.',
           'Cached baseline gzip is decompressed and its SHA is checked before comparison.',
           'Exact canonical JSON byte equality is required, including all text, counts and scores.',
           'No production edits, personal DB, provider, browser or app.main access.',
           'Timings are local observations, not an SLA or universal complexity bound.'])

for baseline in BASELINE['scenarios']:
    density = baseline['density']; count = baseline['activities']
    expected_payload = gzip.decompress((QA / baseline['compressed_output']).read_bytes())
    assert hashlib.sha256(expected_payload).hexdigest() == baseline['output_sha256'], 'Cached baseline failed integrity check.'
    rows, loads, health = fixtures(count, density)
    assert len(health) == baseline['health_rows']
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.bulk_insert_mappings(HealthMetric, health); db.commit()
        statements = []
        def capture(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith('SELECT'):
                statements.append(statement)
        event.listen(engine, 'before_cursor_execute', capture)
        started = perf_counter()
        result = activity_rating.activity_ratings(db, rows, loads=loads, now=NOW)
        duration = perf_counter() - started
        event.remove(engine, 'before_cursor_execute', capture)
    engine.dispose()
    payload = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    exact = payload == expected_payload
    scenario = dict(density=density, activities=count, health_rows=len(health), seconds=round(duration, 6),
        baseline_seconds=baseline['seconds'], speedup=round(baseline['seconds'] / duration, 3),
        select_queries=len(statements), output_sha256=hashlib.sha256(payload).hexdigest(),
        baseline_output_sha256=baseline['output_sha256'], exact_json_equal=exact,
        output_json_bytes=len(payload))
    if not exact:
        scenario['first_difference'] = first_diff(json.loads(expected_payload), json.loads(payload))
    metadata['scenarios'].append(scenario)
    (QA / 'optimized_timings_v2.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps(scenario), flush=True)

metadata['source_unchanged'] = SOURCE_SHA == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
metadata['baseline_metadata_unchanged'] = BASELINE_FILE_SHA == hashlib.sha256((QA / 'baseline_timings.json').read_bytes()).hexdigest()
metadata['all_exact'] = all(item['exact_json_equal'] for item in metadata['scenarios'])
(QA / 'optimized_timings_v2.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
assert metadata['source_unchanged'], 'Production source changed during benchmark.'
assert metadata['baseline_metadata_unchanged'], 'Baseline metadata changed during benchmark.'
assert metadata['all_exact'], 'Optimized output differs from cached baseline.'
print(json.dumps({'report': str(QA / 'optimized_timings_v2.json'), 'all_exact': metadata['all_exact']}), flush=True)
