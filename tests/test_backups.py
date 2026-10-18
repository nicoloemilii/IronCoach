"""Personal backups never use the real user database or credential files."""
import copy
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes_backup import router
from app.api.routes_planning import router as planning_router
from app.db.database import Base, get_db
from app.db.experience_models import ActivityIdentity
from app.db.garmin_models import GarminDelivery
from app.db.iron_models import IntegrationConnection
from app.db.iron_models import RaceGoal
from app.db.models import Activity, Workout
from app.db.planning_models import AthleteSettings
from app.db.progress_models import GoalPreparationItem, WorkoutResolution
from app.db.sync_models import IntervalsState
from app.services import backups, planning


def application(engine):
    app = FastAPI()
    app.include_router(router)
    app.include_router(planning_router)
    def isolated():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = isolated
    return app


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(planning, 'load_profile', lambda: {'name': 'Legacy athlete'})
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Activity(id=1, source='fit', source_id='fit:one', sport='running', name='Original',
                        start_time=datetime(2026, 1, 1, 8), duration_seconds=3600))
        db.add(ActivityIdentity(id=1, activity_id=1, source_key='fit:one'))
        db.add(IntervalsState(id=1, credential_hash='never-export-this-hash'))
        db.add(IntegrationConnection(provider='strava', athlete_id='private-account', scopes='activity:read'))
        db.commit()
    with TestClient(application(engine), client=('127.0.0.1', 55555)) as client:
        yield client, engine
    engine.dispose()
    with backups._PREVIEW_LOCK:
        backups._PREVIEWS.clear()


def preview(client, payload):
    response = client.post('/backups/preview', content=json.dumps(payload), headers={'Content-Type': 'application/json'})
    return response


def restore(client, token):
    return client.post('/backups/restore', json={'preview_token': token, 'confirmation': 'RIPRISTINA'})


def test_export_is_download_and_excludes_credentials_and_remote_refs(api):
    client, engine = api
    with Session(engine) as db:
        db.add(Workout(id=1, date='2026-01-01', sport='running', name='Easy', duration_min=60, payload_json='{}'))
        db.add(GarminDelivery(workout_id=1, content_hash='a' * 64, status='published', reason_code='ok', remote_published=True))
        db.commit()
    response = client.get('/backups/export')
    assert response.status_code == 200, response.text
    assert 'attachment;' in response.headers['content-disposition']
    assert response.headers['cache-control'] == 'no-store'
    payload = response.json()
    assert payload['format_version'] == 1
    assert not {'intervals_state', 'integration_connections', 'sync_runs', 'garmin_deliveries', 'iron_schema_versions'}.intersection(payload['data'])
    assert 'never-export-this-hash' not in response.text and 'private-account' not in response.text
    profile = payload['data']['athlete_settings'][0]
    assert json.loads(profile['payload_json'])['name'] == 'Legacy athlete'
    assert profile['revision'] == 0


