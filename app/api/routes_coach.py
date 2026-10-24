from __future__ import annotations
import json
from datetime import datetime
from datetime import date as Date
from typing import Annotated, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Activity, DailyMetrics, Workout, WorkoutEvaluation, AthleteAdaptation
from app.services.coach import coach_today
from app.services.readiness import calculate_daily_state
from app.services.workout_engine import generate_workout
from app.services.evaluation import evaluate_workout
from app.services.training_load import fitness_metrics, activity_load_snapshot
from app.services.athlete_profile import load_profile
from app.services.adaptation import adaptation_context
from app.services.matching import match_workout_activity, activity_to_actual
from app.services.historical_learning import build_historical_profile
from app.services.historical_capacity import build_historical_capacity
from app.services.athlete_state import build_athlete_state
from app.services.decision_engine import decide_training
from app.services.athlete_clock import athlete_today, activity_day

router = APIRouter()

def _metrics_row(row):
    if not row: return {}
    return {k:getattr(row,k) for k in ('sleep_hours','hrv_ms','hrv_baseline_ms','resting_hr_bpm','resting_hr_baseline_bpm','stress_score','steps','daily_activity_load','body_battery')}

@router.get('/coach/today')
def get_coach_today(ftp_watts: Annotated[float | None, Query(gt=0, allow_inf_nan=False)] = None, db: Session = Depends(get_db)):
    with activity_load_snapshot(db):
        return _get_coach_today(ftp_watts, db)


