"""Profile aggregates keep observed zero, missing evidence and records distinct."""
from datetime import date, datetime, timedelta
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_experience, routes_health_reports
from app.db.database import Base, get_db
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.services import athlete_snapshot, health_reports, planning

TODAY = date(2026, 10, 22)
NOW = datetime(2026, 10, 22, 12)


@pytest.fixture
def api(monkeypatch):
    for module in (routes_health_reports, athlete_snapshot, health_reports):
        monkeypatch.setattr(module, 'athlete_today', lambda: TODAY)
    for module in (athlete_snapshot, health_reports):
        monkeypatch.setattr(module, 'utc_now_naive', lambda: NOW)
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    application = FastAPI()
    application.include_router(routes_health_reports.router)
    application.include_router(routes_experience.router)
    def database():
        with Session(engine) as db:
            yield db
    application.dependency_overrides[get_db] = database
    with TestClient(application) as client:
        yield client, engine
    engine.dispose()


def add(db, identifier, ago=1, **changes):
    fields = dict(source='fit', source_id=identifier, sport='cycling', name=identifier,
                  start_time=NOW - timedelta(days=ago), duration_seconds=3600, distance_meters=20000,
                  elevation_gain_meters=100, calories=500, avg_speed=10, avg_power=150,
                  normalized_power=160, load=30)
    fields.update(changes)
    row = Activity(**fields); db.add(row); db.flush()
    return row


def profile(client, day=None):
    response = client.get('/athlete/derived-profile' + (f'?day={day}' if day else ''))
    assert response.status_code == 200, response.text
    body = response.json(); json.dumps(body, allow_nan=False)
    return body


def sport(body, key='bike'):
    return next(item for item in body['sports'] if item['sport'] == key)


def test_empty_profile_reports_registered_empty_totals_without_a_baseline(api):
    client, _ = api
    body = profile(client)
    assert body['weekly_minutes'] == body['weekly_load'] == 0
    for totals in body['totals'].values():
        assert totals['activities'] == 0
        assert all(totals[key] == 0 for key in ('hours', 'distance_km', 'elevation_m', 'calories', 'load'))
        assert totals['coverage_summary']['hours']['status'] == 'empty'
        assert totals['coverage_summary']['hours']['complete'] is True
    assert body['duration_coverage']['known'] == body['duration_coverage']['expected'] == 0
    assert body['volume_comparison']['status'] == 'no_baseline'
    assert body['volume_comparison']['change_pct'] is None


def test_observed_zero_remains_available_in_totals_and_elevation_record(api):
    client, engine = api
    with Session(engine) as db:
        add(db, 'zero', duration_seconds=0, distance_meters=0, elevation_gain_meters=0,
            calories=0, load=0, avg_speed=0, avg_power=0, normalized_power=0)
        db.commit()
    body = profile(client); bike = sport(body)
    assert body['weekly_minutes'] == body['weekly_load'] == 0
    assert bike['minutes'] == bike['distance_km'] == 0
    assert body['duration_coverage']['known'] == body['duration_coverage']['expected'] == 1
    assert body['duration_coverage']['complete'] is True
    assert bike['records']['days_28']['most_elevation_m']['value'] == 0
    assert bike['records']['days_28']['highest_avg_speed_kmh'] is None


def test_unknown_recent_durations_do_not_invent_a_volume_decline(api):
    client, engine = api
    with Session(engine) as db:
        for ago in (1, 2, 3, 4):
            add(db, f'recent-{ago}', ago, duration_seconds=None)
        for ago in (30, 32, 34, 36):
            add(db, f'previous-{ago}', ago)
        db.commit()
    body = profile(client)
    assert body['weekly_minutes'] is None and body['previous_weekly_minutes'] == 60
    assert body['duration_coverage']['known_minutes'] is None
    assert body['duration_coverage']['known'] == 0 and body['duration_coverage']['expected'] == 4
    assert body['totals']['days_28']['hours'] is None
    assert body['volume_comparison']['status'] == 'insufficient_data'
    assert body['volume_comparison']['reasons'] == ['current_duration_incomplete']
    assert body['volume_comparison']['change_pct'] is None
    assert body['volume_comparison']['current']['start'] == '2026-09-25'
    assert body['volume_comparison']['previous']['end'] == '2026-09-24'
    assert not any('30%' in text for text in body['priorities'])


