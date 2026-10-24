from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
from datetime import datetime, timedelta
from typing import Iterable
from app.services.athlete_clock import activity_day, athlete_today, as_utc_naive, utc_now_naive
from app.services.observed_load import resolve_observed_loads, finite_load

_LOAD_SNAPSHOT = ContextVar('activity_load_snapshot', default=None)


def _observed_load(payload):
    """Malformed legacy evidence stays unavailable, never a canonical fallback."""
    try:
        content = json.loads(payload)
        value = content.get('load') if isinstance(content, dict) else None
        if isinstance(value, bool) or value is None:
            return None
        number = float(value)
        if not math.isfinite(number) or number < 0:
            return None
        return value if isinstance(value, (int, float)) else number
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


@contextmanager
def activity_load_snapshot(db, activities=None):
    """Batch observations for one read operation, scoped to its exact session.

    Presence with a null load differs from no remote observation. A ContextVar
    prevents sharing an athlete's data with concurrent requests or workspaces;
    leaving the context always discards it, including after failures.
    """
    current = _LOAD_SNAPSHOT.get()
    if current is not None and current[0] is db:
        yield current[1]
        return
    details = resolve_observed_loads(db, activities)
    values = {identifier: item['value'] for identifier, item in details.items() if item['observation_count']}
    token = _LOAD_SNAPSHOT.set((db, values, details))
    try:
        yield values
    finally:
        _LOAD_SNAPSHOT.reset(token)


CYCLING_SPORTS = {
    "bike", "ride", "cycling", "biking", "virtualride", "virtual_ride",
    "indoorcycling", "indoor_cycling", "mountainbikeride", "mountain_bike", "gravelride",
}
RUNNING_SPORTS = {"run", "running", "trailrun", "trailrunning", "trail_run", "virtualrun"}
SWIM_SPORTS = {"swim", "swimming", "openwaterswim", "open_water_swim"}
STRENGTH_SPORTS = {"strength", "weighttraining", "weight_training", "strengthtraining", "strength_training",
                   "crossfit", "gym", "palestra", "weightlifting", "weight_lifting"}


def sport_key(sport: str | None) -> str:
    value = sport.lower().strip() if isinstance(sport, str) else 'unknown'
    if value in CYCLING_SPORTS:
        return "bike"
    if value in RUNNING_SPORTS:
        return "run"
    if value in SWIM_SPORTS:
        return "swim"
    if value in STRENGTH_SPORTS:
        return "strength"
    return value or "unknown"


def cycling_tss(duration_seconds: float | None, normalized_power: float | None, ftp_watts: float) -> float:
    duration_seconds = finite_load(duration_seconds)
    normalized_power = finite_load(normalized_power)
    ftp_watts = finite_load(ftp_watts)
    if not duration_seconds or not normalized_power or not ftp_watts:
        return 0.0
    try:
        calculated = 100.0 * (duration_seconds / 3600.0) * (normalized_power / ftp_watts) ** 2
    except (OverflowError, ZeroDivisionError):
        return 0.0
    return round(calculated, 1) if math.isfinite(calculated) else 0.0


def activity_load(activity, ftp_watts: float) -> float:
    """Return the load used by the analysis layer.

    Cycling activities with NP use the athlete FTP to calculate cycling TSS.
    Every other sport uses the sport-specific load already analyzed by
    Intervals.icu. If cycling has no NP, its stored Intervals.icu load is used.
    No synthetic cross-sport multipliers are applied.
    """
    # Intervals analyzed load is authoritative, including an explicit zero.
    if getattr(activity, 'id', None) is not None:
        from sqlalchemy.orm import object_session
        from sqlalchemy.orm.exc import UnmappedInstanceError
        try: db = object_session(activity)
        except UnmappedInstanceError: db = None
        if db:
            snapshot = _LOAD_SNAPSHOT.get()
            if snapshot is not None and snapshot[0] is db and activity.id in snapshot[2]:
                evidence = snapshot[2][activity.id]
            else:
                evidence = resolve_observed_loads(db, [activity])[activity.id]
            if evidence['observation_count']:
                # Legacy fitness requires a numeric input. Zero here is a model
                # imputation for unavailable load, never a measured zero; raw
                # aggregates use the resolver's None and provenance instead.
                return round(evidence['value'], 1) if evidence['value'] is not None else 0.0
    stored = finite_load(getattr(activity, 'load', None))
    if getattr(activity, 'source', None) == 'intervals' and stored is not None:
        return round(stored, 1)
    sport = activity.sport.lower().strip() if isinstance(activity.sport, str) else ''
    if sport in CYCLING_SPORTS:
        calculated = cycling_tss(activity.duration_seconds, activity.normalized_power, ftp_watts)
        if calculated > 0:
            return calculated

    return round(stored, 1) if stored is not None else 0.0


