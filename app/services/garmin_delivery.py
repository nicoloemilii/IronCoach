"""Canonical workouts and an honest boundary for the future official connector.

There is deliberately no unofficial Garmin login, fabricated OAuth endpoint, or
network delivery here. The approved Training API contract is not yet available.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.garmin_models import GarminDelivery


SCHEMA_VERSION = "ironman-coach.workout.v1"
MAX_DEPTH = 5
MAX_EXPANDED_STEPS = 1000
SPORTS = {"bike": "cycling", "run": "running", "swim": "swimming"}
OFFICIAL_LINKS = {
    "training": "https://developer.garmin.com/gc-developer-program/training-api/",
    "activity": "https://developer.garmin.com/gc-developer-program/activity-api/",
    "health": "https://developer.garmin.com/gc-developer-program/health-api/",
    "program": "https://developer.garmin.com/gc-developer-program/program-faq/",
}


def integration_status() -> dict:
    return {
        "provider": "garmin",
        "status": "official_access_required",
        "connected": False,
        "can_send": False,
        "connector_implemented": False,
        "message": "Accesso ufficiale Garmin richiesto. Nessun account collegato e nessun invio al dispositivo attivo.",
        "action_required": [
            "Ottenere approvazione al Garmin Connect Developer Program per Training, Activity e Health API.",
            "Implementare e verificare OAuth 2.0 e contratto API dalla documentazione del portale approvato.",
            "Verificare consenso, modelli di orologio/ciclocomputer e sincronizzazione reale con Garmin Connect.",
        ],
        "device_compatibility": "unknown",
        "last_sync_at": None,
        "data_import_available": False,
        "export": {"format": "json", "schema": SCHEMA_VERSION, "garmin_importable": False},
        "fit_export": {"format": "fit", "available": importlib.util.find_spec("garmin_fit_sdk") is not None,
                       "sports": ["cycling", "running"], "device_compatibility": "untested",
                       "max_expanded_steps": 50, "cloud_delivery": False,
                       "message": "Download file allenamento FIT; richiede trasferimento manuale e prova sui dispositivi."},
        "official_links": OFFICIAL_LINKS,
    }


def _number(value: Any) -> float | None:
    # bool is a subclass of int but is never an intensity/duration.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _issue(issues: list, path: str, code: str, message: str) -> None:
    issues.append({"path": path, "code": code, "message": message})


def _duration(step: dict, path: str, errors: list) -> dict | None:
    duration = step.get("duration")
    if isinstance(duration, dict):
        kind, value, unit = duration.get("type"), duration.get("value"), duration.get("unit")
        if kind == "time" and unit in ("s", "seconds", "min", "minutes"):
            factor, canonical_unit = (60 if unit in ("min", "minutes") else 1), "s"
        elif kind == "distance" and unit in ("m", "meters", "km", "kilometers"):
            factor, canonical_unit = (1000 if unit in ("km", "kilometers") else 1), "m"
        else:
            _issue(errors, path + ".duration", "duration_unit_invalid", "Durata: specificare tempo in s/min o distanza in m/km.")
            return None
    elif "duration_min" in step:
        kind, value, factor, canonical_unit = "time", step["duration_min"], 60, "s"
    elif "duration_seconds" in step:
        kind, value, factor, canonical_unit = "time", step["duration_seconds"], 1, "s"
    elif "distance_meters" in step or "distance_m" in step:
        kind, value, factor, canonical_unit = "distance", step.get("distance_meters", step.get("distance_m")), 1, "m"
    else:
        _issue(errors, path + ".duration", "duration_missing", "Specificare una durata o distanza per ogni fase.")
        return None
    number = _number(value)
    limit = 86400 if kind == "time" else 1_000_000
    if number is None or not 0 < number * factor <= limit:
        _issue(errors, path + ".duration", "duration_invalid", "La durata/distanza deve essere positiva, finita e nei limiti del formato.")
        return None
    return {"type": kind, "value": round(number * factor, 6), "unit": canonical_unit}


def _target(step: dict, path: str, errors: list, warnings: list) -> dict | None:
    target = step.get("target")
    if target is None:
        for field, kind, unit in (
            ("target_power_w", "power", "W"),
            ("target_power_pct_ftp", "power", "percent_ftp"),
            ("target_hr_bpm", "heart_rate", "bpm"),
            ("target_pace_sec_km", "pace", "s/km"),
            ("target_rpe", "rpe", "score"),
        ):
            if field in step and step[field] is not None:
                target = {"type": kind, "value": step[field], "unit": unit}
                break
    if not isinstance(target, dict):
        _issue(errors, path + ".target", "target_missing", "Obiettivo mancante: impostare potenza, FC, passo, RPE o esplicitamente libero.")
        return None
    kind = target.get("type")
    if kind == "open":
        _issue(warnings, path + ".target", "open_target", "Fase senza vincolo di intensità, dichiarata esplicitamente libera.")
        return {"type": "open"}
    units = {
        "power": {"W": 3000, "percent_ftp": 300},
        "heart_rate": {"bpm": 250, "percent_max_hr": 100},
        "pace": {"s/km": 7200, "s/100m": 7200},
        "rpe": {"score": 10},
        "cadence": {"rpm": 300},
    }
    unit = target.get("unit", "score" if kind == "rpe" else None)
    if not isinstance(kind, str) or kind not in units or not isinstance(unit, str) or unit not in units[kind]:
        _issue(errors, path + ".target", "target_unit_invalid", "Tipo o unità dell'obiettivo non supportati dallo schema.")
        return None
    has_value = "value" in target
    has_range = "min" in target or "max" in target
    if has_value == has_range:
        _issue(errors, path + ".target", "target_shape_invalid", "Usare un valore oppure un intervallo min/max completo.")
        return None
    keys = ("value",) if has_value else ("min", "max")
    values = [_number(target.get(key)) for key in keys]
    minimum = 1 if kind == "rpe" else 0
    if any(value is None or value < minimum or value <= 0 or value > units[kind][unit] for value in values):
        _issue(errors, path + ".target", "target_value_invalid", "I valori obiettivo devono essere positivi, finiti e nei limiti del formato.")
        return None
    if has_range and values[0] > values[1]:
        _issue(errors, path + ".target", "target_range_invalid", "Il minimo deve essere minore o uguale al massimo.")
        return None
    normalized = {"type": kind, "unit": unit, **dict(zip(keys, values))}
    if kind == "rpe":
        _issue(warnings, path + ".target", "rpe_device_support_unknown", "RPE è un'indicazione per l'atleta; la traduzione in un target Garmin va verificata.")
    return normalized


def normalize_workout(payload: Any, *, workout_id=None, scheduled_date=None) -> dict:
    """Normalize legacy/canonical steps without guessing missing intensities.

    The return value is safe to serialize even when a stored payload is malformed.
    Bounds keep hostile/accidental nested repeats small without expanding them.
    """
    errors, warnings = [], []
    if not isinstance(payload, dict):
        payload = {}
        _issue(errors, "$", "payload_invalid", "L'allenamento deve essere un oggetto JSON.")
    raw_sport = payload.get("sport")
    sport = SPORTS.get(raw_sport, raw_sport) if isinstance(raw_sport, str) else None
    if sport not in ("cycling", "running", "swimming", "strength"):
        _issue(errors, "sport", "sport_invalid", "Disciplina non supportata dallo schema.")
        sport = "unknown"
    raw_name = payload.get("name")
    name = raw_name.strip()[:200] if isinstance(raw_name, str) else ""
    if not name:
        _issue(errors, "name", "name_missing", "Dare un nome all'allenamento.")

    def walk(steps, path, depth=0):
        if not isinstance(steps, list) or not steps:
            _issue(errors, path, "steps_missing", "Inserire almeno una fase.")
            return [], 0, 0.0, 0.0
        if len(steps) > MAX_EXPANDED_STEPS or depth > MAX_DEPTH:
            _issue(errors, path, "structure_too_large", "Struttura oltre i limiti di fasi o profondità.")
            return [], 0, 0.0, 0.0
        normalized, count, seconds, meters = [], 0, 0.0, 0.0
        for index, step in enumerate(steps):
            current_path = f"{path}[{index}]"
            if not isinstance(step, dict):
                _issue(errors, current_path, "step_invalid", "Ogni fase deve essere un oggetto.")
                continue
            if step.get("type") == "repeat":
                repeats = step.get("repeat_count", step.get("count"))
                if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 100:
                    _issue(errors, current_path, "repeat_invalid", "Le ripetizioni devono essere un intero tra 1 e 100.")
                    continue
                children, child_count, child_seconds, child_meters = walk(step.get("steps"), current_path + ".steps", depth + 1)
                count += child_count * repeats
                seconds += child_seconds * repeats
                meters += child_meters * repeats
                normalized.append({"type": "repeat", "repeat_count": repeats, "steps": children})
            else:
                kind = step.get("type", "active")
                if not isinstance(kind, str) or not kind or len(kind) > 80:
                    kind = "active"
                    _issue(errors, current_path + ".type", "step_type_invalid", "Tipo di fase non valido.")
                duration = _duration(step, current_path, errors)
                target = _target(step, current_path, errors, warnings)
                normalized.append({"type": kind, "duration": duration, "target": target})
                count += 1
                if duration:
                    if duration["type"] == "time":
                        seconds += duration["value"]
                    else:
                        meters += duration["value"]
            if count > MAX_EXPANDED_STEPS:
                _issue(errors, path, "expanded_steps_exceeded", f"Massimo {MAX_EXPANDED_STEPS} fasi dopo le ripetizioni.")
                break
        return normalized, count, seconds, meters

    steps, count, seconds, meters = walk(payload.get("steps"), "steps")
    if seconds > 86400:
        _issue(errors, "steps", "total_duration_exceeded", "Il tempo complessivo supera 24 ore.")
    if meters > 1_000_000:
        _issue(errors, "steps", "total_distance_exceeded", "La distanza complessiva supera il limite del formato.")
    if payload.get("duration_min") is not None and not meters:
        declared = _number(payload["duration_min"])
        if declared is not None and abs(declared * 60 - seconds) > 1:
            _issue(warnings, "duration_min", "duration_mismatch", "La durata dichiarata differisce dalla somma delle fasi: prevalgono le fasi.")
    day = scheduled_date or payload.get("date")
    if day is not None:
        try:
            from datetime import date
            day = date.fromisoformat(str(day)).isoformat()
        except (ValueError, TypeError):
            day = None
            _issue(errors, "date", "date_invalid", "La data deve essere in formato YYYY-MM-DD.")
    structured = {"schema_version": SCHEMA_VERSION, "name": name, "sport": sport, "date": day, "steps": steps}
    content_hash = hashlib.sha256(json.dumps(structured, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    return {
        "workout_id": workout_id,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "compatibility": "unknown",
        "compatibility_message": "Modello e supporto dei target da verificare con l'integrazione ufficiale Garmin.",
        "content_hash": content_hash,
        "structured_workout": structured,
        "totals": {"time_seconds": round(seconds, 6), "distance_meters": round(meters, 6), "expanded_steps": count},
        "garmin_importable": False,
    }


def validate_stored_workout(workout) -> dict:
    try:
        payload = json.loads(workout.payload_json)
    except (ValueError, TypeError, RecursionError):
        payload = None
    return normalize_workout(payload, workout_id=workout.id, scheduled_date=workout.date)


def _delivery_dict(row: GarminDelivery) -> dict:
    return {
        "id": row.id,
        "content_hash": row.content_hash,
        "status": row.status,
        "reason_code": row.reason_code,
        "remote_published": bool(row.remote_published),
        "request_count": row.request_count,
        "created_at": row.created_at.isoformat() + "Z",
        "updated_at": row.updated_at.isoformat() + "Z",
    }


def delivery_status(db: Session, workout, validation: dict | None = None) -> dict:
    validation = validation or validate_stored_workout(workout)
    rows = db.query(GarminDelivery).filter(GarminDelivery.workout_id == workout.id).order_by(GarminDelivery.id.desc()).all()
    current = next((row for row in rows if row.content_hash == validation["content_hash"]), None)
    message = "Nessun allenamento pubblicato su Garmin Connect. L'accesso ufficiale e il connettore devono essere attivati."
    if current and current.reason_code == "invalid_workout":
        message = "Invio bloccato: correggere le fasi dell'allenamento. Nessun dato inviato a Garmin."
    return {
        "workout_id": workout.id,
        "provider": "garmin",
        "status": current.status if current else "not_sent",
        "reason_code": current.reason_code if current else None,
        "remote_published": False,
        "device_status": "unknown",
        "content_hash": validation["content_hash"],
        "content_changed": bool(rows and not current),
        "delivery": _delivery_dict(current) if current else None,
        "history": [_delivery_dict(row) for row in rows],
        "message": message,
        "action_required": integration_status()["action_required"],
    }


def request_delivery(db: Session, workout) -> dict:
    """Persist one blocked record per content revision; never mutate a remote API."""
    validation = validate_stored_workout(workout)
    content_hash = validation["content_hash"]
    row = db.query(GarminDelivery).filter_by(workout_id=workout.id, content_hash=content_hash).first()
    reason = "official_access_required" if validation["valid"] else "invalid_workout"
    reused = row is not None
    if row:
        db.query(GarminDelivery).filter(GarminDelivery.id == row.id).update({
            GarminDelivery.request_count: GarminDelivery.request_count + 1,
            GarminDelivery.updated_at: datetime.utcnow(),
        })
    else:
        row = GarminDelivery(workout_id=workout.id, content_hash=content_hash, reason_code=reason, status="blocked", remote_published=False)
        db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # The uniqueness constraint is the final guard for simultaneous clicks.
        db.rollback()
        row = db.query(GarminDelivery).filter_by(workout_id=workout.id, content_hash=content_hash).one()
        db.query(GarminDelivery).filter(GarminDelivery.id == row.id).update({
            GarminDelivery.request_count: GarminDelivery.request_count + 1,
            GarminDelivery.updated_at: datetime.utcnow(),
        })
        db.commit()
        reused = True
    result = delivery_status(db, workout, validation)
    result["idempotent_replay"] = reused
    result["validation"] = validation
    if not validation["valid"]:
        result["message"] = "Invio bloccato: correggere le fasi dell'allenamento. Nessun dato inviato a Garmin."
    return result
