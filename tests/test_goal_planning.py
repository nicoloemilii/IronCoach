"""Detailed race planning and previews preserve conservative limits and history."""
import json
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes_planning import router
from app.db.database import Base, get_db
from app.db.iron_models import RaceGoal
from app.db.models import Activity, Workout
from app.db.planning_models import PlanRevision
from app.db.progress_models import WorkoutResolution
from app.services import planning

TODAY = date(2026, 10, 17)
MONDAY = date(2026, 10, 19)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(planning, '_today', lambda: TODAY)
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides[get_db] = lambda: db
        yield db, TestClient(application), engine
    engine.dispose()


def goal(db, legs=None, **changes):
    values = dict(name='Synthetic race', date='2027-06-01', priority='finish', target_time=None,
                  legs_json=json.dumps(legs or [{'sport': 'running', 'terrain': 'trail', 'distance_km': 30,
                                                'elevation_gain_m': 1500, 'altitude_max_m': 2500, 'requirements': {}}]))
    values.update(changes)
    row = RaceGoal(**values)
    db.add(row)
    db.commit()
    return row


def sessions(week):
    return [row for day in week['days'] for row in day['sessions'] if row['status'] != 'skipped']


def profile(db, **changes):
    return planning.save_settings(db, {'availability': [60] * 7, **changes})


def test_goal_activation_copies_metadata_checks_revision_and_detaches(setup):
    db, client, _ = setup
    target = goal(db, priority='time', target_time='06:30:00')
    selected = client.put('/planning/goal', json={'goal_id': target.id}, headers={'If-Match': '"0"'})
    assert selected.status_code == 200
    payload = selected.json()
    assert payload['revision'] == 1
    assert payload['profile']['race_goal_id'] == target.id
    assert payload['profile']['event_name'] == target.name and payload['profile']['event_date'] == target.date
    assert payload['profile']['goal_priority'] == 'time' and payload['profile']['target_time'] == '06:30:00'
    assert client.get('/planning/profile').json() == payload
    stale = client.put('/planning/goal', json={'goal_id': None}, headers={'If-Match': '0'})
    assert stale.status_code == 412 and stale.json()['detail']['current_revision'] == 1
    assert client.put('/planning/goal', json={'goal_id': 9999}).status_code == 404
    detached = client.put('/planning/goal', json={'goal_id': None}, headers={'If-Match': '1'})
    assert detached.status_code == 200 and detached.json()['profile']['race_goal_id'] is None
    assert detached.json()['revision'] == 2
    assert client.put('/planning/goal', json={'goal_id': 0}).status_code == 422
    assert client.put('/planning/goal', json={}).status_code == 422


def test_active_trail_goal_orients_real_workouts_without_increasing_capacity(setup):
    db, _, _ = setup
    profile(db, weekly_minutes=900, thresholds={'running_threshold_pace_sec_per_km': {'value': 300, 'source': 'estimated'}})
    baseline = planning.generate_week(db, MONDAY)
    target = goal(db)
    planning.activate_goal(db, target.id)
    week = planning.generate_week(db, MONDAY, regenerate=True)
    active = sessions(week)
    assert active and {row['sport'] for row in active} <= {'running', 'strength'}
    assert week['target_minutes'] == baseline['target_minutes']
    assert week['total_minutes'] <= week['target_minutes'] <= 180
    assert week['race_goal']['id'] == target.id and not week['needs_review']
    assert week['allocation']['shares']['cycling'] == week['allocation']['shares']['swimming'] == 0
    assert 'non tempi gara' in week['allocation']['proxy']['note']
    assert not week['brick_status']['scheduled']
    for row in active:
        assert row['duration_min'] <= (30 if row['sport'] == 'running' else 25)
        assert row['payload']['goal_focus']['goal_id'] == target.id
        assert all(step['target_rpe'] <= 3 for step in row['payload']['steps'])
        if row['sport'] == 'running':
            assert all('reference_pace_sec_per_km' not in step for step in row['payload']['steps'])
            assert any('Nessun dislivello minimo' in step['instruction'] for step in row['payload']['steps'])
    assert all(day['required_minutes'] <= day['available_minutes'] for day in week['days'])


