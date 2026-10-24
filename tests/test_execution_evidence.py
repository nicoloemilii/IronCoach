"""Stored feedback must mean the same thing in weekly review and Coach."""
import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.experience_models import SessionFeedback
from app.db.models import Activity, Workout, WorkoutEvaluation
from app.services import iron_coach, progress
from app.services.athlete_clock import day_start_utc
from app.services.evaluation import evaluate_workout
from app.services.execution_evidence import safe_object, usable_feedback

TODAY = date(2026, 10, 17)
NOW = datetime(2026, 10, 17, 12)
MONDAY = TODAY - timedelta(days=TODAY.weekday())


@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    for module in (progress, iron_coach):
        monkeypatch.setattr(module, 'athlete_today', lambda: TODAY)
        monkeypatch.setattr(module, 'utc_now_naive', lambda: NOW)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_workout(db, *, payload=None, day=MONDAY, status='completed'):
    row = Workout(date=str(day), sport='cycling', name='Synthetic execution', duration_min=60,
        payload_json=json.dumps(payload if payload is not None else {'duration_min': 60, 'expected_rpe': 4}), status=status)
    db.add(row); db.flush()
    return row


def add_activity(db, *, duration=3600, stamp=None, sport='cycling'):
    row = Activity(source='fit', source_id=f'synthetic-{db.query(Activity).count()}', name='Synthetic activity', sport=sport,
                   start_time=stamp or day_start_utc(MONDAY) + timedelta(hours=8), duration_seconds=duration)
    db.add(row); db.flush()
    return row


def add_feedback(db, workout, activity=None, payload=None):
    row = SessionFeedback(workout_id=workout.id, activity_id=activity.id if activity else None,
        payload_json=json.dumps(payload if payload is not None else {'rpe': 4, 'completion_percent': 100, 'actual': {'duration_min': 60}}))
    db.add(row); db.flush()
    return row


def checked(w, f, a=None):
    return usable_feedback(w, f, a, today=TODAY, now=NOW)


@pytest.mark.parametrize('payload', ['[]', 'null', 'true', '"text"', '{', '{"value": NaN}', '{"value": Infinity}',
    '{"value":' + '[' * 40 + '0' + ']' * 40 + '}', '{"huge": "' + 'x' * 524_288 + '"}'],
    ids=['array', 'null', 'boolean', 'string', 'broken', 'nan', 'infinity', 'deep', 'oversized'])
def test_unusable_json_does_not_count_as_execution_or_crash(db, payload):
    w = add_workout(db); a = add_activity(db); f = add_feedback(db, w, a)
    f.payload_json = payload; db.commit()
    assert checked(w, f, a)['issue'] == 'invalid_feedback_payload'
    weekly = progress.week_progress(db)
    assert weekly['counts']['unconfirmed'] == 1
    assert weekly['sessions'][0]['evidence_issue'] == 'invalid_feedback_payload'
    assert weekly['adherence']['percent'] is None
    assert iron_coach.assess(db, a)['score'] is None
    assert iron_coach.overview(db)['unconfirmed_past_sessions'] == 1


@pytest.mark.parametrize('change,issue', [({'rpe': True}, 'invalid_rpe'), ({'rpe': 0}, 'invalid_rpe'),
    ({'rpe': 11}, 'invalid_rpe'), ({'rpe': '4'}, 'invalid_rpe'),
    ({'completion_percent': -1}, 'invalid_completion_percent'), ({'completion_percent': 101}, 'invalid_completion_percent'),
    ({'completion_percent': False}, 'invalid_completion_percent'), ({'actual': []}, 'invalid_actual_payload')])
def test_corrupt_optional_values_fail_closed(db, change, issue):
    w = add_workout(db); a = add_activity(db)
    f = add_feedback(db, w, a, {'rpe': 4, **change})
    assert checked(w, f, a)['issue'] == issue
    assert progress.week_progress(db)['counts']['unconfirmed'] == 1


