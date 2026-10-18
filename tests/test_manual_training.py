"""Functional manual planning and gym progress, isolated from athlete data."""
import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes_training import router
from app.db.database import Base, get_db
from app.db.models import Activity, Workout
from app.db.planning_models import PlanRevision
from app.db.training_models import GymProgress, ensure_training_schema
from app.services import backups, manual_training, planning

TODAY = date(2026, 10, 18)


@pytest.fixture
def manual_api(monkeypatch):
    monkeypatch.setattr(manual_training, 'athlete_today', lambda: TODAY)
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    monkeypatch.setattr(planning, '_today', lambda: TODAY)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(router)
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def create(client, **changes):
    return client.post('/training/manual', json={'date': str(TODAY), 'sport': 'cycling', 'name': 'Fondo personale', 'duration_min': 45, 'purpose': 'Continuità aerobica', **changes})


def gym(client, **changes):
    return create(client, sport='strength', name='Forza gambe', exercises=[
        {'name': 'Squat', 'sets': 3, 'reps': 10, 'weight_kg': 20, 'rest_seconds': 90},
        {'name': 'Plank', 'sets': 2, 'work_seconds': 30, 'rest_seconds': 45}], **changes)


def update_progress(client, workout_id, revision=0, **changes):
    return client.put(f'/training/gym/{workout_id}/progress', json={'expected_revision': revision, **changes})


def test_manual_plan_persists_in_calendar_without_creating_activity(manual_api):
    client, engine = manual_api
    response = create(client)
    assert response.status_code == 201, response.text
    value = response.json()
    assert value['status'] == 'planned' and value['sport'] == 'cycling'
    assert value['payload']['source'] == 'manual' and value['payload']['purpose'] == 'Continuità aerobica'
    assert sum(step['duration_min'] for step in value['payload']['steps']) == 45
    with Session(engine) as db:
        assert db.query(Workout).count() == 1 and db.query(Activity).count() == 0
        assert db.query(PlanRevision).one().action == 'manual_create'


def test_manual_steps_require_exact_total_and_preserve_target_effort(manual_api):
    client, _ = manual_api
    phases = [{'name': 'Facile', 'duration_min': 30, 'target_rpe': 3}, {'name': 'Defaticamento', 'duration_min': 15, 'target_rpe': 2}]
    response = create(client, sport='running', steps=phases)
    assert response.status_code == 201
    assert response.json()['payload']['steps'][0]['target_rpe'] == 3
    assert create(client, steps=phases, duration_min=30).status_code == 422


@pytest.mark.parametrize('changes', [
    {'date': '2026-10-17'}, {'date': '2029-01-01'}, {'sport': 'walking'}, {'name': ' '},
    {'duration_min': True}, {'duration_min': 4}, {'duration_min': 601}, {'duration_min': 30.5},
    {'target_rpe': 0}, {'target_rpe': 11}, {'steps': [{'name': ' ', 'duration_min': 45, 'target_rpe': 3}]},
    {'unexpected': 'value'}, {'sport': 'strength'},
    {'exercises': [{'name': 'Squat', 'sets': 3, 'reps': 10}]},
])
def test_invalid_manual_plan_is_rejected_atomically(manual_api, changes):
    client, engine = manual_api
    assert create(client, **changes).status_code == 422
    with Session(engine) as db:
        assert db.query(Workout).count() == 0 and db.query(PlanRevision).count() == 0


def test_manual_plan_warns_about_budget_without_overwriting_existing_sessions(manual_api):
    client, engine = manual_api
    first = create(client, duration_min=90).json()
    second = create(client, name='Seconda seduta', duration_min=90).json()
    assert any('disponibilità' in message for message in second['warnings'])
    with Session(engine) as db:
        assert db.query(Workout).count() == 2
        assert db.get(Workout, first['id']).duration_min == 90


def test_gym_plan_supports_reps_timed_exercises_and_no_assumed_weight(manual_api):
    client, _ = manual_api
    response = gym(client)
    assert response.status_code == 201, response.text
    value = response.json()
    assert sum(step['duration_min'] for step in value['payload']['steps']) == 45
    exercises = value['payload']['exercises']
    assert exercises[0]['reps'] == 10 and exercises[0]['weight_kg'] == 20
    assert exercises[1]['work_seconds'] == 30 and exercises[1]['weight_kg'] is None


