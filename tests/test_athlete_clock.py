"""Calendar/UTC boundary regression tests; no external services are contacted."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import athlete_clock, athlete_state, coach, matching, readiness, training_load
from app.integrations import intervals
from app.services.athlete_clock import (
    ATHLETE_TIMEZONE, activity_day, athlete_today, as_utc_naive,
    day_start_utc, local_stamp_to_utc_naive,
)


def activity(identifier, stamp, load=20):
    return SimpleNamespace(id=identifier, name=f"Activity {identifier}", sport="cycling",
        start_time=stamp, duration_seconds=3600, load=load, normalized_power=None,
        avg_power=None, avg_hr=None, distance_meters=20000)


@pytest.fixture
def fixed_clock(monkeypatch):
    now = datetime(2026, 10, 17, 0, 30)
    for module in (athlete_clock, athlete_state, coach, matching, readiness, training_load, intervals):
        monkeypatch.setattr(module, "utc_now_naive", lambda: now)
    return now


def test_naive_database_stamps_are_utc_and_aware_stamps_keep_their_instant():
    assert ATHLETE_TIMEZONE == "Europe/Rome"
    stamp = datetime(2026, 10, 16, 22, 30)
    assert activity_day(stamp) == date(2026, 10, 17)
    assert athlete_today(stamp) == date(2026, 10, 17)
    assert activity_day(datetime(2026, 1, 16, 23, 30)) == date(2026, 1, 17)
    aware = datetime(2026, 10, 17, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    assert as_utc_naive(aware) == stamp
    assert activity_day(aware) == date(2026, 10, 17)


@pytest.mark.parametrize("day,hours", [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25), (date(2026, 10, 17), 24)])
def test_local_midnight_utc_boundaries_respect_dst_day_lengths(day, hours):
    start, end = day_start_utc(day), day_start_utc(day + timedelta(days=1))
    assert start.tzinfo is None and end.tzinfo is None
    assert (end - start).total_seconds() == hours * 3600
    assert activity_day(start) == day
    assert activity_day(end - timedelta(microseconds=1)) == day
    assert activity_day(end) == day + timedelta(days=1)


def test_upstream_local_naive_dst_fallback_is_deterministic():
    # Repeated 02:30 selects first occurrence; nonexistent 02:30 shifts ahead.
    assert local_stamp_to_utc_naive(datetime(2026, 10, 25, 2, 30)) == datetime(2026, 10, 25, 0, 30)
    assert local_stamp_to_utc_naive(datetime(2026, 3, 29, 2, 30)) == datetime(2026, 3, 29, 1, 30)


def test_intervals_normalizes_offsets_and_prefers_explicit_utc_start():
    assert intervals._dt("2026-10-17T00:30:00+02:00") == datetime(2026, 10, 16, 22, 30)
    assert intervals._dt(datetime(2026, 10, 17, 0, 30, tzinfo=timezone(timedelta(hours=2)))) == datetime(2026, 10, 16, 22, 30)
    assert intervals._dt("2026-10-17T00:30:00") == datetime(2026, 10, 16, 22, 30)
    row = intervals.activity_to_data({"id": "1", "type": "Ride", "start_date": "2026-10-16T22:30:00Z", "start_date_local": "2026-10-17T10:30:00", "moving_time": 3600})
    assert row["start_time"] == datetime(2026, 10, 16, 22, 30)
    row = intervals.activity_to_data({"id": "2", "start_date_local": "2026-10-17T00:30:00"})
    assert row["start_time"] == datetime(2026, 10, 16, 22, 30)
    row = intervals.activity_to_data({"id": "3", "start_date": "2026-10-16T22:30:00"})
    assert row["start_time"] == datetime(2026, 10, 16, 22, 30)


def test_day_loads_readiness_and_streak_use_rome_days_and_exclude_future(fixed_clock):
    rows = [activity(1, datetime(2026, 10, 16, 21, 59), 10), activity(2, datetime(2026, 10, 16, 22, 30)), activity(3, datetime(2026, 10, 17, 1), 999)]
    assert training_load.daily_loads(rows, 170) == [(date(2026, 10, 16), 10), (date(2026, 10, 17), 20)]
    assert training_load.daily_loads_by_sport(rows, 170)[-1] == (date(2026, 10, 17), {"bike": 20})
    state = readiness.calculate_daily_state(rows, {"sleep_hours": 8})
    assert state["date"] == date(2026, 10, 17)
    assert state["raw"]["load_24h"] == 20
    assert state["raw"]["load_3d"] == 30
    aggregate = athlete_state.build_athlete_state(rows, {}, {}, {})
    assert aggregate["acute"]["24h"]["load"] == 20
    assert aggregate["training_day_streak"] == 2
    assert aggregate["last_session"]["name"] == "Activity 2"
    assert aggregate["last_session"]["hours_since"] == 2


def test_matching_and_race_day_follow_local_date_not_utc_date(fixed_clock):
    row = activity(1, datetime(2026, 10, 16, 22, 30))
    result = matching.match_workout_activity({"sport": "cycling", "duration_min": 60}, [row], "2026-10-17")
    assert result["date_score"] == 100
    future = activity(2, datetime(2026, 10, 17, 1))
    assert not matching.match_workout_activity({"sport": "cycling", "duration_min": 60}, [future], activity_id=2)["matched"]
    summary = coach.coach_today([row, future], profile_override={"goals": {"event_date": "2026-10-17"}})
    assert summary["date"] == date(2026, 10, 17)
    assert summary["event"]["phase"] == "race_day"
    assert summary["event"]["days_to_event"] == 0
    assert summary["last_activity"]["id"] == 1
