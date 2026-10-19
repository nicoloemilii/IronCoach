"""Legacy clients share the validated feedback ledger, without inventing evidence."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes_coach import router as coach_router
from app.api.routes_experience import router as experience_router
from app.db import experience_models, garmin_models, planning_models
from app.db.database import Base, get_db
from app.db.experience_models import SessionFeedback
from app.db.models import Activity, AthleteAdaptation, Workout, WorkoutEvaluation
from app.services import athlete_clock


@pytest.fixture
def legacy_api():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    today = datetime.now(timezone.utc).date()
    with factory() as session:
        for workout_id, sport, day, status in [
            (1, 'cycling', today, 'planned'), (2, 'cycling', today, 'planned'),
            (3, 'cycling', today + timedelta(days=2), 'planned'),
            (4, 'cycling', today, 'cancelled'), (5, 'running', today, 'planned'),
        ]:
            payload = {'sport': sport, 'name': f'Test {workout_id}', 'duration_min': 60, 'expected_rpe': 4,
                       'steps': [{'type': 'warmup', 'duration_min': 30, 'target_power_w': 100},
                                 {'type': 'active', 'duration_min': 30, 'target_power_w': 200}]}
            session.add(Workout(id=workout_id, sport=sport, name=payload['name'], date=str(day),
                                duration_min=60, status=status, payload_json=json.dumps(payload)))
        session.commit()
    app = FastAPI()
    app.include_router(coach_router)
    app.include_router(experience_router)

    def isolated_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = isolated_db
    with TestClient(app) as client:
        yield client, factory, today
    engine.dispose()


def add_activity(factory, today, **overrides):
    data = {'source': 'garmin', 'source_id': 'test-1', 'sport': 'cycling', 'duration_seconds': 3600,
            'start_time': datetime.combine(today, datetime.min.time()) + timedelta(hours=6), 'avg_power': 140}
    data.update(overrides)
    with factory() as session:
        row = Activity(**data)
        session.add(row)
        session.commit()
        return row.id


def test_rpe_alone_is_not_assumed_to_prove_completion(legacy_api):
    client, factory, _ = legacy_api
    response = client.post('/coach/workouts/1/evaluate', json={'rpe': 3})
    assert response.status_code == 422
    assert 'completamento' in response.text
    with factory() as session:
        assert session.query(SessionFeedback).count() == 0
        assert session.get(Workout, 1).status == 'planned'


def test_legacy_evaluation_upserts_and_preserves_response_contract(legacy_api):
    client, factory, _ = legacy_api
    for rpe in (4, 3, 2):
        response = client.post('/coach/workouts/1/evaluate', json={'rpe': rpe, 'duration_min': 45})
        assert response.status_code == 200
        result = response.json()
        assert set(result) == {'workout_id', 'evaluation', 'adaptation_saved'}
        assert result['evaluation']['completion'] == 75
    with factory() as session:
        assert session.query(SessionFeedback).count() == 1
        assert session.query(WorkoutEvaluation).count() == 1
        assert session.query(AthleteAdaptation).count() == 1
        assert session.get(Workout, 1).status == 'completed'


def test_legacy_and_new_feedback_share_one_row(legacy_api):
    client, factory, _ = legacy_api
    assert client.put('/experience/workouts/1/feedback', json={'rpe': 3, 'completion_percent': 100}).status_code == 200
    assert client.post('/coach/workouts/1/evaluate', json={'rpe': 5, 'completion_percent': 50}).status_code == 200
    with factory() as session:
        assert session.query(SessionFeedback).count() == 1
        assert session.query(WorkoutEvaluation).count() == 1
        assert session.query(AthleteAdaptation).count() == 1
        assert json.loads(session.query(SessionFeedback).one().payload_json)['actual']['duration_min'] == 30


def test_longer_reported_duration_is_retained_even_when_completion_is_capped(legacy_api):
    client, factory, _ = legacy_api
    result = client.post('/coach/workouts/1/evaluate', json={'rpe': 4, 'duration_min': 90}).json()
    assert result['evaluation']['completion'] == 100
    with factory() as session:
        content = json.loads(session.query(SessionFeedback).one().payload_json)
        assert content['actual']['duration_min'] == 90


def test_explicit_step_power_is_kept_and_labelled_self_reported(legacy_api):
    client, factory, _ = legacy_api
    response = client.post('/coach/workouts/1/evaluate', json={
        'rpe': 4, 'duration_min': 60, 'avg_power_w': 135,
        'steps': [{'avg_power_w': 100}, {'avg_power_w': 100}],
    })
    assert response.status_code == 200
    result = response.json()['evaluation']
    assert result['power_error_pct'] == 25
    assert result['intensity_control'] == 25
    assert result['evidence_source'] == 'self_reported_step_data'
    with factory() as session:
        content = json.loads(session.query(SessionFeedback).one().payload_json)
        assert content['actual']['steps'][1]['avg_power_w'] == 100
        assert content['actual']['avg_power_w'] == 135
        assert content['step_evidence_source'] == 'self_reported'
        assert session.query(AthleteAdaptation).one().adaptation == result['adaptation']


def test_step_misalignment_and_inconsistent_completion_rejected_before_save(legacy_api):
    client, factory, _ = legacy_api
    assert client.post('/coach/workouts/1/evaluate', json={'rpe': 4, 'duration_min': 60,
                                                         'steps': [{'avg_power_w': 100}]}).status_code == 422
    assert client.post('/coach/workouts/1/evaluate', json={'rpe': 4, 'duration_min': 60,
                                                         'completion_percent': 20}).status_code == 422
    with factory() as session:
        assert session.query(SessionFeedback).count() == 0


def test_imported_observations_win_and_reported_evidence_is_preserved(legacy_api):
    client, factory, today = legacy_api
    activity_id = add_activity(factory, today, duration_seconds=1800)
    response = client.post('/coach/workouts/1/evaluate', json={
        'rpe': 4, 'activity_id': activity_id, 'duration_min': 90, 'avg_power_w': 999,
    })
    assert response.status_code == 200
    assert response.json()['evaluation']['completion'] == 50
    assert response.json()['evaluation']['intensity_control'] is None
    with factory() as session:
        content = json.loads(session.query(SessionFeedback).one().payload_json)
        assert content['actual']['duration_min'] == 30
        assert content['actual']['avg_power_w'] == 140
        assert content['actual']['reported_evidence'] == {'duration_min': 90, 'avg_power_w': 999}


def test_same_activity_conflict_is_enforced_across_legacy_and_new_clients(legacy_api):
    client, factory, today = legacy_api
    activity_id = add_activity(factory, today)
    assert client.put('/experience/workouts/1/feedback', json={'rpe': 4, 'completion_percent': 100, 'activity_id': activity_id}).status_code == 200
    assert client.post('/coach/workouts/2/evaluate', json={'rpe': 4, 'activity_id': activity_id}).status_code == 409
    with factory() as session:
        assert session.query(SessionFeedback).count() == 1


@pytest.mark.parametrize('workout_id,code', [(3, 409), (4, 409), (999, 404)])
def test_evaluate_rejects_future_removed_missing_workout(legacy_api, workout_id, code):
    client, _, _ = legacy_api
    assert client.post(f'/coach/workouts/{workout_id}/evaluate', json={'rpe': 4, 'duration_min': 60}).status_code == code


@pytest.mark.parametrize('payload', [
    {'rpe': 0, 'duration_min': 30}, {'rpe': 11, 'duration_min': 30},
    {'rpe': 3, 'duration_min': -2}, {'rpe': 3, 'duration_min': 'NaN'},
    {'rpe': 3, 'completion_percent': 101}, {'rpe': 3, 'activity_id': -1},
    {'rpe': 3, 'duration_min': 30, 'unknown': 2},
    {'rpe': 3, 'duration_min': 60, 'steps': [{'avg_power_w': -1}, {}]},
])
def test_legacy_evaluation_validates_all_evidence(legacy_api, payload):
    client, _, _ = legacy_api
    assert client.post('/coach/workouts/1/evaluate', json=payload).status_code == 422


def test_good_match_is_only_a_confirmation_candidate_and_never_persists(legacy_api):
    client, factory, today = legacy_api
    activity_id = add_activity(factory, today)
    result = client.post('/coach/workouts/1/match', json={'rpe': 4}).json()
    assert result['match']['matched'] is True
    assert result['match']['activity_id'] == activity_id
    assert result['match']['requires_confirmation'] is True
    assert result['match']['confirmed'] is False
    assert result['auto_completed'] is False
    assert result['evaluation']['preview'] is True
    assert result['evaluation']['intensity_control'] is None
    with factory() as session:
        assert session.query(SessionFeedback).count() == 0
        assert session.query(WorkoutEvaluation).count() == 0
        assert session.get(Workout, 1).status == 'planned'


def test_low_score_does_not_claim_a_match(legacy_api):
    client, factory, today = legacy_api
    add_activity(factory, today, duration_seconds=300)
    result = client.post('/coach/workouts/1/match').json()
    assert result['match']['matched'] is False
    assert result['match']['reason'] == 'no_confident_candidate'
    assert result['auto_completed'] is False


def test_match_excludes_activities_already_used_elsewhere(legacy_api):
    client, factory, today = legacy_api
    activity_id = add_activity(factory, today)
    assert client.post('/coach/workouts/1/evaluate', json={'rpe': 4, 'activity_id': activity_id}).status_code == 200
    assert client.post('/coach/workouts/2/match').json()['match']['matched'] is False
    assert client.post('/coach/workouts/2/match', json={'activity_id': activity_id}).status_code == 409


def test_explicit_match_validates_sport_date_and_existence(legacy_api):
    client, factory, today = legacy_api
    activity_id = add_activity(factory, today)
    far_id = add_activity(factory, today, source_id='far', start_time=datetime.combine(today - timedelta(days=5), datetime.min.time()))
    assert client.post('/coach/workouts/5/match', json={'activity_id': activity_id}).status_code == 422
    assert client.post('/coach/workouts/1/match', json={'activity_id': far_id}).status_code == 422
    assert client.post('/coach/workouts/1/match', json={'activity_id': 999}).status_code == 404


@pytest.mark.parametrize('workout_id,code', [(3, 409), (4, 409), (999, 404)])
def test_match_rejects_future_removed_missing_workout(legacy_api, workout_id, code):
    client, _, _ = legacy_api
    assert client.post(f'/coach/workouts/{workout_id}/match').status_code == code


@pytest.mark.parametrize('existing_feedback', [False, True])
def test_legacy_evaluation_failure_rolls_back_the_entire_feedback_ledger(legacy_api, monkeypatch, existing_feedback):
    from app.api import routes_coach

    client, factory, _ = legacy_api
    if existing_feedback:
        assert client.post('/coach/workouts/1/evaluate', json={'rpe': 3, 'duration_min': 30}).status_code == 200
    with factory() as session:
        before = [(row.payload_json, row.activity_id) for row in session.query(SessionFeedback).all()]
        status = session.get(Workout, 1).status

    def failed_evaluation(*args, **kwargs):
        raise RuntimeError('Injected final evaluation failure')

    monkeypatch.setattr(routes_coach, 'evaluate_workout', failed_evaluation)
    with pytest.raises(RuntimeError, match='Injected final evaluation failure'):
        client.post('/coach/workouts/1/evaluate', json={'rpe': 8, 'duration_min': 60})
    with factory() as session:
        assert [(row.payload_json, row.activity_id) for row in session.query(SessionFeedback).all()] == before
        assert session.get(Workout, 1).status == status
        assert session.query(WorkoutEvaluation).count() == int(existing_feedback)
        assert session.query(AthleteAdaptation).count() == int(existing_feedback)


def test_legacy_review_uses_the_athlete_day_after_local_midnight(legacy_api, monkeypatch):
    from app.api import routes_coach

    client, factory, today = legacy_api
    now = datetime.combine(today - timedelta(days=1), datetime.min.time()) + timedelta(hours=23, minutes=30)

    class FrozenUTC(datetime):
        @classmethod
        def utcnow(cls):
            return now

    monkeypatch.setattr(athlete_clock, 'utc_now_naive', lambda: now)
    monkeypatch.setattr(routes_coach, 'datetime', FrozenUTC)
    response = client.post('/coach/workouts/1/evaluate', json={'rpe': 4, 'duration_min': 60})
    assert response.status_code == 200


def test_explicit_legacy_match_checks_distance_in_local_calendar_days(legacy_api, monkeypatch):
    client, factory, today = legacy_api
    workout_day = today - timedelta(days=2)
    with factory() as session:
        session.get(Workout, 1).date = str(workout_day)
        session.commit()
    # UTC day appears three days away, while the Rome calendar day is two.
    stamp = datetime.combine(today - timedelta(days=5), datetime.min.time()) + timedelta(hours=23, minutes=30)
    activity_id = add_activity(factory, today, start_time=stamp)
    response = client.post('/coach/workouts/1/match', json={'activity_id': activity_id})
    assert response.status_code == 200
    assert response.json()['auto_completed'] is False
