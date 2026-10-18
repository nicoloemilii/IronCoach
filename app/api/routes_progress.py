from datetime import date as Date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes_sync import local_only
from app.db.database import get_db
from app.db.experience_models import SessionFeedback
from app.db.iron_models import RaceGoal
from app.db.models import Workout, WorkoutEvaluation
from app.db.progress_models import GoalPreparationItem, WorkoutResolution
from app.services import progress

router = APIRouter(tags=['Progressi personali'])


class ResolutionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['skipped', 'rest']
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def meaningful_reason(cls, value):
        if not value.strip():
            raise ValueError('Indica un motivo per la riconciliazione.')
        return value.strip()


class PreparationState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    done: StrictBool
    notes: str = Field('', max_length=4000)


class PreparationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: dict[str, PreparationState] = Field(min_length=1, max_length=100)
    context_signature: str | None = Field(None,pattern=r'^[a-f0-9]{64}$')


def workout_or_404(db, workout_id):
    row = db.get(Workout, workout_id)
    if not row:
        raise HTTPException(404, 'Seduta non trovata.')
    return row


def goal_or_404(db, goal_id):
    row = db.get(RaceGoal, goal_id)
    if not row:
        raise HTTPException(404, 'Obiettivo non trovato.')
    return row


@router.get('/progress/week')
def week(start: Date | None = None, db: Session = Depends(get_db)):
    try:
        return progress.week_progress(db, start)
    except OverflowError:
        raise HTTPException(422, 'Settimana fuori dall’intervallo del calendario supportato.') from None


@router.put('/progress/workouts/{workout_id}/resolution', dependencies=[Depends(local_only)])
def resolve(workout_id: int, payload: ResolutionInput, db: Session = Depends(get_db)):
    workout = workout_or_404(db, workout_id)
    existing = progress.active_resolution(db, workout_id)
    if existing:
        no_new_evidence = (not db.query(SessionFeedback).filter_by(workout_id=workout_id).first()
                           and not db.query(WorkoutEvaluation).filter_by(workout_id=workout_id).first()
                           and not progress.remote_resolution_blocked(db, workout_id))
        if existing.kind == payload.status and existing.reason == payload.reason and workout.status == 'skipped' and no_new_evidence:
            return {'workout_id': workout.id, 'status': workout.status, 'resolution': progress.resolution_dict(existing)}
        raise HTTPException(409, 'Annulla la riconciliazione precedente prima di sostituirla.')
    block = progress.resolution_block(db, workout)
    if block:
        raise HTTPException(409, block)
    resolution = WorkoutResolution(workout_id=workout.id, kind=payload.status,
                                   reason=payload.reason, prior_status=workout.status)
    db.add(resolution); workout.status = 'skipped'
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'La seduta è già stata riconciliata: aggiorna la pagina.') from None
    return {'workout_id': workout.id, 'status': workout.status, 'resolution': progress.resolution_dict(resolution)}


@router.delete('/progress/workouts/{workout_id}/resolution', dependencies=[Depends(local_only)])
def undo_resolution(workout_id: int, db: Session = Depends(get_db)):
    workout = workout_or_404(db, workout_id)
    resolution = progress.active_resolution(db, workout.id)
    if not resolution:
        raise HTTPException(404, 'Riconciliazione attiva non trovata.')
    if (workout.status != 'skipped'
            or db.query(SessionFeedback).filter_by(workout_id=workout.id).first()
            or db.query(WorkoutEvaluation).filter_by(workout_id=workout.id).first()
            or progress.remote_resolution_blocked(db, workout.id)):
        raise HTTPException(409, 'La seduta ha nuove evidenze o uno stato remoto da risolvere: annullamento non disponibile.')
    resolution.undone_at = datetime.utcnow(); workout.status = resolution.prior_status
    db.commit()
    return {'workout_id': workout.id, 'status': workout.status, 'resolution': None}


@router.get('/progress/recovery')
def recovery(days: int = Query(28, ge=7, le=90), db: Session = Depends(get_db)):
    return progress.recovery_progress(db, days)


@router.get('/goals/{goal_id}/preparation')
def preparation(goal_id: int, db: Session = Depends(get_db)):
    return progress.goal_preparation(db, goal_or_404(db, goal_id))


@router.put('/goals/{goal_id}/preparation', dependencies=[Depends(local_only)])
def save_preparation(goal_id: int, payload: PreparationInput, db: Session = Depends(get_db)):
    goal = goal_or_404(db, goal_id)
    if payload.context_signature is not None and payload.context_signature != progress.preparation_signature(goal):
        raise HTTPException(409,'L’obiettivo è cambiato dopo l’apertura della checklist. Conserva le note e ricarica i requisiti prima di confermare.')
    _, current = progress.preparation_items(db, goal)
    known = {item['key']: item for item in current}
    if set(payload.items) - set(known):
        raise HTTPException(422, 'Una o più voci non appartengono alla preparazione attuale di questo obiettivo.')
    for key, state in payload.items.items():
        row = db.query(GoalPreparationItem).filter_by(goal_id=goal.id, key=key).first()
        if not row:
            row = GoalPreparationItem(goal_id=goal.id, key=key); db.add(row)
        row.done = state.done; row.notes = state.notes
        row.context_hash = known[key]['_context_hash']; row.updated_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'La preparazione è stata aggiornata contemporaneamente: rileggi le voci.') from None
    return progress.goal_preparation(db, goal)
