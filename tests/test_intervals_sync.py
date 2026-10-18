from unittest.mock import Mock
import json
import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api import routes_sync as routes
from app.integrations import intervals, intervals_credentials as vault
from app.db.database import Base, get_db
from app.db.models import Activity

KEY = 'unit-test-not-a-real-api-key'


@pytest.fixture
def setup(monkeypatch):
    saved = {'api_key': '', 'athlete_id': '0'}
    monkeypatch.setattr(vault, 'credentials', lambda: dict(saved))
    monkeypatch.setattr(vault, 'store', lambda key, athlete: saved.update(api_key=key, athlete_id=athlete, source='protected_local'))
    monkeypatch.setattr(vault, 'disconnect', lambda: saved.update(api_key=''))
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI(); app.include_router(routes.router)
    def db():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_db] = db
    client = TestClient(app, client=('127.0.0.1', 50000))
    yield client, saved, engine
    engine.dispose()


def response(status=200, content=None):
    value = Mock(status_code=status)
    value.json.return_value = content
    return value


def test_refresh_starts_all_connected_providers_with_correct_resource_windows(setup,monkeypatch):
    client,saved,_=setup
    from app.integrations import strava
    from app.services import sync_jobs
    saved['api_key']=KEY
    monkeypatch.setattr(strava,'read_tokens',lambda:{'refresh_token':'not-a-real-token'})
    calls=[]
    def launch(source,days,resources,db,background_tasks):
        assert background_tasks is not None
        calls.append((source,days,resources)); return {'run_id':len(calls)}
    monkeypatch.setattr(sync_jobs,'launch',launch)
    result=client.post('/sync/refresh',json={})
    assert result.status_code==200
    assert calls==[('intervals',35,'both'),('strava',90,'activities')]
    assert len(result.json()['runs'])==2 and not result.json()['messages']
    assert KEY not in result.text and 'not-a-real-token' not in result.text


def test_refresh_reuses_an_active_job_instead_of_starting_another(setup,monkeypatch):
    client,saved,engine=setup
    from app.integrations import strava
    from app.services import sync_jobs
    from app.db.iron_models import SyncRun
    saved['api_key']=KEY;monkeypatch.setattr(strava,'read_tokens',lambda:{})
    with Session(engine) as db:
        row=SyncRun(source='intervals',days=35,resources='both',status='running');db.add(row);db.commit();identifier=row.id
    assert sync_jobs.LOCKS['intervals'].acquire(False)
    try:
        result=client.post('/sync/refresh').json()
        assert result['runs']==[{'source':'intervals','run_id':identifier,'reused':True}]
    finally: sync_jobs.LOCKS['intervals'].release()


def test_refresh_with_no_connections_is_local_only_and_never_returns_secrets(setup,monkeypatch):
    client,_,_=setup
    from app.integrations import strava
    monkeypatch.setattr(strava,'read_tokens',lambda:{})
    assert client.post('/sync/refresh').json()=={'runs':[],'messages':[]}
    assert client.post('/sync/refresh',headers={'Origin':'https://unrelated.invalid'}).status_code==403


def test_intervals_preserves_zero_ascent_and_reads_actual_power_field_names():
    value=intervals.activity_to_data({'id':'synthetic-zero','total_elevation_gain':0,'icu_average_watts':150,'icu_weighted_avg_watts':160})
    assert value['elevation_gain_meters']==0
    assert value['avg_power']==150 and value['normalized_power']==160


def connect(client, monkeypatch):
    monkeypatch.setattr(intervals.requests, 'get', lambda *a, **k: response(content={'id': 'i123', 'name': 'Atleta'}))
    result = client.post('/integrations/intervals/connect', json={'api_key': KEY})
    assert result.status_code == 200, result.text
    assert KEY not in result.text


def test_connect_verify_disconnect_never_returns_key(setup, monkeypatch):
    client, saved, _ = setup
    assert not client.get('/integrations/intervals/status').json()['configured']
    connect(client, monkeypatch)
    status = client.get('/integrations/intervals/status').json()
    assert status['connected'] and status['athlete_name'] == 'Atleta'
    assert KEY not in json.dumps(status)
    assert client.post('/integrations/intervals/verify').json()['connected']
    assert not client.delete('/integrations/intervals').json()['configured']
    assert saved['api_key'] == ''