@pytest.mark.parametrize('duration', [None, -1, True, float('inf'), '60'])
def test_manual_feedback_needs_valid_actual_minutes(db, duration):
    w = add_workout(db); f = add_feedback(db, w, payload={'rpe': 4, 'completion_percent': 100, 'actual': {'duration_min': duration}})
    result = checked(w, f)
    assert not result['usable'] and result['minutes'] is None
    assert progress.week_progress(db)['totals']['confirmed_sessions'] == 0


def test_missing_activity_cannot_be_replaced_by_self_reported_minutes(db):
    w = add_workout(db); f = add_feedback(db, w); f.activity_id = 9999
    assert checked(w, f)['issue'] == 'missing_activity'
    assert progress.week_progress(db)['counts']['unconfirmed'] == 1
    assert iron_coach.overview(db)['unconfirmed_past_sessions'] == 1


def test_unknown_recorded_duration_confirms_occurrence_without_declared_completion(db):
    w = add_workout(db); a = add_activity(db, duration=None)
    add_feedback(db, w, a, {'rpe': 4, 'completion_percent': 0, 'actual': {'duration_min': 0}})
    body = progress.week_progress(db)
    assert body['counts']['completed'] == 1 and body['counts']['modified'] == 0
    assert body['totals']['confirmed_actual_minutes'] is None
    assert body['sessions'][0]['completion_percent'] is None
    assert body['adherence']['percent'] is None
    assert iron_coach.assess(db, a)['score'] is None
    assert iron_coach.overview(db)['unconfirmed_past_sessions'] == 0


@pytest.mark.parametrize('linked', [True, False])
def test_real_zero_duration_is_confirmed_and_counts_as_zero_adherence(db, linked):
    w = add_workout(db); a = add_activity(db, duration=0) if linked else None
    add_feedback(db, w, a, {'actual': {'duration_min': 0}, 'rpe': 4})
    body = progress.week_progress(db)
    assert body['counts']['modified'] == 1
    assert body['sessions'][0]['completion_percent'] == 0
    assert body['totals']['confirmed_actual_minutes'] == 0
    assert body['adherence']['percent'] == 0


@pytest.mark.parametrize('mode', ['future_today', 'future_day', 'wrong_sport', 'outside_window', 'negative_duration'])
def test_invalid_link_cannot_hide_a_past_gap(db, mode):
    w = add_workout(db)
    a = add_activity(db, stamp=NOW + timedelta(hours=1) if mode == 'future_today' else NOW + timedelta(days=1) if mode == 'future_day'
        else day_start_utc(MONDAY - timedelta(days=3)) if mode == 'outside_window' else None,
        sport='running' if mode == 'wrong_sport' else 'cycling', duration=-60 if mode == 'negative_duration' else 3600)
    add_feedback(db, w, a)
    assert not checked(w, db.query(SessionFeedback).one(), a)['usable']
    assert progress.week_progress(db)['counts']['unconfirmed'] == 1
    assert iron_coach.overview(db)['unconfirmed_past_sessions'] == 1


def test_completed_label_or_evaluation_without_feedback_does_not_confirm(db):
    w = add_workout(db)
    db.add(WorkoutEvaluation(workout_id=w.id, payload_json='{"overall":100}')); db.flush()
    assert progress.week_progress(db)['counts']['unconfirmed'] == 1
    assert iron_coach.overview(db)['unconfirmed_past_sessions'] == 1


@pytest.mark.parametrize('payload', ['null', '[]', '{', '{"duration_min": "60"}', '{"steps": true}', '{"duration_min": NaN}'])
def test_invalid_plan_payload_does_not_confirm_or_crash(db, payload):
    w = add_workout(db); w.payload_json = payload
    a = add_activity(db); f = add_feedback(db, w, a)
    assert checked(w, f, a)['issue'] == 'invalid_plan_payload'
    assert progress.week_progress(db)['counts']['unconfirmed'] == 1
    assert iron_coach.assess(db, a)['score'] is None


def test_finite_extreme_durations_never_emit_infinite_ratio_or_total(db):
    for index in range(2):
        w = add_workout(db); w.duration_min = 1e-308
        add_feedback(db, w, payload={'actual': {'duration_min': 1e308}, 'rpe': 4})
    body = progress.week_progress(db)
    assert body['adherence']['percent'] is None
    assert body['totals']['confirmed_actual_minutes'] is None
    json.dumps(body, allow_nan=False)


