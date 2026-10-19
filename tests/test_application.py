"""Exercise the real router graph against an isolated database and profile."""
import copy
import importlib
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import database
from app.services import athlete_profile, planning
from app.services.athlete_clock import athlete_today


@pytest.fixture
def client(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setattr(athlete_profile, 'load_profile', lambda: copy.deepcopy(athlete_profile.DEFAULT_PROFILE))
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    from app.api import routes_coach, routes_athlete
    monkeypatch.setattr(routes_coach, 'load_profile', lambda: copy.deepcopy(athlete_profile.DEFAULT_PROFILE))
    monkeypatch.setattr(routes_athlete, 'load_profile', lambda: copy.deepcopy(athlete_profile.DEFAULT_PROFILE))
    module = importlib.import_module('app.main')
    database.Base.metadata.create_all(engine)
    def db():
        with Session(engine) as session:
            yield session
    module.app.dependency_overrides[database.get_db] = db
    yield TestClient(module.app,base_url='http://127.0.0.1',client=('127.0.0.1',51000))
    module.app.dependency_overrides.clear()
    engine.dispose()


def test_all_page_read_apis_are_wired(client):
    for path in ['/app', '/web/app.js', '/web/style.css', '/coach/today', '/activities', '/planning/profile',
                 '/planning/week', '/experience/summary', '/experience/checkin', '/experience/race-preparation', '/integrations/garmin/status', '/integrations/intervals/status']:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)


def test_browser_root_opens_web_app_without_breaking_json_status(client):
    browser = client.get('/', headers={'Accept': 'text/html'}, follow_redirects=False)
    assert browser.status_code == 307 and browser.headers['location'] == '/app'
    page = client.get('/', headers={'Accept': 'text/html'})
    assert page.status_code == 200 and '<title>Iron Coach</title>' in page.text
    status = client.get('/', headers={'Accept': 'application/json'})
    assert status.status_code == 200 and status.json()['app'] == 'Ironman Coach'


@pytest.mark.parametrize('path', ['/app', '/web/style.css', '/web/metric-status.js', '/web/app.js'])
def test_web_assets_require_cache_revalidation(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-cache'


def test_calendar_checkin_feedback_and_export_contract(client):
    checkin = {'sleep_hours': 8, 'stress_score': 25, 'fatigue': None, 'soreness': None, 'motivation': None, 'sleep_quality': None}
    assert client.put('/experience/checkin', json=checkin).status_code == 200
    future = athlete_today() + timedelta(days=7)
    response = client.post('/planning/week', json={'start': str(future)})
    assert response.status_code == 200, response.text
    assert any(day['sessions'] for day in response.json()['days'])
    response = client.post('/coach/workouts', json={'sport': 'cycling'})
    assert response.status_code == 200, response.text
    workout = response.json()
    response = client.put(f"/experience/workouts/{workout['id']}/feedback", json={'rpe': 4, 'completion_percent': 100})
    assert response.status_code == 200, response.text
    assert client.get(f"/workouts/{workout['id']}/export.fit").status_code == 200
    delivery = client.post(f"/workouts/{workout['id']}/garmin/send").json()
    assert delivery['status'] == 'blocked' and not delivery['remote_published']


def test_pain_pauses_new_daily_prescriptions(client):
    assert client.put('/experience/checkin', json={'pain': True}).status_code == 200
    day = client.get('/coach/today').json()
    assert day['training_paused'] and day['workout']['duration_min'] == 0
    assert client.post('/coach/workouts', json={'sport': 'cycling'}).status_code == 409


def test_real_app_returns_validation_errors_for_nonstandard_json_numbers(client):
    response = client.put('/coach/daily-metrics', content='{"sleep_hours":NaN}',
                          headers={'content-type': 'application/json'})
    assert response.status_code == 422
    assert 'input' not in response.json()['detail'][0]
