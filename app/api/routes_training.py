from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.manual_training import ManualWorkoutInput, GymProgressInput, create_manual, read_progress, save_progress

router = APIRouter(prefix='/training', tags=['Manual training'])


@router.post('/manual', status_code=201)
def create_manual_workout(payload: ManualWorkoutInput, db: Session = Depends(get_db)):
    return create_manual(db, payload)


@router.get('/gym/{workout_id}/progress')
def get_gym_progress(workout_id: int, db: Session = Depends(get_db)):
    return read_progress(db, workout_id)


@router.put('/gym/{workout_id}/progress')
def put_gym_progress(workout_id: int, payload: GymProgressInput, db: Session = Depends(get_db)):
    return save_progress(db, workout_id, payload)
