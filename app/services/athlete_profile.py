from __future__ import annotations

from pathlib import Path
import json
from copy import deepcopy
import os
from threading import RLock
from typing import Any
from app.db.database import DATA_DIR

PROFILE_PATH = DATA_DIR / 'athlete_profile.json'
PROFILE_LOCK = RLock()

DEFAULT_PROFILE: dict[str, Any] = {
    "name": None,
    "age": None,
    "weight_kg": 55.0,
    "height_cm": 175.0,
    "ftp_watts": 170.0,
    "cycling": {"ftp_watts": 170.0, "threshold_hr_bpm": None, "max_hr_bpm": None},
    "running": {"threshold_pace_sec_per_km": None, "threshold_hr_bpm": None, "max_hr_bpm": None},
    "swimming": {"css_sec_per_100m": None},
    "goals": {"event": "Ironman Cervia 2027", "event_date": "2027-09-20", "target_time": None},
}


def _merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_profile() -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        return deepcopy(DEFAULT_PROFILE)
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        return _merge(DEFAULT_PROFILE, data)
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_PROFILE)


def save_profile(data: dict[str, Any]) -> dict[str, Any]:
    profile = _merge(DEFAULT_PROFILE, data)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROFILE_LOCK:
        temporary = PROFILE_PATH.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, PROFILE_PATH)
    return profile
