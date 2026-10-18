"""Home dates and explanations use actual observations, including non-triathlon sports."""
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_iron, routes_experience
from app.db.database import Base, get_db
from app.db.models import Activity, Workout
from app.db.iron_models import HealthMetric
from app.services import athlete_profile, planning, readiness, iron_coach

TODAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 18, 12)


@pytest.fixture
def home_api(monkeypatch):
    for module in (routes_iron, routes_experience, iron_coach):
        monkeypatch.setattr(module, 'athlete_today', lambda: TODAY)
    for module in (routes_iron, readiness, iron_coach):
        if hasattr(module, 'utc_now_naive'):
            monkeypatch.setattr(module, 'utc_now_naive', lambda: NOW)
    monkeypatch.setattr(athlete_profile, 'load_profile', lambda: {})
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI(); app.include_router(routes_iron.router); app.include_router(routes_experience.router)
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    yield TestClient(app, client=('127.0.0.1', 55000)), engine
    engine.dispose()


def activity(db, identifier, sport, day, minutes=30):
    db.add(Activity(source='manual', source_id=identifier, sport=sport, name=identifier,
                    start_time=datetime.combine(day, datetime.min.time()) + timedelta(hours=7),
                    duration_seconds=minutes * 60, distance_meters=1000, elevation_gain_meters=50, load=10))


def wellness(db, day, kind, value):
    db.add(HealthMetric(date=str(day), kind=kind, value=value, unit={'sleep_hours':'ore','hrv_ms':'ms','resting_hr_bpm':'bpm','ctl':'punti','atl':'punti','tsb':'punti'}[kind],
                        source='intervals', observed_at=str(day), synced_at=NOW.isoformat()))


def test_weekly_summary_includes_gym_walking_and_every_other_sport_once(home_api):
    client, engine = home_api
    with Session(engine) as db:
        for i, sport in enumerate(['swimming','cycling','running','WeightTraining','Walk','Hike','Yoga','gym']):
            activity(db, str(i), sport, TODAY - timedelta(days=1))
        db.commit()
    body = client.get('/experience/summary').json()
    assert body['sports']['strength']['sessions'] == 2
    assert body['sports']['walk']['sessions'] == 2
    assert body['sports']['other']['sessions'] == 1
    assert sum(item['sessions'] for item in body['sports'].values()) == 8
    assert sum(item['minutes'] for item in body['sports'].values()) == 240


def test_historical_home_does_not_leak_later_activities_or_wellness(home_api):
    client, engine = home_api
    yesterday = TODAY - timedelta(days=1)
    with Session(engine) as db:
        activity(db, 'past', 'Walk', yesterday)
        activity(db, 'later', 'running', TODAY, minutes=90)
        wellness(db, yesterday, 'sleep_hours', 7)
        wellness(db, TODAY, 'sleep_hours', 4)
        db.commit()
    body = client.get(f'/dashboard/home?day={yesterday}').json()
    assert body['date'] == str(yesterday) and body['historical'] is True
    assert body['week']['activities'] == 1 and body['week']['hours'] == .5
    sleep = next(item for item in body['condition'] if item['kind'] == 'sleep_hours')
    assert sleep['latest']['value'] == 7 and sleep['latest']['date'] == str(yesterday)
    assert all(point['date'] <= str(yesterday) for point in sleep['points'])
    assert body['recovery']['date'] == str(yesterday) and body['recovery']['readiness'] is not None
    assert body['coach']['recorded_activities'] == 1
    summary = client.get(f'/experience/summary?end={yesterday}').json()
    assert summary['end'] == str(yesterday) and summary['sports']['walk']['sessions'] == 1
    assert summary['sports']['run']['sessions'] == 0


def test_missing_today_recovery_explains_yesterdays_existing_observations(home_api):
    client, engine = home_api
    yesterday = TODAY - timedelta(days=1)
    with Session(engine) as db:
        for ago in range(1, 10):
            day = TODAY - timedelta(days=ago)
            for kind, value in [('sleep_hours',7.2),('hrv_ms',57),('resting_hr_bpm',52)]:
                wellness(db, day, kind, value)
        wellness(db, TODAY, 'ctl', 48)
        db.commit()
    body = client.get('/dashboard/home').json()
    assert body['recovery']['readiness'] is None
    assert body['recovery']['available_inputs'] == []
    assert str(yesterday) in body['recovery']['explanation']
    assert len(body['recovery']['latest_observations']) == 3
    previous = client.get(f'/dashboard/home?day={yesterday}').json()['recovery']
    assert previous['readiness'] is not None and len(previous['available_inputs']) == 3
    assert 'facoltativi' in previous['explanation']


