"""The same provider identity wins everywhere; ambiguity stays unavailable."""
import json
import math
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.iron_models import ActivityObservation, HealthMetric, RaceGoal
from app.db.models import Activity
from app.db.planning_models import AthleteSettings
from app.services import activity_rating, health_reports, training_load
from app.services.observed_load import resolve_observed_loads, select_observation

NOW = datetime(2026, 10, 22, 12)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(training_load, 'utc_now_naive', lambda: NOW)
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def activity(db, *, identifier='canonical', source='intervals', load=90, **changes):
    data = dict(source=source, source_id=identifier, sport='cycling', name='Synthetic',
        start_time=NOW - timedelta(hours=1), duration_seconds=3600, normalized_power=200, load=load,
        avg_hr=140, avg_power=180)
    data.update(changes)
    row = Activity(**data); db.add(row); db.flush()
    return row


def observe(db, row, key, value, *, raw=False):
    observation = ActivityObservation(activity_id=row.id, source_key='intervals:' + key,
        payload_json=value if raw else json.dumps({'load': value}))
    db.add(observation); db.flush()
    return observation


@pytest.mark.parametrize('identifier', ['canonical', 'intervals:canonical'])
@pytest.mark.parametrize('value', [None, 0, 50])
def test_canonical_provider_identity_wins_over_later_alias(db, identifier, value):
    row = activity(db, identifier=identifier)
    canonical = observe(db, row, 'canonical', value)
    observe(db, row, 'other-provider-activity', 500)
    evidence = resolve_observed_loads(db, [row])[row.id]
    assert evidence['value'] == value and evidence['reason'] == 'canonical_identity'
    assert evidence['source_key'] == canonical.source_key and evidence['observation_count'] == 2
    assert not evidence['conflict']
    assert activity_rating.recorded_loads(db, [row])[row.id] == value
    assert health_reports._known_loads(db, [row]) == ([] if value is None else [(NOW.date(), value)])
    expected_model = value or 0
    assert training_load.activity_load(row, 170) == expected_model
    with training_load.activity_load_snapshot(db):
        assert training_load.activity_load(row, 170) == expected_model
    assert training_load.daily_loads([row], 170, NOW.date()) == [(NOW.date(), expected_model)]


@pytest.mark.parametrize('first,second,status,value', [(50, 500, 'conflict', None), (None, 50, 'conflict', None),
    (50, 50, 'available', 50), (0, 0, 'available', 0), (None, None, 'missing', None)])
def test_noncanonical_aliases_must_agree_on_validated_load(db, first, second, status, value):
    row = activity(db, source='fit')
    observe(db, row, 'first', first); observe(db, row, 'second', second)
    evidence = resolve_observed_loads(db, [row])[row.id]
    assert evidence['status'] == status and evidence['value'] == value
    assert evidence['conflict'] == (status == 'conflict')
    assert activity_rating.recorded_loads(db, [row])[row.id] == value
    assert training_load.activity_load(row, 170) == (value or 0)
    # Invalid/conflicting evidence cannot turn into an NP/FTP estimate.
    with training_load.activity_load_snapshot(db):
        assert training_load.activity_load(row, 170) == (value or 0)


@pytest.mark.parametrize('raw', ['[]', 'null', 'broken', '{"load":true}', '{"load":-5}', '{"load":"50"}',
    '{"load":NaN}', '{"load":Infinity}', '{"load":' + str(10 ** 1000) + '}',
    '{"nested":' + '[' * 2000 + '0' + ']' * 2000 + ',"load":50}'],
    ids=['array', 'null', 'broken', 'boolean', 'negative', 'string', 'nan', 'infinity', 'hugeint', 'deep'])
def test_corrupt_observed_load_fails_closed_consistently(db, raw):
    row = activity(db)
    observe(db, row, 'canonical', raw, raw=True)
    result = resolve_observed_loads(db, [row])[row.id]
    assert result['value'] is None and result['status'] == 'invalid'
    assert activity_rating.recorded_loads(db, [row])[row.id] is None
    assert health_reports._known_loads(db, [row]) == []
    assert training_load.activity_load(row, 170) == 0
    with training_load.activity_load_snapshot(db):
        assert training_load.activity_load(row, 170) == 0


