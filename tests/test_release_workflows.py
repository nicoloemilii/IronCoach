"""Combined-router release workflows using synthetic data and isolated SQLite."""
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_backup, routes_coach, routes_iron, routes_planning, routes_progress
from app.db.database import Base, get_db
from app.db.iron_models import HealthMetric, RaceGoal, SchemaVersion, User
from app.db.migrations import migrate
from app.db.models import Activity, DailyMetrics, Workout
from app.db.planning_models import AthleteSettings, PlanRevision
from app.db.progress_models import GoalPreparationItem, WorkoutResolution
from app.services import athlete_state, coach, health, planning, progress, readiness, training_load
from app.services.athlete_profile import DEFAULT_PROFILE


@pytest.fixture
def release_api(monkeypatch):
    clock = {'day': date(2026, 10, 17)}
    monkeypatch.setattr(planning, '_today', lambda: clock['day'])
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    monkeypatch.setattr(routes_coach, 'load_profile', lambda: deepcopy(DEFAULT_PROFILE))
    monkeypatch.setattr(routes_coach, 'athlete_today', lambda: clock['day'])
    for module in (coach, readiness, athlete_state, training_load, progress):
        monkeypatch.setattr(module, 'athlete_today', lambda *args: clock['day'])
        monkeypatch.setattr(module, 'utc_now_naive', lambda: datetime.combine(clock['day'], datetime.min.time()) + timedelta(hours=12))
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    application = FastAPI()
    for module in (routes_iron, routes_planning, routes_coach, routes_progress, routes_backup):
        application.include_router(module.router)

    def isolated():
        with Session(engine) as db:
            yield db

    application.dependency_overrides[get_db] = isolated
    yield TestClient(application, client=('127.0.0.1', 51000)), engine, clock
    engine.dispose()


def create_goal(client, sport='running'):
    response = client.post('/goals', json={'name': 'Synthetic release race', 'date': '2027-06-01',
        'legs': [{'sport': sport, 'terrain': 'water' if sport == 'swimming' else 'trail',
                  'distance_km': 2 if sport == 'swimming' else 30, 'elevation_gain_m': 0 if sport == 'swimming' else 1500}]})
    assert response.status_code == 201, response.text
    return response.json()


def activate(client, target):
    current = client.get('/planning/profile').json()
    response = client.put('/planning/goal', json={'goal_id': target['id']}, headers={'If-Match': str(current['revision'])})
    assert response.status_code == 200, response.text
    return response.json()


def test_active_updated_goal_controls_daily_coach_and_saved_discipline(release_api):
    client, engine, _ = release_api
    target = create_goal(client)
    activate(client, target)
    updated = client.patch(f"/goals/{target['id']}", json={'name': 'Updated trail target', 'date': '2027-07-15'})
    assert updated.status_code == 200
    response = client.get('/coach/today')
    assert response.status_code == 200, response.text
    coach_today = response.json()
    assert coach_today['event']['name'] == 'Updated trail target'
    assert coach_today['event']['date'] == '2027-07-15'
    assert coach_today['allowed_sports'] == ['running']
    assert coach_today['workout']['sport'] == 'running'
    assert coach_today['workout']['goal_focus']['goal_id'] == target['id']
    assert client.post('/coach/workouts', json={'sport': 'cycling'}).status_code == 409
    saved = client.post('/coach/workouts', json={'sport': 'running'})
    assert saved.status_code == 200, saved.text
    with Session(engine) as db:
        assert db.query(Workout).count() == 1 and db.query(Workout).one().sport == 'running'


