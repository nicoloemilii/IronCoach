"""Iron Coach contracts against synthetic data and isolated SQLite databases."""
import json
import sqlite3
from datetime import date, datetime, timedelta
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_iron
from app.db.database import Base, get_db
from app.db.experience_models import ActivityIdentity, SessionFeedback
from app.db.iron_models import (
    ActivityObservation, DuplicateCandidate, HealthMetric, SchemaVersion, User,
)
from app.db.migrations import migrate
from app.db.models import Activity, DailyMetrics, Workout, WorkoutEvaluation
from app.db import progress_models  # Register goal preparation tables before isolated create_all.
from app.db.repository import save_activity
from app.services import health, iron_coach, race_gap

TODAY = date(2026, 10, 17)


@pytest.fixture
def iron_api(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    for module in (routes_iron, iron_coach, race_gap):
        monkeypatch.setattr(module, 'athlete_today', lambda: TODAY)
    app = FastAPI()
    app.include_router(routes_iron.router)

    def isolated_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    yield TestClient(app, client=('127.0.0.1', 50000)), engine
    engine.dispose()


def activity_data(source='fit', identifier='sample-1', day=TODAY, **changes):
    data = {'source': source, 'source_id': identifier, 'sport': 'running',
            'name': 'Attività sintetica', 'start_time': datetime.combine(day, datetime.min.time()) + timedelta(hours=8),
            'duration_seconds': 3600, 'distance_meters': 10000,
            'elevation_gain_meters': 0, 'load': 30}
    data.update(changes)
    return data


def goal_payload(**changes):
    data = {'name': 'Obiettivo sintetico', 'date': '2027-06-01', 'priority': 'finish',
            'legs': [{'sport': 'running', 'terrain': 'road', 'distance_km': 21.1,
                      'requirements': {'weekly_hours': 1, 'longest_distance_km': 10}}]}
    data.update(changes)
    return data


def metric(response, kind):
    return next(row for row in response['metrics'] if row['kind'] == kind)


def test_wellness_precedence_preserves_manual_null_and_real_zero(iron_api):
    client, engine = iron_api
    with Session(engine) as db:
        db.add(DailyMetrics(date=str(TODAY), sleep_hours=9, hrv_ms=55, steps=123))
        db.commit()
        records = [{'id': str(TODAY), 'ctl': 30, 'atl': 45,
                    'sleepSecs': 27000, 'hrv': None, 'steps': 0}]
        assert health.import_wellness(db, records) == (1, [])
        assert health.import_wellness(db, records) == (1, [])
        assert db.query(HealthMetric).count() == 6
    response = client.get(f'/health/metrics?from={TODAY}&to={TODAY}')
    assert response.status_code == 200
    body = response.json()
    assert metric(body, 'sleep_hours')['latest']['value'] == 7.5
    assert metric(body, 'sleep_hours')['latest']['source'] == 'intervals'
    assert metric(body, 'steps')['latest']['value'] == 0
    assert metric(body, 'steps')['latest']['source'] == 'intervals'
    assert metric(body, 'hrv_ms')['latest']['value'] == 55
    assert metric(body, 'hrv_ms')['latest']['source'] == 'manual'
    assert metric(body, 'tsb')['latest']['value'] == -15
    assert metric(body, 'resting_hr_bpm')['latest'] is None
    with Session(engine) as db:
        manual = db.query(DailyMetrics).one()
        assert manual.sleep_hours == 9 and manual.steps == 123
        assert db.query(HealthMetric).filter_by(kind='hrv_ms', source='intervals').one().value is None


def test_wellness_invalid_record_is_partial_and_does_not_poison_session(iron_api):
    _, engine = iron_api
    with Session(engine) as db:
        count, errors = health.import_wellness(db, [
            {'id': str(TODAY), 'sleepSecs': 25 * 3600},
            {'id': str(TODAY - timedelta(days=1)), 'hrv': float('nan')},
            {'id': 'invalid', 'steps': 3},
            {'id': str(TODAY), 'steps': 0, 'ctl': 0, 'atl': 0},
        ])
        assert count == 1 and len(errors) == 3
        assert db.query(HealthMetric).filter_by(kind='steps').one().value == 0
        assert db.query(HealthMetric).filter_by(kind='tsb').one().value == 0


def test_custom_battery_extrema_and_daily_model_loads_remain_distinct(iron_api):
    client,engine=iron_api
    with Session(engine) as db:
        count,errors=health.import_wellness(db,[{'id':str(TODAY),'ctlLoad':0,'atlLoad':40,
                                              'customFields':{'BodyBatteryMin':0,'BodyBatteryMax':90}}])
        assert count==1 and not errors
    body=client.get('/health/metrics').json()
    assert metric(body,'body_battery_min')['latest']['value']==0
    assert metric(body,'body_battery_max')['latest']['value']==90
    assert metric(body,'body_battery')['latest'] is None
    assert metric(body,'daily_ctl_load')['latest']['value']==0
    assert metric(body,'daily_atl_load')['latest']['value']==40
    assert metric(body,'daily_activity_load')['latest'] is None
    assert metric(body,'stress_score')['latest'] is None
    assert metric(body,'stress_level')['latest'] is None
    assert 'soggettiva' in metric(body,'stress_level')['unavailable_reason']


def test_health_baseline_staleness_and_date_validation(iron_api):
    client, engine = iron_api
    with Session(engine) as db:
        records = [{'id': str(TODAY - timedelta(days=offset)), 'hrv': 50 + offset}
                   for offset in range(3, 11)]
        health.import_wellness(db, records)
    body = client.get(f'/health/metrics?from={TODAY - timedelta(days=28)}&to={TODAY}').json()
    hrv = metric(body, 'hrv_ms')
    assert hrv['baseline'] == 57 and hrv['stale'] is True
    assert hrv['latest']['date'] == str(TODAY - timedelta(days=3))
    assert client.get('/health/metrics?from=2026-10-17&to=2026-10-01').status_code == 422
    assert client.get('/health/metrics?from=2000-01-01&to=2026-10-17').status_code == 422


def test_goal_crud_and_gap_with_complete_evidence(iron_api):
    client, engine = iron_api
    monday = TODAY - timedelta(days=TODAY.weekday())
    with Session(engine) as db:
        for week in range(1, 5):
            db.add(Activity(**activity_data(identifier=f'week-{week}', day=monday - timedelta(days=week * 7 - 1))))
        # An old exceptional session cannot inflate the current 90-day capacity.
        db.add(Activity(**activity_data(identifier='old', day=TODAY - timedelta(days=100), distance_meters=100000)))
        db.commit()
    response = client.post('/goals', json=goal_payload())
    assert response.status_code == 201, response.text
    goal = response.json(); identifier = goal['id']
    assert client.get(f'/goals/{identifier}').json() == goal
    assert len(client.get('/goals').json()) == 1
    gap = client.get(f'/goals/{identifier}/gap-analysis').json()
    assert gap['score'] == 100 and gap['status'] == 'available'
    assert gap['method_version'] == 'coverage-1'
    requirements = {row['kind']: row for row in gap['legs'][0]['requirements']}
    assert requirements['weekly_hours']['observed'] == 1
    assert requirements['longest_distance_km']['observed'] == 10
    assert 'longest_elevation_m' not in requirements
    changed = client.patch(f'/goals/{identifier}', json={'name': 'Nuovo nome', 'priority': 'time', 'target_time': '04:30:00'})
    assert changed.status_code == 200 and changed.json()['target_time'] == '04:30:00'
    assert client.delete(f'/goals/{identifier}').status_code == 204
    assert client.get(f'/goals/{identifier}').status_code == 404
    assert client.get(f'/goals/{identifier}/gap-analysis').status_code == 404


@pytest.mark.parametrize('patch', [
    {'legs': []}, {'date': 'not-a-date'}, {'target_time': '4:99:00'},
    {'legs': [{'sport': 'flying', 'distance_km': 10}]},
    {'legs': [{'sport': 'running', 'distance_km': -1}]},
    {'legs': [{'sport': 'running', 'distance_km': 10, 'requirements': {'weekly_hours': 0}}]},
    {'legs': [{'sport': 'running', 'distance_km': 10, 'altitude_min_m': 2000, 'altitude_max_m': 1000}]},
])
def test_invalid_goal_is_rejected_without_persisting(iron_api, patch):
    client, _ = iron_api
    assert client.post('/goals', json=goal_payload(**patch)).status_code == 422
    assert client.get('/goals').json() == []


def test_gap_missing_data_and_specificity_never_become_zero_or_complete(iron_api):
    client, engine = iron_api
    payload = goal_payload(legs=[{'sport': 'running', 'terrain': 'trail', 'distance_km': 40,
                                'elevation_gain_m': 2000, 'altitude_max_m': 2500,
                                'requirements': {'weekly_hours': 1, 'longest_distance_km': 10,
                                                 'longest_elevation_m': 1000}}])
    identifier = client.post('/goals', json=payload).json()['id']
    initial = client.get(f'/goals/{identifier}/gap-analysis').json()
    assert initial['score'] is None and initial['status'] == 'insufficient_data'
    assert all(row['score'] is None for row in initial['legs'][0]['requirements'])
    monday = TODAY - timedelta(days=TODAY.weekday())
    with Session(engine) as db:
        for week in range(1, 5):
            db.add(Activity(**activity_data(identifier=f'trail-{week}', day=monday - timedelta(days=week * 7 - 1),
                                            elevation_gain_meters=1000)))
        db.commit()
    partial = client.get(f'/goals/{identifier}/gap-analysis').json()
    assert partial['score'] is None
    requirements = {row['kind']: row for row in partial['legs'][0]['requirements']}
    assert requirements['longest_elevation_m']['score'] == 100
    assert requirements['altitude_confirmed']['score'] is None
    payload['legs'][0]['requirements'].update(terrain_confirmed=False, altitude_confirmed=True)
    assert client.patch(f'/goals/{identifier}', json={'legs': payload['legs']}).status_code == 200
    result = client.get(f'/goals/{identifier}/gap-analysis').json()
    assert result['score'] == 80 and result['status'] == 'available'


def test_missing_activity_duration_keeps_weekly_capacity_unknown(iron_api):
    client, engine = iron_api
    monday = TODAY - timedelta(days=TODAY.weekday())
    with Session(engine) as db:
        db.add(Activity(**activity_data(day=monday - timedelta(days=1), duration_seconds=None)))
        db.commit()
    identifier = client.post('/goals', json=goal_payload()).json()['id']
    result = client.get(f'/goals/{identifier}/gap-analysis').json()
    weekly = next(row for row in result['legs'][0]['requirements'] if row['kind'] == 'weekly_hours')
    assert weekly['observed'] is None and weekly['score'] is None
    assert result['score'] is None and result['status'] == 'insufficient_data'


def add_linked_workout(db, activity, rpe=None):
    payload = {'sport': 'running', 'duration_min': 60, 'expected_rpe': 4, 'steps': []}
    workout = Workout(sport='running', name='Programma sintetico', date=str(TODAY),
                      duration_min=60, payload_json=json.dumps(payload))
    db.add(workout); db.flush()
    db.add(SessionFeedback(workout_id=workout.id, activity_id=activity.id,
                           payload_json=json.dumps({'activity_id': activity.id, 'rpe': rpe})))
    db.commit()
    return workout


def test_recorded_rating_does_not_require_link_but_execution_still_requires_evidence(iron_api):
    client, engine = iron_api
    with Session(engine) as db:
        activity = Activity(**activity_data())
        db.add(activity); db.commit(); identifier = activity.id
    unlinked = client.get(f'/activities/{identifier}/assessment').json()
    assert unlinked['score'] is not None and unlinked['scope'] == 'recorded_session'
    assert unlinked['execution_assessment']['score'] is None
    with Session(engine) as db:
        add_linked_workout(db, db.get(Activity, identifier))
    duration_only = client.get(f'/activities/{identifier}/assessment').json()
    assert duration_only['score'] is not None and duration_only['evidence']
    assert duration_only['execution_assessment']['score'] is None
    with Session(engine) as db:
        feedback = db.query(SessionFeedback).one()
        feedback.payload_json = json.dumps({'rpe': 4})
        db.commit()
    assessed = client.get(f'/activities/{identifier}/assessment').json()
    execution = assessed['execution_assessment']
    assert execution['score'] == 100 and execution['judgment'] == 'Eccellente'
    assert execution['rules_version'] == 'rules-1'
    assert any('intensità' in limit for limit in execution['limits'])
    with Session(engine) as db:
        db.get(Activity, identifier).duration_seconds = 7200
        db.commit()
    overshoot = client.get(f'/activities/{identifier}/assessment').json()
    assert overshoot['execution_assessment']['score'] == 50
    assert overshoot['execution_assessment']['judgment'] == 'Insufficiente'
    assert client.get('/activities/99999/assessment').status_code == 404


@pytest.mark.parametrize('score, judgment', [
    (None, 'Non valutabile'), (0, 'Scarso'), (39.9, 'Scarso'), (40, 'Insufficiente'),
    (60, 'Adeguato'), (75, 'Buono'), (90, 'Eccellente'), (100, 'Eccellente'),
])
def test_grade_boundaries(score, judgment):
    assert iron_coach.grade(score) == judgment


def test_overview_requires_three_assessable_sessions_and_explains_future_workout(iron_api):
    client, engine = iron_api
    with Session(engine) as db:
        for index in range(2):
            row = Activity(**activity_data(identifier=f'assessed-{index}'))
            db.add(row); db.flush(); add_linked_workout(db, row, rpe=4)
    assert client.get('/coach/overview').json()['score'] is None
    with Session(engine) as db:
        row = Activity(**activity_data(identifier='assessed-3'))
        db.add(row); db.flush(); add_linked_workout(db, row, rpe=4)
    result = client.get('/coach/overview').json()
    assert result['score'] == 100 and result['judgment'] == 'Corretta'
    assert result['upcoming'] and all(row['reason'] and row['expected_benefit'] for row in result['upcoming'])


def test_similar_activities_are_candidates_until_explicit_merge(iron_api):
    client, engine = iron_api
    with Session(engine) as db:
        fit = save_activity(db, activity_data(load=None))
        remote = save_activity(db, activity_data('intervals', 'remote-1', load=55))
        assert fit.id != remote.id and db.query(Activity).count() == 2
        assert save_activity(db, activity_data('intervals', 'remote-1', load=55)).id == remote.id
        assert db.query(DuplicateCandidate).count() == 1
        assert db.query(ActivityObservation).count() == 2
        add_linked_workout(db, remote, rpe=4)
        db.add(WorkoutEvaluation(workout_id=1, activity_id=remote.id, payload_json='{}'))
        db.commit(); keep_id = fit.id; remove_id = remote.id
    pending = client.get('/activities/duplicates/pending').json()
    assert len(pending) == 1
    result = client.post(f"/activities/duplicates/{pending[0]['id']}/merge")
    assert result.status_code == 200 and result.json()['activity_id'] == keep_id
    with Session(engine) as db:
        assert db.query(Activity).count() == 1 and db.get(Activity, remove_id) is None
        assert db.get(Activity, keep_id).load == 55
        assert {row.activity_id for row in db.query(ActivityIdentity)} == {keep_id}
        assert {row.activity_id for row in db.query(ActivityObservation)} == {keep_id}
        assert db.query(SessionFeedback).one().activity_id == keep_id
        assert json.loads(db.query(SessionFeedback).one().payload_json)['activity_id'] == keep_id
        assert db.query(WorkoutEvaluation).one().activity_id == keep_id
        assert save_activity(db, activity_data('intervals', 'remote-1')).id == keep_id
    assert client.get('/activities/duplicates/pending').json() == []


def test_duplicate_keep_and_conflicting_feedback_do_not_destroy_data(iron_api):
    client, engine = iron_api
    with Session(engine) as db:
        first = save_activity(db, activity_data())
        second = save_activity(db, activity_data('intervals', 'remote-1'))
        add_linked_workout(db, first, rpe=3)
        add_linked_workout(db, second, rpe=4)
    candidate = client.get('/activities/duplicates/pending').json()[0]['id']
    assert client.post(f'/activities/duplicates/{candidate}/merge').status_code == 409
    with Session(engine) as db:
        assert db.query(Activity).count() == 2 and db.query(SessionFeedback).count() == 2
    assert client.post(f'/activities/duplicates/{candidate}/keep').json() == {'status': 'kept'}
    assert client.get('/activities/duplicates/pending').json() == []
    assert client.post(f'/activities/duplicates/{candidate}/merge').status_code == 404


def test_source_id_collision_keeps_separate_provider_identities(iron_api):
    _, engine = iron_api
    with Session(engine) as db:
        first = save_activity(db, activity_data('fit', 'same-id'))
        second = save_activity(db, activity_data('intervals', 'same-id', day=TODAY - timedelta(days=7)))
        assert first.id != second.id and first.source_id != second.source_id
        assert save_activity(db, activity_data('intervals', 'same-id')).id == second.id
        assert db.query(Activity).count() == 2


def test_home_compares_same_week_period_and_prioritizes_intervals_load(iron_api):
    client, engine = iron_api
    with Session(engine) as db:
        current = save_activity(db, activity_data(load=10))
        db.add(ActivityObservation(activity_id=current.id, source_key='intervals:linked',
                                   payload_json=json.dumps({'load': 55})))
        save_activity(db, activity_data(identifier='previous', day=TODAY - timedelta(days=7), load=50))
        # Sunday from last week is outside the corresponding Mon-Sat period.
        save_activity(db, activity_data(identifier='sunday', day=TODAY - timedelta(days=6), load=300))
        db.commit()
    result = client.get('/dashboard/home')
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['week']['activities'] == 1 and body['week']['hours'] == 1
    assert body['week']['load'] == 55
    assert body['previous_same_period']['load'] == 50
    assert body['load_trend_percent'] == 10
    ctl = next(row for row in body['condition'] if row['kind'] == 'ctl')
    assert ctl['latest']['source'] == 'local_estimate'


def test_mutating_goal_routes_reject_nonlocal_or_cross_origin_requests(iron_api):
    client, _ = iron_api
    assert client.post('/goals', json=goal_payload(), headers={'Origin': 'https://untrusted.invalid'}).status_code == 403
    remote = TestClient(client.app, client=('192.168.1.10', 50000))
    assert remote.post('/goals', json=goal_payload()).status_code == 403


def test_migration_backups_original_and_is_idempotent():
    # pytest's restrictive temp-directory ACLs are inaccessible in the Windows
    # sandbox; a uniquely named, ordinary workspace directory is sufficient.
    folder = Path.cwd() / f'.migration-check-{uuid4().hex}'
    folder.mkdir()
    path = folder / 'legacy.db'
    engine = create_engine(f'sqlite:///{path}')
    DailyMetrics.__table__.create(engine)
    Activity.__table__.create(engine)
    with Session(engine) as db:
        db.add(DailyMetrics(date=str(TODAY), sleep_hours=7, steps=0))
        db.add(Activity(**activity_data()))
        db.commit()
    migrate(engine)
    backups = list((folder / 'backups').glob('*-before-iron-v1-*.db'))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as backup:
        assert backup.execute('SELECT COUNT(*) FROM activities').fetchone()[0] == 1
        assert backup.execute('SELECT steps FROM daily_metrics').fetchone()[0] == 0
        assert backup.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='iron_schema_versions'").fetchone()[0] == 0
    assert inspect(engine).has_table('race_goals')
    with Session(engine) as db:
        assert db.query(Activity).count() == 1 and db.query(DailyMetrics).count() == 1
        assert db.get(User, 1).timezone == 'Europe/Rome'
        assert db.query(HealthMetric).filter_by(source='manual').count() == 2
        assert db.query(HealthMetric).filter_by(kind='steps').one().value == 0
        assert db.get(SchemaVersion, 1)
    migrate(engine)
    assert len(list((folder / 'backups').glob('*.db'))) == 1
    with Session(engine) as db:
        assert db.query(HealthMetric).count() == 2 and db.query(SchemaVersion).count() == 2
    engine.dispose()
    # Remove only the exact files this test created, without recursive deletion.
    path.unlink()
    backups[0].unlink()
    (folder / 'backups').rmdir()
    folder.rmdir()
