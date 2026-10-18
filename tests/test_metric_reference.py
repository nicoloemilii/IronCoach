"""Personal color references use historical comparable evidence, not population cutoffs."""
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.iron_models import HealthMetric
from app.services import health, progress


def test_descriptive_reference_rejects_missing_nonfinite_and_short_history():
    invalid = [{'value': value} for value in (None, True, float('nan'), float('inf'), '50')]
    assert health.reference_range(invalid + [{'value': 50}] * 6) is None
    result = health.reference_range(invalid + [{'value': value} for value in (40, 42, 44, 46, 48, 50, 52)])
    assert result == {'lower': 34.0, 'upper': 58.0, 'count': 7, 'method': 'descriptive_iqr'}


def test_zero_sleep_remains_an_observation_but_zero_hrv_is_not_a_reference():
    points = [{'value': 0}] * 7
    assert health.reference_range(points)['count'] == 7
    assert health.reference_range(points, positive_only=True) is None


def test_health_and_recovery_ranges_use_same_source_and_separate_time_windows(monkeypatch):
    today = date(2026, 10, 18)
    monkeypatch.setattr(progress, 'athlete_today', lambda: today)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            for ago in range(36):
                # The current week cannot vote in the recovery reference.
                value = 80 if ago < 7 else 50
                day = str(today - timedelta(days=ago))
                db.add(HealthMetric(date=day, kind='hrv_ms', source='intervals', value=value, unit='ms', observed_at=day, synced_at=day))
            # A newer personal note and a very old outlier are not comparable.
            db.add(HealthMetric(date=str(today), kind='hrv_ms', source='manual', value=1000, unit='ms', observed_at=str(today), synced_at=str(today)))
            old = str(today - timedelta(days=60))
            db.add(HealthMetric(date=old, kind='hrv_ms', source='intervals', value=1000, unit='ms', observed_at=old, synced_at=old))
            db.commit()
            body = health.health_response(db, today, today)
            hrv = next(item for item in body['metrics'] if item['kind'] == 'hrv_ms')
            assert hrv['reference_range']['count'] == 28
            assert hrv['reference_range']['lower'] == 50
            recovery = progress.recovery_progress(db)
            hrv = next(item for item in recovery['metrics'] if item['kind'] == 'hrv_ms')
            assert hrv['current'] == 80 and hrv['baseline'] == 50
            assert hrv['reference_range'] == {'lower': 50, 'upper': 50, 'count': 28, 'method': 'descriptive_iqr'}
    finally:
        engine.dispose()