def test_goal_distance_climbing_and_terrain_change_allocation_not_capacity(setup):
    db, _, _ = setup
    profile(db)
    legs = [{'sport': 'cycling', 'terrain': 'road', 'distance_km': 80, 'elevation_gain_m': 0},
            {'sport': 'running', 'terrain': 'road', 'distance_km': 10, 'elevation_gain_m': 0}]
    target = goal(db, legs)
    planning.activate_goal(db, target.id)
    initial = planning.generate_week(db, MONDAY)
    first_share = initial['allocation']['shares']['running']
    legs[1].update(terrain='trail', distance_km=30, elevation_gain_m=3000)
    target.legs_json = json.dumps(legs)
    db.commit()
    review = planning.get_week(db, MONDAY)
    assert review['needs_review'] and review['race_goal']['signature'] != initial['race_goal']['signature']
    # Reading a changed goal never rewrites existing sessions.
    assert [row['payload'] for row in sessions(review)] == [row['payload'] for row in sessions(initial)]
    regenerated = planning.generate_week(db, MONDAY, regenerate=True)
    assert regenerated['allocation']['shares']['running'] > first_share
    assert regenerated['target_minutes'] == initial['target_minutes']
    assert regenerated['total_minutes'] <= regenerated['target_minutes']
    assert not regenerated['needs_review']


def test_detailed_goal_respects_pool_exclusions_and_missing_sport_evidence(setup):
    db, _, _ = setup
    profile(db, pool_access=False, excluded_sports=['running'], weekly_minutes=900)
    target = goal(db, [{'sport': 'swimming', 'terrain': 'water', 'distance_km': 4},
                       {'sport': 'cycling', 'terrain': 'road', 'distance_km': 180, 'elevation_gain_m': 5000},
                       {'sport': 'running', 'terrain': 'trail', 'distance_km': 42, 'elevation_gain_m': 1500}])
    planning.activate_goal(db, target.id)
    week = planning.generate_week(db, MONDAY)
    assert {row['sport'] for row in sessions(week)} <= {'cycling', 'strength'}
    assert all(row['duration_min'] <= (60 if row['sport'] == 'cycling' else 25) for row in sessions(week))
    assert any('piscina non disponibile' in text for text in week['race_goal']['constraints'])
    assert any('escluse' in text for text in week['race_goal']['constraints'])


def test_deleted_active_goal_warns_and_drops_stale_taper_context(setup):
    db, _, _ = setup
    profile(db)
    target = goal(db, date='2026-10-22')
    planning.activate_goal(db, target.id)
    before = planning.generate_week(db, MONDAY)
    assert before['phase'] == 'race_week'
    identifier = target.id
    db.delete(target)
    db.commit()
    missing = planning.get_week(db, MONDAY)
    assert missing['race_goal'] is None and missing['goal_warnings'] and missing['needs_review']
    assert missing['phase'] == 'base'
    assert planning.load_settings(db)['race_goal_id'] == identifier
    assert any('eliminato' in text for text in missing['rationale'])


def comparable(value):
    copy = deepcopy(value)
    copy.get('payload', {}).get('planning', {}).get('last_change', {}).pop('at', None)
    return copy


@pytest.mark.parametrize('action', ['move', 'shorten', 'skip'])
def test_preview_has_no_writes_matches_actual_change_and_undo(setup, action):
    db, client, engine = setup
    profile(db)
    week = planning.generate_week(db, MONDAY)
    row = max(sessions(week), key=lambda item: item['duration_min'])
    body = {'action': action, 'reason': 'Synthetic preview'}
    if action == 'move':
        body['date'] = str(MONDAY)  # The equal-slot schedule leaves Monday as rest.
    elif action == 'shorten':
        body['duration_min'] = 15
    writes = []
    commits = []

    def capture(_, __, statement, *args):
        if statement.lstrip().split()[0].upper() in ('UPDATE', 'INSERT', 'DELETE'):
            writes.append(statement)

    def commit(_):
        commits.append(True)

    event.listen(engine, 'before_cursor_execute', capture)
    event.listen(engine, 'commit', commit)
    try:
        preview = client.post(f"/planning/workouts/{row['id']}/preview", json=body)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
        event.remove(engine, 'commit', commit)
    assert preview.status_code == 200, preview.text
    assert writes == commits == [] and not db.dirty
    predicted = preview.json()
    # The calendar adds read-only execution evidence. Mutation snapshots still
    # contain every original ledger field and never persist those derived values.
    ledger = {key: row[key] for key in ('id', 'date', 'sport', 'name', 'duration_min', 'status', 'payload')}
    assert predicted['before'] == ledger
    assert planning.serialize_workout(db.get(Workout, row['id'])) == ledger
    assert db.query(PlanRevision).count() == 1
    actual = client.patch(f"/planning/workouts/{row['id']}", json=body)
    assert actual.status_code == 200, actual.text
    assert comparable(predicted['after']) == comparable(actual.json()['workout'])
    assert predicted['impact']['after_minutes'] == actual.json()['week']['total_minutes']
    assert predicted['impact']['delta_minutes'] == actual.json()['week']['total_minutes'] - week['total_minutes']
    restored = planning.undo_week(db, MONDAY)
    assert next(item for item in sessions(restored) if item['id'] == row['id']) == row


