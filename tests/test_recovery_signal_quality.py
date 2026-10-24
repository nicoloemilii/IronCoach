"""Invalid observations remain inspectable and never become recovery evidence."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.iron_models import HealthMetric
from app.services import health, progress, readiness


TODAY = date(2026, 10, 19)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(progress, 'athlete_today', lambda: TODAY)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def metric(db, kind, value, ago=0, source='intervals'):
    day = str(TODAY - timedelta(days=ago))
    db.add(HealthMetric(date=day, kind=kind, value=value, source=source,
                        unit=health.METRICS[kind]['unit'], observed_at=day,
                        synced_at=day))


@pytest.mark.parametrize('kind,baseline,factor', [
    ('hrv_ms', 'hrv_baseline_ms', 'hrv'),
    ('resting_hr_bpm', 'resting_hr_baseline_bpm', 'resting_hr'),
])
@pytest.mark.parametrize('invalid', [0, -1, True, float('nan'), float('inf'), '50', 10 ** 1000])
def test_unusable_cardiac_reading_does_not_vote_in_daily_recovery(kind, baseline, factor, invalid):
    output = readiness.calculate_daily_state([], {kind: invalid, baseline: 50}, day=TODAY)
    assert output['factors'][factor] is None
    assert output['readiness'] is None and output['confidence'] == 0
    # An invalid reading cannot improve or worsen another valid signal.
    with_sleep = readiness.calculate_daily_state([], {kind: invalid, baseline: 50, 'sleep_hours': 4}, day=TODAY)
    only_sleep = readiness.calculate_daily_state([], {'sleep_hours': 4}, day=TODAY)
    assert with_sleep['readiness'] == only_sleep['readiness']
    assert with_sleep['confidence'] == only_sleep['confidence']


@pytest.mark.parametrize('baseline', [0, -1, True, float('nan'), float('inf')])
def test_unusable_baseline_does_not_produce_a_cardiac_factor(baseline):
    output = readiness.calculate_daily_state([], {'hrv_ms': 50, 'hrv_baseline_ms': baseline,
                                                 'resting_hr_bpm': 60, 'resting_hr_baseline_bpm': baseline}, day=TODAY)
    assert output['factors']['hrv'] is None and output['factors']['resting_hr'] is None
    assert output['readiness'] is None and output['confidence'] == 0


def test_real_zeroes_in_optional_signals_remain_available():
    output = readiness.calculate_daily_state([], {'sleep_hours': 0, 'stress_score': 0,
                                                 'steps': 0, 'daily_activity_load': 0,
                                                 'body_battery': 0}, day=TODAY)
    assert output['factors']['sleep'] == 0 and output['factors']['body_battery'] == 0
    assert output['factors']['stress'] == 100 and output['factors']['steps'] == 100
    assert output['factors']['daily_activity'] == 100
    assert output['confidence'] == 71.4


@pytest.mark.parametrize('kind,baseline', [('hrv_ms', 'hrv_baseline_ms'),
                                           ('resting_hr_bpm', 'resting_hr_baseline_bpm')])
def test_imported_zero_is_retained_but_excluded_from_effective_metrics_and_baseline(db, kind, baseline):
    for ago in range(1, 7):
        metric(db, kind, 50, ago)
    metric(db, kind, 0, 7)
    metric(db, kind, 0)
    db.commit()
    raw = next(item for item in health.health_response(db, TODAY, TODAY)['metrics'] if item['kind'] == kind)
    assert raw['latest']['value'] == 0 and raw['points'][0]['value'] == 0
    assert raw['baseline'] is None and raw['reference_range'] is None
    effective = health.effective_metrics(db, TODAY)
    assert effective[kind] is None and effective[baseline] is None
    # One additional real reading completes the seven-reading reference.
    metric(db, kind, 50, 8)
    db.commit()
    effective = health.effective_metrics(db, TODAY)
    assert effective[kind] is None and effective[baseline] == 50
    assert db.query(HealthMetric).filter_by(kind=kind, value=0).count() == 2


@pytest.mark.parametrize('kind', ['hrv_ms', 'resting_hr_bpm'])
def test_invalid_new_observation_cannot_refresh_older_usable_trend(db, kind):
    for ago in range(7, 14):
        metric(db, kind, 50, ago)
    for ago in (3, 4, 5):
        metric(db, kind, 55, ago)
    metric(db, kind, 0)
    db.commit()
    output = next(item for item in progress.recovery_progress(db)['metrics'] if item['kind'] == kind)
    assert output['current'] == 55 and output['recent_delta'] == 5
    assert output['stale'] and output['status'] == 'stale'
    assert output['latest_observation_date'] == str(TODAY)
    assert output['latest_usable_date'] == str(TODAY - timedelta(days=3))
    assert output['recent_dates'] == [str(TODAY - timedelta(days=ago)) for ago in (5, 4, 3)]
    assert output['points'][-1]['value'] == 0


def test_invalid_reading_from_another_source_does_not_replace_usable_reference(db):
    for ago in range(7, 14):
        metric(db, 'hrv_ms', 50, ago)
    for ago in (1, 2, 3):
        metric(db, 'hrv_ms', 55, ago)
    metric(db, 'hrv_ms', 0, source='manual')
    db.commit()
    output = next(item for item in progress.recovery_progress(db)['metrics'] if item['kind'] == 'hrv_ms')
    assert output['source'] == 'intervals' and output['baseline'] == 50
    assert output['current'] == 55 and not output['stale']
    assert output['latest_usable_date'] == str(TODAY - timedelta(days=1))
    assert output['points'][-1]['source'] == 'manual'
