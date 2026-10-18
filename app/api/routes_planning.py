from datetime import date as Date
import re
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.services.time_constraints import CoachRequest, CoachApply, preview as coach_preview, apply as coach_apply

from app.db.database import get_db
from app.services.planning import PlanningProfile, generate_week, get_settings, get_week, modify_workout, save_settings, undo_week, activate_goal, preview_workout

router = APIRouter(prefix="/planning", tags=["planning"])


class WeekRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: Date | None = None
    regenerate: bool = False


class WorkoutChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["move", "shorten", "skip"]
    date: Date | None = None
    duration_min: int | None = Field(None, ge=15, le=600)
    reason: str = Field("", max_length=400)


class GoalActivation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal_id: int | None = Field(gt=0)


def expected_revision(if_match):
    if if_match is None:
        return None
    value = if_match.strip()
    if len(value) > 21 or not re.fullmatch(r'(?:[0-9]+|"[0-9]+")', value):
        raise HTTPException(422, 'If-Match non valido: indica la revisione numerica del profilo, ad esempio "3".')
    revision = int(value.strip('"'))
    if revision > 9223372036854775807:
        raise HTTPException(422, 'Revisione del profilo fuori intervallo.')
    return revision


@router.get("/profile")
def read_profile(db: Session = Depends(get_db)):
    return get_settings(db)


@router.put("/profile")
def update_profile(payload: PlanningProfile, db: Session = Depends(get_db), if_match: str | None = Header(None, alias="If-Match")):
    return save_settings(db, payload, expected_revision=expected_revision(if_match))


@router.put('/goal')
def select_goal(payload: GoalActivation, db: Session = Depends(get_db), if_match: str | None = Header(None, alias='If-Match')):
    return activate_goal(db, payload.goal_id, expected_revision(if_match))


@router.get("/week")
def read_week(start: Date | None = None, db: Session = Depends(get_db)):
    return get_week(db, start)


@router.post("/week")
def create_week(payload: WeekRequest | None = None, db: Session = Depends(get_db)):
    payload = payload or WeekRequest()
    return generate_week(db, payload.start, payload.regenerate)


@router.patch("/workouts/{workout_id}")
def change_workout(workout_id: int, payload: WorkoutChange, db: Session = Depends(get_db), x_plan_preview: str | None = Header(None, alias='X-Plan-Preview')):
    return modify_workout(db, workout_id, payload.action, payload.date, payload.duration_min, payload.reason, preview_signature=x_plan_preview)


@router.post('/workouts/{workout_id}/preview')
def preview_change(workout_id: int, payload: WorkoutChange, db: Session = Depends(get_db)):
    return preview_workout(db, workout_id, payload.action, payload.date, payload.duration_min, payload.reason)


@router.post("/undo")
def undo(payload: WeekRequest | None = None, db: Session = Depends(get_db)):
    return undo_week(db, payload.start if payload else None)


@router.post('/coach/preview')
def preview_coach_request(payload: CoachRequest, db: Session = Depends(get_db)):
    return coach_preview(db, payload)


@router.post('/coach/apply')
def apply_coach_request(payload: CoachApply, db: Session = Depends(get_db)):
    return coach_apply(db, payload)
