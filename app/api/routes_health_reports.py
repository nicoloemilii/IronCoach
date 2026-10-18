"""Personal symptom journal and automatically refreshed athlete snapshot."""
from datetime import date as Date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.health_reports import HealthReport
from app.services.athlete_clock import athlete_today
from app.services.athlete_snapshot import derived_profile
from app.services.health_reports import HealthReportInput, active_restrictions, get_report, payload, report_analysis, save_report, serialize

router = APIRouter()


def _day(day):
    value = day or athlete_today()
    if not Date(1970, 1, 1) <= value <= athlete_today():
        raise HTTPException(422, 'La data deve essere compresa fra il 1970 e oggi.')
    return value


@router.get('/health/reports')
def list_reports(end: Date | None = None, limit: int = Query(40, ge=1, le=100), db: Session = Depends(get_db)):
    end = _day(end)
    rows = db.query(HealthReport).filter(HealthReport.date <= str(end)).order_by(HealthReport.date.desc(), HealthReport.id.desc()).limit(limit).all()
    return {'reports': [serialize(row, db) for row in rows], 'restrictions': active_restrictions(db, end)}


@router.post('/health/reports', status_code=201)
def create_report(value: HealthReportInput, db: Session = Depends(get_db)):
    return serialize(save_report(db, value), db)


@router.patch('/health/reports/{identifier}')
def update_report(identifier: int, changes: dict = Body(...), db: Session = Depends(get_db)):
    row = get_report(db, identifier)
    merged = payload(row) | changes
    if changes.get('status') == 'active' and 'resolved_on' not in changes:
        merged['resolved_on'] = None
    try:
        value = HealthReportInput.model_validate(merged)
    except ValidationError as exc:
        # Pydantic's ctx can contain a ValueError, which is not JSON serializable.
        raise HTTPException(422, [{'loc': item['loc'], 'msg': item['msg'], 'type': item['type']} for item in exc.errors()])
    return serialize(save_report(db, value, row), db)


@router.delete('/health/reports/{identifier}')
def delete_report(identifier: int, db: Session = Depends(get_db)):
    row = get_report(db, identifier)
    db.delete(row)
    db.commit()
    return {'deleted': identifier}


@router.get('/health/reports/{identifier}/analysis')
def analyze_report(identifier: int, db: Session = Depends(get_db)):
    return report_analysis(db, get_report(db, identifier))


@router.get('/athlete/derived-profile')
def get_derived_profile(day: Date | None = None, db: Session = Depends(get_db)):
    return derived_profile(db, _day(day))
