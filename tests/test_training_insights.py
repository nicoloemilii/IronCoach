"""Observed patterns preserve missing values, local dates and tenant boundaries."""
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_training_insights
from app.db import database
from app.db.database import Base, get_db
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.services import training_insights as insights


NOW = datetime(2026, 10, 18, 20)
TODAY = date(2026, 10, 18)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(insights, 'utc_now_naive', lambda: NOW)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(routes_training_insights.router)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def activity(db, key, *, day=TODAY, stamp=None, sport='cycling', minutes=60, load=20, **changes):
    values = dict(source='fit', source_id=key, sport=sport, name='Attività sintetica',
                  start_time=stamp or datetime.combine(day, datetime.min.time()) + timedelta(hours=8),
                  duration_seconds=None if minutes is None else minutes * 60,
                  load=load, distance_meters=20000, elevation_gain_meters=100)
    values.update(changes)
    row = Activity(**values)
    db.add(row)
    db.flush()
    return row


def response(client, as_of=None):
    reply = client.get('/dashboard/training-insights', params={'as_of': str(as_of)} if as_of else {})
    assert reply.status_code == 200, reply.text
    return reply.json()


def sport(body, key):
    canonical = {'bike': 'cycling', 'run': 'running', 'swim': 'swimming'}.get(key, key)
    return next(row for row in body['sports'] if row['key'] == canonical)


def test_empty_windows_have_dates_but_do_not_invent_coverage_or_readiness(api):
    client, _ = api
    body = response(client)
    assert body['status'] == 'empty'
    assert body['timezone'] == 'Europe/Rome'
    assert body['period']['start'] == '2026-09-21'
    assert body['period']['end'] == '2026-10-18'
    assert body['comparison_period']['start'] == '2026-08-24'
    assert body['comparison_period']['end'] == '2026-09-20'
    assert body['volume']['current']['minutes'] == 0
    assert body['volume']['current']['load'] == 0
    assert body['coverage']['current']['load'] == {'known': 0, 'total': 0, 'percent': None, 'complete': True, 'aggregation_error': False}
    assert body['continuity']['active_days'] == 0
    assert body['continuity']['longest_gap_days'] == 28
    assert body['continuity']['current_gap_days'] == 28
    assert body['volume']['minutes_change_pct'] is None
    assert len(body['continuity']['days']) == 28
    assert len(body['weeks']) == 8
    assert body['sports'] == []
    assert 'non sono sedute saltate' in body['continuity']['gap_note'].lower()
    assert 'readiness' not in body and 'target' not in body


def test_aliases_and_observed_discipline_shares_use_equal_periods(api):
    client, engine = api
    with Session(engine) as db:
        for key, name, minutes in [('bike', 'MountainBikeRide', 60), ('run', 'TrailRun', 30),
                                   ('swim', 'OpenWaterSwim', 30), ('strength', 'WeightTraining', 60),
                                   ('other', 'Kayaking', 20)]:
            activity(db, key, day=TODAY - timedelta(days=5), sport=name, minutes=minutes)
        activity(db, 'old-bike', day=TODAY - timedelta(days=30), minutes=20)
        activity(db, 'old-run', day=TODAY - timedelta(days=30), sport='running', minutes=40)
        db.commit()
    body = response(client)
    assert body['volume']['current']['minutes'] == 200
    assert body['volume']['previous']['minutes'] == 60
    assert body['volume']['minutes_change_pct'] == 233.3
    assert sport(body, 'bike')['current_share_pct'] == 30
    assert sport(body, 'bike')['previous_share_pct'] == 33.3
    assert sport(body, 'bike')['share_change_pp'] == -3.3
    assert sport(body, 'strength')['current_minutes'] == 60
    assert sport(body, 'other')['current_minutes'] == 20
    assert {row['key'] for row in body['sports']} == {'cycling', 'running', 'swimming', 'strength', 'other'}
    assert body['continuity']['active_days'] == 1
    assert body['continuity']['previous_active_days'] == 1
    assert body['provenance']['sources'] == [{'source': 'fit', 'current_activities': 5, 'previous_activities': 2}]


