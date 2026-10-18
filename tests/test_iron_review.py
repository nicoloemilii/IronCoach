"""Regression cases found while reviewing the new personal Iron Coach API."""
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_iron
from app.db.database import Base, get_db
from app.db.experience_models import ActivityIdentity, SessionFeedback
from app.db.iron_models import ActivityObservation, DuplicateCandidate, HealthMetric, RaceGoal
from app.db.models import Activity, DailyMetrics, Workout, WorkoutEvaluation
from app.db.repository import save_activity
from app.integrations import strava
from app.services import health, iron_coach, race_gap

TODAY = date(2026, 10, 17)
NOW = datetime(2026, 10, 17, 10)


@pytest.fixture
def api(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(strava, 'read_tokens', lambda: {})
    for module in (routes_iron, iron_coach, race_gap):
        monkeypatch.setattr(module, 'athlete_today', lambda: TODAY)
        monkeypatch.setattr(module, 'utc_now_naive', lambda: NOW)
    application = FastAPI()
    application.include_router(routes_iron.router)

    def isolated_db():
        with Session(engine) as db:
            yield db

    application.dependency_overrides[get_db] = isolated_db
    yield TestClient(application, client=('127.0.0.1', 50000)), engine
    engine.dispose()


def data(source='fit', identifier='activity', **changes):
    record = dict(source=source, source_id=identifier, sport='running', start_time=NOW - timedelta(hours=2),
                  name='Synthetic', duration_seconds=3600, distance_meters=10000, elevation_gain_meters=0, load=30)
    record.update(changes)
    return record


def link(db, activity, duration=60, expected_rpe=4):
    payload = dict(sport='running', duration_min=duration, steps=[])
    if expected_rpe is not None:
        payload['expected_rpe'] = expected_rpe
    workout = Workout(date=str(TODAY), sport='running', name='Synthetic plan', duration_min=duration,
                      payload_json=json.dumps(payload))
    db.add(workout)
    db.flush()
    feedback = SessionFeedback(workout_id=workout.id, activity_id=activity.id,
                               payload_json=json.dumps({'activity_id': activity.id, 'rpe': 4}))
    db.add(feedback)
    db.commit()
    return feedback


@pytest.mark.parametrize('duration,planned,target', [(None, 60, 4), (3600, 0, 4), (3600, 60, None)])
def test_incomplete_execution_evidence_does_not_receive_grade(api, duration, planned, target):
    client, engine = api
    with Session(engine) as db:
        activity = Activity(**data(duration_seconds=duration))
        db.add(activity)
        db.flush()
        identifier = activity.id
        link(db, activity, duration=planned, expected_rpe=target)
    response = client.get(f'/activities/{identifier}/assessment')
    assert response.status_code == 200
    assert response.json()['execution_assessment']['score'] is None
    assert response.json()['execution_assessment']['judgment'] == 'Non valutabile'
    if duration is None:
        assert response.json()['execution_assessment']['evidence'][0].startswith('Durata eseguita non disponibile;')


def test_unknown_load_does_not_create_zero_fitness_and_remote_null_wins(api):
    client, engine = api
    with Session(engine) as db:
        activity = Activity(**data(load=30))
        db.add(activity)
        db.flush()
        db.add(ActivityObservation(activity_id=activity.id, source_key='intervals:remote',
                                   payload_json=json.dumps({'load': None})))
        db.commit()
    body = client.get('/dashboard/home').json()
    assert body['week']['load'] is None
    assert all(row['latest'] is None for row in body['condition'] if row['kind'] in ('ctl', 'atl', 'tsb'))


def test_dashboard_reads_actual_strava_connection_without_returning_credentials(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(strava, 'read_tokens', lambda: {'refresh_token': 'synthetic-secret', 'athlete_id': 123})
    response = client.get('/dashboard/home')
    assert response.json()['sources']['strava'] == {'status': 'connected', 'connected': True}
    assert 'synthetic-secret' not in response.text
    monkeypatch.setattr(strava, 'read_tokens', lambda: (_ for _ in ()).throw(ValueError('unreadable vault')))
    assert client.get('/dashboard/home').json()['sources']['strava'] == {'status': 'unavailable', 'connected': False}


def test_cleared_manual_metric_is_not_resurrected_from_migration(api):
    _, engine = api
    with Session(engine) as db:
        db.add(DailyMetrics(date=str(TODAY), sleep_hours=None))
        db.add(HealthMetric(date=str(TODAY), kind='sleep_hours', source='manual', value=8,
                            unit='ore', observed_at=str(TODAY), synced_at='migration'))
        db.commit()
        assert health.effective_metrics(db, TODAY).get('sleep_hours') is None
        health.import_wellness(db, [{'id': str(TODAY), 'sleepSecs': 25200}])
        assert health.effective_metrics(db, TODAY)['sleep_hours'] == 7


def test_wellness_malformed_items_are_partial_errors(api):
    _, engine = api
    with Session(engine) as db:
        count, errors = health.import_wellness(db, [None, [], 'invalid', {'id': str(TODAY), 'steps': True},
                                                   {'id': str(TODAY), 'steps': 0}])
        assert count == 1 and len(errors) == 4
        assert health.effective_metrics(db, TODAY)['steps'] == 0


def test_intervals_stress_preserves_native_scale_and_real_zero(api):
    _, engine = api
    with Session(engine) as db:
        db.add(DailyMetrics(date=str(TODAY), stress_score=70))
        db.commit()
        assert health.import_wellness(db, [{'id': str(TODAY), 'stress': 0}]) == (1, [])
        values = health.effective_metrics(db, TODAY)
        assert values['stress_level'] == 0
        assert values['stress_score'] == 70
        metric = db.query(HealthMetric).filter_by(kind='stress_level').one()
        assert metric.unit == 'livello Intervals' and metric.source == 'intervals'


def test_baseline_requires_recent_measurements(api):
    _, engine = api
    with Session(engine) as db:
        records = [{'id': str(TODAY - timedelta(days=i)), 'hrv': 50} for i in range(50, 58)]
        records.append({'id': str(TODAY), 'hrv': 60})
        health.import_wellness(db, records)
        body = health.health_response(db, TODAY - timedelta(days=90), TODAY)
        metric = next(row for row in body['metrics'] if row['kind'] == 'hrv_ms')
        assert metric['baseline'] is None


def test_effective_imported_baselines_require_same_source_and_recent_history(api):
    _, engine = api
    with Session(engine) as db:
        db.add(DailyMetrics(date=str(TODAY), hrv_ms=100, hrv_baseline_ms=120))
        db.commit()
        health.import_wellness(db, [{'id': str(TODAY), 'hrv': 50, 'restingHR': 55}])
        insufficient = health.effective_metrics(db, TODAY)
        assert insufficient['hrv_ms'] == 50 and insufficient['hrv_baseline_ms'] is None
        assert insufficient['resting_hr_baseline_bpm'] is None
        records = [{'id': str(TODAY - timedelta(days=i)), 'hrv': 40, 'restingHR': 50} for i in range(1, 8)]
        health.import_wellness(db, records)
        result = health.effective_metrics(db, TODAY)
        assert result['hrv_baseline_ms'] == 40 and result['resting_hr_baseline_bpm'] == 50


def test_future_timestamp_on_current_day_is_excluded_everywhere(api):
    client, engine = api
    with Session(engine) as db:
        activity = Activity(**data(start_time=NOW + timedelta(hours=3), distance_meters=100000))
        db.add(activity)
        db.flush()
        link(db, activity)
        goal = RaceGoal(name='Synthetic', date='2027-06-01', priority='finish', legs_json=json.dumps([
            {'sport': 'running', 'terrain': 'road', 'distance_km': 10,
             'requirements': {'weekly_hours': 1, 'longest_distance_km': 10}}]))
        db.add(goal)
        db.commit()
        identifier = goal.id
    assert client.get('/dashboard/home').json()['week']['activities'] == 0
    assert client.get('/coach/overview').json()['evidence'] == ['0 attività valutabili su 0 negli ultimi 28 giorni.']
    gap = client.get(f'/goals/{identifier}/gap-analysis').json()
    assert all(row['observed'] is None for row in gap['legs'][0]['requirements'])


def test_unconfirmed_past_plan_prevents_positive_management_judgment(api):
    client, engine = api
    with Session(engine) as db:
        for index in range(3):
            activity = Activity(**data(identifier=f'confirmed-{index}'))
            db.add(activity)
            db.flush()
            link(db, activity)
        db.add(Workout(date=str(TODAY - timedelta(days=2)), sport='running', name='Unreviewed',
                       duration_min=60, status='planned', payload_json='{}'))
        db.commit()
    body = client.get('/coach/overview').json()
    assert body['execution_score'] == 100
    assert body['score'] is None and body['judgment'] == 'Non valutabile'
    assert body['unconfirmed_past_sessions'] == 1
    assert body['actions']


def test_merge_preserves_third_activity_comparisons_and_nested_references(api):
    client, engine = api
    with Session(engine) as db:
        keep = save_activity(db, data())
        remove = save_activity(db, data('intervals', 'remote'))
        third = save_activity(db, data('strava', 'other'))
        keep_id, remove_id, third_id = keep.id, remove.id, third.id
        feedback = link(db, remove)
        feedback.payload_json = json.dumps({'activity_id': remove.id, 'actual': {'activity_id': remove.id}, 'rpe': 4})
        db.add(WorkoutEvaluation(workout_id=feedback.workout_id, activity_id=remove.id,
                                 payload_json=json.dumps({'matched_activity_id': remove.id})))
        candidate = db.query(DuplicateCandidate).filter_by(activity_id=remove.id, other_id=keep.id).one()
        identifier = candidate.id
        # A keep decision between remove and third survives the merge into keep.
        decision = db.query(DuplicateCandidate).filter_by(activity_id=third.id, other_id=remove.id).one()
        decision.status = 'kept'
        db.commit()
    assert client.post(f'/activities/duplicates/{identifier}/merge').status_code == 200
    with Session(engine) as db:
        assert db.get(Activity, remove_id) is None
        relations = db.query(DuplicateCandidate).all()
        assert len(relations) == 1
        assert {relations[0].activity_id, relations[0].other_id} == {keep_id, third_id}
        assert relations[0].status == 'kept'
        content = json.loads(db.query(SessionFeedback).one().payload_json)
        assert content['actual']['activity_id'] == keep_id
        assert json.loads(db.query(WorkoutEvaluation).one().payload_json)['matched_activity_id'] == keep_id


def test_merge_retains_identity_of_legacy_activity(api):
    client, engine = api
    with Session(engine) as db:
        keep = Activity(**data(identifier='keep'))
        remove = Activity(**data('intervals', 'legacy'))
        db.add_all([keep, remove])
        db.flush()
        keep_id = keep.id
        candidate = DuplicateCandidate(activity_id=remove.id, other_id=keep.id)
        db.add(candidate)
        db.commit()
        identifier = candidate.id
    assert client.post(f'/activities/duplicates/{identifier}/merge').status_code == 200
    with Session(engine) as db:
        assert db.query(ActivityIdentity).filter_by(source_key='intervals:legacy').one().activity_id == keep_id
        assert save_activity(db, data('intervals', 'legacy')).id == keep_id
        assert db.query(Activity).count() == 1