@pytest.mark.parametrize('exercise', [
    {'name': 'Squat', 'sets': 0, 'reps': 10},
    {'name': 'Squat', 'sets': 3, 'reps': 10, 'work_seconds': 20},
    {'name': 'Squat', 'sets': 3},
    {'name': ' ', 'sets': 3, 'reps': 10},
    {'name': 'Squat', 'sets': 3, 'reps': 10, 'weight_kg': -1},
    {'name': 'Squat', 'sets': 3, 'reps': 10, 'rest_seconds': 601},
    {'name': 'Squat', 'sets': True, 'reps': 10},
])
def test_gym_exercise_validation(manual_api, exercise):
    client, _ = manual_api
    assert create(client, sport='strength', exercises=[exercise]).status_code == 422


def test_progress_is_revisioned_and_completion_does_not_fabricate_activity(manual_api):
    client, engine = manual_api
    identifier = gym(client).json()['id']
    initial = client.get(f'/training/gym/{identifier}/progress').json()
    assert initial['revision'] == 0 and initial['total_sets'] == 5
    sets = [{'exercise_index': index, 'set_index': number} for index, count in [(0, 3), (1, 2)] for number in range(count)]
    response = update_progress(client, identifier, completed_sets=sets, notes='Tecnica controllata')
    assert response.status_code == 200, response.text
    assert response.json()['revision'] == 1 and response.json()['finished'] is True
    assert response.json()['activity_created'] is False
    assert update_progress(client, identifier, revision=0).status_code == 412
    assert client.get(f'/training/gym/{identifier}/progress').json()['completed_count'] == 5
    with Session(engine) as db:
        assert db.get(Workout, identifier).status == 'planned'
        assert db.query(Activity).count() == 0 and db.query(GymProgress).count() == 1
    reset = update_progress(client, identifier, revision=1, completed_sets=[])
    assert reset.json()['finished'] is False and reset.json()['completed_count'] == 0


@pytest.mark.parametrize('changes', [
    {'active_exercise': 2},
    {'completed_sets': [{'exercise_index': 0, 'set_index': 3}]},
    {'completed_sets': [{'exercise_index': 2, 'set_index': 0}]},
    {'completed_sets': [{'exercise_index': 0, 'set_index': 0}] * 2},
    {'timer': {'running': True, 'remaining_seconds': 60}},
    {'timer': {'running': False, 'remaining_seconds': 601}},
    {'expected_revision': True},
])
def test_invalid_progress_keeps_original_plan(manual_api, changes):
    client, engine = manual_api
    identifier = gym(client).json()['id']
    assert update_progress(client, identifier, **changes).status_code == 422
    with Session(engine) as db:
        assert db.query(GymProgress).count() == 0


