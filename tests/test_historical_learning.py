"""Historical capacity uses the athlete calendar and an explicit as-of day."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.historical_learning import build_historical_profile


def activity(stamp, **changes):
    return SimpleNamespace(start_time=stamp, sport='cycling', duration_seconds=3600,
                           distance_meters=20000, load=20, avg_power=None,
                           normalized_power=None, avg_speed=None, **changes)


def test_history_span_and_training_days_use_rome_calendar():
    rows = [activity(datetime(2026, 10, 18, 21, 30)), activity(datetime(2026, 10, 18, 22, 30))]
    history = build_historical_profile(rows, reference_date=date(2026, 10, 19))
    assert history['all_time']['period'] == {'from': '2026-10-18', 'to': '2026-10-19', 'days': 2}
    assert history['all_time']['training_days'] == 2
    assert history['all_time']['max_weekly_load'] == 20
    assert history['all_time']['max_weekly_duration_hours'] == 1


def test_reference_day_excludes_later_observations_from_every_window():
    rows = [activity(datetime(2026, 10, 17, 8)), activity(datetime(2026, 10, 18, 8))]
    history = build_historical_profile(rows, reference_date=date(2026, 10, 17))
    assert history['all_time']['activities'] == 1
    assert history['recent_90d']['activities'] == 1
    assert history['ironman']['cycling']['activities'] == 1


def test_ninety_day_window_contains_exactly_ninety_calendar_days():
    reference = date(2026, 10, 19)
    rows = [activity(datetime.combine(reference - timedelta(days=offset), datetime.min.time()) + timedelta(hours=8))
            for offset in (0, 89, 90)]
    history = build_historical_profile(rows, reference_date=reference)
    assert history['all_time']['activities'] == 3
    assert history['recent_90d']['activities'] == 2


def test_mixed_naive_and_offset_timestamps_are_ordered_by_instant():
    rows = [activity(datetime(2026, 10, 18, 22, 30)),
            activity(datetime(2026, 10, 18, 22, 30, tzinfo=timezone.utc))]
    history = build_historical_profile(rows, reference_date=date(2026, 10, 19))
    assert history['all_time']['training_days'] == 1
    assert history['all_time']['period']['from'] == '2026-10-19'