def test_rome_midnight_and_multiple_sessions_count_distinct_dates(api):
    client, engine = api
    with Session(engine) as db:
        # Rome midnight is 22:00 UTC in September. First row belongs to Sep 21.
        activity(db, 'boundary', stamp=datetime(2026, 9, 20, 22))
        activity(db, 'same-day', stamp=datetime(2026, 9, 21, 8))
        activity(db, 'last-day', day=TODAY)
        db.commit()
    body = response(client)
    assert body['continuity']['active_days'] == 2
    assert body['continuity']['days'][0]['activities'] == 2
    assert body['continuity']['days'][0]['minutes'] == 120
    assert body['continuity']['longest_gap_days'] == 26
    assert body['continuity']['current_gap_days'] == 0
    assert body['continuity']['active_weeks'] == 2


def test_four_anchored_weeks_never_become_five_iso_weeks(api):
    client, engine = api
    # Ending on Wednesday spans five ISO weeks, but four comparable 7d blocks.
    end = date(2026, 10, 14)
    first = end - timedelta(days=27)
    with Session(engine) as db:
        for index in (0, 6, 7, 14, 21, 27):
            activity(db, str(index), day=first + timedelta(days=index))
        db.commit()
    body = response(client, end)
    assert body['continuity']['active_weeks'] == 4
    assert body['continuity']['total_weeks'] == 4
    assert [week['period'] for week in body['weeks']] == ['previous'] * 4 + ['current'] * 4
    assert sum(week['minutes'] for week in body['weeks'][4:]) == body['volume']['current']['minutes']
    assert all((date.fromisoformat(week['end']) - date.fromisoformat(week['start'])).days == 6 for week in body['weeks'])


def test_today_and_reference_stop_at_same_local_time_across_dst(api, monkeypatch):
    client, engine = api
    # Today 13:00 CET, previous final day 13:00 CEST -> different UTC hours.
    now = datetime(2026, 10, 25, 12)
    monkeypatch.setattr(insights, 'utc_now_naive', lambda: now)
    with Session(engine) as db:
        activity(db, 'prior-before', stamp=datetime(2026, 9, 27, 10, 59), minutes=10)
        activity(db, 'prior-cutoff', stamp=datetime(2026, 9, 27, 11), minutes=20)
        activity(db, 'prior-later', stamp=datetime(2026, 9, 27, 11, 1), minutes=999)
        activity(db, 'now', stamp=now, minutes=30)
        activity(db, 'later-today', stamp=now + timedelta(seconds=1), minutes=999)
        activity(db, 'tomorrow', stamp=datetime(2026, 10, 26, 8), minutes=999)
        db.commit()
    body = response(client)
    assert body['as_of'] == '2026-10-25'
    assert body['period']['cutoff_utc'] == '2026-10-25T12:00:00Z'
    assert body['comparison_period']['cutoff_utc'] == '2026-09-27T11:00:00Z'
    assert body['volume']['previous']['minutes'] == 30
    assert body['volume']['current']['minutes'] == 30
    assert body['volume']['minutes_change_pct'] == 0
    assert [i for i, week in enumerate(body['weeks']) if week['partial_day']] == [3, 7]
    assert sum(week['minutes'] for week in body['weeks'][:4]) == 30
    assert sum(week['minutes'] for week in body['weeks'][4:]) == 30


def test_historical_period_uses_complete_local_days_and_ignores_later_data(api):
    client, engine = api
    with Session(engine) as db:
        activity(db, 'end-of-local-day', stamp=datetime(2026, 10, 10, 21, 59), minutes=10)
        activity(db, 'next-local-day', stamp=datetime(2026, 10, 10, 22), minutes=500)
        activity(db, 'too-early', stamp=datetime(2026, 8, 15, 21, 59), minutes=500)
        activity(db, 'first-local-day', stamp=datetime(2026, 8, 15, 22), minutes=20)
        db.commit()
    body = response(client, date(2026, 10, 10))
    assert body['volume']['current']['minutes'] == 10
    assert body['volume']['previous']['minutes'] == 20
    assert body['period']['includes_partial_day'] is False
    assert body['comparison_period']['includes_partial_day'] is False
    assert not any(week['partial_day'] for week in body['weeks'])