def test_deleted_linked_goal_does_not_leave_stale_daily_event(release_api):
    client, engine, _ = release_api
    target = create_goal(client)
    assert client.patch(f"/goals/{target['id']}", json={'priority': 'time', 'target_time': '04:30:00'}).status_code == 200
    target = client.get(f"/goals/{target['id']}").json()
    linked = activate(client, target)
    assert linked['profile']['event_name'] and linked['profile']['event_date'] and linked['profile']['target_time']
    assert client.put(f"/goals/{target['id']}/preparation", json={
        'items': {'equipment': {'done': True, 'notes': 'Note sintetiche della gara da eliminare'}}}).status_code == 200
    assert client.delete(f"/goals/{target['id']}").status_code == 204
    current = client.get('/planning/profile').json()
    assert current['revision'] == linked['revision'] + 1
    assert current['profile']['race_goal_id'] is None
    assert current['profile']['event_name'] == ''
    assert current['profile']['event_date'] is None and current['profile']['target_time'] is None
    with Session(engine) as db:
        assert db.get(RaceGoal, target['id']) is None
        assert db.query(GoalPreparationItem).filter_by(goal_id=target['id']).count() == 0
        assert db.get(AthleteSettings, 1).revision == current['revision']
    stale = client.put('/planning/profile', json=linked['profile'], headers={'If-Match': str(linked['revision'])})
    assert stale.status_code == 412
    result = client.get('/coach/today')
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['race_goal'] is None
    assert body['event']['date'] is None and not body['event']['name']
    assert body['event']['phase'] == 'base'
    assert not any('eliminato' in warning for warning in body['goal_warnings'])
    exported = client.get('/backups/export')
    assert exported.status_code == 200, exported.text
    payload = exported.json()
    assert payload['data']['race_goals'] == [] and payload['data']['goal_preparation_items'] == []
    profile = json.loads(payload['data']['athlete_settings'][0]['payload_json'])
    assert profile['race_goal_id'] is None and profile['event_name'] == ''
    preview = client.post('/backups/preview', content=exported.content, headers={'Content-Type': 'application/json'})
    assert preview.status_code == 200, preview.text


def test_deleting_another_goal_preserves_active_profile_and_its_preparation(release_api):
    client, engine, _ = release_api
    active = create_goal(client)
    other = create_goal(client, sport='swimming')
    linked = activate(client, active)
    for target in (active, other):
        assert client.put(f"/goals/{target['id']}/preparation", json={'items': {
            'equipment': {'done': True, 'notes': f"Gara {target['id']}"}}}).status_code == 200
    assert client.delete(f"/goals/{other['id']}").status_code == 204
    assert client.get('/planning/profile').json() == linked
    with Session(engine) as db:
        assert db.get(RaceGoal, active['id'])
        assert db.get(RaceGoal, other['id']) is None
        assert db.query(GoalPreparationItem).count() == 1
        assert db.query(GoalPreparationItem).one().goal_id == active['id']
    exported = client.get('/backups/export')
    preview = client.post('/backups/preview', content=exported.content, headers={'Content-Type': 'application/json'})
    assert preview.status_code == 200, preview.text


@pytest.mark.parametrize('sport,excluded,pool', [
    (None, ['cycling', 'running', 'swimming'], True),
    ('swimming', [], False),
    ('running', ['running'], True),
])
def test_unavailable_daily_disciplines_pause_prescriptions(release_api, sport, excluded, pool):
    client, engine, _ = release_api
    saved = client.put('/planning/profile', json={'excluded_sports': excluded, 'pool_access': pool})
    assert saved.status_code == 200
    if sport:
        activate(client, create_goal(client, sport))
    response = client.get('/coach/today')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['allowed_sports'] == [] and body['training_paused'] is True
    assert body['workout']['sport'] == 'recovery' and body['workout']['duration_min'] == 0
    assert client.post('/coach/workouts', json={}).status_code == 409
    with Session(engine) as db:
        assert db.query(Workout).count() == 0


def test_goal_plan_preview_signature_and_confirmed_change_work_across_routers(release_api):
    client, engine, _ = release_api
    assert client.put('/planning/profile', json={'availability': [60] * 7}).status_code == 200
    activate(client, create_goal(client))
    generated = client.post('/planning/week', json={'start': '2026-10-19'})
    assert generated.status_code == 200, generated.text
    workouts = [row for day in generated.json()['days'] for row in day['sessions'] if row['status'] == 'planned']
    row = max(workouts, key=lambda item: item['duration_min'])
    body = {'action': 'shorten', 'duration_min': 15, 'reason': 'Synthetic release check'}
    preview = client.post(f"/planning/workouts/{row['id']}/preview", json=body)
    assert preview.status_code == 200, preview.text
    with Session(engine) as db:
        assert db.query(PlanRevision).count() == 1
        assert db.get(Workout, row['id']).duration_min == row['duration_min']
    changed = client.patch(f"/planning/workouts/{row['id']}", json=body,
                           headers={'X-Plan-Preview': preview.json()['preview_signature']})
    assert changed.status_code == 200, changed.text
    assert changed.json()['workout']['duration_min'] == 15
    assert changed.json()['week']['total_minutes'] == preview.json()['impact']['after_minutes']
    assert changed.json()['workout']['payload']['goal_focus']['goal_id'] == generated.json()['race_goal']['id']