def test_null_and_invalid_have_distinct_provenance_and_are_not_equivalent(db):
    row = activity(db, source='fit')
    observe(db, row, 'null', None); observe(db, row, 'bad', '[]', raw=True)
    result = resolve_observed_loads(db, [row])[row.id]
    assert result['status'] == 'conflict'
    db.query(ActivityObservation).filter_by(source_key='intervals:bad').delete()
    result = resolve_observed_loads(db, [row])[row.id]
    assert result['status'] == 'missing' and result['value_reason'] == 'explicit_null'


def test_equivalent_loads_do_not_claim_payload_equivalence(db):
    row = activity(db, source='fit')
    first = observe(db, row, 'first', '{"load":50,"distance":100}', raw=True)
    second = observe(db, row, 'second', '{"load":50,"distance":999}', raw=True)
    result = select_observation(row, [second, first])
    assert result['reason'] == 'equivalent_loads' and result['observation_count'] == 2
    assert result['value'] == 50


def test_requested_ids_batch_at_five_hundred_and_exclude_unrelated_payloads(db):
    rows = [activity(db, identifier=f'id-{index}') for index in range(501)]
    unrelated = activity(db, identifier='unrelated')
    observe(db, unrelated, 'unrelated', '{"load":' + str(10 ** 1000) + '}', raw=True)
    statements = []
    def capture(conn, cursor, statement, parameters, context, many):
        if 'FROM activity_observations' in statement:
            statements.append((statement, parameters))
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        result = resolve_observed_loads(db, rows)
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert len(result) == 501 and unrelated.id not in result
    assert len(statements) == 2
    assert all('activity_observations.activity_id IN' in sql for sql, _ in statements)
    assert len(statements[0][1]) == 501 and len(statements[1][1]) == 2  # IDs plus LIKE parameter.


def test_no_observation_preserves_finite_canonical_and_existing_tss_estimate(db):
    measured = activity(db, load=0)
    estimated = activity(db, identifier='fit', source='fit', load=None)
    assert resolve_observed_loads(db, [measured, estimated])[measured.id]['value'] == 0
    assert training_load.activity_load(measured, 170) == 0
    assert training_load.activity_load(estimated, 170) == pytest.approx(138.4)


@pytest.mark.parametrize('value', [float('inf'), float('nan'), -150, 1e308, True])
def test_invalid_normalized_power_never_produces_nonfinite_or_squared_negative_tss(db, value):
    row = activity(db, source='fit', load=None, normalized_power=value)
    # A direct helper retains bool provenance that SQLite Float would erase.
    assert training_load.cycling_tss(3600, value, 170) == 0
    if isinstance(value, bool):
        return
    assert training_load.activity_load(row, 170) == 0
    metrics = training_load.fitness_metrics([row], 170, NOW.date())
    assert all(math.isfinite(value) for value in metrics.values() if isinstance(value, (int, float)))


@pytest.mark.parametrize('kind,value,source', [('hrv_ms', 0, 'intervals'), ('resting_hr_bpm', 0, 'intervals'),
    ('tsb', -35, 'local_estimate'), ('sleep_hours', 25, 'intervals')])
def test_unusable_wellness_does_not_supply_recovery_rating(db, kind, value, source):
    row = activity(db)
    for day in range(2, 11):
        db.add(HealthMetric(date=str((NOW - timedelta(days=day)).date()), kind=kind,
            value=50 if kind in ('hrv_ms', 'resting_hr_bpm') else 8, unit='test', source='intervals',
            observed_at='synthetic', synced_at='synthetic'))
    db.add(HealthMetric(date=str((NOW - timedelta(days=1)).date()), kind=kind, value=value,
        source=source, unit='test', observed_at='synthetic', synced_at='synthetic')); db.flush()
    result = activity_rating.activity_ratings(db, now=NOW)[row.id]
    # The valid preceding day-2 HRV/RHR may remain usable; zero must not be used
    # or described as an adverse change in its place.
    recovery = [item for item in result['components'] if item['key'] == 'recovery']
    assert not recovery or recovery[0]['score'] == 85


@pytest.mark.parametrize('legs', ['[null]', '{}', '[3]', '"bad"', '{', '[' * 2000 + '0' + ']' * 2000])
def test_malformed_goal_never_crashes_unrelated_activity_rating(db, legs):
    row = activity(db)
    goal = RaceGoal(name='Synthetic', date='2027-01-01', legs_json=legs)
    db.add(goal); db.flush()
    db.add(AthleteSettings(id=1, payload_json=json.dumps({'race_goal_id': goal.id}))); db.flush()
    assert activity_rating.activity_ratings(db, now=NOW)[row.id]['score'] is not None