def test_missing_fields_are_partial_and_explicit_zero_is_known(api):
    client, engine = api
    with Session(engine) as db:
        activity(db, 'unknown', minutes=None, load=None, distance_meters=None, elevation_gain_meters=None, normalized_power=250)
        activity(db, 'known-zero', minutes=0, load=0, distance_meters=0, elevation_gain_meters=0)
        activity(db, 'reference', day=TODAY - timedelta(days=30), minutes=60, load=100)
        db.commit()
    body = response(client)
    current = body['volume']['current']
    assert current['activities'] == 2
    assert current['minutes'] == current['load'] == current['distance_km'] == current['elevation_m'] == 0
    assert all(item['known'] == 1 and item['percent'] == 50 and not item['complete'] for item in current['coverage'].values())
    assert body['volume']['minutes_change_pct'] is None
    assert body['volume']['comparable'] is False
    assert 'durata manca' in body['volume']['summary'].lower()
    assert body['continuity']['active_days'] == 1


def test_entirely_unknown_load_never_uses_power_or_default_ftp(api):
    client, engine = api
    with Session(engine) as db:
        activity(db, 'np-without-load', load=None, normalized_power=250)
        db.commit()
    body = response(client)
    assert body['volume']['current']['load'] is None
    assert body['coverage']['current']['load']['known'] == 0
    assert body['weeks'][-1]['load'] is None
    assert body['provenance']['recorded_load_only'] is True
    assert body['provenance']['external_calls'] is False


def test_canonical_intervals_observation_null_overrides_stale_load(api):
    client, engine = api
    with Session(engine) as db:
        cleared = activity(db, 'cleared', load=90, source='intervals')
        zero = activity(db, 'zero', load=40, source='intervals')
        known = activity(db, 'known', load=10, source='intervals')
        db.add_all([
            ActivityObservation(activity_id=cleared.id, source_key='intervals:cleared', payload_json='{"load":null}'),
            ActivityObservation(activity_id=zero.id, source_key='intervals:zero', payload_json='{"load":0}'),
            ActivityObservation(activity_id=known.id, source_key='intervals:known', payload_json='{"load":35}'),
        ])
        db.commit()
    body = response(client)
    assert body['volume']['current']['load'] == 35
    assert body['coverage']['current']['load']['known'] == 2
    assert body['coverage']['current']['load']['total'] == 3
    assert body['coverage']['current']['load']['percent'] == 66.7


@pytest.mark.parametrize('raw', ['not-json', '[1,2]', '{"load":true}', '{"load":-10}', '{"load":"NaN"}'])
def test_invalid_authoritative_load_remains_unknown(api, raw):
    client, engine = api
    with Session(engine) as db:
        row = activity(db, 'invalid', load=90)
        db.add(ActivityObservation(activity_id=row.id, source_key='intervals:invalid', payload_json=raw))
        db.commit()
    body = response(client)
    assert body['volume']['current']['load'] is None


def test_missing_durations_disable_comparison_but_keep_observed_composition(api):
    client, engine = api
    with Session(engine) as db:
        activity(db, 'known-bike', minutes=30)
        activity(db, 'unknown-run', sport='running', minutes=None)
        activity(db, 'old-run', day=TODAY - timedelta(days=30), sport='running', minutes=30)
        db.commit()
    body = response(client)
    assert body['volume']['current']['minutes'] == 30
    assert body['volume']['minutes_change'] is None
    assert sport(body, 'bike')['current_share_pct'] == 100
    assert sport(body, 'run')['current_share_pct'] is None
    assert all(row['share_change_pp'] is None for row in body['sports'])


def test_small_durations_do_not_create_shares_over_100_through_rounding(api):
    client, engine = api
    with Session(engine) as db:
        activity(db, 'second-bike', minutes=1 / 60)
        activity(db, 'second-run', sport='running', minutes=1 / 60)
        db.commit()
    body = response(client)
    assert sport(body, 'bike')['current_share_pct'] == 50
    assert sport(body, 'run')['current_share_pct'] == 50
    assert sum(row['current_share_pct'] or 0 for row in body['sports']) == 100