def _get_coach_today(ftp_watts, db):
    activities = db.query(Activity).filter(Activity.start_time <= datetime.utcnow()).order_by(Activity.start_time.asc()).all()
    profile = load_profile()
    from app.services.planning import load_settings
    settings = load_settings(db)
    from app.services.goal_planning import goal_context
    settings, race_goal, goal_warnings = goal_context(db, settings)
    known_ftp = settings.get('thresholds', {}).get('cycling_ftp_watts', {}).get('value')
    if known_ftp:
        profile.setdefault('cycling', {})['ftp_watts'] = known_ftp
        profile['ftp_watts'] = known_ftp
    profile['_power_targets_available'] = bool(known_ftp)
    profile['goals'] = {'event': settings.get('event_name') or '', 'event_date': settings.get('event_date'), 'target_time': settings.get('target_time')}
    ftp = float(ftp_watts or profile.get('cycling', {}).get('ftp_watts') or profile.get('ftp_watts') or 170)
    base = coach_today(activities, ftp_watts=ftp, profile_override=profile)
    base['profile'] = profile
    base['race_goal'] = race_goal; base['goal_warnings'] = goal_warnings
    from app.services.time_constraints import windows
    allowed = [sport for sport in ('cycling', 'running', 'swimming') if sport not in settings['excluded_sports'] and windows(settings,str(base['date']),sport)['allowed']
               and (sport != 'swimming' or settings['pool_access'])
               and (not race_goal or sport in race_goal['disciplines'])]
    base['allowed_sports'] = allowed
    row = db.query(DailyMetrics).filter(DailyMetrics.date == str(base['date'])).first()
    metrics = _metrics_row(row)
    from app.services.health import effective_metrics
    imported = effective_metrics(db, base['date'])
    metrics.update(imported)
    for key in ('ctl', 'atl', 'tsb'):
        if key in imported:
            base['fitness'][key] = imported[key]

    adaptation_rows = db.query(AthleteAdaptation).order_by(AthleteAdaptation.created_at.desc()).all()
    ctx = adaptation_context(adaptation_rows)
    last_adaptation = adaptation_rows[0] if adaptation_rows else None
    adaptation = {**ctx, 'source_workout_id': last_adaptation.source_workout_id if last_adaptation else None}

    historical = build_historical_profile(activities, ftp, reference_date=base['date'])
    historical_capacity = build_historical_capacity(historical)
    state = build_athlete_state(activities, metrics, base['fitness'], historical_capacity, adaptation, ftp, base['date'])
    from app.db.experience_models import AthleteCheckin
    journal = db.query(AthleteCheckin).filter_by(date=str(base['date'])).first()
    state['subjective'] = json.loads(journal.payload_json) if journal else {}
    from app.api.routes_iron import dashboard
    context = dashboard(day=Date.fromisoformat(str(base['date'])), db=db)['coach']['training_assessment']
    state['coach_context'] = context
    base['coach_context'] = context
    decision = decide_training(state, phase=base['event']['phase'])
    if allowed and decision['sport'] not in allowed:
        decision = decide_training(state, phase=base['event']['phase'], requested_sport=allowed[0])

    base['readiness'] = state['readiness']
    base['readiness']['phase'] = base['event']['phase']
    base['adaptation'] = adaptation
    base['historical'] = historical
    base['historical_capacity'] = historical_capacity
    base['athlete_state'] = state
    base['decision'] = decision
    base['recommendation'] = decision['recommendation']
    base['reason'] = decision['reasons'][0]

    base['workout'] = generate_workout(
        activities, base['profile'], base['fitness'], base['readiness'],
        decision['sport'], decision['recommendation'], decision['confidence'],
        historical_capacity=historical_capacity, decision=decision
    )
    journal_data = state['subjective']
    base['training_paused'] = bool(context['session_policy']['mode'] == 'rest' or not allowed or journal_data.get('pain') or (state['readiness'].get('readiness') is not None and state['readiness']['readiness'] < 35))
    if base['training_paused']:
        base['workout'] = {'sport': 'recovery', 'name': 'Recupero e rivalutazione', 'duration_min': 0,
                           'reason': 'Il check-in richiede una rivalutazione prima di programmare una nuova seduta.', 'steps': [], 'expected_rpe': 0}
        if not allowed:
            base['workout']['reason'] = 'Nessuna disciplina disponibile per la proposta giornaliera: verifica esclusioni, pause temporanee, piscina e obiettivo. Le sedute di forza restano gestibili dal calendario.'
            base['reason'] = base['workout']['reason']
    elif race_goal and base['workout'].get('sport') in race_goal['disciplines']:
        from app.services.goal_planning import focus_for_sport
        base['workout']['goal_focus'] = focus_for_sport(race_goal, base['workout']['sport'])
    base['scheduled_workouts'] = [
        {'id': w.id, 'date': w.date, 'sport': w.sport, 'name': w.name, 'duration_min': w.duration_min,
         'status': w.status, 'payload': json.loads(w.payload_json)}
        for w in db.query(Workout).filter(Workout.date == str(base['date']), Workout.status.in_(['planned', 'completed'])).order_by(Workout.id).all()
    ]
    base['load_reference'] = {'ftp_watts': ftp, 'source': settings.get('thresholds', {}).get('cycling_ftp_watts', {}).get('source') if known_ftp else 'legacy_assumption',
                              'note': 'I carichi bici possono dipendere dalla FTP; conferma la soglia nel profilo.'}
    return base

class DailyMetricsInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    date: Date | None = None
    sleep_hours: float | None = Field(None, ge=0, le=24)
    hrv_ms: float | None = Field(None, gt=0, le=1000)
    hrv_baseline_ms: float | None = Field(None, gt=0, le=1000)
    resting_hr_bpm: float | None = Field(None, ge=20, le=250)
    resting_hr_baseline_bpm: float | None = Field(None, ge=20, le=250)
    stress_score: float | None = Field(None, ge=0, le=100)
    steps: int | None = Field(None, ge=0, le=200000)
    daily_activity_load: float | None = Field(None, ge=0, le=3000)
    body_battery: float | None = Field(None, ge=0, le=100)

    @model_validator(mode='after')
    def observed_date(self):
        if self.date is not None and not Date(1970, 1, 1) <= self.date <= athlete_today():
            raise ValueError('Le metriche osservate richiedono una data dal 1970 a oggi.')
        return self


