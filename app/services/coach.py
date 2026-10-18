from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from app.services.athlete_profile import load_profile
from app.services.athlete_clock import activity_day, athlete_today, as_utc_naive, day_start_utc, utc_now_naive
from app.services.training_load import activity_load, fitness_metrics, fitness_metrics_by_sport, sport_key


def _valid(a) -> bool:
    try:
        duration = float(a.duration_seconds or 0)
    except (TypeError, ValueError):
        duration = 0
    return bool(a.start_time) and (duration >= 60 or float(a.load or 0) > 0)


def coach_today(activities: Iterable, ftp_watts: float | None = None, profile_override: dict | None = None) -> dict:
    profile = profile_override if profile_override is not None else load_profile()
    ftp = float(ftp_watts or profile.get("ftp_watts") or 170)
    now = utc_now_naive()
    today = athlete_today(now)
    acts = [a for a in activities if _valid(a) and as_utc_naive(a.start_time) <= now]
    acts.sort(key=lambda a: as_utc_naive(a.start_time), reverse=True)
    last = acts[0] if acts else None
    recent = [a for a in acts if as_utc_naive(a.start_time) >= day_start_utc(today - timedelta(days=6))]
    load_7 = round(sum(activity_load(a, ftp) for a in recent), 1)
    fitness = fitness_metrics(acts, ftp)
    by_sport = fitness_metrics_by_sport(acts, ftp)

    # Decision-support layer. Race phase is considered only when an event date is configured.
    tsb = fitness["tsb"]
    goals = profile.get("goals") or {}
    event_date_raw = goals.get("event_date")
    event_date = None
    days_to_event = None
    phase = "base"
    if event_date_raw:
        try:
            event_date = datetime.fromisoformat(str(event_date_raw)).date()
            days_to_event = (event_date - today).days
            if days_to_event < 0:
                phase = "post_race"
            elif days_to_event == 0:
                phase = "race_day"
            elif days_to_event <= 3:
                phase = "race_prep"
            elif days_to_event <= 7:
                phase = "taper"
            elif days_to_event <= 28:
                phase = "peak"
            else:
                phase = "base"
        except (TypeError, ValueError):
            event_date = None

    if last:
        hours = float(last.duration_seconds or 0) / 3600
        last_load = activity_load(last, ftp)
        if hours >= 2.5 or last_load >= 180:
            recommendation = "recovery"
            reason = "L'ultima seduta ha avuto un carico elevato; oggi privilegia recupero o lavoro molto leggero."
        elif tsb <= -25:
            recommendation = "easy"
            reason = "La fatica recente è elevata rispetto alla fitness; evita oggi un altro carico intenso."
        elif tsb >= 10:
            recommendation = "quality"
            reason = "Il bilancio di carico recente è relativamente favorevole a una seduta qualitativa, se il recupero percepito è buono."
        else:
            recommendation = "endurance"
            reason = "Il bilancio di carico è intermedio; una seduta aerobica controllata è una scelta neutra per il coach."
    else:
        recommendation = "easy"
        reason = "Non sono presenti attività valide sufficienti per una raccomandazione più specifica."

    return {
        "date": today,
        "profile": profile,
        "fitness": fitness,
        "fitness_by_sport": by_sport,
        "event": {
            "name": goals.get("event"),
            "date": event_date,
            "days_to_event": days_to_event,
            "phase": phase,
        },
        "recent_7d": {"sessions": len(recent), "load": load_7},
        "last_activity": None if not last else {
            "id": last.id,
            "sport": sport_key(last.sport),
            "name": last.name,
            "start_time": last.start_time,
            "duration_seconds": last.duration_seconds,
            "load": round(activity_load(last, ftp), 1),
        },
        "recommendation": recommendation,
        "reason": reason,
        "status": "decision_support",
    }
