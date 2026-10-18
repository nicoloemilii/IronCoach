from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean

from app.services.readiness import calculate_daily_state
from app.services.training_load import activity_load, sport_key
from app.services.athlete_clock import activity_day, athlete_today, as_utc_naive, day_start_utc, utc_now_naive


def _valid(a):
    return bool(getattr(a, 'start_time', None)) and (float(getattr(a, 'duration_seconds', 0) or 0) >= 60 or float(getattr(a, 'load', 0) or 0) > 0)


def _window(activities, ftp, days, end_date):
    cutoff = day_start_utc(end_date - timedelta(days=days - 1))
    stop = min(day_start_utc(end_date + timedelta(days=1)), utc_now_naive())
    rows = [a for a in activities if _valid(a) and cutoff <= as_utc_naive(a.start_time) <= stop and activity_day(a.start_time) <= end_date]
    loads = [activity_load(a, ftp) for a in rows]
    return {
        'sessions': len(rows),
        'load': round(sum(loads), 1),
        'duration_min': round(sum(float(a.duration_seconds or 0) for a in rows) / 60, 1),
        'sports': {s: sum(activity_load(a, ftp) for a in rows if sport_key(a.sport) == s) for s in sorted({sport_key(a.sport) for a in rows})},
    }


def _recent_streak(activities, end_date):
    now = utc_now_naive()
    days = set(activity_day(a.start_time) for a in activities if _valid(a) and as_utc_naive(a.start_time) <= now and activity_day(a.start_time) <= end_date)
    streak = 0
    d = end_date
    while d in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def _training_stress(load_3d, load_7d, sessions_3d):
    # A bounded index used only as decision context, not as a replacement for TSB.
    value = min(100.0, load_3d * 0.30 + load_7d * 0.08 + max(0, sessions_3d - 1) * 8)
    return round(value, 1)


def build_athlete_state(activities, metrics, fitness, historical_capacity, adaptation_context_data=None, ftp=170.0, day=None):
    now = utc_now_naive()
    day = day or athlete_today(now)
    readiness = calculate_daily_state(activities, metrics or {}, ftp, day)
    w1 = _window(activities, ftp, 1, day)
    w3 = _window(activities, ftp, 3, day)
    w7 = _window(activities, ftp, 7, day)
    w28 = _window(activities, ftp, 28, day)

    valid = sorted([a for a in activities if _valid(a) and as_utc_naive(a.start_time) <= now and activity_day(a.start_time) <= day], key=lambda a: as_utc_naive(a.start_time), reverse=True)
    last = valid[0] if valid else None
    last_load = activity_load(last, ftp) if last else 0.0
    hours_since_last = None
    if last:
        hours_since_last = round((now - as_utc_naive(last.start_time)).total_seconds() / 3600, 1)

    return {
        'date': day,
        'fitness': fitness,
        'readiness': readiness,
        'acute': {'24h': w1, '3d': w3, '7d': w7, '28d': w28},
        'last_session': {
            'sport': sport_key(last.sport),
            'name': getattr(last, 'name', None),
            'load': round(last_load, 1),
            'duration_min': round(float(last.duration_seconds or 0) / 60, 1),
            'hours_since': hours_since_last,
        } if last else None,
        'training_stress_index': _training_stress(w3['load'], w7['load'], w3['sessions']),
        'training_day_streak': _recent_streak(activities, day),
        'historical_capacity': historical_capacity or {},
        'adaptation': adaptation_context_data or {'action': 'none', 'confidence': 0.0, 'sample_size': 0, 'trend': 'none'},
    }
