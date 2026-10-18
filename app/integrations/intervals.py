from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import time

import requests
from app.integrations import intervals_credentials
from app.services.athlete_clock import athlete_today, as_utc_naive, local_stamp_to_utc_naive, utc_now_naive

BASE_URL = "https://intervals.icu/api/v1"


def _auth():
    key = intervals_credentials.credentials().get('api_key', '')
    if not key:
        raise RuntimeError("INTERVALS_API_KEY non configurata")
    return ("API_KEY", key)


class IntervalsError(Exception):
    def __init__(self, message, status=502):
        self.message = message; self.status = status
        super().__init__(message)


def get_remote(resource, params=None, configuration=None):
    import re
    try:
        config = configuration if configuration is not None else intervals_credentials.credentials()
    except (RuntimeError, OSError, ValueError):
        raise IntervalsError('Impossibile leggere la chiave protetta. Ricollega Intervals.icu.', 503) from None
    key = config.get('api_key', '')
    athlete_id = config.get('athlete_id', '0')
    if not key:
        raise IntervalsError('Collega Intervals.icu inserendo la chiave API in Connessioni.', 409)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', athlete_id):
        raise IntervalsError('ID atleta non valido.', 422)
    for attempt in range(2):
        try:
            response = requests.get(f'{BASE_URL}/athlete/{athlete_id}{resource}', params=params,
                                    auth=('API_KEY', key), timeout=20, allow_redirects=False)
        except requests.RequestException:
            if attempt == 0:
                time.sleep(.5); continue
            raise IntervalsError('Intervals.icu non è raggiungibile. Controlla la connessione e riprova.') from None
        if response.status_code >= 500 and attempt == 0:
            time.sleep(.5); continue
        break
    if response.status_code == 401:
        raise IntervalsError('Chiave API non valida o revocata. Ricollega Intervals.icu.', 401)
    if response.status_code == 403:
        raise IntervalsError('Intervals.icu non autorizza l’accesso a questi dati.', 403)
    if response.status_code == 429:
        raise IntervalsError('Limite di richieste Intervals.icu raggiunto. Attendi prima di riprovare.', 429)
    if response.status_code != 200:
        raise IntervalsError('Intervals.icu non ha completato la richiesta. Riprova più tardi.')
    try:
        return response.json()
    except ValueError:
        raise IntervalsError('Intervals.icu ha restituito una risposta non valida.') from None


def verify_connection(configuration=None):
    result = get_remote('', configuration=configuration)
    if not isinstance(result, dict) or not result.get('id'):
        raise IntervalsError('Impossibile verificare il profilo atleta Intervals.icu.')
    return {'id': str(result['id']), 'name': str(result.get('name') or result.get('firstname') or result['id'])[:200]}


def _dt(value, *, naive_is_utc=False):
    """Return naive UTC; local values without offset use the app's Rome timezone.

    The explicit start_date UTC field can opt out of the local fallback. Aware
    timestamps always retain their actual instant regardless of field name.
    """
    if not value:
        return None
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return as_utc_naive(stamp) if naive_is_utc else local_stamp_to_utc_naive(stamp)


def _sport(activity: dict[str, Any]) -> str:
    value = str(activity.get("type") or activity.get("sport") or "unknown").lower()
    mapping = {"ride": "cycling", "bike": "cycling", "run": "running", "swim": "swimming"}
    return mapping.get(value, value)


def list_activities(days: int = 3650) -> list[dict[str, Any]]:
    end = athlete_today()
    start = end - timedelta(days=days)
    params = {
        "oldest": start.strftime("%Y-%m-%d"),
        "newest": end.strftime("%Y-%m-%d"),
    }
    result = get_remote('/activities', params=params)
    if not isinstance(result, list):
        raise IntervalsError('L’elenco attività ricevuto non è valido.')
    return result


def activity_to_data(activity: dict[str, Any]) -> dict[str, Any]:
    activity_id = str(activity["id"])
    return {
        "source": "intervals",
        "source_id": activity_id,
        "sport": _sport(activity),
        "name": activity.get("name") or activity_id,
        "start_time": (_dt(activity.get("start_date"), naive_is_utc=True) if activity.get("start_date") else _dt(activity.get("start_date_local"))) or utc_now_naive(),
        "duration_seconds": activity.get("moving_time") or activity.get("elapsed_time") or activity.get("duration"),
        "distance_meters": activity.get("distance"),
        "elevation_gain_meters": next((activity[k] for k in ('total_elevation_gain', 'elevation_gain') if activity.get(k) is not None), None),
        "avg_hr": activity.get("average_heartrate") or activity.get("avg_hr"),
        "max_hr": activity.get("max_heartrate") or activity.get("max_hr"),
        "avg_power": activity.get("icu_average_watts") or activity.get("average_power") or activity.get("avg_power"),
        "normalized_power": activity.get("icu_weighted_avg_watts") or activity.get("icu_np") or activity.get("normalized_power") or activity.get("np"),
        "avg_speed": activity.get("average_speed") or activity.get("avg_speed"),
        "avg_cadence": activity.get("average_cadence") or activity.get("avg_cadence"),
        "calories": activity.get("calories"),
        # Intervals.icu analyzed sport-specific training load. Keep it as the
        # authoritative load for non-cycling sports and as a fallback.
        "load": next((activity[k] for k in ('icu_training_load', 'training_load', 'load') if activity.get(k) is not None), None),
    }