def test_three_valid_scores_do_not_override_one_unusable_future_link(db):
    for index in range(3):
        w = add_workout(db); a = add_activity(db); add_feedback(db, w, a, {'rpe': 4})
    w = add_workout(db); a = add_activity(db, stamp=NOW + timedelta(hours=1)); add_feedback(db, w, a, {'rpe': 4})
    body = iron_coach.overview(db)
    assert body['execution_score'] == 100 and body['score'] is None
    assert body['unconfirmed_past_sessions'] == 1


@pytest.mark.parametrize('planned,actual', [([], {}), ({}, None), ({'duration_min': 60}, {'duration_min': None}),
    ({'duration_min': 0}, {'duration_min': 60}), ({'duration_min': float('inf')}, {'duration_min': 60}),
    ({'duration_min': True}, {'duration_min': 60}), ({'duration_min': 60}, {'duration_min': '60'})])
def test_evaluator_missing_duration_has_no_completion_or_overall(planned, actual):
    result = evaluate_workout(planned, actual)
    assert result['completion'] is None and result['overall'] is None
    assert result['adaptation'] == 'hold'
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('expected', [None, 0, 11, True, '4', float('nan')])
def test_unknown_or_invalid_target_rpe_is_never_assumed_to_be_four(expected):
    result = evaluate_workout({'duration_min': 60, 'expected_rpe': expected}, {'duration_min': 60, 'rpe': 4})
    assert result['rpe_score'] is None
    json.dumps(result, allow_nan=False)


def test_evaluator_ignores_malformed_step_shapes_without_crashing():
    result = evaluate_workout({'duration_min': 60, 'steps': [None, 'bad', {'target_power_w': 100}]},
        {'duration_min': 60, 'steps': [True, {}, {'avg_power_w': 75}]})
    assert result['intensity_control'] == 25 and result['power_error_pct'] == 25


def test_safe_object_bounds_preexisting_objects_and_cycles():
    cycle = {}; cycle['cycle'] = cycle
    assert safe_object(cycle) is None
    assert safe_object({'many': [0] * 20_001}) is None
    assert safe_object({1: 'invalid key'}) is None
    assert safe_object({'value': 10 ** 1000}) is None


def test_weekly_and_coach_queries_are_constant_for_one_hundred_workouts(db):
    for index in range(100):
        w = add_workout(db); a = add_activity(db); add_feedback(db, w, a, {'rpe': 4})
    # Large unrelated history must not be fetched and filtered in Python.
    old = add_activity(db, stamp=NOW - timedelta(days=200)); old_id = old.id
    future = add_activity(db, stamp=NOW + timedelta(days=200)); future_id = future.id
    db.commit(); db.expunge_all()
    statements = []
    def collect(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', collect)
    try:
        body = progress.week_progress(db)
        assert len(statements) == 6 and body['counts']['completed'] == 100
        assert body['totals']['recorded_activities'] == 100
        assert not any(isinstance(row, Activity) and row.id in (old_id, future_id) for row in db.identity_map.values())
        assert all('WHERE' in statement.upper() for statement in statements)
        statements.clear(); db.expunge_all()
        overview = iron_coach.overview(db)
        assert len(statements) <= 8 and overview['score'] == 100 and overview['recorded_activities'] == 100
        assert not any(isinstance(row, Activity) and row.id in (old_id, future_id) for row in db.identity_map.values())
    finally:
        event.remove(db.bind, 'before_cursor_execute', collect)


def test_week_uses_rome_calendar_utc_boundaries(db):
    before = add_activity(db, stamp=day_start_utc(MONDAY) - timedelta(microseconds=1))
    inside = add_activity(db, stamp=day_start_utc(MONDAY))
    db.commit()
    body = progress.week_progress(db)
    assert body['totals']['recorded_activities'] == 1
    assert body['totals']['recorded_minutes'] == inside.duration_seconds / 60
