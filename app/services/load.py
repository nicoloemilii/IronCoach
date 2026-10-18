from __future__ import annotations
import math
from typing import Any

def _positive(value: Any) -> float | None:
    try: number = float(value)
    except (TypeError, ValueError): return None
    return number if math.isfinite(number) and number > 0 else None

def calculate_load(data: dict, ftp_watts: float = 170.0) -> float | None:
    duration = _positive(data.get("duration_seconds"))
    if duration is None: return None
    hours = duration / 3600.0
    sport = str(data.get("sport") or "").lower()
    np = _positive(data.get("normalized_power")); ftp = _positive(ftp_watts)
    if sport in {"cycling", "bike", "biking"} and np and ftp:
        return round(100.0 * hours * (np / ftp) ** 2, 1)
    multiplier = {"running": 1.0, "run": 1.0, "swimming": 0.8, "swim": 0.8}.get(sport, 0.7)
    return round(hours * 60.0 * multiplier, 1)