@pytest.mark.parametrize('sport,expected', [('Ride', 'bike'), ('Crossfit', 'strength'), ('palestra', 'strength'),
    ('strengthtraining', 'strength'), ('gym', 'strength'), ('weightlifting', 'strength'), ('weight_lifting', 'strength'), ('Workout', 'workout'), ('Walk', 'walk')])
def test_shared_sport_aliases_do_not_invent_running_or_strength(sport, expected):
    assert training_load.sport_key(sport) == expected


def test_positive_observed_load_without_canonical_load_or_duration_enters_fitness(db):
    row = activity(db, load=None, duration_seconds=None)
    observe(db, row, 'canonical', 30)
    assert activity_rating.recorded_loads(db, [row])[row.id] == 30
    assert training_load.daily_loads([row], 170, NOW.date()) == [(NOW.date(), 30)]
    assert training_load.daily_loads_by_sport([row], 170, NOW.date()) == [(NOW.date(), {'bike': 30})]
    assert training_load.fitness_metrics([row], 170, NOW.date())['ctl'] > 0


def test_short_artifact_uses_effective_observation_not_old_canonical_load(db):
    row = activity(db, load=90, duration_seconds=38)
    observe(db, row, 'canonical', None)
    assert training_load.daily_loads([row], 170, NOW.date()) == []
    assert training_load.daily_loads_by_sport([row], 170, NOW.date()) == []


def test_targeted_rating_exactly_matches_full_context_and_avoids_unrelated_health(db):
    for days in range(3, 43, 3):
        activity(db, identifier=f'prior-{days}', start_time=NOW - timedelta(days=days), load=50)
    target = activity(db)
    old = activity(db, identifier='unrelated-old', start_time=NOW - timedelta(days=2000))
    future = activity(db, identifier='unrelated-future', start_time=NOW + timedelta(days=2000))
    for offset in (1, 2, 10, 20, 1000):
        db.add(HealthMetric(date=str((NOW - timedelta(days=offset)).date()), kind='tsb', value=-5,
            source='intervals', unit='points', observed_at='synthetic', synced_at='synthetic'))
    db.flush()
    expected = activity_rating.activity_ratings(db, now=NOW)[target.id]
    queries = []
    def capture(conn, cursor, statement, parameters, context, many):
        queries.append((statement, parameters))
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        actual = activity_rating.activity_ratings(db, now=NOW, target_ids={target.id})
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert actual == {target.id: expected}
    observations = [(sql, params) for sql, params in queries if 'FROM activity_observations' in sql]
    assert len(observations) == 1
    assert old.id not in observations[0][1][:-1] and future.id not in observations[0][1][:-1]
    wellness = [(sql, params) for sql, params in queries if 'FROM health_metrics' in sql]
    assert len(wellness) == 1 and str((NOW - timedelta(days=29)).date()) in wellness[0][1]


def test_legacy_first_calendar_days_do_not_underflow_target_or_health_history(db):
    first = activity(db, identifier='first-day', start_time=datetime(1, 1, 1, 12))
    second = activity(db, identifier='second-day', start_time=datetime(1, 1, 2, 12))
    full = activity_rating.activity_ratings(db, now=NOW)
    assert full[first.id]['score'] is not None and full[second.id]['score'] is not None
    assert activity_rating.activity_ratings(db, now=NOW, target_ids={second.id}) == {second.id: full[second.id]}


def test_missing_or_conflicting_load_prevents_an_incomplete_symptom_load_comparison(db, monkeypatch):
    monkeypatch.setattr(health_reports, 'utc_now_naive', lambda: NOW)
    for index, ago in enumerate((5, 10, 15, 1, 2)):
        activity(db, identifier=f'report-{index}', start_time=NOW - timedelta(days=ago), load=50)
    unknown = activity(db, identifier='unknown', source='fit', start_time=NOW - timedelta(days=1), load=90)
    observe(db, unknown, 'one', 50); observe(db, unknown, 'two', 500)
    result = health_reports.report_analysis(db, SimpleNamespace(date=str(NOW.date()), suspected_cause='Synthetic hypothesis'))
    assert result['load_coverage']['conflict'] == 1 and not result['load_coverage']['complete']
    assert not any(row['kind'] == 'load' for row in result['comparisons'])