@pytest.mark.parametrize('body,status', [
    ({'action': 'move', 'date': '2026-10-16'}, 422),
    ({'action': 'move', 'date': '2026-10-26'}, 422),
    ({'action': 'shorten', 'duration_min': 60}, 422),
    ({'action': 'move'}, 422),
])
def test_invalid_previews_and_mutations_share_validation(setup, body, status):
    db, client, _ = setup
    profile(db)
    week = planning.generate_week(db, MONDAY)
    row = max(sessions(week), key=lambda item: item['duration_min'])
    preview = client.post(f"/planning/workouts/{row['id']}/preview", json=body)
    mutation = client.patch(f"/planning/workouts/{row['id']}", json=body)
    assert preview.status_code == mutation.status_code == status
    assert preview.json()['detail'] == mutation.json()['detail']
    assert db.query(PlanRevision).count() == 1


def test_preview_does_not_autoflush_unrelated_pending_state(setup):
    db, _, engine = setup
    profile(db)
    week = planning.generate_week(db, MONDAY)
    row = max(sessions(week), key=lambda item: item['duration_min'])
    activity = Activity(source='synthetic', source_id='unrelated', sport='running', start_time=datetime(2026, 10, 16, 8), name='Original')
    db.add(activity)
    db.commit()
    activity.name = 'Unsaved edit'
    writes = []

    def capture(_, __, statement, *args):
        if statement.lstrip().split()[0].upper() in ('UPDATE', 'INSERT', 'DELETE'):
            writes.append(statement)

    event.listen(engine, 'before_cursor_execute', capture)
    try:
        planning.preview_workout(db, row['id'], 'shorten', duration_min=15)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert writes == [] and activity in db.dirty
    db.rollback()
    assert activity.name == 'Original'


def test_undo_preserves_an_independent_active_rest_resolution(setup):
    db, _, _ = setup
    profile(db)
    week = planning.generate_week(db, MONDAY)
    row = sessions(week)[0]
    workout = db.get(Workout, row['id'])
    workout.status = 'skipped'
    db.add(WorkoutResolution(workout_id=row['id'], kind='rest', reason='Explicit independent decision', prior_status='planned'))
    db.commit()
    planning.undo_week(db, MONDAY)
    assert db.get(Workout, row['id']).status == 'skipped'
    assert db.query(WorkoutResolution).one().undone_at is None


def brick(db, day, identifier, bike_minutes=30, run_minutes=20):
    settings = planning.load_settings(db)
    rows = []
    for sport, minutes in [('cycling', bike_minutes), ('running', run_minutes)]:
        payload = planning._make_workout(settings, sport, minutes, 'support', 'base', 1, MONDAY)
        row = Workout(date=str(day), sport=sport, name=payload['name'], duration_min=minutes,
                      payload_json=json.dumps(payload), status='planned')
        db.add(row)
        rows.append(row)
    db.flush()
    for sequence, row in enumerate(rows, 1):
        payload = json.loads(row.payload_json)
        payload['brick'] = {'group_id': identifier, 'sequence': sequence,
                            'partner_workout_id': rows[2 - sequence].id, 'transition_minutes': 5,
                            'original_name': row.name, 'original_purpose': payload['purpose']}
        row.payload_json = json.dumps(payload)
    db.commit()
    return rows


@pytest.mark.parametrize('action', ['move', 'skip'])
def test_brick_preview_predicts_linked_changes_and_transition_time(setup, action):
    db, client, _ = setup
    profile(db, availability=[90] * 7)
    bike, run = brick(db, MONDAY + timedelta(days=5), 'pair')
    before_bike, before_run = planning.serialize_workout(bike), planning.serialize_workout(run)
    body = {'action': action}
    if action == 'move':
        body['date'] = str(MONDAY)
    predicted = client.post(f'/planning/workouts/{bike.id}/preview', json=body)
    assert predicted.status_code == 200
    preview = predicted.json()
    assert len(preview['affected_workouts']) == 2
    saturday = preview['impact']['days'][5]
    assert saturday['before_required_minutes'] == 55
    assert saturday['after_required_minutes'] == (0 if action == 'move' else 20)
    assert preview['impact']['delta_minutes'] == (0 if action == 'move' else -30)
    assert planning.serialize_workout(run) == before_run
    actual = client.patch(f'/planning/workouts/{bike.id}', json=body,
                          headers={'X-Plan-Preview': preview['preview_signature']})
    assert actual.status_code == 200
    assert comparable(actual.json()['workout']) == comparable(preview['after'])
    planning.undo_week(db, MONDAY)
    assert planning.serialize_workout(db.get(Workout, bike.id)) == before_bike
    assert planning.serialize_workout(db.get(Workout, run.id)) == before_run


