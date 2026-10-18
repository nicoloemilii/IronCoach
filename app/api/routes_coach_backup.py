from __future__ import annotations
import json
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Activity, DailyMetrics, Workout, WorkoutEvaluation, AthleteAdaptation
from app.services.coach import coach_today
from app.services.readiness import calculate_daily_state
from app.services.workout_engine import generate_workout
from app.services.evaluation import evaluate_workout
from app.services.training_load import fitness_metrics
from app.services.athlete_profile import load_profile
from app.services.adaptation import adaptation_context
from app.services.matching import match_workout_activity, activity_to_actual

router = APIRouter()

def _metrics_row(row):
    if not row: return {}
    return {k:getattr(row,k) for k in ('sleep_hours','hrv_ms','hrv_baseline_ms','resting_hr_bpm','resting_hr_baseline_bpm','stress_score','steps','daily_activity_load','body_battery')}

@router.get('/coach/today')
def get_coach_today(ftp_watts: float | None = None, db: Session = Depends(get_db)):
    activities = db.query(Activity).order_by(Activity.start_time.asc()).all()
    base = coach_today(activities, ftp_watts=ftp_watts)
    row = db.query(DailyMetrics).filter(DailyMetrics.date == str(base['date'])).first()
    metrics = _metrics_row(row)
    base['readiness'] = calculate_daily_state(activities, metrics, ftp_watts or base['profile'].get('ftp_watts') or 170, base['date'])
    base['readiness']['phase'] = base['event']['phase']
    adaptation_rows = db.query(AthleteAdaptation).order_by(AthleteAdaptation.created_at.desc()).all()
    ctx = adaptation_context(adaptation_rows, 'cycling')
    adaptation = ctx['action'] if ctx['trend'] == 'persistent' else None
    last_adaptation = adaptation_rows[0] if adaptation_rows else None
    base['adaptation'] = {**ctx, 'source_workout_id': last_adaptation.source_workout_id if last_adaptation else None}
    base['workout'] = generate_workout(activities, base['profile'], base['fitness'], base['readiness'], 'cycling', adaptation)
    return base

@router.put('/coach/daily-metrics')
def upsert_daily_metrics(payload: dict, db: Session = Depends(get_db)):
    day = str(payload.get('date') or datetime.utcnow().date())
    row = db.query(DailyMetrics).filter(DailyMetrics.date == day).first()
    if not row: row = DailyMetrics(date=day); db.add(row)
    allowed=('sleep_hours','hrv_ms','hrv_baseline_ms','resting_hr_bpm','resting_hr_baseline_bpm','stress_score','steps','daily_activity_load','body_battery')
    for k in allowed:
        if k in payload: setattr(row,k,payload[k])
    db.commit(); db.refresh(row)
    return {'status':'saved','date':day,'metrics':_metrics_row(row)}

@router.get('/coach/readiness')
def get_readiness(date: str | None = None, db: Session = Depends(get_db)):
    day = datetime.fromisoformat(date).date() if date else datetime.utcnow().date()
    acts=db.query(Activity).order_by(Activity.start_time.asc()).all()
    row=db.query(DailyMetrics).filter(DailyMetrics.date==str(day)).first()
    return calculate_daily_state(acts,_metrics_row(row),load_profile().get('ftp_watts') or 170,day)

@router.post('/coach/workouts')
def create_workout(payload: dict | None = None, db: Session = Depends(get_db)):
    payload=payload or {}; today=get_coach_today(db=db); sport=payload.get('sport','cycling')
    rows = db.query(AthleteAdaptation).order_by(AthleteAdaptation.created_at.desc()).all()
    ctx = adaptation_context(rows, sport)
    adaptation = ctx['action'] if ctx['trend'] == 'persistent' else None
    workout=generate_workout(db.query(Activity).all(),today['profile'],today['fitness'],today['readiness'],sport,adaptation)
    row=Workout(date=str(today['date']),sport=workout['sport'],name=workout['name'],duration_min=workout['duration_min'],payload_json=json.dumps(workout),status='planned')
    db.add(row); db.commit(); db.refresh(row)
    return {'id':row.id, **workout}

@router.post('/coach/workouts/{workout_id}/evaluate')
def evaluate(workout_id:int,payload:dict,db:Session=Depends(get_db)):
    row=db.query(Workout).filter(Workout.id==workout_id).first()
    if not row: return {'error':'workout_not_found'}
    result=evaluate_workout(json.loads(row.payload_json),payload)
    ev=WorkoutEvaluation(workout_id=workout_id,activity_id=payload.get('activity_id'),rpe=payload.get('rpe'),payload_json=json.dumps(result))
    db.add(ev); row.status='completed'
    confidence = 100.0 if result.get('overall') is not None else 25.0
    db.add(AthleteAdaptation(sport=row.sport, adaptation=result['adaptation'], source_workout_id=row.id, confidence=confidence, note=result.get('note')))
    db.commit()
    return {'workout_id':workout_id,'evaluation':result,'adaptation_saved':result['adaptation']}


@router.post('/coach/workouts/{workout_id}/match')
def match(workout_id: int, payload: dict | None = None, db: Session = Depends(get_db)):
    row = db.query(Workout).filter(Workout.id == workout_id).first()
    if not row:
        return {'error': 'workout_not_found'}
    payload = payload or {}
    result = match_workout_activity(json.loads(row.payload_json), db.query(Activity).all(), str(row.date), payload.get('activity_id'))
    if not result.get('matched'):
        return {'workout_id': workout_id, 'match': result}
    actual = activity_to_actual(result['activity'])
    if payload.get('rpe') is not None:
        actual['rpe'] = payload.get('rpe')
    evaluation = evaluate_workout(json.loads(row.payload_json), actual)
    evaluation['match_confidence'] = result['confidence']
    evaluation['matched_activity_id'] = result['activity_id']
    return {'workout_id': workout_id, 'match': {k:v for k,v in result.items() if k != 'activity'}, 'actual': actual, 'evaluation': evaluation}
