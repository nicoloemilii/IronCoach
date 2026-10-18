from __future__ import annotations
from datetime import datetime, timedelta
from app.services.training_load import activity_load, sport_key


def _sport_key(sport: str) -> str:
    return {"cycling": "bike", "running": "run", "swimming": "swim"}.get(sport, sport)


def _recent(activities, ftp, sport, days):
    cutoff = datetime.utcnow() - timedelta(days=days)
    key = _sport_key(sport)
    return [a for a in activities if getattr(a, "start_time", None) and a.start_time >= cutoff and sport_key(a.sport) == key]


def _historical_duration(historical_sport: dict, default_min: float, progression: bool = False) -> int:
    recent = historical_sport.get("recent_90d", {})
    confidence = float(historical_sport.get("confidence") or 0)
    avg_duration = float(recent.get("avg_session_duration_min") or 0)
    longest = float(historical_sport.get("longest_session_min") or 0)

    if confidence < 50 or avg_duration <= 0:
        return int(default_min)

    target = avg_duration * (1.10 if progression else 1.0)
    if longest > 0:
        target = min(target, longest)

    return max(int(default_min), int(round(target / 5.0) * 5))


def generate_workout(activities, profile, fitness, readiness, sport="cycling", adaptation=None, adaptation_confidence=0.0, historical_capacity=None, decision=None):
    sport = sport if sport in ("cycling", "running", "swimming") else "cycling"
    ftp = float(profile.get("cycling", {}).get("ftp_watts") or profile.get("ftp_watts") or 170)
    tsb = float(fitness.get("tsb") or 0)
    r = readiness.get("readiness")
    readiness_confidence = float(readiness.get("confidence") or 0)
    adaptation_confidence = float(adaptation_confidence or 0)
    decision = decision or {}
    recommendation = decision.get("recommendation") or adaptation or "endurance"
    phase = readiness.get("phase") or "base"
    recent3 = _recent(activities, ftp, sport, 3)
    recent7 = _recent(activities, ftp, sport, 7)
    load7 = sum(activity_load(a, ftp) for a in recent7)

    historical = historical_capacity or {}
    historical_sport = historical.get("sports", {}).get(sport, {})
    historical_90 = historical_sport.get("recent_90d", {})
    historical_12 = historical_sport.get("recent_12m", {})
    historical_confidence = float(historical_sport.get("confidence") or 0)
    historical_longest = float(historical_sport.get("longest_session_min") or 0)
    historical_avg_duration = float(historical_90.get("avg_session_duration_min") or 0)
    historical_avg_load = float(historical_90.get("avg_load") or 0)

    # Do not interpret missing readiness as good readiness.
    force_easy = recommendation in ("recovery", "easy") or adaptation == "reduce_next_similar" or (r is not None and r < 40) or tsb <= -30 or len(recent3) >= 3
    if force_easy:
        kind = "recovery"
        steps = [("warmup", 10, 95), ("easy", 35, 110), ("cooldown", 10, 90)] if sport == "cycling" else [("warmup", 8, None), ("easy", 32, None), ("cooldown", 5, None)]
    elif phase in ("taper", "race_prep"):
        kind = "taper"
        steps = [("warmup", 15, 105), ("endurance", 35, 120), ("cooldown", 10, 95)] if sport == "cycling" else [("warmup", 10, None), ("easy", 25, None), ("cooldown", 5, None)]
    elif recommendation == "progression" and adaptation_confidence >= 60 and (r is None or r >= 65) and tsb > -20:
        progression_duration = _historical_duration(historical_sport, 85, progression=True)
        kind = "endurance_progression"
        if sport == "cycling":
            warmup = 15
            cooldown = 12
            main_total = max(20, progression_duration - warmup - cooldown)
            endurance = round(main_total * 0.86)
            tempo = main_total - endurance
            steps = [
                ("warmup", warmup, 100),
                ("endurance", endurance, 125),
                ("tempo", tempo, 140),
                ("cooldown", cooldown, 100),
            ]
        else:
            steps = [
                ("warmup", 10, None),
                ("endurance", 45, None),
                ("tempo", 10, None),
                ("cooldown", 5, None),
            ]
    elif recommendation == "quality" and sport == "cycling":
        kind = "quality"
        steps = [("warmup", 15, 100), ("endurance", 15, 125), ("interval", 5, 170), ("recovery", 5, 105), ("interval", 5, 175), ("cooldown", 10, 95)]
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
        if sport == "cycling" and watts is not None and profile.get('_power_targets_available', True):
            # Templates were expressed against 170 W; scale to the athlete's FTP.
            ratio = watts / 170.0
            item.update(target_power_w=round(ftp * ratio), target_power_pct_ftp=round(ratio * 100, 1))
        item['target_rpe'] = 2 if typ in ('warmup', 'cooldown', 'recovery', 'easy') else 6 if typ == 'interval' else 4
        item['instruction'] = 'Ritmo controllato; usa lo sforzo percepito se mancano sensori o soglie verificate.'
        out.append(item)

    policy = decision.get('session_policy') or {}
    cap = policy.get('max_duration_min')
    if cap is not None:
        minutes = min(sum(step['duration_min'] for step in out), cap)
        out = [] if not minutes else [dict(type='warmup', duration_min=5, target_rpe=2),
            dict(type='easy', duration_min=max(0, minutes-10), target_rpe=2),
            dict(type='cooldown', duration_min=min(5, minutes), target_rpe=2)]
        kind = 'recovery'

    readiness_text = "unavailable" if r is None else f"{r:.1f}"
    return {
        "sport": sport,
        "name": f"{kind.replace('_', ' ').title()} - {sport.title()}",
        "duration_min": sum(x["duration_min"] for x in out),
        "expected_rpe": min(3,policy.get("max_rpe") or 3) if kind in ("recovery", "taper") else 4 if kind.startswith("endurance") else 5,
        "decision": {
            "readiness": r,
            "readiness_confidence": readiness_confidence,
            "tsb": round(tsb, 1),
            "phase": phase,
            "sport_7d_load": round(load7, 1),
            "sport_sessions_3d": len(recent3),
            "adaptation": adaptation or "none",
            "recommendation": recommendation,
            "decision_confidence": float(decision.get("confidence") or adaptation_confidence),
            "historical_confidence": historical_confidence,
            "historical_avg_duration_min": round(historical_avg_duration, 1),
            "historical_longest_session_min": round(historical_longest, 1),
            "historical_avg_load": round(historical_avg_load, 1),
        },
        "reason": decision.get("reasons", [f"readiness={readiness_text}, TSB={tsb:.1f}"])[0],
        "steps": out,
    }













