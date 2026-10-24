"""Historical power records and explicit, cancellable archive analysis."""
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services import effort_jobs

router = APIRouter(tags=['Record personali'])


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    mode: Literal['stored', 'intervals'] = 'stored'
    refresh: bool = False


@router.get('/athlete/effort-records')
def get_records(
    response: Response,
    period: Literal['all_time', 'last_90_days', 'last_28_days'] = Query('all_time'),
    sport: Literal['cycling', 'running', 'swimming', 'strength', 'other'] = Query('cycling'),
    db: Session = Depends(get_db),
):
    from app.services.effort_records import records, provider_available
    response.headers['Cache-Control'] = 'private, no-store'
    result = records(db, period=period, sport=sport)
    result.update(provider={'can_fetch': provider_available()}, **effort_jobs.scans(db))
    return result


@router.post('/athlete/effort-records/scans')
def start_scan(body: ScanRequest, response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'private, no-store'
    return effort_jobs.launch(db, mode=body.mode, refresh=body.refresh)


@router.get('/athlete/effort-records/scans/{identifier}')
def get_scan(identifier: str, response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'private, no-store'
    return effort_jobs.get_scan(db, identifier)


@router.post('/athlete/effort-records/scans/{identifier}/cancel')
def cancel_scan(identifier: str, response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'private, no-store'
    return effort_jobs.cancel(db, identifier)