def _valid_activity(activity, known_load=None) -> bool:
    """Exclude obvious malformed zero/near-zero sessions from fitness load."""
    if not activity.start_time:
        return False
    duration = finite_load(activity.duration_seconds)
    # A 38-second open-water activity with no distance/load is a data-quality
    # artifact for training analysis, not a meaningful workout.
    effective = finite_load(known_load) if known_load is not None else finite_load(activity.load)
    if (duration is None or duration < 60) and not (effective or 0) > 0:
        return False
    return True


def _effective_loads(activities, ftp_watts):
    """Batch each session once, including positive observed load without duration."""
    from sqlalchemy.orm import object_session
    from sqlalchemy.orm.exc import UnmappedInstanceError
    groups = defaultdict(list); values = {}
    for activity in activities:
        try:
            db = object_session(activity)
        except UnmappedInstanceError:
            db = None
        groups[db].append(activity)
    for db, rows in groups.items():
        if db is None:
            for row in rows:
                values[id(row)] = activity_load(row, ftp_watts)
        else:
            with activity_load_snapshot(db, rows):
                for row in rows:
                    values[id(row)] = activity_load(row, ftp_watts)
    return values


def daily_loads(activities: Iterable, ftp_watts: float, end_date=None):
    now = utc_now_naive()
    end_date = end_date or athlete_today(now)
    activities = [row for row in activities if isinstance(getattr(row, 'start_time', None), datetime)
                  and as_utc_naive(row.start_time) <= now and activity_day(row.start_time) <= end_date]
    loads = _effective_loads(activities, ftp_watts)
    by_day = defaultdict(float)
    for activity in activities:
        if not _valid_activity(activity, loads[id(activity)]):
            continue
        day = activity_day(activity.start_time)
        if day > end_date:
            continue
        by_day[day] += loads[id(activity)]
    if not by_day:
        return []
    first = min(by_day)
    result = []
    day = first
    while day <= end_date:
        result.append((day, round(by_day.get(day, 0.0), 1)))
        day += timedelta(days=1)
    return result


def daily_loads_by_sport(activities: Iterable, ftp_watts: float, end_date=None):
    now = utc_now_naive()
    end_date = end_date or athlete_today(now)
    activities = [row for row in activities if isinstance(getattr(row, 'start_time', None), datetime)
                  and as_utc_naive(row.start_time) <= now and activity_day(row.start_time) <= end_date]
    loads = _effective_loads(activities, ftp_watts)
    by_day = defaultdict(lambda: defaultdict(float))
    first_date = None
    for activity in activities:
        if not _valid_activity(activity, loads[id(activity)]):
            continue
        day = activity_day(activity.start_time)
        if day > end_date:
            continue
        first_date = day if first_date is None or day < first_date else first_date
        by_day[day][sport_key(activity.sport)] += loads[id(activity)]
    if first_date is None:
        return []

    result = []
    day = first_date
    while day <= end_date:
        result.append((day, {sport: round(value, 1) for sport, value in by_day.get(day, {}).items()}))
        day += timedelta(days=1)
    return result


def _ema(series, time_constant_days: int):
    value = 0.0
    k = 1.0 / float(time_constant_days)
    for _, load in series:
        value += (load - value) * k
    return value


def fitness_metrics(activities, ftp_watts: float, end_date=None, ctl_days: int = 42, atl_days: int = 7):
    series = daily_loads(activities, ftp_watts, end_date=end_date)
    if not series:
        return {"ctl": 0.0, "atl": 0.0, "tsb": 0.0, "days": 0}
    ctl = _ema(series, ctl_days)
    atl = _ema(series, atl_days)
    return {
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(ctl - atl, 1),
        "days": len(series),
        "ctl_time_constant_days": ctl_days,
        "atl_time_constant_days": atl_days,
    }


def fitness_metrics_by_sport(activities, ftp_watts: float, end_date=None, ctl_days: int = 42, atl_days: int = 7):
    daily = daily_loads_by_sport(activities, ftp_watts, end_date=end_date)
    if not daily:
        return {}

    sports = sorted({sport for _, loads in daily for sport in loads})
    output = {}
    for sport in sports:
        series = [(day, loads.get(sport, 0.0)) for day, loads in daily]
        ctl = _ema(series, ctl_days)
        atl = _ema(series, atl_days)
        output[sport] = {
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(ctl - atl, 1),
        }
    return output


def data_quality(activities, ftp_watts: float):
    issues = []
    for activity in activities:
        duration = float(activity.duration_seconds or 0)
        distance = float(activity.distance_meters or 0)
        load = activity_load(activity, ftp_watts)
        if duration < 60 and load == 0:
            issues.append({
                "id": activity.id,
                "sport": activity.sport,
                "name": activity.name,
                "start_time": activity.start_time,
                "reason": "session_under_60s_without_load",
                "duration_seconds": round(duration, 1),
                "distance_meters": round(distance, 1),
            })
    return {"issues": issues, "count": len(issues)}
