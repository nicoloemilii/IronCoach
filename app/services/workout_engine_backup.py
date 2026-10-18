from __future__ import annotations
from datetime import datetime, timedelta
from app.services.training_load import activity_load, sport_key


def _sport_key(sport: str) -> str:
    return {"cycling": "bike", "running": "run", "swimming": "swim"}.get(sport, sport)


def _recent(activities, ftp, sport, days):
    cutoff = datetime.utcnow() - timedelta(days=days)
    key = _sport_key(sport)
    return [a for a in activities if getattr(a, "start_time", None) and a.start_time >= cutoff and sport_key(a.sport) == key]


def generate_workout(activities, profile, fitness, readiness, sport="cycling", adaptation=None):
    sport = sport if sport in ("cycling", "running", "swimming") else "cycling"
    ftp = float(profile.get("cycling", {}).get("ftp_watts") or profile.get("ftp_watts") or 170)
    tsb = float(fitness.get("tsb") or 0)
    r = readiness.get("readiness")
    confidence = float(readiness.get("confidence") or 0)
    phase = readiness.get("phase") or "base"
    recent3 = _recent(activities, ftp, sport, 3)
    recent7 = _recent(activities, ftp, sport, 7)
    load7 = sum(activity_load(a, ftp) for a in recent7)

    # Do not interpret missing readiness as good readiness.
    force_easy = adaptation == "reduce_next_similar" or (r is not None and r < 40) or tsb <= -30 or len(recent3) >= 3
    if force_easy:
        kind = "recovery"
        steps = [("warmup", 10, 95), ("easy", 35, 110), ("cooldown", 10, 90)] if sport == "cycling" else [("warmup", 8, None), ("easy", 32, None), ("cooldown", 5, None)]
    elif phase in ("taper", "race_prep"):
        kind = "taper"
        steps = [("warmup", 15, 105), ("endurance", 35, 120), ("cooldown", 10, 95)] if sport == "cycling" else [("warmup", 10, None), ("easy", 25, None), ("cooldown", 5, None)]
    elif adaptation == "consider_progression" and confidence >= 60 and (r is None or r >= 65) and tsb > -20:
        kind = "endurance_progression"
        steps = [("warmup", 15, 100), ("endurance", 50, 125), ("tempo", 8, 140), ("cooldown", 12, 100)] if sport == "cycling" else [("warmup", 10, None), ("endurance", 45, None), ("tempo", 10, None), ("cooldown", 5, None)]
    elif sport == "cycling":
        kind = "endurance"
        steps = [("warmup", 15, 100), ("endurance", 60, 120), ("cooldown", 15, 100)]
    elif sport == "running":
        kind = "easy_endurance"
        steps = [("warmup", 10, None), ("easy", 45, None), ("cooldown", 5, None)]
    else:
        kind = "aerobic"
        steps = [("warmup", 10, None), ("aerobic", 35, None), ("cooldown", 5, None)]

    out = []
    for typ, mins, watts in steps:
        item = {"type": typ, "duration_min": mins}
        if sport == "cycling" and watts is not None:
            item.update(target_power_w=watts, target_power_pct_ftp=round(watts / ftp * 100, 1))
        out.append(item)

    readiness_text = "unavailable" if r is None else f"{r:.1f}"
    return {
        "sport": sport,
        "name": f"{kind.replace('_', ' ').title()} — {sport.title()}",
        "duration_min": sum(x[1] for x in steps),
        "expected_rpe": 3 if kind in ("recovery", "taper") else 4 if kind.startswith("endurance") else 5,
        "decision": {
            "readiness": r,
            "readiness_confidence": confidence,
            "tsb": round(tsb, 1),
            "phase": phase,
            "sport_7d_load": round(load7, 1),
            "sport_sessions_3d": len(recent3),
            "adaptation": adaptation or "none",
        },
        "reason": f"readiness={readiness_text}, confidence={confidence:.1f}%, TSB={tsb:.1f}, phase={phase}, sport_7d_load={load7:.1f}, sessions_3d={len(recent3)}, adaptation={adaptation or 'none'}",
        "steps": out,
    }
