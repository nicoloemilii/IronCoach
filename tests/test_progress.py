"""Progress uses confirmed evidence, preserves audit, and never calls accounts."""
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_iron, routes_progress
from app.db.database import Base, get_db
from app.db.experience_models import AthleteCheckin, SessionFeedback
from app.db.garmin_models import GarminDelivery
from app.db.iron_models import HealthMetric, RaceGoal
from app.db.models import Activity, DailyMetrics, Workout, WorkoutEvaluation
from app.db.progress_models import GoalPreparationItem, WorkoutResolution
from app.services import progress, race_gap

TODAY = date(2026, 10, 17)
NOW = datetime(2026, 10, 17, 12)
MONDAY = TODAY - timedelta(days=TODAY.weekday())


@pytest.fixture
def api(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    for module in (progress, race_gap):
        monkeypatch.setattr(module, 'athlete_today', lambda: TODAY)
        monkeypatch.setattr(module, 'utc_now_naive', lambda: NOW)
    app = FastAPI(); app.include_router(routes_progress.router); app.include_router(routes_iron.router)

    def isolated_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    yield TestClient(app, client=('127.0.0.1', 50000)), engine
    engine.dispose()


def workout(db, day=MONDAY, status='planned', minutes=60):
    row = Workout(date=str(day), sport='cycling', name='Seduta sintetica', duration_min=minutes,
                  status=status, payload_json=json.dumps({'sport': 'cycling', 'duration_min': minutes}))
    db.add(row); db.flush()
    return row


def activity(db, identifier='a1', day=MONDAY, duration=3600, hour=8, **changes):
    data = dict(source='fit', source_id=identifier, sport='cycling', name='Registrata',
                start_time=datetime.combine(day, datetime.min.time()) + timedelta(hours=hour),
                duration_seconds=duration, distance_meters=20000, elevation_gain_meters=100)
    data.update(changes)
    row = Activity(**data); db.add(row); db.flush()
    return row


def feedback(db, w, a=None, minutes=60, completion=100, feeling='as_expected'):
    db.add(SessionFeedback(workout_id=w.id, activity_id=a.id if a else None,
                           payload_json=json.dumps({'completion_percent': completion, 'rpe': 4,
                                                    'feeling': feeling, 'actual': {'duration_min': minutes}})))
    w.status = 'completed'


def goal(db, **changes):
    leg = dict(sport='cycling', terrain='road', distance_km=90, elevation_gain_m=1000,
               requirements={'weekly_hours': 1, 'longest_distance_km': 20, 'longest_elevation_m': 100})
    leg.update(changes)
    row = RaceGoal(name='Gara sintetica', date='2027-06-01', priority='finish', legs_json=json.dumps([leg]))
    db.add(row); db.commit()
    return row.id


def add_metric(db, kind, value, day, source='intervals'):
    unit = {'hrv_ms': 'ms', 'resting_hr_bpm': 'bpm', 'sleep_hours': 'ore'}[kind]
    db.add(HealthMetric(date=str(day), kind=kind, value=value, unit=unit, source=source,
                        observed_at=str(day), synced_at=NOW.isoformat() + 'Z'))


def item(body, key):
    return next(row for row in body['items'] if row['key'] == key)


def test_empty_and_future_week_do_not_invent_execution_or_adherence(api):
    client, engine = api
    assert client.get('/progress/week').json()['adherence']['percent'] is None
    future_monday = MONDAY + timedelta(days=7)
    with Session(engine) as db:
        w = workout(db, future_monday)
        # Even a corrupted future feedback cannot count as executed now.
        feedback(db, w)
        activity(db, day=future_monday)
        activity(db, 'later-today', day=TODAY, hour=23)
        db.commit()
    body = client.get(f'/progress/week?start={future_monday + timedelta(days=2)}').json()
    assert body['start'] == str(future_monday)
    assert body['counts']['planned'] == 1 and body['counts']['completed'] == 0
    assert body['unconfirmed_past'] == []
    assert body['adherence']['percent'] is None
    assert body['totals']['recorded_activities'] == 0
    assert client.get('/progress/week').json()['totals']['recorded_activities'] == 0


def test_week_separates_confirmed_modified_skipped_rest_and_unknown(api):
    client, engine = api
    with Session(engine) as db:
        shortened = workout(db)
        actual = activity(db, duration=1800)
        feedback(db, shortened, actual, minutes=60)  # Actual beats declaration.
        completed = workout(db, MONDAY + timedelta(days=1))
        feedback(db, completed, minutes=60)
        skipped = workout(db, MONDAY + timedelta(days=2))
        rested = workout(db, MONDAY + timedelta(days=3))
        unresolved = workout(db, MONDAY + timedelta(days=4), status='completed')
        # No feedback means no evidence, despite the old completed state.
        future = workout(db, TODAY + timedelta(days=1))
        db.commit(); skipped_id = skipped.id; rest_id = rested.id; unresolved_id = unresolved.id
    assert client.put(f'/progress/workouts/{skipped_id}/resolution', json={'status': 'skipped', 'reason': 'Impegno personale'}).status_code == 200
    assert client.put(f'/progress/workouts/{rest_id}/resolution', json={'status': 'rest', 'reason': 'Riposo scelto'}).status_code == 200
    body = client.get('/progress/week').json()
    assert body['counts'] == {'completed': 1, 'modified': 1, 'skipped': 1, 'rest': 1, 'unconfirmed': 1, 'planned': 1}
    assert body['totals']['confirmed_actual_minutes'] == 90
    assert body['totals']['recorded_minutes'] == 30
    assert body['totals']['planned_minutes'] == 360
    assert body['adherence']['percent'] == 50
    assert body['adherence']['evaluated_sessions'] == 3
    assert body['adherence']['unknown_sessions'] == 1
    assert body['unconfirmed_past'][0]['id'] == unresolved_id
    assert not body['unconfirmed_past'][0]['can_resolve']
    bike = next(row for row in body['sports'] if row['sport'] == 'cycling')
    assert bike['confirmed_actual_minutes'] == 90 and bike['recorded_activities'] == 1
    assert body['suggestions'] and 'confermare' in body['review']


def test_future_linked_activity_does_not_confirm_past_workout(api):
    client, engine = api
    with Session(engine) as db:
        w = workout(db)
        a = activity(db, day=TODAY, hour=23)
        feedback(db, w, a)
        db.commit()
    body = client.get('/progress/week').json()
    assert body['counts']['unconfirmed'] == 1
    assert body['totals']['confirmed_sessions'] == 0
    assert body['adherence']['percent'] is None


def test_missing_duration_is_unknown_and_unlinked_activity_is_not_plan_evidence(api):
    client, engine = api
    with Session(engine) as db:
        w = workout(db)
        a = activity(db, duration=None)
        feedback(db, w, a, minutes=60)
        workout(db, MONDAY + timedelta(days=1))
        activity(db, 'unlinked', day=MONDAY + timedelta(days=1))
        db.commit()
    body = client.get('/progress/week').json()
    assert body['adherence']['percent'] is None
    assert body['totals']['confirmed_actual_minutes'] is None
    assert body['totals']['recorded_minutes'] is None
    assert body['counts']['unconfirmed'] == 1


def test_resolution_is_explicit_idempotent_and_undo_retains_history(api):
    client, engine = api
    with Session(engine) as db:
        w = workout(db); db.commit(); identifier = w.id
    endpoint = f'/progress/workouts/{identifier}/resolution'
    payload = {'status': 'rest', 'reason': '  Riposo scelto  '}
    first = client.put(endpoint, json=payload)
    assert first.status_code == 200
    assert first.json()['status'] == 'skipped' and first.json()['resolution']['status'] == 'rest'
    assert first.json()['resolution']['reason'] == 'Riposo scelto'
    assert client.put(endpoint, json=payload).status_code == 200
    assert client.put(endpoint, json={'status': 'skipped', 'reason': 'Cambio idea'}).status_code == 409
    with Session(engine) as db:
        assert db.query(WorkoutResolution).count() == 1
        assert db.query(SessionFeedback).count() == 0 and db.query(WorkoutEvaluation).count() == 0
    assert client.delete(endpoint).json()['status'] == 'planned'
    with Session(engine) as db:
        assert db.query(WorkoutResolution).one().undone_at is not None
    assert client.delete(endpoint).status_code == 404
    assert client.put(endpoint, json={'status': 'skipped', 'reason': 'Non svolta'}).status_code == 200
    with Session(engine) as db:
        assert db.query(WorkoutResolution).count() == 2


@pytest.mark.parametrize('mode', ['today', 'future', 'completed', 'feedback', 'evaluation', 'published', 'pending'])
def test_resolution_rejects_nonpast_or_existing_evidence_and_remote_state(api, mode):
    client, engine = api
    with Session(engine) as db:
        w = workout(db, TODAY if mode == 'today' else TODAY + timedelta(days=1) if mode == 'future' else MONDAY,
                    status='completed' if mode == 'completed' else 'planned')
        if mode == 'feedback':
            feedback(db, w); w.status = 'planned'
        if mode == 'evaluation':
            db.add(WorkoutEvaluation(workout_id=w.id, payload_json='{}'))
        if mode in ('published', 'pending'):
            db.add(GarminDelivery(workout_id=w.id, content_hash='x', status=mode,
                                  remote_published=mode == 'published', reason_code='synthetic'))
        db.commit(); identifier = w.id
    result = client.put(f'/progress/workouts/{identifier}/resolution', json={'status': 'skipped', 'reason': 'Non svolta'})
    assert result.status_code == 409
    with Session(engine) as db:
        assert db.query(WorkoutResolution).count() == 0


def test_resolution_undo_rejects_new_feedback_and_validates_local_input(api):
    client, engine = api
    with Session(engine) as db:
        w = workout(db); db.commit(); identifier = w.id
    endpoint = f'/progress/workouts/{identifier}/resolution'
    assert client.put(endpoint, json={'status': 'rest', 'reason': '   '}).status_code == 422
    assert client.put(endpoint, json={'status': 'completed', 'reason': 'No'}).status_code == 422
    remote = TestClient(client.app, client=('192.168.1.10', 1234))
    assert remote.put(endpoint, json={'status': 'rest', 'reason': 'No'}).status_code == 403
    assert client.put(endpoint, json={'status': 'rest', 'reason': 'Riposo'}).status_code == 200
    with Session(engine) as db:
        feedback(db, db.get(Workout, identifier)); db.commit()
    assert client.delete(endpoint).status_code == 409
    assert client.put(endpoint, json={'status': 'rest', 'reason': 'Riposo'}).status_code == 409
    assert client.put('/progress/workouts/999/resolution', json={'status': 'rest', 'reason': 'Riposo'}).status_code == 404


def test_recovery_compares_personal_medians_without_crossing_sources(api):
    client, engine = api
    with Session(engine) as db:
        for offset in range(7, 14):
            add_metric(db, 'hrv_ms', 50, TODAY - timedelta(days=offset))
            add_metric(db, 'resting_hr_bpm', 60, TODAY - timedelta(days=offset))
            add_metric(db, 'sleep_hours', 8, TODAY - timedelta(days=offset))
        for offset in range(3):
            add_metric(db, 'hrv_ms', 60, TODAY - timedelta(days=offset))
            add_metric(db, 'resting_hr_bpm', 58, TODAY - timedelta(days=offset))
            add_metric(db, 'sleep_hours', 8, TODAY - timedelta(days=offset))
        db.add(AthleteCheckin(date=str(TODAY), payload_json=json.dumps({'fatigue': 4, 'sleep_quality': 2, 'notes': 'Sensazioni'})))
        db.commit()
    body = client.get('/progress/recovery').json()
    metrics = {row['kind']: row for row in body['metrics']}
    assert metrics['hrv_ms']['baseline'] == 50 and metrics['hrv_ms']['current'] == 60
    assert metrics['hrv_ms']['recent_delta'] == 10 and metrics['hrv_ms']['status'] == 'above_baseline'
    assert metrics['resting_hr_bpm']['status'] == 'below_baseline'
    assert metrics['sleep_hours']['status'] == 'unchanged'
    assert all(row['counts'] == {'baseline': 7, 'recent': 3} for row in body['metrics'])
    assert body['subjective'][0]['fatigue'] == 4 and body['subjective'][0]['source'] == 'self_reported'
    assert 'confidence' not in json.dumps(body) and 'diagnosi' in body['limits'][0]


def test_recovery_source_change_and_missing_baseline_stay_insufficient(api):
    client, engine = api
    with Session(engine) as db:
        for offset in range(7, 14):
            db.add(DailyMetrics(date=str(TODAY - timedelta(days=offset)), hrv_ms=40))
        for offset in range(3):
            add_metric(db, 'hrv_ms', 60, TODAY - timedelta(days=offset))
        # Future data must not make the reference complete.
        for offset in range(1, 8):
            add_metric(db, 'hrv_ms', 70, TODAY + timedelta(days=offset))
        db.commit()
    body = client.get('/progress/recovery').json()
    hrv = next(row for row in body['metrics'] if row['kind'] == 'hrv_ms')
    assert hrv['source'] == 'intervals' and hrv['baseline'] is None
    assert hrv['current'] == 60 and hrv['recent_delta'] is None
    assert hrv['status'] == 'insufficient_data'
    assert all(row['date'] <= str(TODAY) for row in hrv['points'])
    sleep = next(row for row in body['metrics'] if row['kind'] == 'sleep_hours')
    assert sleep['source'] is None and sleep['current'] is None


def test_recovery_marks_stale_and_requires_three_recent_points(api):
    client, engine = api
    with Session(engine) as db:
        for offset in range(7, 14):
            add_metric(db, 'hrv_ms', 50, TODAY - timedelta(days=offset))
        for offset in (3, 4, 5):
            add_metric(db, 'hrv_ms', 55, TODAY - timedelta(days=offset))
        add_metric(db, 'resting_hr_bpm', 60, TODAY)
        db.commit()
    body = client.get('/progress/recovery').json()
    metrics = {row['kind']: row for row in body['metrics']}
    assert metrics['hrv_ms']['status'] == 'stale' and metrics['hrv_ms']['stale']
    assert metrics['hrv_ms']['recent_delta'] == 5
    assert metrics['resting_hr_bpm']['current'] is None
    assert client.get('/progress/recovery?days=6').status_code == 422
    assert client.get('/progress/recovery?days=91').status_code == 422


@pytest.mark.parametrize('kind', ['hrv_ms', 'resting_hr_bpm'])
def test_recovery_invalid_zero_does_not_supply_third_usable_recent_reading(api, kind):
    client, engine = api
    with Session(engine) as db:
        for offset in range(7, 14):
            add_metric(db, kind, 50, TODAY - timedelta(days=offset))
        for offset, value in enumerate([0, 50, 50]):
            add_metric(db, kind, value, TODAY - timedelta(days=offset))
        db.commit()
    metric = next(row for row in client.get('/progress/recovery').json()['metrics'] if row['kind'] == kind)
    assert metric['counts'] == {'baseline': 7, 'recent': 2}
    assert metric['current'] is None and metric['recent_delta'] is None
    assert metric['status'] == 'insufficient_data'
    assert metric['points'][-1]['value'] == 0  # Original observation remains inspectable.


def test_preparation_persists_per_goal_and_keeps_user_confirmation_distinct(api):
    client, engine = api
    with Session(engine) as db:
        identifier = goal(db)
        other = goal(db)
    body = client.get(f'/goals/{identifier}/preparation').json()
    assert body['gap_score'] is None
    assert not item(body, 'leg_0_weekly_hours')['verified']
    response = client.put(f'/goals/{identifier}/preparation', json={'items': {
        'equipment': {'done': True, 'notes': 'Provato in uscita'},
        'leg_0_weekly_hours': {'done': True, 'notes': 'Conferma personale, dati incompleti'},
    }})
    assert response.status_code == 200
    saved = client.get(f'/goals/{identifier}/preparation').json()
    assert item(saved, 'equipment')['done'] and item(saved, 'equipment')['self_confirmed']
    assert item(saved, 'equipment')['notes'] == 'Provato in uscita'
    capacity = item(saved, 'leg_0_weekly_hours')
    assert capacity['self_confirmed'] and not capacity['verified']
    assert saved['gap_score'] is None  # A checkbox cannot fabricate readiness.
    assert not item(client.get(f'/goals/{other}/preparation').json(), 'equipment')['done']
    assert client.put(f'/goals/{identifier}/preparation', json={'items': {'equipment': {'done': False, 'notes': 'Da ricontrollare'}}}).status_code == 200
    assert not item(client.get(f'/goals/{identifier}/preparation').json(), 'equipment')['done']
    with Session(engine) as db:
        assert db.query(GoalPreparationItem).count() == 2


def test_preparation_derives_verified_capacity_and_multisport_specificity(api):
    client, engine = api
    with Session(engine) as db:
        identifier = goal(db, terrain='trail', altitude_max_m=2000,
                          requirements={'weekly_hours': 1, 'longest_distance_km': 20,
                                        'longest_elevation_m': 100, 'terrain_confirmed': True})
        row = db.get(RaceGoal, identifier)
        legs = json.loads(row.legs_json)
        legs.append({'sport': 'running', 'terrain': 'road', 'distance_km': 10,
                     'requirements': {'weekly_hours': 1, 'longest_distance_km': 10}})
        row.legs_json = json.dumps(legs)
        for index in range(1, 5):
            activity(db, f'week-{index}', day=MONDAY - timedelta(days=7 * index - 1))
        db.commit()
    body = client.get(f'/goals/{identifier}/preparation').json()
    assert item(body, 'leg_0_weekly_hours')['verified']
    assert item(body, 'leg_0_weekly_hours')['done']
    terrain = item(body, 'leg_0_terrain_confirmed')
    assert terrain['self_confirmed'] and not terrain['verified']
    assert not item(body, 'leg_0_altitude_confirmed')['done']
    assert item(body, 'bricks')['category'] == 'technique'
    assert item(body, 'leg_1_technique')
    assert client.put(f'/goals/{identifier}/preparation', json={'items': {'leg_0_terrain_confirmed': {'done': False, 'notes': 'Da verificare ancora'}}}).status_code == 200
    assert not item(client.get(f'/goals/{identifier}/preparation').json(), 'leg_0_terrain_confirmed')['done']


def test_preparation_configuration_change_invalidates_old_confirmation(api):
    client, engine = api
    with Session(engine) as db:
        identifier = goal(db)
    assert client.put(f'/goals/{identifier}/preparation', json={'items': {'equipment': {'done': True, 'notes': 'Prova precedente'}}}).status_code == 200
    with Session(engine) as db:
        row = db.get(RaceGoal, identifier)
        legs = json.loads(row.legs_json); legs[0]['terrain'] = 'trail'
        row.legs_json = json.dumps(legs); db.commit()
    result = item(client.get(f'/goals/{identifier}/preparation').json(), 'equipment')
    assert not result['done'] and not result['self_confirmed'] and result['confirmation_stale']
    assert result['notes'] == 'Prova precedente'


def test_preparation_rejects_unknown_key_atomically_and_never_accepts_fake_bool(api):
    client, engine = api
    with Session(engine) as db:
        identifier = goal(db)
    endpoint = f'/goals/{identifier}/preparation'
    assert client.put(endpoint, json={'items': {'equipment': {'done': True}, 'unknown': {'done': True}}}).status_code == 422
    with Session(engine) as db:
        assert db.query(GoalPreparationItem).count() == 0
    assert client.put(endpoint, json={'items': {'equipment': {'done': 'false'}}}).status_code == 422
    assert client.put(endpoint, json={'items': {'equipment': {'done': True, 'notes': 'a' * 4001}}}).status_code == 422
    assert client.get('/goals/9999/preparation').status_code == 404
    assert client.put('/goals/9999/preparation', json={'items': {'equipment': {'done': True}}}).status_code == 404


def test_preparation_context_signature_rejects_stale_updates_without_losing_notes(api):
    client, engine = api
    with Session(engine) as db:
        identifier = goal(db)
    endpoint = f'/goals/{identifier}/preparation'
    original = client.get(endpoint).json()
    signature = original['context_signature']
    assert len(signature) == 64 and set(signature) <= set('0123456789abcdef')
    saved = client.put(endpoint, json={'context_signature': signature, 'items': {
        'equipment': {'done': True, 'notes': 'Note già salvate da conservare'}}})
    assert saved.status_code == 200, saved.text
    with Session(engine) as db:
        persisted = db.query(GoalPreparationItem).one()
        old_hash, old_updated = persisted.context_hash, persisted.updated_at
    # The public goal API changes requirements after the checklist was opened.
    changed = client.patch(f'/goals/{identifier}', json={'legs': [{
        'sport': 'cycling', 'terrain': 'trail', 'distance_km': 120, 'elevation_gain_m': 2000}]})
    assert changed.status_code == 200, changed.text
    stale = client.put(endpoint, json={'context_signature': signature, 'items': {
        'equipment': {'done': False, 'notes': 'Bozza obsoleta che non deve sovrascrivere'},
        'hydration': {'done': True, 'notes': 'Nemmeno questa voce deve essere inserita'}}})
    assert stale.status_code == 409 and 'ricarica' in stale.json()['detail']
    with Session(engine) as db:
        assert db.query(GoalPreparationItem).count() == 1
        persisted = db.query(GoalPreparationItem).one()
        assert persisted.done and persisted.notes == 'Note già salvate da conservare'
        assert persisted.context_hash == old_hash and persisted.updated_at == old_updated
    fresh = client.get(endpoint).json()
    assert fresh['context_signature'] != signature
    assert item(fresh, 'equipment')['notes'] == 'Note già salvate da conservare'
    assert item(fresh, 'equipment')['confirmation_stale']
    assert not item(fresh, 'equipment')['self_confirmed']
    updated = client.put(endpoint, json={'context_signature': fresh['context_signature'], 'items': {
        'equipment': {'done': False, 'notes': 'Nuova prova dopo rilettura requisiti'}}})
    assert updated.status_code == 200, updated.text
    assert item(updated.json(), 'equipment')['notes'] == 'Nuova prova dopo rilettura requisiti'


@pytest.mark.parametrize('invalid', ['', 'short', 'a' * 63, 'a' * 65, 'g' * 64, 'A' * 64, 17, True, []])
def test_preparation_context_signature_malformed_does_not_overwrite_notes(api, invalid):
    client, engine = api
    with Session(engine) as db:
        identifier = goal(db)
    endpoint = f'/goals/{identifier}/preparation'
    signature = client.get(endpoint).json()['context_signature']
    saved = client.put(endpoint, json={'context_signature': signature, 'items': {
        'equipment': {'done': True, 'notes': 'Valore persistente'}}})
    assert saved.status_code == 200, saved.text
    response = client.put(endpoint, json={'context_signature': invalid, 'items': {
        'equipment': {'done': False, 'notes': 'Da rifiutare'}, 'hydration': {'done': True}}})
    assert response.status_code == 422, response.text
    with Session(engine) as db:
        assert db.query(GoalPreparationItem).count() == 1
        persisted = db.query(GoalPreparationItem).one()
        assert persisted.done is True and persisted.notes == 'Valore persistente'