@router.put('/coach/daily-metrics')
def upsert_daily_metrics(payload: DailyMetricsInput, db: Session = Depends(get_db)):
    payload = payload.model_dump(exclude_unset=True)
    day = str(payload.get('date') or athlete_today())
    row = db.query(DailyMetrics).filter(DailyMetrics.date == day).first()
    if not row: row = DailyMetrics(date=day); db.add(row)
    allowed=('sleep_hours','hrv_ms','hrv_baseline_ms','resting_hr_bpm','resting_hr_baseline_bpm','stress_score','steps','daily_activity_load','body_battery')
    for k in allowed:
        if k in payload: setattr(row,k,payload[k])
    db.commit(); db.refresh(row)
    return {'status':'saved','date':day,'metrics':_metrics_row(row)}

@router.get('/coach/readiness')
def get_readiness(date: Date | None = None, db: Session = Depends(get_db)):
    day = date or athlete_today()
    if not Date(1970, 1, 1) <= day <= athlete_today():
        raise HTTPException(422, 'Scegli una data dal 1970 a oggi.')
    acts=db.query(Activity).order_by(Activity.start_time.asc()).all()
    row=db.query(DailyMetrics).filter(DailyMetrics.date==str(day)).first()
    from app.services.health import effective_metrics
    metrics = {**_metrics_row(row), **effective_metrics(db, day)}
    return calculate_daily_state(acts,metrics,load_profile().get('ftp_watts') or 170,day)

@router.get('/coach/workouts')
def list_workouts(limit: int = 100, db: Session = Depends(get_db)):
    limit = max(1, min(500, limit))
    rows = db.query(Workout).filter(Workout.status != 'superseded').order_by(Workout.created_at.desc()).limit(limit).all()
    return [{'id':r.id,'created_at':r.created_at,'date':r.date,'sport':r.sport,'name':r.name,'duration_min':r.duration_min,'status':r.status,'payload':json.loads(r.payload_json)} for r in rows]

@router.post('/coach/workouts')
def create_workout(payload: dict | None = None, db: Session = Depends(get_db)):
    payload = payload or {}
    sport = payload.get('sport')
    today = get_coach_today(db=db)
    if today.get('training_paused'):
        raise HTTPException(409, 'Il check-in richiede recupero e rivalutazione: nessuna nuova seduta viene prescritta oggi.')
    if today.get('coach_context', {}).get('session_policy', {}).get('mode') == 'easy' and any(w['status']=='planned' for w in today['scheduled_workouts']):
        raise HTTPException(409, 'Il Coach richiede di alleggerire: modifica la seduta già programmata invece di aggiungerne una seconda.')
    activities = db.query(Activity).order_by(Activity.start_time.asc()).all()
    ftp = float(today['profile'].get('cycling', {}).get('ftp_watts') or today['profile'].get('ftp_watts') or 170)
    requested = sport if sport in ('cycling', 'running', 'swimming') else None
    if requested and requested not in today['allowed_sports']:
        raise HTTPException(409, 'La disciplina non è disponibile nel profilo, nell’obiettivo o nelle pause temporanee. Aggiorna le preferenze prima di salvarla.')
    if requested is None and today['decision']['sport'] in today['allowed_sports']:
        requested = today['decision']['sport']
    decision = decide_training(today['athlete_state'], phase=today['event']['phase'], requested_sport=requested)
    workout = generate_workout(
        activities, today['profile'], today['fitness'], today['readiness'],
        decision['sport'], decision['recommendation'], decision['confidence'],
        historical_capacity=today['historical_capacity'], decision=decision
    )
    if today.get('race_goal') and workout['sport'] in today['race_goal']['disciplines']:
        from app.services.goal_planning import focus_for_sport
        workout['goal_focus'] = focus_for_sport(today['race_goal'], workout['sport'])
    row = Workout(date=str(today['date']), sport=workout['sport'], name=workout['name'], duration_min=workout['duration_min'], payload_json=json.dumps(workout), status='planned')
    db.add(row); db.commit(); db.refresh(row)
    return {'id': row.id, **workout}

