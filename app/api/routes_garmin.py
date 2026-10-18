"""Public contract for local Garmin preparation, without pretending to connect."""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Workout
from app.services.garmin_delivery import (
    delivery_status,
    integration_status,
    request_delivery,
    validate_stored_workout,
)
from app.services.fit_export import FitExportError, encode_fit_workout, fit_export_metadata


router = APIRouter(tags=["garmin"])


def _workout(db: Session, workout_id: int) -> Workout:
    row = db.query(Workout).filter(Workout.id == workout_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="workout_not_found")
    return row


@router.get("/integrations/garmin/status")
def get_garmin_status():
    return integration_status()


@router.get("/workouts/{workout_id}/garmin/validate")
def validate(workout_id: int, db: Session = Depends(get_db)):
    result = validate_stored_workout(_workout(db, workout_id))
    result['fit_export'] = fit_export_metadata(result)
    return result


@router.get("/workouts/{workout_id}/garmin/status")
def get_delivery(workout_id: int, db: Session = Depends(get_db)):
    return delivery_status(db, _workout(db, workout_id))


@router.post("/workouts/{workout_id}/garmin/send")
def send(workout_id: int, db: Session = Depends(get_db)):
    # A blocked result is an application state, not successful remote delivery.
    return request_delivery(db, _workout(db, workout_id))


@router.get("/workouts/{workout_id}/export.json")
def export_workout(workout_id: int, db: Session = Depends(get_db)):
    validation = validate_stored_workout(_workout(db, workout_id))
    body = {
        "format": "Ironman Coach structured workout JSON",
        "garmin_importable": False,
        "notice": "Formato interno aperto, non un file FIT/TCX importabile su Garmin Connect o dispositivo.",
        "workout": validation["structured_workout"],
        "content_hash": validation["content_hash"],
        "validation": {key: validation[key] for key in ("valid", "errors", "warnings", "compatibility", "totals")},
    }
    return Response(
        content=json.dumps(body, ensure_ascii=False, indent=2, allow_nan=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="workout-{workout_id}.json"'},
    )


@router.get("/workouts/{workout_id}/export.fit")
def export_fit(workout_id: int, db: Session = Depends(get_db)):
    row = _workout(db, workout_id)
    validation = validate_stored_workout(row)
    try:
        data, metadata = encode_fit_workout(validation, created_at=row.created_at)
    except FitExportError as error:
        status = 503 if any(item['code'] == 'sdk_unavailable' for item in error.metadata['errors']) else 422
        raise HTTPException(status_code=status, detail=error.metadata)
    return Response(
        content=data, media_type="application/vnd.ant.fit",
        headers={"Content-Disposition": f'attachment; filename="workout-{workout_id}.fit"',
                 "X-Workout-Content-Hash": validation['content_hash'],
                 "X-Device-Compatibility": "untested",
                 "X-FIT-Warnings": ','.join(warning['code'] for warning in metadata['warnings'])},
    )
