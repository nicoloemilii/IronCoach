"""Planning adaptations use valid canonical execution evidence, never JSON defaults."""
import json
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_planning
from app.db import experience_models, garmin_models, planning_models, progress_models
from app.db.database import Base, get_db
from app.db.experience_models import SessionFeedback
from app.db.models import Activity, Workout
from app.services import planning
from app.services.athlete_profile import DEFAULT_PROFILE


TODAY = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 10)
MONDAY = date(2026, 10, 19)


@pytest.fixture
def api(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(planning, '_today', lambda: TODAY)
    monkeypatch.setattr(planning, 'utc_now_naive', lambda: NOW)
    monkeypatch.setattr(planning, 'load_profile', lambda: deepcopy(DEFAULT_PROFILE))
    app = FastAPI(); app.include_router(routes_planning.router)

    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def seed(engine, *, duration=3600, content=None, raw=None, stamp=None,
         workout_sport='cycling', activity_sport='cycling', status='completed',
         manual=False, missing_activity=False, day=MONDAY):
    with Session(engine) as db:
        workout = Workout(date=str(day), sport=workout_sport, name='Piano sintetico',
                          duration_min=60, status=status,
                          payload_json=json.dumps({'sport': workout_sport, 'duration_min': 60, 'steps': []}))
        db.add(workout); db.flush()
        activity = None
        if not manual and not missing_activity:
            activity = Activity(source='fit', source_id=f'planning-{workout.id}', sport=activity_sport,
                                name='Registrata', start_time=stamp or datetime(2026, 10, 19, 8),
                                duration_seconds=duration, distance_meters=25000)
            db.add(activity); db.flush()
        if content is None:
            content = {'rpe': 4, 'feeling': 'as_expected', 'completion_percent': 100,
                       'actual': {'duration_min': 60}, 'evaluation': {'completion': 100}}
        db.add(SessionFeedback(workout_id=workout.id, activity_id=(activity.id if activity else 999 if missing_activity else None),
                               payload_json=raw if raw is not None else json.dumps(content), updated_at=NOW))
        db.commit()
        return workout.id


def signal(client):
    result = client.get('/planning/week?start=2026-10-19')
    assert result.status_code == 200, result.text
    return result.json()['feedback_adaptation']['sports']['cycling']


@pytest.mark.parametrize('payload', [
    '[', '[]', 'null', '{"rpe": NaN}', '{"rpe": Infinity}',
    '{"rpe": true}', '{"rpe": -1}', '{"rpe": "8"}',
    '{"evaluation": null, "rpe": 8}', '{"evaluation": [], "rpe": 8}',
    '{"feeling": "unknown", "rpe": 8}', '{"actual": [], "rpe": 8}',
])
def test_corrupt_feedback_cannot_break_week_or_reduce_plan(api, payload):
    client, engine = api
    seed(engine, raw=payload)
    assert signal(client) == {'samples': 0, 'concerning': 0, 'factor': 1.0, 'workout_ids': []}


@pytest.mark.parametrize('changes', [
    {'stamp': NOW + timedelta(minutes=1)},
    {'stamp': datetime(2026, 10, 21, 8)},
    {'stamp': datetime(2026, 10, 16, 8)},
    {'activity_sport': 'running'},
    {'missing_activity': True},
    {'duration': -1},
    {'duration': float('inf')},
    {'status': 'cancelled'},
    {'status': 'superseded'},
    {'status': 'skipped'},
])
def test_invalid_link_cannot_adapt_future_training(api, changes):
    client, engine = api
    seed(engine, content={'rpe': 8, 'feeling': 'harder', 'evaluation': {'completion': 0}}, **changes)
    assert signal(client)['samples'] == 0


def test_missing_duration_does_not_become_zero_completion(api):
    client, engine = api
    seed(engine, duration=None, content={'rpe': 4, 'feeling': 'as_expected',
                                       'actual': {'duration_min': 0}, 'evaluation': {'completion': 0}})
    assert signal(client) == {'samples': 1, 'concerning': 0, 'factor': 1.0, 'workout_ids': []}


@pytest.mark.parametrize('content', [
    {'rpe': 8, 'feeling': 'as_expected'},
    {'rpe': 4, 'feeling': 'harder'},
])
def test_missing_duration_preserves_independent_perceived_effort(api, content):
    client, engine = api
    identifier = seed(engine, duration=None, content=content)
    assert signal(client) == {'samples': 1, 'concerning': 1, 'factor': .8, 'workout_ids': [identifier]}


@pytest.mark.parametrize('duration,concerning', [(0, 1), (1200, 1), (3600, 0)])
def test_canonical_duration_overrides_stale_stored_completion(api, duration, concerning):
    client, engine = api
    identifier = seed(engine, duration=duration, content={'rpe': 4, 'evaluation': {'completion': 100 if concerning else 0}})
    actual = signal(client)
    assert actual == {'samples': 1, 'concerning': concerning, 'factor': .8 if concerning else 1.0,
                      'workout_ids': [identifier] if concerning else []}


def test_manual_valid_duration_contributes_once_without_evaluation(api):
    client, engine = api
    identifier = seed(engine, manual=True, content={'rpe': 4, 'actual': {'duration_min': 20}})
    assert signal(client) == {'samples': 1, 'concerning': 1, 'factor': .8, 'workout_ids': [identifier]}


def test_aliases_and_rome_midnight_use_same_canonical_evidence(api):
    client, engine = api
    identifier = seed(engine, day=TODAY, workout_sport='bike', activity_sport='virtualride',
                      stamp=datetime(2026, 10, 19, 23), duration=1200)
    assert signal(client) == {'samples': 1, 'concerning': 1, 'factor': .8, 'workout_ids': [identifier]}


def test_editing_old_feedback_does_not_make_a_new_adaptation_signal(api):
    client, engine = api
    seed(engine, day=TODAY - timedelta(days=16), stamp=NOW - timedelta(days=16), content={'rpe': 8})
    assert signal(client)['samples'] == 0


def test_feedback_signal_query_is_batched_and_read_only(api):
    _, engine = api
    for _ in range(30):
        seed(engine)
    statements = []
    def record(conn, cursor, sql, parameters, context, executemany):
        statements.append(sql.lstrip().split(' ', 1)[0].upper())
    event.listen(engine, 'before_cursor_execute', record)
    try:
        with Session(engine) as db:
            result = planning._feedback_signals(db, MONDAY)
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert result['sports']['cycling']['samples'] == 30
    assert statements == ['SELECT']


def test_unknown_and_invalid_durations_do_not_inflate_observed_capacity(api):
    client, engine = api
    for index in range(6):
        seed(engine, day=TODAY - timedelta(days=index + 1), stamp=NOW - timedelta(days=index + 1))
    seed(engine, duration=None)
    seed(engine, duration=float('inf'))
    result = client.get('/planning/week?start=2026-10-26')
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['target_minutes'] == 94  # 360 observed minutes / four weeks * 1.05.
    assert body['confidence'] == 51
    assert any('2 attività con durata assente o non valida' in reason for reason in body['rationale'])


def week_session(client):
    result = client.get('/planning/week?start=2026-10-19')
    assert result.status_code == 200, result.text
    body = result.json()
    return body, next(session for day in body['days'] for session in day['sessions'])


def test_calendar_legacy_future_link_is_unconfirmed_without_rewriting_ledger(api):
    client, engine = api
    identifier = seed(engine, stamp=datetime(2026, 10, 21, 8))
    body, session = week_session(client)
    assert session['status'] == 'completed'
    assert session['execution_status'] == 'unconfirmed'
    assert session['execution_source'] is None
    assert session['evidence_issue'] == 'future_activity'
    assert session['confirmed_actual_minutes'] is None
    assert body['summary']['cycling']['completed_minutes'] == 0
    assert body['summary']['cycling']['completed_planned_minutes'] == 0
    assert body['summary']['cycling']['unconfirmed_sessions'] == 1
    with Session(engine) as db:
        assert db.get(Workout, identifier).status == 'completed'


def test_calendar_unknown_duration_has_no_completed_minute_total(api):
    client, engine = api
    seed(engine, duration=None)
    body, session = week_session(client)
    assert session['execution_status'] == 'completed'
    assert session['completion_percent'] is None
    assert session['confirmed_actual_minutes'] is None
    assert body['summary']['cycling']['completed_minutes'] is None
    assert body['summary']['cycling']['unknown_duration_sessions'] == 1
    assert body['summary']['cycling']['confirmed_sessions'] == 1
    assert body['summary']['cycling']['completed_planned_minutes'] == 60


@pytest.mark.parametrize('manual,minutes', [(False, 30), (True, 45)])
def test_calendar_actual_minutes_remain_distinct_from_planned_credits(api, manual, minutes):
    client, engine = api
    seed(engine, manual=manual, duration=minutes * 60,
         content={'rpe': 4, 'feeling': 'as_expected', 'actual': {'duration_min': minutes}})
    body, session = week_session(client)
    assert session['execution_status'] == 'modified'
    assert session['confirmed_actual_minutes'] == minutes
    assert body['summary']['cycling']['completed_minutes'] == minutes
    assert body['summary']['cycling']['completed_planned_minutes'] == 60
    assert body['summary']['cycling']['confirmed_sessions'] == 1


def test_calendar_evidence_is_batched_for_many_sessions(api):
    _, engine = api
    for _ in range(30):
        seed(engine)
    statements = []
    def record(conn, cursor, sql, parameters, context, executemany):
        statements.append(sql.lstrip().split(' ', 1)[0].upper())
    with Session(engine) as db:
        workouts = db.query(Workout).all()
        event.listen(engine, 'before_cursor_execute', record)
        try:
            result = planning._week_execution(db, workouts)
        finally:
            event.remove(engine, 'before_cursor_execute', record)
    assert len(result) == 30
    assert all(row['execution_status'] == 'completed' for row in result.values())
    assert statements == ['SELECT', 'SELECT']


@pytest.mark.parametrize('payload', ['[', '[]', 'null', '{"duration_min": Infinity}'])
def test_calendar_corrupt_plan_is_readable_but_cannot_be_silently_edited(api, payload):
    client, engine = api
    identifier = seed(engine, status='planned', day=TODAY)
    with Session(engine) as db:
        db.get(Workout, identifier).payload_json = payload
        db.commit()
    body, session = week_session(client)
    assert session['payload_issue']
    assert session['payload'] == {'sport': 'cycling', 'name': 'Piano sintetico', 'duration_min': 60, 'steps': []}
    assert session['evidence_issue'] == 'invalid_plan_payload'
    assert body['summary']['cycling']['completed_minutes'] == 0
    preview = client.post(f'/planning/workouts/{identifier}/preview', json={'action': 'shorten', 'duration_min': 30})
    change = client.patch(f'/planning/workouts/{identifier}', json={'action': 'shorten', 'duration_min': 30})
    assert preview.status_code == change.status_code == 409
    with Session(engine) as db:
        stored = db.get(Workout, identifier)
        assert stored.payload_json == payload
        assert stored.duration_min == 60
        assert stored.status == 'planned'
