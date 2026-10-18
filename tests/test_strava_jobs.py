"""OAuth, refresh rotation, privacy boundary and background synchronization."""
import json
from datetime import datetime
from urllib.parse import urlsplit, parse_qs
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.integrations import strava, intervals
from app.api import routes_iron, routes_sync, routes_strava
from app.services import sync_jobs
from app.db.database import Base, get_db
from app.db.iron_models import SyncRun, HealthMetric
from app.db.models import Activity


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    for router in (routes_iron.router, routes_sync.router, routes_strava.router): app.include_router(router)
    def database():
        with Session(engine) as db: yield db
    app.dependency_overrides[get_db] = database
    tokens = {}
    monkeypatch.setattr(strava, 'read_tokens', lambda: dict(tokens))
    monkeypatch.setattr(strava, 'store_tokens', lambda value: (tokens.clear(), tokens.update(value)))
    monkeypatch.setenv('STRAVA_CLIENT_ID', '123')
    monkeypatch.setenv('STRAVA_CLIENT_SECRET', 'secret-test-only')
    monkeypatch.setattr(strava, 'COOLDOWN', 0)
    strava.PENDING.clear(); strava.purge_cache()
    yield TestClient(app, client=('127.0.0.1', 50000)), engine, tokens
    engine.dispose(); strava.PENDING.clear(); strava.purge_cache()