def test_known_empty_current_period_can_show_recorded_drop_without_infinite_growth(api):
    client, engine = api
    with Session(engine) as db:
        activity(db, 'old', day=TODAY - timedelta(days=30), minutes=60)
        db.commit()
    body = response(client)
    assert body['volume']['minutes_change_pct'] == -100
    assert body['continuity']['active_days'] == 0
    assert body['continuity']['previous_active_days'] == 1
    with Session(engine) as db:
        db.query(Activity).delete()
        activity(db, 'new', minutes=60)
        db.commit()
    body = response(client)
    assert body['volume']['minutes_change_pct'] is None
    assert 'non è definita' in body['volume']['summary']


def test_negative_nonfinite_values_are_not_exposed_as_observed_values(api):
    client, engine = api
    with Session(engine) as db:
        activity(db, 'invalid-numbers', minutes=-10, load=float('inf'), distance_meters=-1, elevation_gain_meters=float('inf'))
        db.commit()
    body = response(client)
    for key in ('minutes', 'load', 'distance_km', 'elevation_m'):
        assert body['volume']['current'][key] is None
    json.dumps(body, allow_nan=False)


def test_large_finite_fields_cannot_overflow_the_response(api):
    client, engine = api
    with Session(engine) as db:
        activity(db, 'huge-a', minutes=1, load=1e308, elevation_gain_meters=1e308)
        activity(db, 'huge-b', minutes=1, load=1e308, elevation_gain_meters=1e308)
        db.commit()
    body = response(client)
    assert body['volume']['current']['load'] is None
    assert body['coverage']['current']['load']['aggregation_error'] is True
    assert body['coverage']['current']['load']['complete'] is False
    json.dumps(body, allow_nan=False)


@pytest.mark.parametrize('value', ['not-a-date', '2026-02-30', '1969-12-31', '2026-10-19', '9999-12-31'])
def test_query_rejects_invalid_future_or_unbounded_dates(api, value):
    client, _ = api
    assert client.get('/dashboard/training-insights', params={'as_of': value}).status_code == 422


def test_service_normalizes_aware_now_and_supports_earliest_query(api):
    _, engine = api
    with Session(engine) as db:
        body = insights.training_insights(db, now=datetime(2026, 10, 19, 0, 30, tzinfo=timezone(timedelta(hours=2))))
        assert body['as_of'] == '2026-10-19'
        early = insights.training_insights(db, date(1970, 1, 1), now=NOW)
        assert early['period']['end'] == '1970-01-01'


def test_endpoint_is_read_only_and_database_queries_are_bounded(api):
    client, engine = api
    with Session(engine) as db:
        activity(db, 'read-only')
        db.commit()
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        response(client)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert len(statements) == 2
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
    assert all('activities.start_time >=' in statement and 'activities.start_time <' in statement for statement in statements)


def test_hosted_route_uses_authenticated_workspace_without_global_activity_leak(tmp_path, monkeypatch):
    from app.commercial.middleware import ProductBoundary
    from app.commercial.accounts import COOKIE

    monkeypatch.setattr(database, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(insights, 'utc_now_naive', lambda: NOW)
    identifiers = [uuid4().hex, uuid4().hex]
    class Accounts:
        def authenticate(self, cookie):
            if cookie not in ('a', 'b'):
                return None
            return {'id': identifiers[0 if cookie == 'a' else 1], 'csrf': 'test-csrf'}

        def limit(self, *args):
            return None

    app = FastAPI()
    app.include_router(routes_training_insights.router)
    app.add_middleware(ProductBoundary, settings=SimpleNamespace(hosted=True, public_url='https://product.test', vault_key='test'), accounts=Accounts())
    try:
        for identifier, minutes in zip(identifiers, (25, 90)):
            with database.workspace_scope(identifier), database.current_session() as db:
                activity(db, f'only-{minutes}', minutes=minutes)
                db.commit()
        with TestClient(app, base_url='https://product.test') as client:
            denied = client.get('/dashboard/training-insights')
            assert denied.status_code == 401
            assert denied.headers['cache-control'] == 'no-store'
            for cookie, minutes in [('a', 25), ('b', 90)]:
                client.cookies.set(COOKIE, cookie)
                body = response(client)
                assert body['volume']['current']['minutes'] == minutes
                assert body['volume']['current']['activities'] == 1
        assert database.WORKSPACE.get() is None
    finally:
        for path in list(database._WORKSPACE_ENGINES):
            if str(tmp_path) in path:
                database._WORKSPACE_ENGINES.pop(path).dispose()
