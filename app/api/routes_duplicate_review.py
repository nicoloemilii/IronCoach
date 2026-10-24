"""Preview and private journal for explicitly confirmed duplicate reviews."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field
from app.api.routes_sync import local_only
from app.db.database import get_db
from app.services import duplicate_review


router = APIRouter(tags=['Revisione duplicati'])


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state_token: str | None = Field(None, pattern=r'^[a-f0-9]{64}$')


@router.get('/activities/duplicates/review-history')
def review_history(limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    return duplicate_review.history(db, limit)


@router.get('/activities/duplicates/{identifier}/preview')
def preview(identifier: int, db: Session = Depends(get_db)):
    return duplicate_review.preview(db, identifier)


@router.post('/activities/duplicates/reviews/{identifier}/undo', dependencies=[Depends(local_only)])
def undo(identifier: int, db: Session = Depends(get_db)):
    return duplicate_review.undo(db, identifier)