def test_move_counts_transition_of_an_existing_brick_on_destination_day(setup):
    db, client, _ = setup
    profile(db, availability=[84] * 7)
    brick(db, MONDAY, 'existing', bike_minutes=30, run_minutes=20)
    payload = planning._make_workout(planning.load_settings(db), 'running', 30, 'support', 'base', 1, MONDAY)
    row = Workout(date=str(MONDAY + timedelta(days=1)), sport='running', name=payload['name'],
                  duration_min=30, payload_json=json.dumps(payload), status='planned')
    db.add(row)
    db.commit()
    body = {'action': 'move', 'date': str(MONDAY)}
    assert client.post(f'/planning/workouts/{row.id}/preview', json=body).status_code == 409
    assert client.patch(f'/planning/workouts/{row.id}', json=body).status_code == 409


@pytest.mark.parametrize('changed', ['week', 'profile', 'goal'])
def test_preview_signature_rejects_concurrent_changes_and_fresh_confirmation_succeeds(setup, changed):
    db, client, engine = setup
    profile(db)
    target = goal(db)
    planning.activate_goal(db, target.id)
    week = planning.generate_week(db, MONDAY)
    row = max(sessions(week), key=lambda item: item['duration_min'])
    body = {'action': 'shorten', 'duration_min': 15}
    preview = client.post(f"/planning/workouts/{row['id']}/preview", json=body).json()
    # No request state changed: repeated previews have a stable signature.
    assert client.post(f"/planning/workouts/{row['id']}/preview", json=body).json()['preview_signature'] == preview['preview_signature']
    if changed == 'week':
        other = next(item for item in sessions(week) if item['id'] != row['id'])
        planning.modify_workout(db, other['id'], 'skip')
    elif changed == 'profile':
        settings = planning.load_settings(db)
        planning.save_settings(db, {**settings, 'name': 'Another window'})
    else:
        legs = json.loads(target.legs_json)
        legs[0]['distance_km'] += 10
        target.legs_json = json.dumps(legs)
        db.commit()
    original = planning.serialize_workout(db.get(Workout, row['id']))
    audits = db.query(PlanRevision).count()
    writes = []

    def capture(_, __, statement, *args):
        if statement.lstrip().split()[0].upper() in ('UPDATE', 'INSERT', 'DELETE'):
            writes.append(statement)

    event.listen(engine, 'before_cursor_execute', capture)
    try:
        stale = client.patch(f"/planning/workouts/{row['id']}", json=body,
                             headers={'X-Plan-Preview': preview['preview_signature']})
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert stale.status_code == 409 and 'anteprima' in stale.json()['detail']
    assert writes == []
    assert planning.serialize_workout(db.get(Workout, row['id'])) == original
    assert db.query(PlanRevision).count() == audits
    fresh = client.post(f"/planning/workouts/{row['id']}/preview", json=body).json()
    assert fresh['preview_signature'] != preview['preview_signature']
    accepted = client.patch(f"/planning/workouts/{row['id']}", json=body,
                            headers={'X-Plan-Preview': fresh['preview_signature']})
    assert accepted.status_code == 200 and accepted.json()['workout']['duration_min'] == 15


def test_stale_preview_becoming_invalid_returns_conflict_without_second_change(setup):
    db, client, _ = setup
    profile(db)
    week = planning.generate_week(db, MONDAY)
    row = max(sessions(week), key=lambda item: item['duration_min'])
    body = {'action': 'shorten', 'duration_min': 15}
    preview = client.post(f"/planning/workouts/{row['id']}/preview", json=body).json()
    planning.modify_workout(db, row['id'], 'shorten', duration_min=15)
    audits = db.query(PlanRevision).count()
    stale = client.patch(f"/planning/workouts/{row['id']}", json=body,
                         headers={'X-Plan-Preview': preview['preview_signature']})
    assert stale.status_code == 409 and 'anteprima' in stale.json()['detail']
    assert db.query(PlanRevision).count() == audits
    assert db.get(Workout, row['id']).duration_min == 15
