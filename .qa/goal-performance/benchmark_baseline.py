"""Synthetic, isolated baseline timings and exact output digests. No app.main."""
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
os.environ['IRON_DATA_DIR'] = str(QA / 'isolated-data')
os.environ['IRON_AUTO_SYNC'] = '0'

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from app.db.database import Base
from app.db.iron_models import HealthMetric
from app.services import activity_rating

NOW = datetime(2026, 10, 22, 12)
SOURCE = ROOT / 'app/services/activity_rating.py'
SOURCE_SHA = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
REPORT = QA / 'baseline_timings.json'
BASELINE_COPY = QA / 'activity_rating.baseline.py'
if not BASELINE_COPY.exists():
    BASELINE_COPY.write_bytes(SOURCE.read_bytes())


def fixtures(count, density):
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


metadata = dict(source_sha256=SOURCE_SHA, python=platform.python_version(), platform=platform.platform(),
    now=NOW.isoformat(), scenarios=[], notes=[
        'Every activity has positive finite load/duration and intensity readings; four sports, deterministic varying values.',
        'Four valid same-source wellness readings are present on every relevant date, including the prior baseline.',
        'Dense spans 35 elapsed days at each size; spread has one activity every 12 hours.',
        'Timing includes rating SQL+computation but excludes fixture setup, serialization and digest writing.',
        'No personal DB, provider, browser or app.main is accessed. Timings are local observations, not an SLA.'])

for density in ('dense_35_days', 'spread_12_hours'):
    for count in (1000, 3000, 10000):
        rows, loads, health = fixtures(count, density)
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
        target = QA / f'baseline_{density}_{count}.json.gz'
        target.write_bytes(gzip.compress(payload, mtime=0))
        scenario = dict(density=density, activities=count, health_rows=len(health), seconds=round(duration, 6),
            select_queries=len(statements), output_sha256=hashlib.sha256(payload).hexdigest(),
            output_json_bytes=len(payload), compressed_output=target.name)
        metadata['scenarios'].append(scenario)
        REPORT.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        print(json.dumps(scenario), flush=True)

assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA, 'Production rating source changed during measurement.'
print(json.dumps({'report': str(REPORT), 'source_sha256': SOURCE_SHA}), flush=True)
