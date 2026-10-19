"""Batch reads preserve provenance and never cross session/request boundaries."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_coach
from app.db import health_reports, sync_models, training_models
from app.db.database import Base
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.services import planning
from app.services.athlete_clock import utc_now_naive
from app.services.athlete_profile import DEFAULT_PROFILE
from app.services.training_load import activity_load, activity_load_snapshot


@contextmanager
def database():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


def activity(db, identifier=1, *, observed=25, source='intervals', load=90):
    row = Activity(id=identifier, source=source, source_id=f'synthetic-{identifier}', sport='running',
                   start_time=utc_now_naive()-timedelta(hours=identifier), duration_seconds=3600, load=load)
    db.add(row)
    if observed != 'absent':
        db.add(ActivityObservation(activity_id=identifier, source_key=f'intervals:synthetic-{identifier}',
                                   payload_json=json.dumps({'load': observed})))
    db.commit()
    return row


def test_batch_snapshot_preserves_absence_null_zero_and_observed_value():
    with database() as db:
        rows = [activity(db, 1, observed='absent'), activity(db, 2, observed=None),
                activity(db, 3, observed=0), activity(db, 4, observed=55)]
        expected = [activity_load(row, 170) for row in rows]
        assert expected == [90, 0, 0, 55]
        queries = []

        def capture(connection, cursor, statement, *args):
            queries.append(statement)

        event.listen(db.bind, 'before_cursor_execute', capture)
        try:
            with activity_load_snapshot(db):
                for _ in range(5):
                    assert [activity_load(row, 170) for row in rows] == expected
        finally:
            event.remove(db.bind, 'before_cursor_execute', capture)
        assert len(queries) == 1


def test_nested_session_scope_and_failure_restore_previous_snapshot():
    with database() as first, database() as second:
        one = activity(first, observed=15)
        two = activity(second, observed=85)
        with pytest.raises(RuntimeError):
            with activity_load_snapshot(first):
                assert activity_load(one, 170) == 15
                with pytest.raises(ValueError):
                    with activity_load_snapshot(second):
                        assert activity_load(two, 170) == 85
                        assert activity_load(one, 170) == 15
                        raise ValueError('Inner read failure')
                assert activity_load(one, 170) == 15
                assert activity_load(two, 170) == 85
                raise RuntimeError('Outer read failure')
        observation = first.query(ActivityObservation).one()
        observation.payload_json = '{"load":0}'
        first.commit()
        assert activity_load(one, 170) == 0
        with activity_load_snapshot(first):
            assert activity_load(one, 170) == 0


def test_concurrent_snapshots_with_identical_activity_ids_stay_isolated():
    barrier = Barrier(2)

    def read(value):
        with database() as db:
            row = activity(db, observed=value)
            with activity_load_snapshot(db):
                barrier.wait(timeout=10)
                return [activity_load(row, 170) for _ in range(30)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        one, two = executor.map(read, [15, 85])
    assert one == [15] * 30 and two == [85] * 30


def test_coach_batches_observation_queries_for_a_large_history(monkeypatch):
    monkeypatch.setattr(routes_coach, 'load_profile', lambda: deepcopy(DEFAULT_PROFILE))
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    monkeypatch.setattr('app.integrations.strava.read_tokens', lambda: {})
    with database() as db:
        for identifier in range(1, 181):
            activity(db, identifier, observed=None if identifier % 10 == 0 else 65)
        queries = []

        def capture(connection, cursor, statement, *args):
            if 'activity_observations' in statement:
                queries.append(statement)

        event.listen(db.bind, 'before_cursor_execute', capture)
        try:
            result = routes_coach.get_coach_today(db=db)
        finally:
            event.remove(db.bind, 'before_cursor_execute', capture)
        assert result['historical']['all_time']['activities'] == 180
        assert len(queries) == 1


@pytest.mark.parametrize('payload', ['broken json', '[]', 'null', '{"load":NaN}',
                                     '{"load":Infinity}', '{"load":true}', '{"load":-5}'])
def test_invalid_remote_evidence_stays_unknown_in_batched_and_fallback_reads(payload):
    with database() as db:
        row = activity(db, observed=25)
        db.query(ActivityObservation).one().payload_json = payload
        db.commit()
        assert activity_load(row, 170) == 0
        with activity_load_snapshot(db) as observations:
            assert row.id in observations and observations[row.id] is None
            assert activity_load(row, 170) == 0


@pytest.mark.parametrize('payload', ['broken json', '[]', '{"load":Infinity}'])
def test_unrelated_future_invalid_observation_cannot_break_coach(monkeypatch, payload):
    monkeypatch.setattr(routes_coach, 'load_profile', lambda: deepcopy(DEFAULT_PROFILE))
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    monkeypatch.setattr('app.integrations.strava.read_tokens', lambda: {})
    with database() as db:
        activity(db, 1, observed=35)
        future = activity(db, 2, observed=25)
        future.start_time = utc_now_naive() + timedelta(days=10)
        db.query(ActivityObservation).filter_by(activity_id=future.id).one().payload_json = payload
        db.commit()
        result = routes_coach.get_coach_today(db=db)
        assert result['historical']['all_time']['activities'] == 1
