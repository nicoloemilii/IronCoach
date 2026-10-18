"""Calendar for this single-athlete app; database activity stamps are naive UTC.

Never compare a stored UTC timestamp with a naive Rome wall-clock timestamp.
Translate local calendar boundaries to UTC first, including 23/25-hour DST days.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

ATHLETE_TIMEZONE = "Europe/Rome"
ATHLETE_TZ = ZoneInfo(ATHLETE_TIMEZONE)


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc_naive(stamp: datetime) -> datetime:
    """Normalize aware stamps; persisted naive stamps already represent UTC."""
    if stamp.tzinfo is None:
        return stamp
    return stamp.astimezone(timezone.utc).replace(tzinfo=None)


def athlete_today(now: datetime | None = None) -> date:
    return activity_day(now if now is not None else utc_now_naive())


def activity_day(stamp: datetime) -> date:
    return as_utc_naive(stamp).replace(tzinfo=timezone.utc).astimezone(ATHLETE_TZ).date()


def day_start_utc(day: date) -> datetime:
    return as_utc_naive(datetime.combine(day, time.min, tzinfo=ATHLETE_TZ))


def local_stamp_to_utc_naive(stamp: datetime) -> datetime:
    """Fallback for upstream local stamps lacking an offset: assume Europe/Rome.

    An explicit offset always wins. A naive DST overlap selects its first
    occurrence (fold=0). A nonexistent spring-forward local time uses the prior
    offset, normalizing forward across the gap when converted back from UTC.
    These are deterministic fallback rules, not inferred device timezones.
    """
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ATHLETE_TZ, fold=0)
    return as_utc_naive(stamp)
