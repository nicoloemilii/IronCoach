"""HTTP contracts use isolated data and never contact provider accounts."""
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes_effort_records import router
from app.db.database import Base, get_db
from app.db.effort_models import EffortAnalysis, EffortScan
from app.db.models import Activity
from app.services import effort_jobs, effort_records


@pytest.fixture
def api(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Activity(source='fit', source_id='synthetic', sport='cycling',
                        name='Synthetic ride', start_time=datetime(2026, 1, 1)))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    def isolated():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = isolated
    monkeypatch.setattr(effort_records, 'provider_available', lambda: False)
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_read_has_explicit_pending_coverage_and_no_analysis_or_fetch(api, monkeypatch):
    client, engine = api
    def forbidden(*args, **kwargs):
        raise AssertionError('GET must not calculate curves or retrieve telemetry')
    monkeypatch.setattr(effort_records, 'best_power_efforts', forbidden)
    monkeypatch.setattr(effort_records.intervals, 'get_activity_remote', forbidden)
    response = client.get('/athlete/effort-records')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'private, no-store'
    result = response.json()
    assert result['coverage']['total'] == result['coverage']['pending'] == 1
    assert result['coverage']['complete_index'] is False
    assert result['provider'] == {'can_fetch': False}
    assert result['active_scan'] is result['last_scan'] is None
    assert len(result['records']) == 8
    with Session(engine) as db:
        assert db.query(EffortAnalysis).count() == db.query(EffortScan).count() == 0
        assert db.query(Activity).one().name == 'Synthetic ride'


@pytest.mark.parametrize('suffix', ['?period=unknown', '?sport=all', '?period=2026-01-01'])
def test_invalid_record_scopes_are_rejected(api, suffix):
    assert api[0].get('/athlete/effort-records' + suffix).status_code == 422


@pytest.mark.parametrize('body', [dict(mode='automatic'), dict(mode='stored', refresh='true'),
                                 dict(mode='stored', athlete_id=2)])
def test_scan_payload_is_strict(api, monkeypatch, body):
    monkeypatch.setattr(effort_jobs, 'launch', lambda *args, **kwargs: pytest.fail('invalid launch'))
    assert api[0].post('/athlete/effort-records/scans', json=body).status_code == 422


def test_scan_starts_only_on_explicit_post_and_passes_requested_mode(api, monkeypatch):
    calls = []
    monkeypatch.setattr(effort_jobs, 'launch', lambda db, **kwargs: calls.append(kwargs) or dict(id='test', status='queued'))
    response = api[0].post('/athlete/effort-records/scans', json={'mode': 'stored', 'refresh': True})
    assert response.status_code == 200
    assert response.json()['status'] == 'queued'
    assert response.headers['cache-control'] == 'private, no-store'
    assert calls == [{'mode': 'stored', 'refresh': True}]


def test_poll_and_cancel_unknown_job_never_reveal_another_database(api):
    for method, suffix in [('get', ''), ('post', '/cancel')]:
        response = getattr(api[0], method)('/athlete/effort-records/scans/foreign-job' + suffix)
        assert response.status_code == 404


def test_old_persisted_running_job_is_reported_interrupted_without_get_write(api):
    client, engine = api
    with Session(engine) as db:
        db.add(EffortScan(id='old', status='running', mode='stored'))
        db.commit()
    result = client.get('/athlete/effort-records').json()
    assert result['active_scan'] is None
    assert result['last_scan']['status'] == 'interrupted'
    assert result['last_scan']['error_code'] == 'server_restart'
    with Session(engine) as db:
        assert db.get(EffortScan, 'old').status == 'running'
