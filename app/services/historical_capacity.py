from __future__ import annotations

from statistics import mean


def _sport(data: dict, key: str) -> dict:
    return data.get("sports", {}).get(key, {})


def _weeks(data: dict) -> float:
    days = float(data.get("period", {}).get("days") or 0)
    return max(days / 7.0, 1.0)


def _sessions_per_week(sport_data: dict, parent_window: dict) -> float:
    activities = float(sport_data.get("activities") or 0)
    return round(activities / _weeks(parent_window), 2)


def _confidence(recent_activities: int, yearly_activities: int) -> float:
    if recent_activities >= 20 and yearly_activities >= 40:
        return 100.0
    if recent_activities >= 10 and yearly_activities >= 20:
        return 85.0
    if recent_activities >= 5 and yearly_activities >= 10:
        return 70.0
    if recent_activities >= 2:
        return 50.0
    return 25.0


def build_historical_capacity(historical: dict) -> dict:
    if historical.get("status") != "ok":
        return {
            "status": "insufficient_data",
            "confidence": 0.0,
        }

    recent = historical.get("recent_90d", {})
    year = historical.get("recent_12m", {})
    all_time = historical.get("all_time", {})

    def capacity(s90: dict, s12: dict) -> dict:
        recent_duration = float(s90.get("avg_session_duration_min") or 0)
        yearly_duration = float(s12.get("avg_session_duration_min") or 0)

        recent_load = float(s90.get("avg_load") or 0)
        yearly_load = float(s12.get("avg_load") or 0)

        longest_recent = float(s90.get("longest_session_min") or 0)
        longest_year = float(s12.get("longest_session_min") or 0)

        recent_activities = int(s90.get("activities") or 0)
        yearly_activities = int(s12.get("activities") or 0)

        return {
            "recent_90d": {
                "activities": recent_activities,
                "sessions_per_week": _sessions_per_week(s90, recent),
                "avg_session_duration_min": round(recent_duration, 1),
                "avg_load": round(recent_load, 1),
                "longest_session_min": round(longest_recent, 1),
            },
            "recent_12m": {
                "activities": yearly_activities,
                "sessions_per_week": _sessions_per_week(s12, year),
                "avg_session_duration_min": round(yearly_duration, 1),
                "avg_load": round(yearly_load, 1),
                "longest_session_min": round(longest_year, 1),
            },
            "durability_ratio": round(
                recent_duration / yearly_duration, 2
            ) if yearly_duration > 0 else None,
            "load_ratio": round(
                recent_load / yearly_load, 2
            ) if yearly_load > 0 else None,
            "longest_session_min": round(
                max(longest_recent, longest_year), 1
            ),
            "confidence": _confidence(recent_activities, yearly_activities),
        }

    capacity = {
        "cycling": capacity(
            _sport(recent, "bike"),
            _sport(year, "bike"),
        ),
        "running": capacity(
            _sport(recent, "run"),
            _sport(year, "run"),
        ),
        "swimming": capacity(
            _sport(recent, "swim"),
            _sport(year, "swim"),
        ),
    }

    return {
        "status": "ok",
        "confidence": 100.0 if recent.get("activities", 0) >= 20 else 70.0,
        "weekly": {
            "recent_90d_hours": round(
                float(recent.get("avg_duration_hours_per_week") or 0), 2
            ),
            "recent_90d_load": round(
                float(recent.get("avg_load_per_week") or 0), 1
            ),
            "recent_90d_max_load": round(
                float(recent.get("max_weekly_load") or 0), 1
            ),
            "recent_90d_max_hours": round(
                float(recent.get("max_weekly_duration_hours") or 0), 2
            ),
            "recent_12m_hours": round(
                float(year.get("avg_duration_hours_per_week") or 0), 2
            ),
            "recent_12m_load": round(
                float(year.get("avg_load_per_week") or 0), 1
            ),
            "recent_12m_max_load": round(
                float(year.get("max_weekly_load") or 0), 1
            ),
            "all_time_max_load": round(
                float(all_time.get("max_weekly_load") or 0), 1
            ),
            "all_time_max_hours": round(
                float(all_time.get("max_weekly_duration_hours") or 0), 2
            ),
        },
        "sports": capacity,
        "learning_rules": {
            "use_recent_90d_as_primary_capacity": True,
            "use_recent_12m_as_secondary_context": True,
            "use_all_time_for_context_only": True,
            "ignore_historical_power_baseline": True,
        },
    }
