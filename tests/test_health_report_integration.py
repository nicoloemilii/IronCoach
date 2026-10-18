"""Symptom restrictions use one policy across Home, Coach and persisted workouts."""
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_coach, routes_health_reports, routes_iron
from app.db.database import Base, get_db
from app.db.iron_models import HealthMetric
from app.db.models import DailyMetrics, Workout
from app.services import athlete_snapshot, athlete_state, coach, health_reports, iron_coach, planning, readiness, training_load
from app.services.athlete_profile import DEFAULT_PROFILE

TODAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 18, 12)


@pytest.fixture
def integrated_api(monkeypatch):
    for module in (routes_coach, routes_health_reports, routes_iron, athlete_snapshot, athlete_state, coach,
                   health_reports, iron_coach, readiness, training_load):
        if hasattr(module, 'athlete_today'):
            monkeypatch.setattr(module, 'athlete_today', lambda *args: TODAY)
        if hasattr(module, 'utc_now_naive'):
            monkeypatch.setattr(module, 'utc_now_naive', lambda: NOW)
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    monkeypatch.setattr(routes_coach, 'load_profile', lambda: deepcopy(DEFAULT_PROFILE))
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(DailyMetrics(date=str(TODAY), sleep_hours=8, hrv_ms=60, hrv_baseline_ms=60,
                            resting_hr_bpm=50, resting_hr_baseline_bpm=50, body_battery=90, stress_score=10))
        for kind, value in [('ctl',50), ('atl',45), ('tsb',5)]:
            db.add(HealthMetric(date=str(TODAY), kind=kind, value=value, unit='punti', source='intervals',
                                observed_at=str(TODAY), synced_at=NOW.isoformat()))
        db.commit()
    application = FastAPI()
    for module in (routes_coach, routes_health_reports, routes_iron):
        application.include_router(module.router)
    def database():
        with Session(engine) as db:
            yield db
    application.dependency_overrides[get_db] = database
    with TestClient(application, client=('127.0.0.1', 51000)) as client:
        yield client, engine
    engine.dispose()


def create_report(client, **changes):
    response = client.post('/health/reports', json={'date': str(TODAY), 'kind': 'cold', 'severity':1, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def policy(client):
    response = client.get('/dashboard/home')
    assert response.status_code == 200, response.text
    return response.json()['coach']['training_assessment']['session_policy']


@pytest.mark.parametrize('kind,severity,location', [('cold',1,''), ('illness',1,''), ('injury',1,'ginocchio'), ('contracture',3,'polpaccio')])
def test_active_report_stops_home_coach_and_saved_workout_consistently(integrated_api, kind, severity, location):
    client, engine = integrated_api
    create_report(client, kind=kind, severity=severity, body_location=location)
    home_policy = policy(client)
    assert home_policy == {'mode':'rest', 'max_duration_min':0, 'max_rpe':0}
    overview = client.get('/coach/overview').json()['training_assessment']
    assert overview['session_policy'] == home_policy
    today = client.get('/coach/today').json()
    assert today['training_paused'] is True
    assert today['coach_context']['session_policy'] == home_policy
    assert today['workout']['duration_min'] == 0 and today['workout']['steps'] == []
    assert client.post('/coach/workouts', json={'sport':'running'}).status_code == 409
    assert client.get('/athlete/derived-profile').json()['restrictions']['training_paused'] is True
    with Session(engine) as db:
        assert db.query(Workout).count() == 0


def test_mild_contracture_caps_both_proposal_and_saved_workout_at_twenty_minutes_rpe_two(integrated_api):
    client, engine = integrated_api
    assert policy(client)['mode'] == 'normal'
    create_report(client, kind='contracture', body_location='polpaccio sinistro', severity=2)
    home_policy = policy(client)
    assert home_policy == {'mode':'easy', 'max_duration_min':20, 'max_rpe':2}
    today = client.get('/coach/today').json()
    assert today['training_paused'] is False
    assert today['workout']['duration_min'] == 20 and today['workout']['expected_rpe'] == 2
    assert all(step['target_rpe'] <= 2 for step in today['workout']['steps'])
    saved = client.post('/coach/workouts', json={'sport':'running'})
    assert saved.status_code == 200, saved.text
    value = saved.json()
    assert value['duration_min'] == 20 and value['expected_rpe'] == 2
    assert sum(step['duration_min'] for step in value['steps']) == 20
    assert all(step['target_rpe'] <= 2 for step in value['steps'])
    # The restriction cannot be circumvented by requesting another discipline.
    assert client.post('/coach/workouts', json={'sport':'cycling'}).status_code == 409
    with Session(engine) as db:
        assert db.query(Workout).count() == 1 and db.query(Workout).one().duration_min == 20


def test_resolving_cold_removes_its_restriction_without_retaining_an_old_pause(integrated_api):
    client, _ = integrated_api
    report = create_report(client, date=str(TODAY-timedelta(days=1)))
    assert client.get('/coach/today').json()['training_paused'] is True
    response = client.patch(f"/health/reports/{report['id']}", json={'status':'resolved'})
    assert response.status_code == 200
    assert policy(client) == {'mode':'normal', 'max_duration_min':None, 'max_rpe':None}
    today = client.get('/coach/today').json()
    assert today['training_paused'] is False
    assert today['workout']['duration_min'] > 20
    assert client.post('/coach/workouts', json={'sport':'running'}).status_code == 200


def test_mild_contracture_does_not_override_a_stricter_rest_policy(integrated_api):
    client, engine = integrated_api
    create_report(client, kind='contracture', body_location='coscia', severity=1)
    with Session(engine) as db:
        db.query(HealthMetric).filter_by(date=str(TODAY),kind='tsb').one().value = -35
        db.commit()
    assert policy(client) == {'mode':'rest','max_duration_min':0,'max_rpe':0}
    assert client.get('/coach/today').json()['training_paused'] is True
    assert client.post('/coach/workouts', json={'sport':'running'}).status_code == 409


def test_current_symptom_does_not_rewrite_historical_home(integrated_api):
    client, _ = integrated_api
    create_report(client)
    response = client.get(f'/dashboard/home?day={TODAY-timedelta(days=1)}')
    assert response.status_code == 200, response.text
    assert response.json()['coach']['training_assessment']['session_policy']['mode'] == 'normal'
    assert policy(client)['mode'] == 'rest'
