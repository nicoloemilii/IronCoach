"""Encode bounded bike/run workouts with the official Garmin FIT Python SDK.

Specification: https://developer.garmin.com/fit/articles/file-types/workout.html
Encoder contract: https://github.com/garmin/fit-python-sdk#encoder
Dynamic subfields are written through their main raw fields: Python's Encoder
deliberately ignores subfield names. No USB, account, cloud, or device mutation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util


MAX_FIT_STEPS = 50
SUPPORTED_SPORTS = ("cycling", "running")


class FitExportError(ValueError):
    def __init__(self, metadata: dict):
        self.metadata = metadata
        super().__init__(metadata.get("message", "FIT export unavailable"))


def _text(value: str, max_bytes: int) -> str:
    cleaned = "".join(character for character in value if ord(character) >= 32 and ord(character) != 127)
    return cleaned.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _flatten(steps: list, output: list) -> None:
    for step in steps:
        if step["type"] == "repeat":
            for _ in range(step["repeat_count"]):
                _flatten(step["steps"], output)
                if len(output) > MAX_FIT_STEPS:
                    return
        else:
            output.append(step)
        if len(output) > MAX_FIT_STEPS:
            return


def _target_fields(target: dict) -> tuple[dict, str, str | None]:
    kind = target["type"]
    if kind == "open":
        return {"target_type": "open", "target_value": 0}, "Intensita libera.", None
    low = target.get("value", target.get("min"))
    high = target.get("value", target.get("max"))
    unit = target.get("unit")
    if kind == "rpe":
        # FIT has no generic RPE workout target. Instructions are retained, and
        # this loss of machine-enforced intensity is explicit in metadata/notes.
        cue = f"RPE {low:g}" + (f"-{high:g}" if high != low else "") + "/10; target libero, seguire lo sforzo percepito."
        return {"target_type": "open", "target_value": 0}, cue, "rpe_as_open_target"
    if kind == "power" and unit in ("W", "percent_ftp"):
        fit_kind, scale, offset = "power", 1, 1000 if unit == "W" else 0
    elif kind == "heart_rate" and unit in ("bpm", "percent_max_hr"):
        fit_kind, scale, offset = "heart_rate", 1, 100 if unit == "bpm" else 0
    elif kind == "cadence" and unit == "rpm":
        fit_kind, scale, offset = "cadence", 1, 0
    elif kind == "pace" and unit == "s/km":
        # Slower pace is the lower speed. Raw FIT speed units are 1/1000 m/s.
        return {"target_type": "speed", "target_value": 0,
                "custom_target_value_low": round(1_000_000 / high),
                "custom_target_value_high": round(1_000_000 / low)}, f"Passo {low:g}-{high:g} s/km.", None
    else:
        raise ValueError("Target non esportabile in FIT per bici/corsa.")
    if round(low * scale) <= 0 or round(high * scale) <= 0:
        raise ValueError("Target inferiore alla precisione intera FIT: non viene arrotondato a zero.")
    return {"target_type": fit_kind, "target_value": 0,
            "custom_target_value_low": round(low * scale) + offset,
            "custom_target_value_high": round(high * scale) + offset}, f"Target {low:g}-{high:g} {unit}.", None


def _prepare(validation: dict) -> tuple[list[dict], dict]:
    errors = list(validation.get("errors") or [])
    warnings = [{"code": "device_untested", "message": "File verificato nel software, importazione/esecuzione non ancora provate su Forerunner 955 o Edge 530."}]
    workout = validation.get("structured_workout") or {}
    sport = workout.get("sport")
    if not validation.get("valid"):
        errors.append({"code": "invalid_workout", "message": "Correggere gli errori strutturali prima di esportare."})
    if sport not in SUPPORTED_SPORTS:
        errors.append({"code": "sport_not_supported", "message": "Esportazione FIT disponibile per bici e corsa. Nuoto/forza richiedono una struttura specifica verificata."})
    flattened = []
    if not errors:
        _flatten(workout["steps"], flattened)
        if len(flattened) > MAX_FIT_STEPS:
            errors.append({"code": "too_many_fit_steps", "message": f"Massimo {MAX_FIT_STEPS} fasi dopo l'espansione delle ripetizioni."})
        if any(step["type"] == "repeat" for step in workout["steps"]):
            warnings.append({"code": "repeats_flattened", "message": "Le ripetizioni sono esportate come sequenza equivalente di singole fasi."})
    messages = []
    intensity = {"warmup": "warmup", "cooldown": "cooldown", "recovery": "recovery", "rest": "rest", "interval": "interval"}
    warning_codes = {warning["code"] for warning in warnings}
    for index, step in enumerate(flattened[:MAX_FIT_STEPS]):
        duration = step["duration"]
        scale = 1000 if duration["type"] == "time" else 100
        raw_duration = round(duration["value"] * scale)
        if not 0 < raw_duration < 0xFFFFFFFF:
            errors.append({"code": "duration_precision_unsupported", "message": f"Fase {index + 1}: durata/distanza fuori dalla precisione FIT."})
            continue
        try:
            target_fields, cue, warning = _target_fields(step["target"])
        except ValueError as error:
            errors.append({"code": "target_not_supported", "message": f"Fase {index + 1}: {error}"})
            continue
        if warning and warning not in warning_codes:
            warnings.append({"code": warning, "message": "RPE conservato nelle note; il dispositivo riceve un target aperto, senza allarmi di intensità RPE."})
            warning_codes.add(warning)
        target = step['target']
        if target.get('type') in ('power', 'heart_rate', 'cadence'):
            values = [target['value']] if 'value' in target else [target['min'], target['max']]
            if any(value != round(value) for value in values) and 'integer_target_rounding' not in warning_codes:
                warnings.append({'code': 'integer_target_rounding', 'message': 'Potenza, frequenza cardiaca o cadenza arrotondate alla precisione intera FIT.'})
                warning_codes.add('integer_target_rounding')
        messages.append({"message_index": index, "wkt_step_name": _text(f"{index + 1}. {step['type']}", 31),
                         "intensity": intensity.get(step["type"], "active"),
                         "duration_type": duration["type"], "duration_value": raw_duration,
                         **target_fields, "notes": _text(cue, 180)})
    sdk_available = importlib.util.find_spec("garmin_fit_sdk") is not None
    if not sdk_available:
        errors.append({"code": "sdk_unavailable", "message": "Installare la dipendenza ufficiale garmin-fit-sdk dalle requirements del progetto."})
    metadata = {"available": not errors, "format": "fit", "file_type": "workout", "sport": sport,
                "errors": errors, "warnings": warnings,
                "expanded_steps": validation.get('totals', {}).get('expanded_steps', len(flattened)),
                "max_steps": MAX_FIT_STEPS, "content_hash": validation.get("content_hash"),
                "device_compatibility": "untested", "cloud_delivery": False,
                "calendar_scheduled": False, "sdk": "garmin-fit-sdk",
                "message": "Download FIT locale; nessun invio automatico, calendario o presenza sul dispositivo confermati."}
    return messages, metadata


def fit_export_metadata(validation: dict) -> dict:
    """Explain eligibility and any semantic conversions before download."""
    return _prepare(validation)[1]


def encode_fit_workout(validation: dict, *, created_at: datetime | None = None) -> tuple[bytes, dict]:
    steps, metadata = _prepare(validation)
    if not metadata["available"]:
        raise FitExportError(metadata)
    from garmin_fit_sdk import Decoder, Encoder, Profile, Stream

    workout = validation["structured_workout"]
    encoder = Encoder()
    stamp = created_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    serial = int(validation["content_hash"][:8], 16) or 1
    encoder.on_mesg(Profile['mesg_num']['FILE_ID'], {
        'type': 'workout', 'manufacturer': 'development', 'product': 1,
        'serial_number': serial, 'time_created': stamp,
    })
    encoder.on_mesg(Profile['mesg_num']['WORKOUT'], {
        'sport': workout['sport'], 'num_valid_steps': len(steps),
        'wkt_name': _text(workout['name'], 63),
        'wkt_description': 'Ironman Coach. RPE in notes; manual transfer; device untested.',
    })
    for step in steps:
        encoder.on_mesg(Profile['mesg_num']['WORKOUT_STEP'], step)
    data = bytes(encoder.close())
    if not Decoder(Stream.from_byte_array(bytearray(data))).check_integrity():
        raise RuntimeError('La verifica di integrità del file FIT generato non è riuscita.')
    return data, metadata