def test_explicit_past_rest_survives_planner_undo_and_remains_rest_in_review(release_api):
    client, engine, clock = release_api
    assert client.put('/planning/profile', json={'availability': [60] * 7}).status_code == 200
    activate(client, create_goal(client))
    generated = client.post('/planning/week', json={'start': '2026-10-12'})
    assert generated.status_code == 200, generated.text
    row = next(row for day in generated.json()['days'] for row in day['sessions'] if row['status'] == 'planned')
    clock['day'] = date(2026, 10, 19)
    resolved = client.put(f"/progress/workouts/{row['id']}/resolution", json={'status': 'rest', 'reason': 'Recupero scelto e confermato'})
    assert resolved.status_code == 200, resolved.text
    undone = client.post('/planning/undo', json={'start': '2026-10-12'})
    assert undone.status_code == 200, undone.text
    with Session(engine) as db:
        assert db.get(Workout, row['id']).status == 'skipped'
        assert db.query(WorkoutResolution).filter_by(workout_id=row['id']).one().undone_at is None
    review = client.get('/progress/week?start=2026-10-12')
    assert review.status_code == 200, review.text
    assert review.json()['counts']['rest'] == 1
    reviewed = next(item for item in review.json()['sessions'] if item['id'] == row['id'])
    assert reviewed['status'] == 'rest' and reviewed['evidence_source'] == 'explicit_resolution'


def test_existing_version_one_migrates_once_with_snapshot_and_keeps_cleared_manual_data():
    folder = Path.cwd() / f'.release-migration-{uuid4().hex}'
    folder.mkdir()
    database = folder / 'synthetic-v1.db'
    engine = create_engine(f'sqlite:///{database}')
    progress_tables = {'workout_resolutions', 'goal_preparation_items'}
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name not in progress_tables])
    try:
        with Session(engine) as db:
            db.add_all([SchemaVersion(version=1), User(id=1, timezone='Europe/Rome'),
                DailyMetrics(date='2026-10-17', sleep_hours=None, steps=0),
                HealthMetric(date='2026-10-17', kind='sleep_hours', source='manual', value=8,
                             unit='ore', observed_at='2026-10-17', synced_at='old-v1-copy'),
                Activity(source='synthetic', source_id='preserved', sport='running',
                         start_time=datetime(2026, 10, 16, 8), duration_seconds=1800)])
            db.commit()
        migrate(engine)
        assert progress_tables <= set(inspect(engine).get_table_names())
        backups = list((folder / 'backups').glob('*-before-iron-v2-*.db'))
        assert len(backups) == 1
        with closing(sqlite3.connect(backups[0])) as snapshot:
            assert snapshot.execute('SELECT version FROM iron_schema_versions').fetchall() == [(1,)]
            assert snapshot.execute('SELECT sleep_hours,steps FROM daily_metrics').fetchone() == (None, 0)
            assert snapshot.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='workout_resolutions'").fetchone()[0] == 0
        with Session(engine) as db:
            assert {row.version for row in db.query(SchemaVersion)} == {1, 2, 3}
            assert db.query(Activity).one().source_id == 'preserved'
            assert db.query(HealthMetric).count() == 1  # Version two does not repeat manual import.
            assert health.effective_metrics(db, date(2026, 10, 17)) == {'steps': 0}
        migrate(engine)
        assert len(list((folder / 'backups').glob('*.db'))) == 1
    finally:
        engine.dispose()
        database.unlink(missing_ok=True)
        for snapshot in (folder / 'backups').glob('*.db'):
            snapshot.unlink()
        if (folder / 'backups').exists():
            (folder / 'backups').rmdir()
        folder.rmdir()
