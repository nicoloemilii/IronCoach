"""Read-only synthetic probes; no personal database or provider is accessed."""
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter
from datetime import datetime, timedelta
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['IRON_DATA_DIR'] = str(Path(__file__).parent / 'isolated-data')
os.environ['IRON_AUTO_SYNC'] = '0'

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from app.db.database import Base
from app.db.models import Activity
from app.db.iron_models import ActivityObservation, HealthMetric, RaceGoal
from app.db.planning_models import AthleteSettings
from app.services import activity_rating as rating, training_load as load

NOW = datetime(2026, 10, 22, 12)
load.utc_now_naive = lambda: NOW
results = {}


def activity(db, day=0, **changes):
    value = dict(source='fit', source_id=f'a-{db.query(Activity).count()}', name='Synthetic', sport='cycling',
        start_time=NOW - timedelta(days=day, hours=3), duration_seconds=3600,
        load=50, avg_hr=130, avg_power=150, normalized_power=150)
    value.update(changes)
    row = Activity(**value); db.add(row); db.flush()
    return row


def probe(key, callback):
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            results[key] = callback(db)
    except Exception as error:
        results[key] = {'error': type(error).__name__, 'message': str(error)[:180]}
    finally:
        engine.dispose()


def competing_aliases(db):
    row = activity(db)
    for key, value in [('old-alias', 50), ('new-alias', 500)]:
        db.add(ActivityObservation(activity_id=row.id, source_key='intervals:' + key, payload_json=json.dumps({'load': value})))
    db.flush()
    recorded = rating.recorded_loads(db, [row])[row.id]
    with load.activity_load_snapshot(db):
        snapshot = load.activity_load(row, 170)
    return {'rating_load': recorded, 'fitness_load': snapshot, 'same_canonical_activity_id': row.id}


def invalid_health(db, kind, value, source):
    for days in range(3, 43, 3):
        activity(db, days)
    row = activity(db)
    for days in range(9, 16):
        db.add(HealthMetric(date=str((NOW - timedelta(days=days)).date()), kind='hrv_ms', value=50,
            source='intervals', unit='ms', observed_at='synthetic', synced_at='synthetic'))
    db.flush()
    before = rating.activity_ratings(db, now=NOW)[row.id]
    db.add(HealthMetric(date=str((NOW - timedelta(days=1)).date()), kind=kind, value=value,
        source=source, unit='test', observed_at='synthetic', synced_at='synthetic'))
    db.flush()
    after = rating.activity_ratings(db, now=NOW)[row.id]
    return {'before_score': before['score'], 'after_score': after['score'],
        'new_recovery_component': [part for part in after['components'] if part['key'] == 'recovery']}


def bad_load(db, payload):
    row = activity(db)
    db.add(ActivityObservation(activity_id=row.id, source_key='intervals:bad', payload_json=payload)); db.flush()
    return rating.recorded_loads(db, [row])


def broken_goal(db):
    row = activity(db)
    goal = RaceGoal(name='Synthetic', date='2027-01-01', priority='finish', legs_json='[null]')
    db.add(goal); db.flush()
    db.add(AthleteSettings(id=1, payload_json=json.dumps({'race_goal_id': goal.id}))); db.flush()
    return rating.activity_ratings(db, now=NOW)[row.id]


def bad_power(db, value):
    row = activity(db, load=None, normalized_power=value)
    measured = load.activity_load(row, 170)
    metrics = load.fitness_metrics([row], 170, end_date=NOW.date())
    return {'activity_load': str(measured), 'all_metrics_finite': all(math.isfinite(v) for v in metrics.values() if isinstance(v, (int, float))),
        'metrics': {key: str(value) for key, value in metrics.items()}}


def observations_query_scope(db):
    target = activity(db)
    for index in range(100):
        row = activity(db, index + 100)
        db.add(ActivityObservation(activity_id=row.id, source_key=f'intervals:unrelated-{index}', payload_json='{"load":50}'))
    db.flush(); statements = []
    def capture(conn, cursor, statement, params, context, many):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        rating.recorded_loads(db, [target])
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    return {'requested_activities': 1, 'unrelated_observations': 100, 'select': statements}


def scaling(db):
    output = []
    for count in (250, 500, 1000, 2000):
        activities = [SimpleNamespace(id=index, sport='cycling', start_time=NOW - timedelta(seconds=count - index),
            duration_seconds=3600, avg_hr=130, avg_power=150, normalized_power=150) for index in range(count)]
        start = perf_counter()
        values = rating.activity_ratings(db, activities, loads={row.id: 50 for row in activities}, now=NOW)
        output.append({'activities': count, 'milliseconds': round((perf_counter() - start) * 1000, 1), 'rated': len(values)})
    return output


probe('competing_intervals_aliases', competing_aliases)
probe('invalid_zero_hrv', lambda db: invalid_health(db, 'hrv_ms', 0, 'intervals'))
probe('estimated_tsb_used_as_observed_recovery', lambda db: invalid_health(db, 'tsb', -35, 'local_estimate'))
probe('oversized_load_integer', lambda db: bad_load(db, json.dumps({'load': 10 ** 1000})))
probe('deep_load_payload', lambda db: bad_load(db, '{"nested":' + '[' * 2000 + '0' + ']' * 2000 + ',"load":50}'))
probe('malformed_goal_legs', broken_goal)
probe('nonfinite_normalized_power', lambda db: bad_power(db, float('inf')))
probe('extreme_normalized_power', lambda db: bad_power(db, 1e308))
probe('negative_normalized_power', lambda db: bad_power(db, -150))
probe('single_activity_observation_query_scope', observations_query_scope)
probe('dense_history_scaling', scaling)
target = Path(__file__).with_suffix('.json')
target.write_text(json.dumps(results, indent=2, allow_nan=False), encoding='utf-8')
print(json.dumps(results, indent=2, allow_nan=False))
