"""Retrospective duplicate leads require review and never alter observed data."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_iron, routes_training_insights
from app.db import database, repository
from app.db.database import Base, get_db
from app.db.experience_models import ActivityIdentity, SessionFeedback
from app.db.iron_models import ActivityObservation, DuplicateCandidate
from app.db.models import Activity, WorkoutEvaluation
from app.services import training_insights


NOW = datetime(2026, 10, 18, 20)
TODAY = date(2026, 10, 18)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(repository, 'utc_now_naive', lambda: NOW)
    monkeypatch.setattr(training_insights, 'utc_now_naive', lambda: NOW)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(routes_iron.router)
    app.include_router(routes_training_insights.router)
    def isolated_session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = isolated_session
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        yield client, engine, app
    engine.dispose()


def activity(db, identifier, **changes):
    values = dict(source='intervals', source_id=identifier, sport='cycling', name='Sintetica',
                  start_time=datetime(2026, 10, 17, 8), duration_seconds=3600, distance_meters=20000,
                  elevation_gain_meters=100, avg_hr=130, max_hr=160, avg_power=100,
                  normalized_power=120, avg_speed=5, avg_cadence=80, calories=400, load=35)
    values.update(changes)
    row = Activity(**values)
    db.add(row)
    db.flush()
    return row


def scan(client, **params):
    response = client.post('/activities/duplicates/scan', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def summary(client, **params):
    response = client.get('/activities/duplicates/summary', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_read_only_summary_finds_legacy_same_source_rows_without_creating_candidates(api):
    client, engine, _ = api
    with Session(engine) as db:
        activity(db, 'legacy-a')
        activity(db, 'legacy-b', name='Nome diverso', sport='bike')
        db.commit()
    result = summary(client)
    assert result['scanned_activities'] == result['eligible_activities'] == 2
    assert result['possible_pairs'] == result['unreviewed_pairs'] == result['unresolved_pairs'] == 1
    assert result['pending_candidates'] == 0
    assert result['truncated'] is False
    with Session(engine) as db:
        assert db.query(DuplicateCandidate).count() == 0
        assert db.query(Activity).count() == 2


def test_scan_is_idempotent_and_only_persists_review_candidates(api):
    client, engine, _ = api
    with Session(engine) as db:
        first = activity(db, 'a')
        second = activity(db, 'b')
        db.add(ActivityIdentity(source_key='intervals:a', activity_id=first.id))
        db.add(ActivityObservation(source_key='intervals:a', activity_id=first.id, payload_json='{"load":35}'))
        db.add(SessionFeedback(workout_id=10, activity_id=first.id, payload_json='{"note":"conserva"}'))
        db.add(WorkoutEvaluation(workout_id=10, activity_id=second.id, payload_json='{"matched":true}'))
        db.commit()
        before = [(row.id, row.source_id, row.duration_seconds, row.load) for row in db.query(Activity).order_by(Activity.id)]
    result = scan(client)
    assert result['new_candidates'] == 1
    assert result['pending_candidates'] == 1
    assert result['unreviewed_pairs'] == 0
    assert result['more_candidates_available'] is False
    assert scan(client)['new_candidates'] == 0
    assert len(client.get('/activities/duplicates/pending').json()) == 1
    with Session(engine) as db:
        assert [(row.id, row.source_id, row.duration_seconds, row.load) for row in db.query(Activity).order_by(Activity.id)] == before
        assert db.query(ActivityIdentity).count() == db.query(ActivityObservation).count() == 1
        assert db.query(SessionFeedback).one().payload_json == '{"note":"conserva"}'
        assert db.query(WorkoutEvaluation).one().payload_json == '{"matched":true}'
        candidate = db.query(DuplicateCandidate).one()
        assert candidate.activity_id > candidate.other_id


@pytest.mark.parametrize('status', ['kept', 'merged', 'pending'])
def test_existing_decisions_are_respected_in_both_pair_orientations(api, status):
    client, engine, _ = api
    with Session(engine) as db:
        first = activity(db, 'a')
        second = activity(db, 'b')
        # Opposite to the deterministic orientation created by a new scan.
        db.add(DuplicateCandidate(activity_id=first.id, other_id=second.id, status=status))
        db.commit()
    result = scan(client)
    assert result['new_candidates'] == 0
    assert result['unreviewed_pairs'] == 0
    assert result['unresolved_pairs'] == (1 if status == 'pending' else 0)
    assert result['kept_pairs'] == (1 if status == 'kept' else 0)
    with Session(engine) as db:
        assert db.query(DuplicateCandidate).count() == 1
        assert db.query(Activity).count() == 2


def test_explicit_keep_is_not_recreated_and_releases_comparison(api):
    client, engine, _ = api
    with Session(engine) as db:
        activity(db, 'current')
        activity(db, 'current-copy')
        activity(db, 'previous', start_time=datetime(2026, 9, 10, 8))
        db.commit()
    body = client.get('/dashboard/training-insights').json()
    assert body['quality']['current_possible_duplicates'] == 1
    assert body['volume']['current']['minutes'] == 120
    assert body['volume']['minutes_change_pct'] is None
    assert body['volume']['comparable'] is False
    assert body['sports'][0]['share_change_pp'] is None
    assert 'duplicati' in body['volume']['summary'].lower()
    scan(client)
    candidate = client.get('/activities/duplicates/pending').json()[0]['id']
    assert client.post(f'/activities/duplicates/{candidate}/keep').status_code == 200
    assert scan(client)['new_candidates'] == 0
    body = client.get('/dashboard/training-insights').json()
    assert body['quality']['current_possible_duplicates'] == 0
    assert body['volume']['minutes_change_pct'] == 100
    assert body['volume']['current']['activities'] == 2


def test_only_reference_duplicates_suppress_change_without_changing_raw_totals(api):
    client, engine, _ = api
    with Session(engine) as db:
        activity(db, 'current')
        for key in ('old-a', 'old-b'):
            activity(db, key, start_time=datetime(2026, 9, 10, 8))
        db.commit()
    body = client.get('/dashboard/training-insights').json()
    assert body['quality']['current_possible_duplicates'] == 0
    assert body['quality']['previous_possible_duplicates'] == 1
    assert body['volume']['current']['minutes'] == 60
    assert body['volume']['previous']['minutes'] == 120
    assert body['volume']['minutes_change_pct'] is None


@pytest.mark.parametrize('changes', [
    {'start_time': datetime(2026, 10, 17, 8, 0, 1)}, {'sport': 'running'},
    {'duration_seconds': 3599}, {'distance_meters': None}, {'load': None},
    {'elevation_gain_meters': 0}, {'avg_hr': 131}, {'max_hr': 161},
    {'avg_power': 101}, {'normalized_power': 121}, {'avg_speed': 5.1},
    {'avg_cadence': 81}, {'calories': 401},
])
def test_near_matches_or_different_known_fields_are_not_exact_duplicates(api, changes):
    client, engine, _ = api
    with Session(engine) as db:
        activity(db, 'a')
        activity(db, 'b', **changes)
        db.commit()
    assert summary(client)['possible_pairs'] == 0
    assert scan(client)['new_candidates'] == 0


@pytest.mark.parametrize('changes', [
    {'duration_seconds': None, 'distance_meters': None},
    {'duration_seconds': 0, 'distance_meters': 0},
    {'load': float('inf')}, {'duration_seconds': -10},
])
def test_unknown_empty_or_invalid_records_do_not_create_false_groups(api, changes):
    client, engine, _ = api
    with Session(engine) as db:
        activity(db, 'a', **changes)
        activity(db, 'b', **changes)
        db.commit()
    assert summary(client)['eligible_activities'] == 0
    assert scan(client)['new_candidates'] == 0


def test_known_distance_can_support_missing_duration_and_all_sources_can_match(api):
    client, engine, _ = api
    with Session(engine) as db:
        activity(db, 'a', duration_seconds=None)
        activity(db, 'b', source='fit', duration_seconds=None)
        db.commit()
    assert scan(client)['new_candidates'] == 1


def test_future_records_are_excluded_from_summary_and_scan(api):
    client, engine, _ = api
    with Session(engine) as db:
        for key in ('later-a', 'later-b'):
            activity(db, key, start_time=NOW + timedelta(seconds=1))
        db.commit()
    assert summary(client)['scanned_activities'] == 0
    assert scan(client)['new_candidates'] == 0


def test_scan_bound_is_explicit_and_query_validation_rejects_excess(api):
    client, engine, _ = api
    with Session(engine) as db:
        for key in ('a', 'b', 'c'):
            activity(db, key)
        db.commit()
    result = scan(client, limit=2)
    assert result['scanned_activities'] == 2
    assert result['truncated'] is True
    assert result['new_candidates'] == 1
    assert client.get('/activities/duplicates/summary?limit=0').status_code == 422
    assert client.post('/activities/duplicates/scan?limit=50001').status_code == 422


def test_large_groups_are_created_in_bounded_repeatable_batches(api, monkeypatch):
    client, engine, _ = api
    monkeypatch.setattr(repository, 'MAX_NEW_DUPLICATE_CANDIDATES', 2)
    with Session(engine) as db:
        for key in ('a', 'b', 'c'):
            activity(db, key)
        db.commit()
    first = scan(client)
    assert first['new_candidates'] == 2
    assert first['unreviewed_pairs'] == 1
    assert first['more_candidates_available'] is True
    second = scan(client)
    assert second['new_candidates'] == 1
    assert second['more_candidates_available'] is False
    assert scan(client)['new_candidates'] == 0
    assert summary(client)['possible_pairs'] == 3


def test_get_summary_never_issues_mutating_sql(api):
    client, engine, _ = api
    with Session(engine) as db:
        activity(db, 'a')
        activity(db, 'b')
        db.commit()
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        summary(client)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)


def test_scan_rejects_nonlocal_and_cross_origin_without_writes(api):
    client, engine, app = api
    with Session(engine) as db:
        activity(db, 'a')
        activity(db, 'b')
        db.commit()
    assert client.post('/activities/duplicates/scan', headers={'Origin': 'https://other.test'}).status_code == 403
    with TestClient(app, client=('203.0.113.9', 12345)) as external:
        assert external.post('/activities/duplicates/scan').status_code == 403
    with Session(engine) as db:
        assert db.query(DuplicateCandidate).count() == 0


def test_excess_review_decisions_stop_writes_and_suppress_uncertain_comparison(api, monkeypatch):
    client, engine, _ = api
    monkeypatch.setattr(repository, 'MAX_DUPLICATE_DECISIONS', 1)
    with Session(engine) as db:
        rows = [activity(db, key) for key in ('a', 'b', 'c')]
        db.add_all([DuplicateCandidate(activity_id=rows[1].id, other_id=rows[0].id, status='kept'),
                    DuplicateCandidate(activity_id=rows[2].id, other_id=rows[0].id, status='kept')])
        db.commit()
    assert summary(client)['truncated'] is True
    assert client.post('/activities/duplicates/scan').status_code == 422
    body = client.get('/dashboard/training-insights').json()
    assert body['quality']['review_truncated'] is True
    assert body['volume']['comparable'] is False
    with Session(engine) as db:
        assert db.query(DuplicateCandidate).count() == 2


def test_failed_batch_is_rolled_back_without_partial_candidates(api):
    _, engine, _ = api
    with Session(engine) as db:
        for key in ('a', 'b', 'c'):
            activity(db, key)
        db.commit()
    inserted = 0
    def fail_second_insert(connection, cursor, statement, parameters, context, executemany):
        nonlocal inserted
        if statement.lstrip().upper().startswith('INSERT INTO DUPLICATE_CANDIDATES'):
            inserted += 1
            if inserted == 2:
                raise RuntimeError('Synthetic batch interruption')
    event.listen(engine, 'before_cursor_execute', fail_second_insert)
    try:
        with Session(engine) as db:
            with pytest.raises(RuntimeError, match='Synthetic batch interruption'):
                repository.scan_exact_duplicates(db)
            db.rollback()
    finally:
        event.remove(engine, 'before_cursor_execute', fail_second_insert)
    assert inserted == 2
    with Session(engine) as db:
        assert db.query(DuplicateCandidate).count() == 0
        assert db.query(Activity).count() == 3


def test_hosted_scan_requires_session_and_csrf_and_only_writes_own_workspace(tmp_path, monkeypatch):
    from app.commercial.accounts import COOKIE
    from app.commercial.middleware import ProductBoundary

    monkeypatch.setattr(database, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(repository, 'utc_now_naive', lambda: NOW)
    identifiers = [uuid4().hex, uuid4().hex]
    class Accounts:
        def authenticate(self, cookie):
            if cookie not in ('a', 'b'):
                return None
            return {'id': identifiers[0 if cookie == 'a' else 1], 'csrf': 'expected-test-csrf'}

        def limit(self, *args):
            return None

    app = FastAPI()
    app.include_router(routes_iron.router)
    app.add_middleware(ProductBoundary, settings=SimpleNamespace(hosted=True, public_url='https://product.test', vault_key='test'), accounts=Accounts())
    try:
        for identifier in identifiers:
            with database.workspace_scope(identifier), database.current_session() as db:
                activity(db, 'a')
                activity(db, 'b')
                db.commit()
        with TestClient(app, base_url='https://product.test') as client:
            assert client.get('/activities/duplicates/summary').status_code == 401
            assert client.post('/activities/duplicates/scan').status_code == 401
            client.cookies.set(COOKIE, 'a')
            assert client.post('/activities/duplicates/scan').status_code == 403
            assert summary(client)['pending_candidates'] == 0
            created = client.post('/activities/duplicates/scan', headers={'X-CSRF-Token': 'expected-test-csrf'})
            assert created.status_code == 200
            assert created.json()['new_candidates'] == 1
            client.cookies.set(COOKIE, 'b')
            assert summary(client)['pending_candidates'] == 0
            assert summary(client)['unreviewed_pairs'] == 1
        assert database.WORKSPACE.get() is None
    finally:
        for path in list(database._WORKSPACE_ENGINES):
            if str(tmp_path) in path:
                database._WORKSPACE_ENGINES.pop(path).dispose()
