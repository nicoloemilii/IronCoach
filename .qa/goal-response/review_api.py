"""Independent, synthetic HTTP contract evidence. Never opens the personal DB."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes_training_response import router
from app.db.database import Base, get_db
from app.db.iron_models import ActivityObservation, DuplicateCandidate, HealthMetric
from app.db.models import Activity, DailyMetrics
from app.services import training_response as service
from app.services.athlete_clock import day_start_utc

AS_OF = date(2026, 10, 18)
NOW = datetime(2026, 10, 19, 12)
START = AS_OF - timedelta(days=84)


def activity(db, key, day, load=30, **changes):
    data = dict(source='fit', source_id=key, name='Synthetic QA', sport='running',
                start_time=day_start_utc(day) + timedelta(hours=8),
                duration_seconds=3600, distance_meters=10000, load=load)
    data.update(changes)
    row = Activity(**data)
    db.add(row)
    db.flush()
    return row


def wellness(db, day, kind, value, source='intervals'):
    units = {'hrv_ms': 'ms', 'resting_hr_bpm': 'bpm', 'sleep_hours': 'ore'}
    row = HealthMetric(date=str(day), kind=kind, value=value, source=source,
                       unit=units[kind], observed_at=str(day), synced_at='2026-10-19T12:00:00Z')
    db.add(row)
    return row


def seed_normal(db):
    rows = {}
    for index in range(85):
        day = START + timedelta(days=index)
        manual = 30 <= index <= 32
        wellness(db, day, 'hrv_ms', None if manual else 45 + index % 13)
        wellness(db, day, 'resting_hr_bpm', None if manual else 55 + index % 7)
        wellness(db, day, 'sleep_hours', None if manual else 6 + (index % 5) / 3)
        if manual:
            db.add(DailyMetrics(date=str(day), hrv_ms=52 + index % 3,
                                resting_hr_bpm=58 + index % 2, sleep_hours=7.5))
        if index < 84 and index % 7:
            rows[index] = activity(db, f'base-{index}', day,
                                   load=0 if index % 11 == 0 else 10 + (index % 9) * 20)
    rows[2].load = None
    activity(db, 'partial-day-extra', START + timedelta(days=3), load=None)
    rows[4].load = 90
    db.add(ActivityObservation(source_key='intervals:null-load', activity_id=rows[4].id,
                              payload_json='{"load":null}'))
    # A realistic near-match across the selected window's Rome midnight.
    boundary = activity(db, 'boundary-selected', START, 30,
                        start_time=day_start_utc(START) + timedelta(seconds=30))
    outside = activity(db, 'boundary-outside', START - timedelta(days=1), 31,
                       start_time=day_start_utc(START) - timedelta(seconds=30), duration_seconds=3590)
    db.add(DuplicateCandidate(activity_id=boundary.id, other_id=outside.id, status='pending'))
    exact = rows[6]
    activity(db, 'unscanned-exact', START + timedelta(days=6), exact.load)
    kept = rows[8]
    kept_copy = activity(db, 'explicit-distinct', START + timedelta(days=8), kept.load)
    db.add_all([DuplicateCandidate(activity_id=kept_copy.id, other_id=kept.id, status='kept'),
                DuplicateCandidate(activity_id=kept.id, other_id=kept_copy.id, status='pending')])
    # A cleared live manual field must not resurrect a migrated value.
    clear_day = START + timedelta(days=40)
    db.add(DailyMetrics(date=str(clear_day), hrv_ms=None))
    wellness(db, clear_day, 'hrv_ms', 999, source='manual')
    db.query(HealthMetric).filter_by(date=str(clear_day), kind='hrv_ms', source='intervals').one().value = None
    db.query(HealthMetric).filter_by(date=str(START + timedelta(days=41)), kind='sleep_hours').one().value = 25
    db.query(HealthMetric).filter_by(date=str(START + timedelta(days=42)), kind='hrv_ms').one().value = 0
    # Deliberate future values must never enter an earlier as_of response.
    activity(db, 'future-observation-must-not-leak', AS_OF + timedelta(days=1), load=99999)
    for kind in ('hrv_ms', 'resting_hr_bpm', 'sleep_hours'):
        wellness(db, AS_OF + timedelta(days=1), kind, 99999)
    db.commit()


def seed_extreme(db):
    start = AS_OF - timedelta(days=18)
    for index in range(19):
        day = start + timedelta(days=index)
        wellness(db, day, 'hrv_ms', 1.75e308 if index % 2 else 5e306)
        wellness(db, day, 'resting_hr_bpm', 55 + index % 3)
        wellness(db, day, 'sleep_hours', 7)
        if index < 18:
            activity(db, f'extreme-{index}', day, 1.75e308 if index % 2 else 8e307)
    db.commit()


def seed_estimated(db):
    start = AS_OF - timedelta(days=12)
    for index in range(13):
        day = start + timedelta(days=index)
        for kind, value in [('hrv_ms', 50 + index), ('resting_hr_bpm', 55 + index % 4), ('sleep_hours', 7)]:
            wellness(db, day, kind, value, source='local_estimate')
        if index < 12:
            activity(db, f'estimated-{index}', day, 10 + index * 10)
    db.commit()


def snapshot(db):
    return {table.name: [dict(row._mapping) for row in db.execute(select(table))]
            for table in Base.metadata.sorted_tables}


def run():
    source_paths = ['app/services/training_response.py', 'app/api/routes_training_response.py',
                    'app/services/training_insights.py', 'app/services/health.py',
                    'app/services/athlete_clock.py', 'app/db/repository.py']
    source_hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in source_paths}
    original_now = service.utc_now_naive
    service.utc_now_naive = lambda: NOW
    fixtures, reports = {}, []
    try:
        for scenario, seed in [('normal', seed_normal), ('empty', lambda db: None),
                               ('extreme', seed_extreme), ('estimated', seed_estimated)]:
            engine = create_engine('sqlite://', poolclass=StaticPool,
                                   connect_args={'check_same_thread': False})
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                seed(db)
                before = snapshot(db)
            application = FastAPI()
            application.include_router(router)

            def isolated_db():
                with Session(engine) as db:
                    yield db

            application.dependency_overrides[get_db] = isolated_db
            statements = []
            listener = lambda connection, cursor, statement, parameters, context, executemany: statements.append(statement)
            event.listen(engine, 'before_cursor_execute', listener)
            try:
                with TestClient(application, client=('127.0.0.1', 19001)) as client:
                    requests = [(f'{scenario}_lag1', AS_OF, 1), (f'{scenario}_lag2', AS_OF, 2)]
                    if scenario == 'normal':
                        requests.append(('historical_lag2', AS_OF - timedelta(days=1), 2))
                    for key, as_of, lag in requests:
                        count_before = len(statements)
                        response = client.get('/dashboard/training-response', params={'as_of': str(as_of), 'days': 84, 'lag': lag})
                        assert response.status_code == 200, response.text
                        body = response.json()
                        json.dumps(body, allow_nan=False)
                        assert body['lag_days'] == lag
                        assert body['period']['end'] == str(as_of - timedelta(days=lag))
                        assert body['period']['response_end'] == str(as_of)
                        for metric in body['metrics']:
                            for series in metric['series']:
                                for row in series['rows']:
                                    assert row['training_date'] <= body['period']['end']
                                    assert row['response_date'] <= str(as_of)
                                    assert row['load'] != 99999
                                    if row['eligible']:
                                        assert row['before_source'] == row['after_source'] == series['source']
                                        assert row['load'] is not None
                        fixtures[key] = body
                        if scenario == 'estimated':
                            for metric in body['metrics']:
                                for source_series in metric['series']:
                                    assert source_series['source'] == 'local_estimate'
                                    assert source_series['paired_days'] == 0
                                    assert not source_series['comparison']['available']
                                    assert all(row['before'] is not None and row['after'] is not None
                                               and not row['eligible'] and 'estimated_before' in row['exclusion_reasons']
                                               and 'estimated_after' in row['exclusion_reasons']
                                               for row in source_series['rows'])
                        reports.append({'fixture': key, 'http_status': response.status_code,
                                        'selects': len(statements) - count_before, 'coverage': body['coverage']})
                    for query in ['lag=0', 'lag=3', 'days=27', 'days=181', 'as_of=2030-01-01', 'as_of=2026-02-30']:
                        assert client.get('/dashboard/training-response?' + query).status_code == 422
                    if scenario == 'normal':
                        original_limit = service.MAX_ACTIVITIES
                        service.MAX_ACTIVITIES = 3
                        try:
                            response = client.get('/dashboard/training-response', params={'as_of': str(AS_OF), 'lag': 1})
                            assert response.status_code == 200
                            fixtures['truncated_lag1'] = response.json()
                            assert fixtures['truncated_lag1']['quality']['truncated'] is True
                            assert all(not series['comparison']['available'] and series['paired_days'] == 0
                                       for metric in fixtures['truncated_lag1']['metrics'] for series in metric['series'])
                        finally:
                            service.MAX_ACTIVITIES = original_limit
            finally:
                event.remove(engine, 'before_cursor_execute', listener)
            assert statements and all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
            with Session(engine) as db:
                assert snapshot(db) == before
            reports.append({'scenario': scenario, 'database_unchanged': True, 'all_http_sql_select_only': True})
            engine.dispose()
        main = fixtures['normal_lag1']
        assert str(START) in main['quality']['exclusions']['duplicate_review']
        assert str(START + timedelta(days=6)) in main['quality']['exclusions']['duplicate_review']
        assert str(START + timedelta(days=8)) not in main['quality']['exclusions']['duplicate_review']
        assert str(START + timedelta(days=7)) in main['quality']['exclusions']['days_without_activities']
        assert str(START + timedelta(days=4)) in main['quality']['exclusions']['missing_load']
        hrv = next(metric for metric in main['metrics'] if metric['kind'] == 'hrv_ms')
        intervals = next(series for series in hrv['series'] if series['source'] == 'intervals')
        assert any(row['load'] == 0 and row['eligible'] for row in intervals['rows'])
        assert any('source_mismatch' in row['exclusion_reasons'] for row in intervals['rows'])
        cleared = next(row for row in intervals['rows'] if row['training_date'] == str(START + timedelta(days=40)))
        assert cleared['before'] is None
        assert source_hashes == {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in source_paths}
        output = {'generated_from': 'Actual isolated FastAPI TestClient responses', 'source_sha256': source_hashes,
                  'personal_database_opened': False, 'provider_calls': 0, 'fixtures': fixtures, 'reports': reports}
        target = Path(__file__).with_name('review_api.json')
        target.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps({'result': 'passed', 'fixtures': list(fixtures), 'reports': reports,
                          'artifact': str(target)}, ensure_ascii=False))
    finally:
        service.utc_now_naive = original_now


if __name__ == '__main__':
    run()
