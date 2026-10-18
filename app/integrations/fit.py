from datetime import datetime, timezone
from pathlib import Path

try:
    from fitparse import FitFile
except ImportError:
    FitFile = None


def _timestamp(value):
    if value is None:
        return None

    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)

    return value


def _get_session(fit):
    for record in fit.get_messages("session"):
        return {
            field.name: field.value
            for field in record
            if field.value is not None
        }

    raise ValueError("Il file FIT non contiene una sessione.")


def _calculate_elevation(fit):
    """
    Calcola il dislivello positivo dai dati di altitudine
    presenti nei record FIT.
    """

    elevations = []

    for record in fit.get_messages("record"):
        altitude = record.get_value("enhanced_altitude")

        if altitude is None:
            altitude = record.get_value("altitude")

        if altitude is not None:
            elevations.append(float(altitude))

    if len(elevations) < 2:
        return None

    ascent = 0.0

    for previous, current in zip(elevations, elevations[1:]):
        difference = current - previous

        # Ignoriamo piccole oscillazioni del sensore GPS/barometrico.
        if difference > 1.0:
            ascent += difference

    return round(ascent, 1)


def parse_fit(file_path: str | Path) -> dict:
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"File FIT non trovato: {file_path}"
        )

    fit = FitFile(str(file_path))

    session = _get_session(fit)

    sport = session.get("sport", "unknown")

    if hasattr(sport, "name"):
        sport = sport.name

    start_time = _timestamp(
        session.get("start_time")
    )

    return {
        "source": "fit",
        "source_id": file_path.stem,
        "sport": str(sport),
        "name": file_path.stem,

        "start_time": (
            start_time
            or datetime.utcnow()
        ),

        "duration_seconds": session.get(
            "total_elapsed_time"
        ),

        "distance_meters": session.get(
            "total_distance"
        ),

        "elevation_gain_meters": (
            session.get("total_ascent")
            or _calculate_elevation(fit)
        ),

        "avg_hr": session.get(
            "avg_heart_rate"
        ),

        "max_hr": session.get(
            "max_heart_rate"
        ),

        "avg_power": session.get(
            "avg_power"
        ),

        "normalized_power": session.get(
            "normalized_power"
        ),

        "avg_speed": session.get(
            "avg_speed"
        ),

        "avg_cadence": session.get(
            "avg_cadence"
        ),

        "calories": session.get(
            "total_calories"
        ),
    }