@pytest.mark.parametrize('status', [401, 403, 429, 500, 302])
def test_failed_verification_does_not_save_credentials(setup, monkeypatch, status):
    client, saved, _ = setup
    monkeypatch.setattr(intervals.requests, 'get', lambda *a, **k: response(status=status))
    result = client.post('/integrations/intervals/connect', json={'api_key': KEY})
    assert result.status_code == (status if status in (401, 403, 429) else 502)
    assert not saved['api_key'] and KEY not in result.text


def test_missing_credentials_and_failed_network_are_actionable(setup, monkeypatch):
    client, saved, _ = setup
    assert client.post('/sync/intervals').status_code == 409
    saved['api_key'] = KEY
    def failure(*a, **k): raise requests.Timeout('sensitive ' + KEY)
    monkeypatch.setattr(intervals.requests, 'get', failure)
    result = client.post('/sync/intervals')
    assert result.status_code == 502 and KEY not in result.text
    assert client.get('/integrations/intervals/status').json()['last_result']['status'] == 'error'


def test_sync_counts_repeated_and_crosssource_activities_correctly(setup, monkeypatch):
    client, _, engine = setup
    connect(client, monkeypatch)
    records = [{'id': 'a1', 'type': 'Ride', 'name': 'Bici', 'start_date': '2026-10-16T08:00:00Z', 'moving_time': 3600, 'distance': 20000, 'icu_training_load': 40}]
    captured = []
    def remote(*args, **kwargs):
        captured.append((args, kwargs)); return response(content=records)
    monkeypatch.setattr(intervals.requests, 'get', remote)
    one = client.post('/sync/intervals?days=90')
    assert one.status_code == 200, one.text
    assert one.json()['new_activities'] == 1
    two = client.post('/sync/intervals?days=90').json()
    assert two['new_activities'] == 0 and two['already_synced_or_matched'] == 1
    records[0]['icu_training_load'] = 55
    client.post('/sync/intervals?days=90')
    with Session(engine) as db:
        row = db.query(Activity).one(); assert row.load == 55
        row.source = 'fit'; db.commit()
    three = client.post('/sync/intervals').json()
    assert three['new_activities'] == 0 and three['already_synced_or_matched'] == 1
    assert captured[0][1]['auth'] == ('API_KEY', KEY)
    assert captured[0][1]['allow_redirects'] is False
    status = client.get('/integrations/intervals/status').json()
    assert status['last_success_at'] and KEY not in json.dumps(status)


def test_bad_item_does_not_hide_other_results_or_poison_session(setup, monkeypatch):
    client, _, _ = setup; connect(client, monkeypatch)
    records = [{'id': 'missing', 'name': KEY}, {'id': 'valid', 'type': 'Run', 'start_date': '2026-10-16T08:00:00Z', 'moving_time': 1000}, 'bad']
    monkeypatch.setattr(intervals.requests, 'get', lambda *a, **k: response(content=records))
    result = client.post('/sync/intervals').json()
    assert result['new_activities'] == 1 and len(result['errors']) == 2 and result['status'] == 'partial'
    assert KEY not in json.dumps(result)
    assert client.get('/integrations/intervals/status').json()['last_success_at'] is None


def test_local_only_and_cross_origin_guards(setup):
    client, _, _ = setup
    assert client.post('/integrations/intervals/connect', json={'api_key': KEY}, headers={'Origin':'https://untrusted.invalid'}).status_code == 403
    remote = TestClient(client.app, client=('192.168.1.20', 50000))
    assert remote.post('/sync/intervals').status_code == 403
    assert remote.delete('/integrations/intervals').status_code == 403


def test_concurrent_sync_is_rejected(setup):
    client, _, _ = setup
    routes.SYNC_LOCK.acquire()
    try:
        assert client.post('/sync/intervals').status_code == 409
        assert client.delete('/integrations/intervals').status_code == 409
    finally: routes.SYNC_LOCK.release()
