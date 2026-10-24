"""Synthetic HTTP evidence for complete, partial and missing profile history."""
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent
os.environ['IRON_DATA_DIR'] = str(QA / 'profile-isolated-unused')
os.environ['IRON_AUTO_SYNC'] = '0'
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api import routes_health_reports
from app.db.database import Base, get_db
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.services import athlete_snapshot, health_reports, planning

DAY = date(2026, 10, 22)
NOW = datetime(2026, 10, 22, 12)


def snapshot(engine):
    with engine.connect() as conn:
        return {table.name: [list(row) for row in conn.execute(text(f'SELECT * FROM "{table.name}" ORDER BY 1'))]
                for table in Base.metadata.sorted_tables}


def call(rows, requested_day=None):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for identifier, changes in enumerate(rows, 1):
            fields = dict(source='fit', source_id=f'profile-synthetic-{identifier}',
                sport='cycling', name=f'Attività sintetica {identifier}', start_time=NOW - timedelta(days=1),
                duration_seconds=3600, distance_meters=20000, elevation_gain_meters=100,
                calories=500, avg_power=150, avg_speed=10, normalized_power=160, load=30)
            fields.update(changes)
            raw_load = fields.pop('raw_load', None)
            row = Activity(**fields); db.add(row); db.flush()
            if raw_load is not None:
                db.add(ActivityObservation(activity_id=row.id, source_key=f'intervals:profile-synthetic-{identifier}',
                                           payload_json=raw_load))
        db.commit()
    application = FastAPI(); application.include_router(routes_health_reports.router)
    def isolated():
        with Session(engine) as db:
            yield db
    application.dependency_overrides[get_db] = isolated
    patches = [patch.object(planning, 'load_profile', lambda: {})]
    for module in (athlete_snapshot, health_reports, routes_health_reports):
        patches.append(patch.object(module, 'athlete_today', lambda: DAY))
        if hasattr(module, 'utc_now_naive'):
            patches.append(patch.object(module, 'utc_now_naive', lambda: NOW))
    for item in patches: item.start()
    before = snapshot(engine); statements = []
    def capture(conn, cursor, sql, parameters, context, executemany): statements.append(sql)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with TestClient(application, client=('127.0.0.1', 55001)) as client:
            response = client.get('/athlete/derived-profile' + (f'?day={requested_day}' if requested_day else ''))
        assert response.status_code == 200, response.text
        body = response.json(); json.dumps(body, allow_nan=False)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
        for item in reversed(patches): item.stop()
    read_only = snapshot(engine) == before and all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    engine.dispose()
    assert read_only
    return {'body': body, 'read_only': True,
            'observation_selects': sum('FROM activity_observations' in sql for sql in statements),
            'total_selects': len(statements)}


def scenarios():
    return {
        'empty': [],
        'complete': [{'start_time': NOW - timedelta(days=1), 'duration_seconds': 1800},
                     {'start_time': NOW - timedelta(days=30)}],
        'partial': [{'duration_seconds': 3600}, {'duration_seconds': None, 'distance_meters': None,
                     'raw_load': '{"load":null}'}, {'start_time': NOW - timedelta(days=30)}],
        'unknown': [{'duration_seconds': None}, {'duration_seconds': None},
                    {'start_time': NOW - timedelta(days=30)}],
        'zero': [{'duration_seconds': 0, 'distance_meters': 0, 'elevation_gain_meters': 0,
                  'calories': 0, 'avg_power': 0, 'avg_speed': 0, 'normalized_power': 0, 'load': 0}],
        'invalid': [{'duration_seconds': float('inf'), 'avg_speed': 1e308},
                    {'duration_seconds': -1}, {'duration_seconds': None}],
        'overflow': [{'calories': 1e308, 'load': 1e308}, {'calories': 1e308, 'load': 1e308}],
        'aliases': [{'sport': value} for value in ('Crossfit', 'strengthtraining', 'WeightTraining', 'palestra', 'gym', 'weightlifting')],
        'record_periods': [{'start_time': NOW - timedelta(days=120), 'distance_meters': 200000},
                          {'start_time': NOW - timedelta(days=50), 'distance_meters': 100000},
                          {'start_time': NOW - timedelta(days=20), 'distance_meters': 50000},
                          {'start_time': NOW + timedelta(seconds=1), 'distance_meters': 999000}],
        'query_budget': [{'sport': ('cycling', 'running', 'swimming', 'strength', 'other')[index % 5],
                         'raw_load': '{"load":30}'} for index in range(50)],
    }


if __name__ == '__main__':
    files = [ROOT / name for name in ('app/services/athlete_snapshot.py', 'app/services/observed_load.py',
                                    'app/services/training_load.py', 'app/api/routes_health_reports.py')]
    hashes = {str(file.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(file.read_bytes()).hexdigest() for file in files}
    results = {name: call(rows) for name, rows in scenarios().items()}
    fixtures = {name: result['body'] for name, result in results.items()}
    checks = {
        'complete_registered_change': fixtures['complete']['volume_comparison']['change_pct'] == -50,
        'partial_totals_not_complete': fixtures['partial']['totals']['days_28']['hours'] is None
            and fixtures['partial']['duration_coverage']['known_minutes'] == 60,
        'unknown_not_zero_or_decline': fixtures['unknown']['weekly_minutes'] is None
            and fixtures['unknown']['volume_comparison']['change_pct'] is None
            and not any('30%' in value for value in fixtures['unknown']['priorities']),
        'zero_observed': fixtures['zero']['weekly_minutes'] == 0 and fixtures['zero']['duration_coverage']['known'] == 1,
        'empty_distinct_from_unknown': fixtures['empty']['duration_coverage']['status'] == 'empty'
            and fixtures['unknown']['duration_coverage']['status'] == 'unavailable',
        'invalid_finite_json': fixtures['invalid']['duration_coverage']['invalid'] == 2,
        'overflow_not_serialized': fixtures['overflow']['totals']['all_time']['load'] is None,
        'six_strength_aliases': next(item for item in fixtures['aliases']['sports'] if item['sport'] == 'strength')['activities'] == 6,
        'future_records_excluded': fixtures['record_periods']['total_activities'] == 3,
        'one_observation_preload': results['query_budget']['observation_selects'] == 1,
        'all_gets_readonly': all(result['read_only'] for result in results.values()),
        'source_stable': all(hashes[str(file.relative_to(ROOT)).replace('\\', '/')] == hashlib.sha256(file.read_bytes()).hexdigest() for file in files),
    }
    report = {'scope': 'Actual profile HTTP endpoint; synthetic in-memory SQLite only', 'personal_database_opened': False,
              'provider_access': False, 'visual_verification': False, 'source_sha256': hashes,
              'cases': {name: {key: value for key, value in result.items() if key != 'body'} for name, result in results.items()},
              'fixtures': fixtures, 'checks': checks}
    (QA / 'review_profile.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'cases': len(results), 'checks': checks, 'passed': all(checks.values())}, ensure_ascii=False))
    assert all(checks.values())
