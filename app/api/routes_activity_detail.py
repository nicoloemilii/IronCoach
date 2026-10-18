from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.routes_sync import local_only
from app.db.database import get_db
from app.db.models import Activity
from app.services.activity_detail import activity_detail

router = APIRouter(tags=['Activity detail'])


@router.get('/activities/{identifier}/detail', dependencies=[Depends(local_only)])
def get_activity_detail(identifier: int, response: Response, db: Session = Depends(get_db)):
    activity = db.get(Activity, identifier)
    if not activity:
        raise HTTPException(404, 'Attività non trovata.')
    response.headers['Cache-Control'] = 'private, no-store'
    return activity_detail(db, activity)
