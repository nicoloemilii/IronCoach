"""Read-only training observations in the current athlete's workspace."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.training_insights import training_insights


router = APIRouter(tags=['Tendenze osservate'])


@router.get('/dashboard/training-insights')
def get_training_insights(
    as_of: date | None = Query(None, description='Ultimo giorno del periodo, Europe/Rome; dal 1970 a oggi.'),
    db: Session = Depends(get_db),
):
    try:
        return training_insights(db, as_of)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from None
