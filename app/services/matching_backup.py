from __future__ import annotations
from datetime import datetime, timedelta
from app.services.training_load import activity_load, sport_key


def match_workout_activity(workout: dict, activities, workout_date: str | None = None, activity_id: int | None = None) -> dict:
    if activity_id is not None:
        candidates = [a for a in activities if getattr(a, "id", None) == activity_id]
    else:
        day = datetime.fromisoformat(str(workout_date or datetime.utcnow().date())).date()
        candidates = [a for a in activities if getattr(a, "start_time", None) and abs((a.start_time.date() - day).days) <= 2]
    target_sport = {"cycling":"bike", "running":"run", "swimming":"swim"}.get(workout.get("sport"), workout.get("sport"))
    scored = []
    planned_dur = float(workout.get("duration_min") or 0)
    for a in candidates:
        if sport_key(a.sport) != target_sport:
            continue
        actual_dur = float(a.duration_seconds or 0) / 60
        dur_score = max(0.0, 100.0 - abs(actual_dur - planned_dur) / max(planned_dur, 1) * 100.0)
        day_score = max(0.0, 100.0 - abs((a.start_time.date() - datetime.fromisoformat(str(workout_date or a.start_time.date())).date()).days) * 35.0)
        load = activity_load(a, 170)
        load_score = 50.0 if load <= 0 else min(100.0, load / max(planned_dur * 1.2, 1) * 100.0)
        score = 0.55 * dur_score + 0.30 * day_score + 0.15 * load_score
        scored.append((score, a, dur_score, day_score))
    if not scored:
        return {"matched": False, "confidence": 0.0, "candidates": 0}
    scored.sort(key=lambda x: x[0], reverse=True)
    score, a, dur_score, day_score = scored[0]
    return {"matched": True, "confidence": round(score,1), "candidates": len(scored), "activity_id": a.id, "activity": a, "duration_score": round(dur_score,1), "date_score": round(day_score,1)}


def activity_to_actual(a) -> dict:
    return {
        "activity_id": a.id,
        "duration_min": round(float(a.duration_seconds or 0) / 60, 1),
        "avg_power_w": float(a.avg_power) if a.avg_power is not None else None,
        "normalized_power_w": float(a.normalized_power) if a.normalized_power is not None else None,
        "avg_hr_bpm": float(a.avg_hr) if a.avg_hr is not None else None,
        "load": float(a.load) if a.load is not None else None,
        "rpe": None,
    }
