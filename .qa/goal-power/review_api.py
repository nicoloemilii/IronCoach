"""Independent real FastAPI-router roundtrips over in-memory synthetic data.

No app.main import and no personal database/provider. Assertions use analytic
fixtures, not the production helper's own expected output.
"""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
os.environ['IRON_DATA_DIR'] = str(HERE / 'review-api-unused')
os.environ['IRON_AUTO_SYNC'] = '0'
os.environ['IRON_MODE'] = 'local'
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api.routes_activity_detail import router
from app.db.database import Base, get_db
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.services import activity_detail

NOW = datetime(2026, 10, 22, 12)
SOURCES = ['app/services/power_duration.py', 'app/services/activity_effort.py', 'app/services/activity_detail.py']


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def fingerprint(engine):
    with engine.connect() as conn:
        names = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")).scalars().all()
        return {name: hashlib.sha256(repr(conn.exec_driver_sql(
            'SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid').fetchall()).encode()).hexdigest()
            for name in names}


def streams(times, power):
    return [{'type':'time', 'data':times}, {'type':'watts', 'data':power}]


def main():
    source_before = hashes()
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    app = FastAPI(); app.include_router(router)
    def database():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_db] = database
    ids = {}
    def seed(db, name, payload, *, at=None, source='intervals', extras=None, duration=10):
        row = Activity(source=source, source_id='qa-power-' + name, name=name, sport='cycling',
            start_time=at or NOW - timedelta(days=1), duration_seconds=duration,
            distance_meters=1000, avg_power=999, normalized_power=999, load=30)
        db.add(row); db.flush()
        if payload is not None:
            db.add(ActivityObservation(activity_id=row.id, source_key='intervals:qa-power-' + name,
                payload_json=json.dumps(payload)))
        for index, other in enumerate(extras or []):
            db.add(ActivityObservation(activity_id=row.id, source_key='intervals:qa-alias-' + name + str(index),
                payload_json=json.dumps(other)))
        ids[name] = row.id
    triangle = dict(load=30, elapsed_time=10, moving_time=5, streams=streams([0,4,10], [0,120,0]))
    wrong = dict(load=30, elapsed_time=10, streams=streams([0,4,10], [999,999,999]))
    with Session(engine) as db:
        seed(db, 'canonical', triangle, extras=[wrong])
        seed(db, 'equivalent_load_ambiguous_streams', None, source='fit', extras=[triangle, wrong])
        seed(db, 'conflicting_aliases', None, source='fit', extras=[triangle, dict(wrong, load=90)])
        seed(db, 'recent', dict(load=30, elapsed_time=20, streams=streams(list(range(21)), [100]*13+[1000]*8)),
             at=NOW - timedelta(seconds=12), duration=20)
        seed(db, 'future', wrong, at=NOW + timedelta(days=1))
        seed(db, 'zero', dict(load=30, elapsed_time=20, streams=streams(list(range(21)), [0]*21)))
        seed(db, 'moving_is_not_elapsed', dict(load=30, moving_time=5, streams=streams(list(range(21)), [100]*21)), duration=5)
        seed(db, 'remote_used', wrong)
        db.commit()
    state_before = fingerprint(engine)
    statements, provider_calls = [], []
    def before_cursor(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split(None, 1)[0].upper())
    event.listen(engine, 'before_cursor_execute', before_cursor)
    def block(*args, **kwargs):
        provider_calls.append('forbidden_external_provider')
        raise AssertionError('External provider use is forbidden in independent QA')
    results = {}
    activity_detail._CACHE.clear()
    with patch.object(activity_detail, 'utc_now_naive', lambda: NOW), \
         patch.object(activity_detail.intervals_credentials, 'credentials', lambda: {}), \
         patch.object(activity_detail.intervals, 'get_activity_remote', block), \
         patch('requests.sessions.Session.request', block), \
         TestClient(app, client=('127.0.0.1',55001)) as client:
        for name, identifier in ids.items():
            if name == 'remote_used':
                continue
            response = client.get(f'/activities/{identifier}/detail')
            assert response.status_code == 200, (name, response.text)
            assert response.headers['cache-control'] == 'private, no-store'
            detail = response.json(); result = detail['best_efforts']
            assert result['source'] == 'stored_observation_streams', (name, result)
            results[name] = detail
        first = results['canonical']['best_efforts']['durations'][0]
        assert first['status'] == 'available' and first['mean_power_w'] == 90 and first['start_s'] == 2, first
        assert results['canonical']['summary']['avg_power'] == 999  # Summary does not drive the curve.
        for name in ['equivalent_load_ambiguous_streams', 'conflicting_aliases']:
            assert results[name]['availability']['source_resolution']['ambiguous_identity'], name
            assert not any(row['status'] == 'available' for row in results[name]['best_efforts']['durations']), name
        recent = results['recent']['best_efforts']
        assert recent['coverage']['window_seconds'] == 12, recent
        assert recent['durations'][0]['mean_power_w'] == 100, recent
        assert recent['durations'][1]['status'] == 'insufficient_duration', recent
        assert not any(row['status'] == 'available' for row in results['future']['best_efforts']['durations'])
        zero = results['zero']['best_efforts']['durations'][0]
        assert zero['status'] == 'available' and zero['mean_power_w'] == 0 and zero['coverage_pct'] == 100, zero
        moving = results['moving_is_not_elapsed']['best_efforts']['durations'][1]
        assert moving['status'] == 'available' and moving['mean_power_w'] == 100, moving
        with patch.object(activity_detail, '_cached_remote', lambda identifier: (
                dict(elapsed_time=10), triangle['streams'], [], None, True)):
            response = client.get(f"/activities/{ids['remote_used']}/detail")
            assert response.status_code == 200, response.text
            remote = response.json(); result = remote['best_efforts']
            assert result['source'] == 'intervals_remote_streams', result
            assert remote['availability']['remote_fetched'] is True
            assert result['durations'][0]['mean_power_w'] == 90, result
            results['remote_used'] = remote
    assert provider_calls == [], provider_calls
    assert not any(kind in ('INSERT','UPDATE','DELETE','REPLACE','CREATE','ALTER','DROP') for kind in statements), statements
    assert fingerprint(engine) == state_before, 'GET detail mutated the isolated database'
    assert hashes() == source_before, 'production source changed during this run'
    report = dict(status='passed', fixture='independent-analytic-power-v1', fixed_utc=NOW.isoformat(),
        actual_fastapi_router=True, database='sqlite in-memory', production_sha256=source_before,
        source_unchanged=True, database_unchanged=True, provider_calls=provider_calls,
        sql_statement_kinds=sorted(set(statements)), cases=results,
        boundary='Synthetic HTTP roundtrips; remote source is a stub, no provider/device/visual claim')
    (HERE / 'review_api.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'cases'}))
    engine.dispose(); activity_detail._CACHE.clear()


if __name__ == '__main__':
    main()