def test_coach_explains_insufficient_evaluations_and_unconfirmed_sessions(home_api):
    client, engine = home_api
    with Session(engine) as db:
        activity(db, 'unmatched', 'running', TODAY - timedelta(days=1))
        db.add(Workout(date=str(TODAY-timedelta(days=1)),sport='running',name='Unconfirmed',duration_min=30,payload_json=json.dumps({'sport':'running','duration_min':30}),status='planned'))
        db.commit()
    coach = client.get('/dashboard/home').json()['coach']
    assert coach['score'] is None
    assert '0 attività valutabili su 1' in coach['unavailable_reason']
    assert '1 sedute passate' in coach['unavailable_reason']
    assert 'almeno 3' in coach['unavailable_reason']


@pytest.mark.parametrize('path', ['/dashboard/home?day=', '/experience/summary?end='])
@pytest.mark.parametrize('day', ['2026-10-19', '1969-12-31', 'invalid'])
def test_future_or_invalid_history_dates_are_rejected(home_api, path, day):
    assert home_api[0].get(path + day).status_code == 422


def test_dashboard_keeps_known_ascent_when_another_activity_has_no_measure(home_api):
    client,engine=home_api
    with Session(engine) as db:
        activity(db,'known','running',TODAY-timedelta(days=1))
        activity(db,'unknown','strength',TODAY-timedelta(days=1))
        db.flush(); db.query(Activity).filter_by(source_id='unknown').one().elevation_gain_meters=None
        db.commit()
    body=client.get('/dashboard/home').json()
    assert body['week']['elevation_m']==50
    assert body['week']['elevation_known_m']==50
    assert body['week']['elevation_missing_activities']==1
    assert len(body['week_history'])==4
    assert body['week_assessments']['elevation_m']['tone']!='unknown'


def test_home_and_coach_use_recorded_work_even_without_feedback(home_api):
    client,engine=home_api
    with Session(engine) as db:
        for i in range(4):
            activity(db,str(i),'running',TODAY-timedelta(days=7*i+1))
        db.commit()
    for endpoint in ('/dashboard/home','/coach/overview'):
        value=client.get(endpoint).json(); coach=value.get('coach',value)
        assert coach['score'] is None  # Execution comparison remains unavailable.
        assert coach['training_assessment']['judgment']=='Carico coerente con lo storico'
        assert coach['training_assessment']['tone']=='good'


def test_high_load_context_limits_both_proposal_and_saved_workout(home_api, monkeypatch):
    from app.api import routes_coach
    from app.services import coach
    monkeypatch.setattr(coach, 'athlete_today', lambda *args: TODAY)
    monkeypatch.setattr(coach, 'utc_now_naive', lambda: NOW)
    client, engine = home_api
    client.app.include_router(routes_coach.router)
    with Session(engine) as db:
        for i, load in enumerate([149,100,100,100,100]):
            activity(db,str(i),'running',TODAY-timedelta(days=7*i+1))
            db.flush()
            db.query(Activity).filter_by(source_id=str(i)).one().load=load
        wellness(db,TODAY,'sleep_hours',7)
        wellness(db,TODAY,'ctl',48)
        wellness(db,TODAY,'atl',53)
        wellness(db,TODAY,'tsb',-5)
        db.commit()
    proposal=client.get('/coach/today')
    assert proposal.status_code==200, proposal.text
    today=proposal.json()
    assert today['coach_context']['session_policy']['mode']=='easy'
    assert today['decision']['recommendation'] in ('easy','recovery')
    assert today['workout']['duration_min']<=30
    saved=client.post('/coach/workouts',json={'sport':'running'})
    assert saved.status_code==200, saved.text
    assert saved.json()['duration_min']<=30 and saved.json()['expected_rpe']<=3
    assert client.post('/coach/workouts',json={'sport':'running'}).status_code==409