class LegacyActualStep(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    avg_power_w: float | None = Field(None, ge=0, le=3000)
    avg_hr_bpm: float | None = Field(None, ge=20, le=250)
    duration_min: float | None = Field(None, ge=0, le=2880)
    type: str | None = Field(None, max_length=80)


class LegacyEvaluationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    rpe: float = Field(ge=1, le=10)
    duration_min: float | None = Field(None, ge=0, le=2880)
    completion_percent: float | None = Field(None, ge=0, le=100)
    activity_id: int | None = Field(None, gt=0)
    feeling: Literal['easier', 'as_expected', 'harder'] = 'as_expected'
    notes: str = Field('', max_length=2000)
    steps: list[LegacyActualStep] | None = Field(None, max_length=1000)
    avg_power_w: float | None = Field(None, ge=0, le=3000)
    normalized_power_w: float | None = Field(None, ge=0, le=3000)
    avg_hr_bpm: float | None = Field(None, ge=20, le=250)
    load: float | None = Field(None, ge=0, le=3000)

    @model_validator(mode='after')
    def completion_evidence(self):
        if self.duration_min is None and self.completion_percent is None and self.activity_id is None:
            raise ValueError('RPE da solo non dimostra il completamento: indicare duration_min, completion_percent o activity_id.')
        return self


def _reviewable_workout(db: Session, workout_id: int):
    from app.api.routes_experience import workout_row
    row = workout_row(db, workout_id)
    if row.status in ('skipped', 'cancelled', 'superseded'):
        raise HTTPException(409, 'La seduta è stata rimossa dal piano.')
    if Date.fromisoformat(row.date) > athlete_today():
        raise HTTPException(409, 'Puoi valutare la seduta dal giorno programmato.')
    return row


@router.post('/coach/workouts/{workout_id}/evaluate')
def evaluate(workout_id: int, payload: LegacyEvaluationInput, db: Session = Depends(get_db)):
    try:
        from app.api.routes_experience import begin_feedback_write
        begin_feedback_write(db)
        return _evaluate_feedback(workout_id, payload, db)
    except Exception:
        db.rollback()
        raise


def _evaluate_feedback(workout_id: int, payload: LegacyEvaluationInput, db: Session):
    from app.api.routes_experience import Feedback, persist_feedback
    from app.db.experience_models import SessionFeedback

    row = _reviewable_workout(db, workout_id)
    from app.services.execution_evidence import safe_object
    planned = safe_object(row.payload_json)
    if planned is None:
        raise HTTPException(409, 'Il programma contiene dati non leggibili. Verifica la seduta.')
    if payload.steps is not None and not isinstance(planned.get('steps',[]),list):
        raise HTTPException(409, 'Le fasi del programma non sono leggibili. Verifica la seduta.')
    if payload.steps is not None and len(payload.steps) != len(planned.get('steps', [])):
        raise HTTPException(422, 'I dati per fase richiedono una voce per ogni fase programmata, anche vuota se non misurata.')
    duration = payload.duration_min
    if payload.activity_id is None and duration is not None and payload.completion_percent is not None:
        expected = row.duration_min * payload.completion_percent / 100
        if abs(duration - expected) > 1:
            raise HTTPException(422, 'Durata e percentuale di completamento non sono coerenti; inviare un solo valore.')
    completion = payload.completion_percent
    if completion is None:
        completion = min(100.0, duration / row.duration_min * 100) if duration is not None and row.duration_min > 0 else 100.0
    # This shared path owns association checks and a single feedback/evaluation/
    # adaptation per workout, including when a legacy client retries a save.
    content = persist_feedback(workout_id, Feedback(rpe=payload.rpe, completion_percent=completion,
                            activity_id=payload.activity_id, feeling=payload.feeling, notes=payload.notes), db, commit=False)

    actual = dict(content['actual'])
    provided = payload.model_dump(exclude_none=True, include={'steps', 'avg_power_w', 'normalized_power_w', 'avg_hr_bpm', 'load'})
    if content['source'] == 'self_reported':
        actual.update(provided)
        if duration is not None:
            actual['duration_min'] = duration
    elif provided or duration is not None:
        # Never overwrite an imported observation with caller-supplied values.
        # Explicit phase evidence remains available, labelled as self-reported.
        actual['reported_evidence'] = {**provided, **({'duration_min': duration} if duration is not None else {})}
        if 'steps' in provided:
            actual['steps'] = provided['steps']
    result = evaluate_workout(planned, actual)
    if payload.feeling == 'harder' or payload.rpe >= 8 or (result['completion'] is not None and result['completion'] < 70):
        result['adaptation'] = 'reduce_next_similar'
    result['evidence_source'] = 'self_reported_step_data' if payload.steps is not None else content['source']
    result['intensity_comparison'] = 'self_reported_step_data' if result['intensity_control'] is not None else 'unavailable_without_step_data'
    content.update(actual=actual, evaluation=result, intensity_comparison=result['intensity_comparison'])
    if payload.steps is not None:
        content['step_evidence_source'] = 'self_reported'
    db.query(SessionFeedback).filter_by(workout_id=workout_id).one().payload_json = json.dumps(content)
    db.query(WorkoutEvaluation).filter_by(workout_id=workout_id).one().payload_json = json.dumps(result)
    adaptation_row = db.query(AthleteAdaptation).filter_by(source_workout_id=workout_id).one()
    adaptation_row.adaptation = result['adaptation']
    if payload.steps is not None:
        adaptation_row.confidence = min(adaptation_row.confidence, 50)
    db.commit()
    return {'workout_id': workout_id, 'evaluation': result, 'adaptation_saved': result['adaptation']}


class LegacyMatchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    activity_id: int | None = Field(None, gt=0)
    rpe: float | None = Field(None, ge=1, le=10)


@router.post('/coach/workouts/{workout_id}/match')
def match(workout_id: int, payload: LegacyMatchInput | None = None, db: Session = Depends(get_db)):
    from app.db.experience_models import SessionFeedback
    from app.services.training_load import sport_key

    row = _reviewable_workout(db, workout_id)
    payload = payload or LegacyMatchInput()
    activities = db.query(Activity).all()
    used = {item.activity_id for item in db.query(SessionFeedback).all() if item.activity_id is not None and item.workout_id != workout_id}
    if payload.activity_id is not None:
        requested = db.get(Activity, payload.activity_id)
        if not requested:
            raise HTTPException(404, 'Attività non trovata.')
        if payload.activity_id in used:
            raise HTTPException(409, 'Attività già associata a un’altra seduta.')
        if sport_key(requested.sport) != sport_key(row.sport):
            raise HTTPException(422, 'La disciplina non corrisponde alla seduta.')
        if abs((activity_day(requested.start_time) - Date.fromisoformat(row.date)).days) > 2:
            raise HTTPException(422, 'La data dell’attività è troppo distante dalla seduta.')
    result = match_workout_activity(json.loads(row.payload_json), [a for a in activities if a.id not in used], str(row.date), payload.activity_id)
    result = {key: value for key, value in result.items() if key != 'activity'}
    if not result.get('matched') or result.get('confidence', 0) < 70 or result.get('duration_score', 0) < 50:
        result.update(matched=False, requires_confirmation=False, confirmed=False, reason='no_confident_candidate')
        return {'workout_id': workout_id, 'match': result, 'auto_completed': False}
    candidate = db.get(Activity, result['activity_id'])
    actual = activity_to_actual(candidate)
    if payload.rpe is not None:
        actual['rpe'] = payload.rpe
    evaluation = evaluate_workout(json.loads(row.payload_json), actual)
    evaluation.update(match_confidence=result['confidence'], matched_activity_id=result['activity_id'],
                      preview=True, intensity_comparison='unavailable_without_step_data')
    result.update(requires_confirmation=True, confirmed=False)
    return {'workout_id': workout_id, 'match': result, 'actual': actual, 'evaluation': evaluation, 'auto_completed': False}