def oauth(client, monkeypatch, tokens):
    response = client.post('/integrations/strava/authorize')
    assert response.status_code == 200
    state = parse_qs(urlsplit(response.json()['authorization_url']).query)['state'][0]
    monkeypatch.setattr(strava, 'request', lambda *a, **k: {'access_token': 'test-access', 'refresh_token': 'test-refresh', 'expires_at': 9999999999, 'athlete': {'id': 123}})
    callback = client.get('/integrations/strava/callback', params={'state': state, 'code': 'code-test', 'scope': 'read,activity:read_all'}, follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers['location'] == '/app#connections'
    return state


def test_oauth_bound_to_browser_and_state_used_once(setup, monkeypatch):
    client, _, tokens = setup
    state = oauth(client, monkeypatch, tokens)
    assert client.get('/integrations/strava/status').json()['connected']
    assert 'test-refresh' not in client.get('/integrations/strava/status').text
    assert client.get('/integrations/strava/callback', params={'state': state, 'code': 'code', 'scope': 'read,activity:read_all'}).status_code == 400
    result = client.post('/integrations/strava/authorize').json()
    state = parse_qs(urlsplit(result['authorization_url']).query)['state'][0]
    client.cookies.clear()
    assert client.get('/integrations/strava/callback', params={'state': state, 'code': 'code', 'scope': 'read,activity:read_all'}).status_code == 400


@pytest.mark.parametrize('scope,denied,status', [('read', '', 403), ('read,activity:read_all', 'access_denied', 400)])
def test_oauth_denial_and_missing_activity_scope(setup, scope, denied, status):
    client, _, tokens = setup
    state = parse_qs(urlsplit(client.post('/integrations/strava/authorize').json()['authorization_url']).query)['state'][0]
    assert client.get('/integrations/strava/callback', params={'state': state, 'code': 'test', 'scope': scope, 'error': denied}).status_code == status
    assert not tokens


def test_oauth_blocks_external_redirect_and_cross_origin(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setenv('STRAVA_REDIRECT_URI', 'https://external.invalid/integrations/strava/callback')
    assert client.post('/integrations/strava/authorize').status_code == 409
    assert client.post('/integrations/strava/authorize', headers={'Origin': 'https://external.invalid'}).status_code == 403


def test_refresh_rotation_is_saved_before_next_call(setup, monkeypatch):
    _, _, tokens = setup
    tokens.update(access_token='expired', refresh_token='old-refresh', expires_at=1, scopes=['activity:read'])
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs['data']['refresh_token'])
        return {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_at': 9999999999}
    monkeypatch.setattr(strava, 'request', request)
    assert strava.access_token() == 'new-access'
    assert strava.access_token() == 'new-access'
    assert calls == ['old-refresh'] and tokens['refresh_token'] == 'new-refresh'


def test_strava_activity_is_only_transient_and_removed_after_refresh(setup, monkeypatch):
    client, engine, tokens = setup
    tokens.update(access_token='test', refresh_token='refresh', expires_at=9999999999)
    activities = [{'id': 1, 'name': 'Personal only', 'sport_type': 'Run', 'distance': 10000, 'map': {'secret': 'never-store'}}]
    monkeypatch.setattr(strava, 'request', lambda *a, **k: list(activities))
    response = client.post('/sync/strava?days=90')
    assert response.status_code == 202
    run = client.get(f"/sync/runs/{response.json()['run_id']}").json()
    assert run['status'] == 'success' and 'Personal only' not in json.dumps(run)
    assert len(client.get('/integrations/strava/activities').json()['activities']) == 1
    with Session(engine) as db: assert db.query(Activity).count() == 0
    assert 'secret' not in json.dumps(strava.CACHE)
    activities.clear(); client.post('/sync/strava')
    assert client.get('/integrations/strava/activities').json()['activities'] == []


def test_expired_cache_is_hidden_even_without_successful_network(setup, monkeypatch):
    client, _, _ = setup
    strava.CACHE.update(activities=[{'id': 1}], expires_at=1)
    assert client.get('/integrations/strava/activities').json()['activities'] == []
    assert strava.CACHE['activities'] == []


def test_disconnect_clears_cache_even_if_revoke_fails(setup, monkeypatch):
    client, _, tokens = setup
    tokens.update(refresh_token='test-refresh'); strava.CACHE.update(activities=[{'id': 1}], expires_at=9999999999)
    def failed(*args, **kwargs): raise strava.StravaError('Test', 502)
    monkeypatch.setattr(strava, 'request', failed)
    result = client.delete('/integrations/strava').json()
    assert result['deleted'] and not result['remote_revoked']
    assert not tokens and not strava.CACHE['activities']


def test_rate_limit_blocks_subsequent_calls_without_leaking_response(setup, monkeypatch):
    response = Mock(status_code=429, headers={'Retry-After': '3600'}, content=b'private')
    remote = Mock(return_value=response); monkeypatch.setattr(strava.requests, 'request', remote)
    with pytest.raises(strava.StravaError) as error: strava.request('GET', '/test')
    assert error.value.retry_after == 3600 and 'private' not in str(error.value)
    with pytest.raises(strava.StravaError): strava.request('GET', '/test')
    assert remote.call_count == 1


def test_background_intervals_both_records_partial_and_preserves_last_full_success(setup, monkeypatch):
    client, engine, _ = setup
    monkeypatch.setattr(routes_sync, 'list_activities', lambda days: [{'id': 'i1', 'type': 'Run', 'start_date': '2026-10-15T08:00:00Z', 'moving_time': 3600}])
    def denied(*a, **k): raise intervals.IntervalsError('Wellness negato', 403)
    monkeypatch.setattr(intervals, 'get_remote', denied)
    response = client.post('/sync/intervals?background=true&resources=both')
    assert response.status_code == 200
    result = client.get(f"/sync/runs/{response.json()['run_id']}").json()
    assert result['status'] == 'partial'
    from app.db.sync_models import IntervalsState
    with Session(engine) as db:
        assert db.query(Activity).count() == 1
        assert db.get(IntervalsState, 1).last_success_at is None


def test_background_wellness_idempotent_and_run_polling(setup, monkeypatch):
    client, engine, _ = setup
    monkeypatch.setattr(intervals, 'get_remote', lambda *a, **k: [{'id': '2026-10-15', 'ctl': 50, 'atl': 60, 'steps': 0}])
    for _ in range(2):
        body = client.post('/sync/intervals?background=true&resources=wellness').json()
        assert client.get(f"/sync/runs/{body['run_id']}").json()['status'] == 'success'
    with Session(engine) as db:
        assert db.query(HealthMetric).count() == 4 and db.query(SyncRun).count() == 2
    assert client.get('/sync/runs/99999').status_code == 404


def test_per_provider_job_lock(setup):
    client, _, _ = setup
    sync_jobs.LOCKS['strava'].acquire()
    try: assert client.post('/sync/strava').status_code == 409
    finally: sync_jobs.LOCKS['strava'].release()
