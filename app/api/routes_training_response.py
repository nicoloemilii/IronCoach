"""Read-only, workspace-scoped associations in dated training and wellness."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.training_response import training_response


router = APIRouter(tags=['Risposta osservata'])


@router.get('/dashboard/training-response')
def get_training_response(
    as_of: date | None = Query(None, description='Data limite delle osservazioni, Europe/Rome; dal 1970 a oggi.'),
    days: int = Query(84, ge=28, le=180),
    lag: int = Query(1, ge=1, le=2, description='Giorni di calendario fra le due letture, non ore misurate.'),
    db: Session = Depends(get_db),
):
    try:
        return training_response(db, as_of, days, lag)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from None
