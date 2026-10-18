from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

from app.services.training_load import activity_load, sport_key


IRONMAN_SPORTS = {
    "cycling": {"bike"},
    "running": {"run"},
    "swimming": {"swim"},
}


def _valid(activity) -> bool:
    duration = float(activity.duration_seconds or 0)
    load = float(activity.load or 0)
    return bool(activity.start_time) and (duration >= 60 or load > 0)


def _safe_mean(values):
    values = [float(v) for v in values if v is not None]
    return round(mean(values), 2) if values else None


def _safe_median(values):
    values = [float(v) for v in values if v is not None]
    return round(median(values), 2) if values else None


def _build_window(activities, ftp, label):
    if not activities:
        return {
            "label": label,
            "activities": 0,
            "training_days": 0,
            "duration_hours": 0.0,
            "distance_km": 0.0,
            "load": 0.0,
            "avg_activities_per_week": 0.0,
            "avg_duration_hours_per_week": 0.0,
            "avg_load_per_week": 0.0,
            "max_weekly_load": 0.0,
            "max_weekly_duration_hours": 0.0,
            "sports": {},
        }

    activities = sorted(activities, key=lambda a: a.start_time)

    first_date = activities[0].start_time.date()
    last_date = activities[-1].start_time.date()
    days = max((last_date - first_date).days + 1, 1)
    weeks = max(days / 7.0, 1.0)

    total_duration = sum(
        float(a.duration_seconds or 0) / 3600
        for a in activities
    )

    total_distance = sum(
        float(a.distance_meters or 0) / 1000
        for a in activities
        if a.distance_meters is not None
    )

    total_load = sum(activity_load(a, ftp) for a in activities)

    weekly = defaultdict(
        lambda: {
            "activities": 0,
            "duration_hours": 0.0,
            "load": 0.0,
        }
    )

    for a in activities:
        monday = a.start_time.date() - timedelta(
            days=a.start_time.weekday()
        )
        key = monday.isoformat()

        weekly[key]["activities"] += 1
        weekly[key]["duration_hours"] += (
            float(a.duration_seconds or 0) / 3600
        )
        weekly[key]["load"] += activity_load(a, ftp)

    weekly_values = list(weekly.values())

    grouped = defaultdict(list)

    for a in activities:
        grouped[sport_key(a.sport)].append(a)

    sports = {}

    for key, rows in grouped.items():
        durations = [
            float(a.duration_seconds or 0) / 60
            for a in rows
        ]

        distances = [
            float(a.distance_meters or 0) / 1000
            for a in rows
            if a.distance_meters is not None
        ]

        loads = [
            activity_load(a, ftp)
            for a in rows
        ]

        sports[key] = {
            "activities": len(rows),
            "duration_hours": round(
                sum(durations) / 60, 2
            ),
            "distance_km": round(
                sum(distances), 2
            ),
            "load": round(
                sum(loads), 1
            ),
            "avg_session_duration_min": _safe_mean(
                durations
            ),
            "median_session_duration_min": _safe_median(
                durations
            ),
            "longest_session_min": round(
                max(durations), 1
            ) if durations else None,
            "avg_load": _safe_mean(loads),
            "median_load": _safe_median(loads),
        }

    return {
        "label": label,
        "period": {
            "from": first_date.isoformat(),
            "to": last_date.isoformat(),
            "days": days,
        },
        "activities": len(activities),
        "training_days": len(
            set(a.start_time.date() for a in activities)
        ),
        "duration_hours": round(total_duration, 2),
        "distance_km": round(total_distance, 2),
        "load": round(total_load, 1),
        "avg_activities_per_week": round(
            len(activities) / weeks, 2
        ),
        "avg_duration_hours_per_week": round(
            total_duration / weeks, 2
        ),
        "avg_load_per_week": round(
            total_load / weeks, 1
        ),
        "max_weekly_load": round(
            max(x["load"] for x in weekly_values), 1
        ) if weekly_values else 0.0,
        "max_weekly_duration_hours": round(
            max(x["duration_hours"] for x in weekly_values), 2
        ) if weekly_values else 0.0,
        "sports": dict(sports),
    }


def build_historical_profile(
    activities,
    ftp_watts=170.0,
    reference_date=None,
):
    ftp = float(ftp_watts or 170)

    valid = [
        a for a in activities
        if _valid(a)
    ]

    valid.sort(
        key=lambda a: a.start_time
    )

    if not valid:
        return {
            "status": "no_data",
            "total_activities": 0,
        }

    if reference_date is None:
        reference_date = max(
            a.start_time for a in valid
        ).date()
    elif isinstance(reference_date, datetime):
        reference_date = reference_date.date()

    cutoff_12m = reference_date - timedelta(days=365)
    cutoff_90d = reference_date - timedelta(days=90)

    recent_12m = [
        a for a in valid
        if a.start_time.date() >= cutoff_12m
    ]

    recent_90d = [
        a for a in valid
        if a.start_time.date() >= cutoff_90d
    ]

    # Sport-specific Ironman history.
    ironman = {}

    for ironman_sport, keys in IRONMAN_SPORTS.items():
        rows = [
            a for a in valid
            if sport_key(a.sport) in keys
        ]

        durations = [
            float(a.duration_seconds or 0) / 3600
            for a in rows
        ]

        distances = [
            float(a.distance_meters or 0) / 1000
            for a in rows
            if a.distance_meters is not None
        ]

        loads = [
            activity_load(a, ftp)
            for a in rows
        ]

        data = {
            "activities": len(rows),
            "duration_hours": round(
                sum(durations), 2
            ),
            "distance_km": round(
                sum(distances), 2
            ),
            "load": round(
                sum(loads), 1
            ),
            "avg_session_duration_min": _safe_mean(
                [x * 60 for x in durations]
            ),
            "median_session_duration_min": _safe_median(
                [x * 60 for x in durations]
            ),
            "longest_session_min": round(
                max(durations) * 60, 1
            ) if durations else None,
            "avg_load": _safe_mean(loads),
            "median_load": _safe_median(loads),
        }

        if ironman_sport == "cycling":
            powers = [
                a.avg_power
                for a in rows
                if a.avg_power is not None
            ]

            npowers = [
                a.normalized_power
                for a in rows
                if a.normalized_power is not None
            ]

            data["avg_power_w"] = _safe_mean(powers)
            data["median_power_w"] = _safe_median(powers)
            data["avg_normalized_power_w"] = _safe_mean(npowers)
            data["max_normalized_power_w"] = (
                max(npowers)
                if npowers
                else None
            )

        if ironman_sport == "running":
            speeds = [
                a.avg_speed
                for a in rows
                if a.avg_speed is not None
                and a.avg_speed > 0
            ]

            data["avg_speed"] = _safe_mean(speeds)

        ironman[ironman_sport] = data

    return {
        "status": "ok",
        "generated_at": datetime.utcnow().isoformat(),
        "reference_date": reference_date.isoformat(),

        "all_time": _build_window(
            valid,
            ftp,
            "all_time",
        ),

        "recent_12m": _build_window(
            recent_12m,
            ftp,
            "recent_12m",
        ),

        "recent_90d": _build_window(
            recent_90d,
            ftp,
            "recent_90d",
        ),

        "ironman": ironman,
    }
