from __future__ import annotations
from datetime import datetime
import math
from app.services.training_load import activity_load, sport_key
from app.services.athlete_clock import activity_day, athlete_today, as_utc_naive, utc_now_naive


SPORT_MAP = {
    "cycling": "bike",
    "running": "run",
    "swimming": "swim",
}


def match_workout_activity(
    workout: dict,
    activities,
    workout_date: str | None = None,
    activity_id: int | None = None,
    ftp_watts: float = 170.0,
) -> dict:

    now = utc_now_naive()
    activities = [a for a in activities if getattr(a, "start_time", None) and as_utc_naive(a.start_time) <= now]
    if activity_id is not None:
        candidates = [
            a for a in activities
            if getattr(a, "id", None) == activity_id
        ]
    else:
        day = datetime.fromisoformat(
            str(workout_date or athlete_today(now))
        ).date()

        candidates = [
            a for a in activities
            if getattr(a, "start_time", None)
            and abs((activity_day(a.start_time) - day).days) <= 2
        ]

    target_sport = SPORT_MAP.get(
        workout.get("sport"),
        workout.get("sport")
    )

    planned_dur = float(workout.get("duration_min") or 0)

    scored = []

    for a in candidates:

        if sport_key(a.sport) != target_sport:
            continue

        actual_dur = float(a.duration_seconds or 0) / 60.0

        if planned_dur > 0:
            dur_score = max(
                0.0,
                100.0
                - abs(actual_dur - planned_dur)
                / planned_dur
                * 100.0
            )
        else:
            dur_score = 50.0

        reference_date = datetime.fromisoformat(
            str(workout_date or activity_day(a.start_time))
        ).date()

        day_difference = abs(
            (activity_day(a.start_time) - reference_date).days
        )

        day_score = max(
            0.0,
            100.0 - day_difference * 35.0
        )

        load = activity_load(a, ftp_watts)

        if load <= 0:
            load_score = 50.0
        elif planned_dur > 0:
            load_score = min(
                100.0,
                load / max(planned_dur * 1.2, 1.0) * 100.0
            )
        else:
            load_score = 50.0

        score = (
            0.55 * dur_score
            + 0.30 * day_score
            + 0.15 * load_score
        )

        scored.append(
            (
                score,
                a,
                dur_score,
                day_score,
                load_score,
            )
        )

    if not scored:
        return {
            "matched": False,
            "confidence": 0.0,
            "candidates": 0,
        }

    scored.sort(
        key=lambda x: x[0],
        reverse=True
    )

    score, a, dur_score, day_score, load_score = scored[0]

    return {
        "matched": True,
        "confidence": round(score, 1),
        "candidates": len(scored),
        "activity_id": a.id,
        "activity": a,
        "duration_score": round(dur_score, 1),
        "date_score": round(day_score, 1),
        "load_score": round(load_score, 1),
    }


def activity_to_actual(a) -> dict:
    def value(number):
        return float(number) if isinstance(number, (int,float)) and not isinstance(number,bool) and math.isfinite(number) and number >= 0 else None
    duration = value(a.duration_seconds)
    return {
        "activity_id": a.id,
        "duration_min": round(duration / 60.0, 1) if duration is not None else None,
        "avg_power_w": (
            value(a.avg_power)
            if a.avg_power is not None
            else None
        ),
        "normalized_power_w": (
            value(a.normalized_power)
            if a.normalized_power is not None
            else None
        ),
        "avg_hr_bpm": (
            value(a.avg_hr)
            if a.avg_hr is not None
            else None
        ),
        "load": (
            value(a.load)
            if a.load is not None
            else None
        ),
        "rpe": None,
    }