def test_preview_is_readonly_and_full_restore_preserves_connections(api):
    client, engine = api
    original = client.get('/backups/export').json()
    payload = copy.deepcopy(original)
    payload['data']['activities'][0]['name'] = 'Restored run'
    payload['data']['activities'][0]['avg_hr'] = 0  # A valid zero is not missing.
    with Session(engine) as db:
        db.add(Workout(id=9, date='2026-01-02', sport='cycling', name='Current workout', duration_min=30, payload_json='{}'))
        db.add(GarminDelivery(workout_id=9, content_hash='b' * 64, status='published', reason_code='ok', remote_published=True))
        planning.save_settings(db, {'name': 'Current profile'})
    response = preview(client, payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['total_records'] == sum(row['count'] for row in data['tables'])
    with Session(engine) as db:
        assert db.get(Activity, 1).name == 'Original'
        assert db.get(Workout, 9)
    result = restore(client, data['preview_token'])
    assert result.status_code == 200
    assert result.json()['restored'] and result.json()['reload_required']
    assert result.json()['removed_remote_references'] == 1
    with Session(engine) as db:
        assert db.get(Activity, 1).name == 'Restored run'
        assert db.get(Activity, 1).avg_hr == 0
        assert db.query(Workout).count() == 0
        assert db.query(GarminDelivery).count() == 0
        assert db.get(IntervalsState, 1).credential_hash == 'never-export-this-hash'
        assert db.get(IntegrationConnection, 'strava').athlete_id == 'private-account'
        assert planning.load_settings(db)['name'] == 'Legacy athlete'
        assert db.get(AthleteSettings, 1).revision == 2
    assert restore(client, data['preview_token']).status_code == 409


def test_restore_bumps_above_imported_and_current_revision_and_stale_browser_fails(api):
    client, engine = api
    saved = client.put('/planning/profile', json={'name': 'Current'}).json()
    payload = client.get('/backups/export').json()
    payload['data']['athlete_settings'][0]['revision'] = 40
    payload['data']['athlete_settings'][0]['payload_json'] = json.dumps({'name': 'Imported'})
    token = preview(client, payload).json()['preview_token']
    response = restore(client, token)
    assert response.status_code == 200 and response.json()['profile_revision'] == 41
    stale = client.put('/planning/profile', json={'name': 'Old browser draft'}, headers={'If-Match': str(saved['revision'])})
    assert stale.status_code == 412
    assert client.get('/planning/profile').json()['profile']['name'] == 'Imported'


def test_empty_imported_profile_still_creates_revision_guard(api):
    client, _ = api
    payload = client.get('/backups/export').json()
    payload['data']['athlete_settings'] = []
    token = preview(client, payload).json()['preview_token']
    assert restore(client, token).json()['profile_revision'] == 1
    assert client.put('/planning/profile', json={'name': 'Old unsaved draft'}, headers={'If-Match': '0'}).status_code == 412


def test_changed_data_invalidates_preview_permanently(api):
    client, engine = api
    payload = client.get('/backups/export').json()
    token = preview(client, payload).json()['preview_token']
    with Session(engine) as db:
        db.get(Activity, 1).name = 'New synchronization'
        db.commit()
    response = restore(client, token)
    assert response.status_code == 409 and 'cambiati' in response.json()['detail']
    with Session(engine) as db:
        assert db.get(Activity, 1).name == 'New synchronization'
    assert 'già utilizzata' in restore(client, token).json()['detail']


def test_expired_preview_and_bounded_server_cache(api, monkeypatch):
    client, _ = api
    payload = client.get('/backups/export').json()
    tokens = [preview(client, payload).json()['preview_token'] for _ in range(4)]
    assert len(backups._PREVIEWS) == 3
    assert restore(client, tokens[0]).status_code == 409
    original_now = backups._now
    monkeypatch.setattr(backups, '_now', lambda: original_now() + timedelta(seconds=601))
    assert restore(client, tokens[-1]).status_code == 409


@pytest.mark.parametrize('mutation', [
    lambda p: p.update({'unexpected': True}),
    lambda p: p.update({'format_version': True}),
    lambda p: p.update({'format_version': 5}),
    lambda p: p.update({'generated_at': 'not-a-date'}),
    lambda p: p['data'].update({'integration_connections': []}),
    lambda p: p['data'].pop('activities'),
    lambda p: p['data']['activities'][0].update({'secret': 'no'}),
    lambda p: p['data']['activities'][0].update({'duration_seconds': '3600'}),
    lambda p: p['data']['activities'][0].update({'duration_seconds': float('nan')}),
    lambda p: p['data']['activities'][0].update({'duration_seconds': float('inf')}),
    lambda p: p['data']['activities'][0].update({'id': True}),
    lambda p: p['data']['activities'][0].update({'start_time': '2026-02-31T10:00:00'}),
    lambda p: p['data']['activity_identities'][0].update({'activity_id': 999}),
    lambda p: p['data']['activities'].append(copy.deepcopy(p['data']['activities'][0])),
    lambda p: p['data']['athlete_settings'][0].update({'payload_json': '{"name":"X","api_key":"secret"}'}),
    lambda p: p['data']['athlete_settings'][0].update({'payload_json': '{"weekly_minutes":NaN}'}),
    lambda p: p['data']['athlete_settings'][0].update({'revision': -1}),
    lambda p: p['data']['athlete_settings'][0].update({'payload_json': '[]'}),
])
def test_invalid_backups_are_rejected_without_changes(api, mutation):
    client, engine = api
    payload = client.get('/backups/export').json()
    mutation(payload)
    response = preview(client, payload)
    assert response.status_code == 422, response.text
    with Session(engine) as db:
        assert db.get(Activity, 1).name == 'Original'
        assert db.query(AthleteSettings).count() == 0


def test_unknown_keys_duplicate_json_and_wrong_content_type_are_rejected(api, monkeypatch):
    client, _ = api
    assert client.post('/backups/preview', content=b'{"format_version":1,"format_version":1}', headers={'Content-Type':'application/json'}).status_code == 422
    assert client.post('/backups/preview', content=b'{}', headers={'Content-Type':'text/plain'}).status_code == 415
    monkeypatch.setattr(backups, 'MAX_BYTES', 10)
    assert client.post('/backups/preview', content=b' ' * 11, headers={'Content-Type':'application/json'}).status_code == 413


def test_all_backup_operations_require_loopback_and_same_origin(api):
    client, engine = api
    with TestClient(application(engine), client=('203.0.113.9', 55555)) as remote:
        assert remote.get('/backups/export').status_code == 403
        assert remote.post('/backups/preview', content=b'{}', headers={'Content-Type':'application/json'}).status_code == 403
        assert remote.post('/backups/restore', json={}).status_code == 403
    assert client.get('/backups/export', headers={'Origin': 'https://untrusted.example'}).status_code == 403


def test_restore_does_not_run_during_synchronization(api):
    from app.services.sync_jobs import LOCKS
    client, engine = api
    token = preview(client, client.get('/backups/export').json()).json()['preview_token']
    LOCKS['intervals'].acquire()
    try:
        assert restore(client, token).status_code == 409
    finally:
        LOCKS['intervals'].release()
    assert not LOCKS['strava'].locked()
    with Session(engine) as db:
        assert db.get(Activity, 1).name == 'Original'


def test_restore_rolls_back_all_tables_on_database_error(api):
    client, engine = api
    payload = client.get('/backups/export').json()
    payload['data']['activities'][0]['name'] = 'Should roll back'
    token = preview(client, payload).json()['preview_token']
    def fail_insert(connection, cursor, statement, parameters, context, many):
        if statement.lower().startswith('insert into activities'):
            raise OperationalError(statement, parameters, Exception('injected database failure'))
    event.listen(engine, 'before_cursor_execute', fail_insert)
    try:
        response = restore(client, token)
        assert response.status_code == 503
    finally:
        event.remove(engine, 'before_cursor_execute', fail_insert)
    with Session(engine) as db:
        assert db.get(Activity, 1).name == 'Original'
        assert db.query(ActivityIdentity).count() == 1
        assert db.get(IntervalsState, 1).credential_hash == 'never-export-this-hash'


def test_progress_records_round_trip_and_older_backup_omits_them(api):
    client, engine = api
    with Session(engine) as db:
        db.add(Workout(id=1, date='2026-01-01', sport='running', name='Easy', duration_min=30, payload_json='{}', status='skipped'))
        db.add(WorkoutResolution(workout_id=1, kind='rest', reason='Recupero concordato'))
        db.add(RaceGoal(id=1, name='Race', date='2027-01-01', legs_json=json.dumps([{
            'sport':'running', 'terrain':'road', 'distance_km':10, 'elevation_gain_m':0,
            'elevation_loss_m':0, 'altitude_min_m':None, 'altitude_max_m':None, 'requirements':{}}])))
        db.add(GoalPreparationItem(goal_id=1, key='equipment', done=True, notes='Shoes tested', context_hash='a' * 64))
        db.commit()
    payload = client.get('/backups/export').json()
    assert payload['data']['workout_resolutions'][0]['kind'] == 'rest'
    assert payload['data']['goal_preparation_items'][0]['done'] is True
    token = preview(client, payload).json()['preview_token']
    assert restore(client, token).status_code == 200
    with Session(engine) as db:
        assert db.query(WorkoutResolution).one().reason == 'Recupero concordato'
        assert db.query(GoalPreparationItem).one().notes == 'Shoes tested'
    old = copy.deepcopy(payload)
    old['data'].pop('workout_resolutions')
    old['data'].pop('goal_preparation_items')
    token = preview(client, old).json()['preview_token']
    assert restore(client, token).status_code == 200
    with Session(engine) as db:
        assert db.query(WorkoutResolution).count() == db.query(GoalPreparationItem).count() == 0


def test_active_resolution_uniqueness_and_reference_validation(api):
    client, engine = api
    with Session(engine) as db:
        db.add(Workout(id=1, date='2026-01-01', sport='running', name='Easy', duration_min=30, payload_json='{}', status='skipped'))
        db.add(WorkoutResolution(workout_id=1, kind='rest', reason='Recovery'))
        db.commit()
    original = client.get('/backups/export').json()
    duplicate = copy.deepcopy(original['data']['workout_resolutions'][0]); duplicate['id'] += 1
    payload = copy.deepcopy(original)
    payload['data']['workout_resolutions'].append(duplicate)
    assert preview(client, payload).status_code == 422
    payload = copy.deepcopy(original)
    payload['data']['workout_resolutions'][0]['workout_id'] = 999
    assert preview(client, payload).status_code == 422
    payload = copy.deepcopy(original)
    payload['data']['workout_resolutions'][0]['kind'] = 'completed'
    assert preview(client, payload).status_code == 422


def test_preview_fingerprint_includes_effective_legacy_profile(api, monkeypatch):
    client, _ = api
    token = preview(client, client.get('/backups/export').json()).json()['preview_token']
    monkeypatch.setattr(planning, 'load_profile', lambda: {'name': 'Legacy changed after preview'})
    assert restore(client, token).status_code == 409


def test_profile_goal_reference_must_exist_in_imported_backup(api):
    client, engine = api
    payload = client.get('/backups/export').json()
    profile = json.loads(payload['data']['athlete_settings'][0]['payload_json'])
    profile['race_goal_id'] = 999
    payload['data']['athlete_settings'][0]['payload_json'] = json.dumps(profile)
    response = preview(client, payload)
    assert response.status_code == 422
    with Session(engine) as db:
        assert db.query(AthleteSettings).count() == 0
        assert db.query(RaceGoal).count() == 0
        assert db.get(Activity, 1).name == 'Original'


def test_profile_goal_reference_resolves_within_imported_backup(api):
    client, engine = api
    payload = client.get('/backups/export').json()
    profile = json.loads(payload['data']['athlete_settings'][0]['payload_json'])
    profile['race_goal_id'] = 999
    payload['data']['athlete_settings'][0]['payload_json'] = json.dumps(profile)
    payload['data']['race_goals'] = [{'id': 999, 'name': 'Imported goal', 'date': '2027-01-01',
        'priority': 'finish', 'target_time': None,
        'legs_json': json.dumps([{'sport': 'running', 'terrain': 'road', 'distance_km': 10}])}]
    response = preview(client, payload)
    assert response.status_code == 200, response.text
    assert restore(client, response.json()['preview_token']).status_code == 200
    with Session(engine) as db:
        assert planning.load_settings(db)['race_goal_id'] == 999
        assert db.get(RaceGoal, 999).name == 'Imported goal'


def test_confirmation_and_token_validation_do_not_change_database(api):
    client, engine = api
    token = preview(client, client.get('/backups/export').json()).json()['preview_token']
    assert client.post('/backups/restore', json={'preview_token':token, 'confirmation':'si'}).status_code == 422
    assert client.post('/backups/restore', json={'preview_token':token, 'confirmation':'RIPRISTINA', 'unexpected':True}).status_code == 422
    assert restore(client, token).status_code == 200  # Invalid request bodies did not consume the token.


def test_restore_keeps_online_sqlite_snapshot_before_replacement(monkeypatch):
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    folder = Path.cwd() / f'.backup-check-{uuid4().hex}'
    folder.mkdir()
    filename = folder / 'isolated.db'
    engine = create_engine(f'sqlite:///{filename}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add(Activity(id=1, source='fit', source_id='one', sport='running', name='Before', start_time=datetime(2026, 1, 1)))
            db.commit()
        with TestClient(application(engine), client=('127.0.0.1', 55555)) as client:
            payload = client.get('/backups/export').json()
            payload['data']['activities'][0]['name'] = 'After'
            token = preview(client, payload).json()['preview_token']
            response = restore(client, token)
            assert response.status_code == 200, response.text
            assert response.json()['backup_created']
        snapshots = list((folder / 'backups').glob('*.db'))
        assert len(snapshots) == 1
        with closing(sqlite3.connect(str(snapshots[0]))) as old:
            assert old.execute('select name from activities where id=1').fetchone()[0] == 'Before'
        with Session(engine) as db:
            assert db.get(Activity, 1).name == 'After'
    finally:
        engine.dispose()
        for snapshot in (folder / 'backups').glob('*'):
            snapshot.unlink()
        if (folder / 'backups').exists():
            (folder / 'backups').rmdir()
        filename.unlink(missing_ok=True)
        folder.rmdir()