def test_partial_fields_have_named_subtotals_and_no_complete_totals(api):
    client, engine = api
    with Session(engine) as db:
        add(db, 'known')
        add(db, 'missing', duration_seconds=None, distance_meters=None, calories=None, load=None)
        db.commit()
    body = profile(client); totals = body['totals']['days_28']; bike = sport(body)
    assert totals['hours'] is None and totals['distance_km'] is None and totals['load'] is None
    assert totals['coverage']['duration_seconds'] == 1 and totals['coverage']['distance_meters'] == 1
    assert totals['coverage_summary']['hours']['known_value'] == 1
    assert totals['coverage_summary']['distance_km']['known_value'] == 20
    assert totals['coverage_summary']['load']['known_value'] == 30
    assert totals['coverage_summary']['hours']['status'] == 'partial'
    assert body['weekly_minutes'] is None and body['weekly_load'] is None
    assert bike['minutes'] is None and bike['weekly_minutes'] is None
    assert bike['duration_coverage']['known_minutes'] == 60


@pytest.mark.parametrize('window', ['current', 'previous'])
def test_either_incomplete_window_blocks_volume_comparison(api, window):
    client, engine = api
    with Session(engine) as db:
        add(db, 'recent', 1, duration_seconds=1800)
        add(db, 'previous', 30)
        add(db, 'missing', 2 if window == 'current' else 31, duration_seconds=None)
        db.commit()
    body = profile(client)
    assert body['volume_comparison']['status'] == 'insufficient_data'
    assert f'{window}_duration_incomplete' in body['volume_comparison']['reasons']
    assert body['volume_comparison']['change_pct'] is None
    assert body['volume_comparison']['delta_minutes'] is None
    assert not any('30%' in text for text in body['priorities'])


def test_complete_volume_comparison_describes_exact_registered_windows(api):
    client, engine = api
    with Session(engine) as db:
        add(db, 'recent', 1, duration_seconds=1800)
        add(db, 'previous', 30)
        db.commit()
    body = profile(client); comparison = body['volume_comparison']
    assert comparison['scope'] == 'registered_activity_duration'
    assert comparison['status'] == 'available' and comparison['change_pct'] == -50
    assert comparison['current']['total_minutes'] == 30
    assert comparison['previous']['total_minutes'] == 60
    assert comparison['known_activity_counts'] == comparison['expected_activity_counts'] == {'current': 1, 'previous': 1}
    assert comparison['delta_minutes'] == -30


def test_invalid_durations_and_scaled_record_overflow_do_not_break_http(api):
    client, engine = api
    with Session(engine) as db:
        add(db, 'infinite', duration_seconds=float('inf'), avg_speed=1e308)
        add(db, 'negative', duration_seconds=-1, distance_meters=-1)
        add(db, 'missing', duration_seconds=None, distance_meters=None)
        db.commit()
    body = profile(client)
    assert body['weekly_minutes'] is None
    assert body['duration_coverage']['invalid'] == 2
    assert body['duration_coverage']['missing'] == 1
    assert body['duration_coverage']['known'] == 0
    assert sport(body)['records']['days_28']['highest_avg_speed_kmh']['value'] == 36
    assert sport(body)['records']['days_28']['highest_avg_speed_kmh']['candidate_coverage']['invalid'] == 1


def test_finite_aggregate_overflow_is_unavailable_without_nonfinite_json(api):
    client, engine = api
    with Session(engine) as db:
        for identifier in ('one', 'two'):
            add(db, identifier, calories=1e308, load=1e308)
        db.commit()
    body = profile(client); totals = body['totals']['all_time']
    assert totals['calories'] is None and totals['load'] is None
    assert totals['coverage_summary']['calories']['reason'] == 'aggregate_overflow'
    assert totals['coverage_summary']['load']['reason'] == 'aggregate_overflow'
    assert totals['coverage_summary']['calories']['known'] == 2
    assert body['weekly_load'] is None


def test_duration_overflow_is_not_marked_complete_only_because_counts_match(api):
    client, engine = api
    with Session(engine) as db:
        for index in range(120):
            add(db, f'large-{index}', duration_seconds=1e308)
        add(db, 'baseline', ago=30)
        db.commit()
    body = profile(client); comparison = body['volume_comparison']
    assert body['weekly_minutes'] is None
    assert body['duration_coverage']['known'] == body['duration_coverage']['expected'] == 120
    assert body['duration_coverage']['reason'] == 'aggregate_overflow'
    assert comparison['current']['known_activities'] == comparison['current']['expected_activities'] == 120
    assert comparison['current']['complete'] is False
    assert comparison['current']['coverage_status'] == 'unavailable'
    assert comparison['change_pct'] is None and comparison['status'] == 'insufficient_data'


