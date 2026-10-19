"""Malformed API input must be rejected before calculations or persistence."""
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_analysis, routes_athlete, routes_coach, routes_dashboard
from app.api.errors import request_validation_error
from app.db import health_reports, training_models  # register metadata
from app.db.database import Base, get_db
from app.db.models import DailyMetrics
from app.services.athlete_clock import athlete_today
from app.services.athlete_profile import DEFAULT_PROFILE
from copy import deepcopy


@pytest.fixture
def api(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, request_validation_error)
    app.include_router(routes_dashboard.router)
    app.include_router(routes_analysis.router)
    app.include_router(routes_coach.router)
    app.include_router(routes_athlete.router)
    profile = deepcopy(DEFAULT_PROFILE)
    monkeypatch.setattr(routes_athlete, 'load_profile', lambda: deepcopy(profile))

    def save_profile(value):
        profile.clear()
        profile.update(deepcopy(value))
        return deepcopy(profile)

    monkeypatch.setattr(routes_athlete, 'save_profile', save_profile)

    def isolated_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = isolated_db
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


@pytest.mark.parametrize('path', ['/coach/today', '/activities', '/analysis/summary',
                                  '/analysis/fitness', '/analysis/fitness/sports', '/analysis/quality'])
@pytest.mark.parametrize('ftp', ['NaN', 'Infinity', '-Infinity', '0', '-5'])
def test_ftp_queries_require_positive_finite_values(api, path, ftp):
    client, _ = api
    response = client.get(path, params={'ftp_watts': ftp})
    assert response.status_code == 422


@pytest.mark.parametrize('day', ['invalid', '2026-02-30', '1969-12-31', '9999-12-31'])
def test_invalid_readiness_dates_return_a_validation_error(api, day):
    client, _ = api
    response = client.get('/coach/readiness', params={'date': day})
    assert response.status_code == 422


def test_observed_metrics_reject_future_or_unsupported_dates_without_writes(api):
    client, engine = api
    for day in [date(1969, 12, 31), athlete_today() + timedelta(days=1)]:
        assert client.put('/coach/daily-metrics', json={'date': str(day), 'sleep_hours': 8}).status_code == 422
    with Session(engine) as session:
        assert session.query(DailyMetrics).count() == 0
    assert client.put('/coach/daily-metrics', json={'date': str(athlete_today()), 'sleep_hours': 8}).status_code == 200
    with Session(engine) as session:
        assert session.query(DailyMetrics).one().sleep_hours == 8


@pytest.mark.parametrize('payload', [
    {'cycling': 'invalid'}, {'cycling': None}, {'running': []}, {'swimming': False},
    {'goals': {'event_date': 'not-a-date'}}, {'height_cm': True}, {'height_cm': -1},
    {'ftp_watts': 0}, {'cycling': {'ftp_watts': 'NaN'}}, {'age': 121},
])
def test_legacy_profile_rejects_values_that_would_poison_coach_reads(api, payload):
    client, _ = api
    before = client.get('/athlete/profile').json()
    assert client.put('/athlete/profile', json=payload).status_code == 422
    assert client.get('/athlete/profile').json() == before


def test_legacy_profile_partial_patch_preserves_nested_and_custom_fields(api):
    client, _ = api
    assert client.put('/athlete/profile', json={'cycling': {'threshold_hr_bpm': 160},
                                               'custom': {'preferred_route': 'Lungomare'}}).status_code == 200
    result = client.put('/athlete/profile', json={'cycling': {'ftp_watts': 210},
                                                'goals': {'event_date': '2027-06-01'}})
    assert result.status_code == 200
    profile = result.json()
    assert profile['cycling'] == {'ftp_watts': 210, 'threshold_hr_bpm': 160, 'max_hr_bpm': None}
    assert profile['name'] is None and profile['height_cm'] == 175
    assert profile['custom'] == {'preferred_route': 'Lungomare'}
    assert profile['goals']['event_date'] == '2027-06-01'


def test_legacy_profile_rejects_nonfinite_unknown_fields(api):
    client, _ = api
    before = client.get('/athlete/profile').json()
    response = client.put('/athlete/profile', content='{"custom":{"value":NaN}}', headers={'content-type': 'application/json'})
    assert response.status_code == 422
    assert client.get('/athlete/profile').json() == before


@pytest.mark.parametrize('path,body', [
    ('/coach/daily-metrics', '{"sleep_hours":NaN}'),
    ('/coach/daily-metrics', '{"hrv_ms":Infinity}'),
    ('/coach/workouts/1/evaluate', '{"rpe":-Infinity,"duration_min":60}'),
])
def test_nonstandard_json_numbers_produce_safe_validation_responses(api, path, body):
    client, engine = api
    response = client.request('PUT' if 'daily-metrics' in path else 'POST', path,
                              content=body, headers={'content-type': 'application/json'})
    assert response.status_code == 422
    errors = response.json()['detail']
    assert errors and all(set(error) == {'type', 'loc', 'msg'} for error in errors)
    with Session(engine) as session:
        assert session.query(DailyMetrics).count() == 0
