from __future__ import annotations
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db.models import Activity
from app.services.training_load import activity_load, sport_key


def training_summary(db: Session, days: int = 28, ftp_watts: float = 170.0) -> dict:
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    activities = (
        db.query(Activity)
        .filter(Activity.start_time >= since, Activity.start_time <= now)
        .order_by(Activity.start_time.desc())
        .all()
    )
    sports = {}
    total_duration = total_distance = total_load = 0.0
    for a in activities:
        key = sport_key(a.sport)
        item = sports.setdefault(key, {"sessions": 0, "duration_seconds": 0.0, "distance_meters": 0.0, "load": 0.0})
        duration = float(a.duration_seconds or 0)
        distance = float(a.distance_meters or 0)
        load = float(activity_load(a, ftp_watts))
        item["sessions"] += 1
        item["duration_seconds"] += duration
        item["distance_meters"] += distance
        item["load"] += load
        total_duration += duration
        total_distance += distance
        total_load += load
    for item in sports.values():
        for key in ("duration_seconds", "distance_meters", "load"):
            item[key] = round(float(item[key]), 1)
    return {
        "period_days": days,
        "from": since,
        "to": now,
        "sessions": len(activities),
        "duration_seconds": round(total_duration, 1),
        "distance_meters": round(total_distance, 1),
        "load": round(total_load, 1),
        "ftp_watts": ftp_watts,
        "load_method": "bike TSS from NP/FTP when available; sport-specific Intervals.icu load otherwise",
        "sports": sports,
    }