def test_record_periods_dates_sources_and_ties_are_stable(api):
    client, engine = api
    with Session(engine) as db:
        old = add(db, 'old', 120, distance_meters=200000)
        ninety = add(db, 'ninety', 50, distance_meters=100000)
        first_tie = add(db, 'first-tie', 20, distance_meters=50000)
        second_tie = add(db, 'second-tie', 20, distance_meters=50000)
        add(db, 'missing', distance_meters=None)
        db.commit(); ids = old.id, ninety.id, second_tie.id
    records = sport(profile(client))['records']
    assert [records[period]['longest_distance_km']['activity_id'] for period in ('all_time', 'days_90', 'days_28')] == list(ids)
    record = records['days_28']['longest_distance_km']
    assert record['date'] == '2026-10-02' and record['source'] == 'fit'
    assert record['scope'] == 'whole_activity'
    assert record['candidate_coverage'] == {'known': 2, 'expected': 3, 'missing': 1, 'invalid': 0, 'eligible': 2}


def test_future_and_rome_calendar_boundaries_are_excluded_from_records(api):
    client, engine = api
    with Session(engine) as db:
        included = add(db, 'included', start_time=datetime(2026, 10, 21, 21, 30), distance_meters=20000)
        add(db, 'rome-next-day', start_time=datetime(2026, 10, 21, 22, 30), distance_meters=30000)
        future = add(db, 'future-instant', start_time=NOW + timedelta(seconds=1), distance_meters=999000)
        db.add(ActivityObservation(activity_id=future.id, source_key='intervals:future', payload_json='{"load":999}'))
        db.commit(); identifier = included.id
    older = profile(client, '2026-10-21')
    assert older['total_activities'] == 1
    assert sport(older)['records']['all_time']['longest_distance_km']['activity_id'] == identifier
    current = profile(client)
    assert current['total_activities'] == 2
    assert current['totals']['all_time']['load'] == 60


def test_shared_strength_aliases_agree_with_weekly_summary(api):
    client, engine = api
    aliases = ['Crossfit', 'strengthtraining', 'WeightTraining', 'palestra', 'gym', 'weightlifting']
    with Session(engine) as db:
        for alias in aliases:
            add(db, alias, sport=alias)
        db.commit()
    body = profile(client)
    summary = client.get('/experience/summary?end=2026-10-22').json()
    assert sport(body, 'strength')['activities'] == len(aliases)
    assert summary['sports']['strength']['sessions'] == len(aliases)
    assert sport(body, 'other')['activities'] == 0


def test_profile_load_provenance_is_preloaded_once_and_gets_are_read_only(api):
    client, engine = api
    with Session(engine) as db:
        for index in range(50):
            row = add(db, f'row-{index}', sport=['cycling', 'running', 'swimming', 'strength', 'other'][index % 5])
            db.add(ActivityObservation(activity_id=row.id, source_key=f'intervals:row-{index}', payload_json='{"load":30}'))
        db.commit()
    statements = []
    def capture(conn, cursor, sql, parameters, context, executemany):
        statements.append(sql)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        body = profile(client)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    observation_queries = [sql for sql in statements if 'FROM activity_observations' in sql]
    assert len(observation_queries) == 1
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    assert body['totals']['all_time']['load'] == 1500
    assert body['totals']['days_28']['coverage_summary']['load']['known'] == 50


def test_conflicting_load_observations_remain_explicit_in_profile_coverage(api):
    client, engine = api
    with Session(engine) as db:
        row = add(db, 'ambiguous', load=999)
        db.add(ActivityObservation(activity_id=row.id, source_key='intervals:first', payload_json='{"load":30}'))
        db.add(ActivityObservation(activity_id=row.id, source_key='intervals:second', payload_json='{"load":40}'))
        db.commit()
    body = profile(client)
    coverage = body['totals']['days_28']['coverage_summary']['load']
    assert body['weekly_load'] is None
    assert coverage['known'] == coverage['missing'] == 0
    assert coverage['expected'] == coverage['invalid'] == coverage['conflicting'] == 1
    assert coverage['known_value'] is None and coverage['status'] == 'unavailable'


def test_huge_integer_and_boolean_records_are_rejected_without_overflow():
    rows = [SimpleNamespace(id=1, start_time=NOW, source='fit', name='bad',
                            **{attribute: 10**400 for attribute, *_ in athlete_snapshot.RECORD_FIELDS.values()})]
    assert all(value is None for value in athlete_snapshot._records(rows).values())
    for attribute, *_ in athlete_snapshot.RECORD_FIELDS.values():
        setattr(rows[0], attribute, True)
    assert all(value is None for value in athlete_snapshot._records(rows).values())