def test_running_timer_uses_wall_clock_and_survives_reload(manual_api):
    client, _ = manual_api
    identifier = gym(client).json()['id']
    until = datetime.now(timezone.utc) + timedelta(seconds=90)
    response = update_progress(client, identifier, timer={'running': True, 'remaining_seconds': 90, 'ends_at': until.isoformat()})
    timer = response.json()['progress']['timer']
    assert timer['running'] is True and 88 <= timer['remaining_seconds'] <= 90
    assert client.get(f'/training/gym/{identifier}/progress').json()['progress']['timer']['ends_at'] == until.isoformat().replace('+00:00', 'Z')
    response = update_progress(client, identifier, revision=1, timer={'running': True, 'remaining_seconds': 1, 'ends_at': (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()})
    assert response.json()['progress']['timer'] == {'running': False, 'remaining_seconds': 0, 'ends_at': None}


def test_progress_rejects_future_removed_and_non_gym_sessions(manual_api):
    client, engine = manual_api
    ordinary = create(client).json()['id']
    future = gym(client, date='2026-10-19').json()['id']
    assert client.get('/training/gym/9876/progress').status_code == 404
    assert client.get(f'/training/gym/{ordinary}/progress').status_code == 422
    assert update_progress(client, future).status_code == 409
    with Session(engine) as db:
        db.get(Workout, future).status = 'superseded'
        db.commit()
    assert client.get(f'/training/gym/{future}/progress').status_code == 409


def test_gym_progress_is_portable_and_older_backup_without_it_remains_valid(manual_api):
    client, engine = manual_api
    identifier = gym(client).json()['id']
    update_progress(client, identifier, completed_sets=[{'exercise_index': 0, 'set_index': 0}])
    with Session(engine) as db:
        exported = backups.export_backup(db)
        assert exported['data']['gym_progress'][0]['workout_id'] == identifier
        assert backups.validate_backup(db, json.dumps(exported))['data']['gym_progress']
        old = deepcopy(exported)
        del old['data']['gym_progress']
        assert backups.validate_backup(db, json.dumps(old))['data']['gym_progress'] == []
        corrupt = deepcopy(exported)
        corrupt['data']['gym_progress'][0]['payload_json'] = json.dumps({'completed_sets': [{'exercise_index': 100, 'set_index': 0}], 'active_exercise': 0, 'timer': {'remaining_seconds': 0, 'running': False, 'ends_at': None}, 'notes': ''})
        with pytest.raises(Exception) as failure:
            backups.validate_backup(db, json.dumps(corrupt))
        assert failure.value.status_code == 422


def test_additive_schema_is_idempotent():
    engine = create_engine('sqlite://')
    ensure_training_schema(engine)
    ensure_training_schema(engine)
    with Session(engine) as db:
        assert db.query(GymProgress).count() == 0
    engine.dispose()


def test_initial_automatic_generation_preserves_manual_sessions_and_remains_idempotent(manual_api):
    client, engine = manual_api
    saved = create(client, date='2026-10-20').json()
    with Session(engine) as db:
        assert planning.get_week(db, '2026-10-19')['generated'] is False
        first = planning.generate_week(db, '2026-10-19')
        assert first['generated'] is True
        sessions = [row for day in first['days'] for row in day['sessions']]
        assert len(sessions) > 1
        assert next(row for row in sessions if row['id'] == saved['id'])['payload'] == saved['payload']
        assert planning.generate_week(db, '2026-10-19') == first
        undone = planning.undo_week(db, '2026-10-19')
        assert undone['generated'] is False
        assert [row['id'] for day in undone['days'] for row in day['sessions']] == [saved['id']]


def test_shortening_manual_aerobic_work_preserves_personal_phases_and_can_be_undone(manual_api):
    client, engine = manual_api
    phases = [{'name': 'Fondo', 'duration_min': 30, 'target_rpe': 5, 'instruction': 'La mia indicazione'}, {'name': 'Facile', 'duration_min': 15, 'target_rpe': 2}]
    saved = create(client, date='2026-10-20', sport='running', steps=phases).json()
    with Session(engine) as db:
        preview = planning.preview_workout(db, saved['id'], 'shorten', duration_min=30)
        after = preview['after']['payload']
        assert after['source'] == 'manual' and after['name'] == saved['name']
        assert [step['duration_min'] for step in after['steps']] == [20, 10]
        assert [step['target_rpe'] for step in after['steps']] == [5, 2]
        assert after['steps'][0]['instruction'] == 'La mia indicazione'
        assert db.get(Workout, saved['id']).duration_min == 45
        planning.modify_workout(db, saved['id'], 'shorten', duration_min=30, preview_signature=preview['preview_signature'])
        restored = planning.undo_week(db, '2026-10-19')
        assert next(row for day in restored['days'] for row in day['sessions'] if row['id'] == saved['id'])['payload'] == saved['payload']


def test_gym_calendar_move_preserves_exercises_and_generic_shorten_is_explicitly_rejected(manual_api):
    client, engine = manual_api
    saved = gym(client, date='2026-10-20').json()
    with Session(engine) as db:
        changed = planning.modify_workout(db, saved['id'], 'move', new_date=date(2026, 10, 21))
        assert changed['workout']['payload']['exercises'] == saved['payload']['exercises']
        assert changed['workout']['payload']['steps'] == saved['payload']['steps']
        with pytest.raises(HTTPException) as error:
            planning.preview_workout(db, saved['id'], 'shorten', duration_min=30)
        assert error.value.status_code == 409
        assert db.get(Workout, saved['id']).duration_min == 45
