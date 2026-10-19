from __future__ import annotations
import json
from datetime import date as Date, datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Activity, DailyMetrics, Workout, WorkoutEvaluation, AthleteAdaptation
from app.db.experience_models import AthleteCheckin, SessionFeedback, RacePreparation
from app.db.repository import save_activity
from app.services.evaluation import evaluate_workout
from app.services.matching import activity_to_actual, match_workout_activity
from app.services.training_load import sport_key
from app.services.athlete_clock import athlete_today, activity_day, day_start_utc

router = APIRouter(prefix='/experience', tags=['Athlete experience'])


class Checkin(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    date: Date | None = None
    sleep_hours: float | None = Field(None, ge=0, le=24)
    stress_score: float | None = Field(None, ge=0, le=100)
    fatigue: int | None = Field(None, ge=1, le=5)
    soreness: int | None = Field(None, ge=1, le=5)
    motivation: int | None = Field(None, ge=1, le=5)
    sleep_quality: int | None = Field(None, ge=1, le=5)
    pain: bool = False
    notes: str = Field('', max_length=2000)


@router.get('/checkin')
def get_checkin(day: Date | None = None, db: Session = Depends(get_db)):
    day = day or athlete_today()
    row = db.query(AthleteCheckin).filter_by(date=str(day)).first()
    return {'date': str(day), 'checkin': json.loads(row.payload_json) if row else None}


@router.put('/checkin')
def save_checkin(payload: Checkin, db: Session = Depends(get_db)):
    day = payload.date or athlete_today()
    if day > athlete_today():
        raise HTTPException(422, 'Il check-in non può essere nel futuro.')
    row = db.query(AthleteCheckin).filter_by(date=str(day)).first()
    content = json.loads(row.payload_json) if row else {}
    content.update(payload.model_dump(mode='json', exclude_unset=True))
    content['date'] = str(day)
    if not row:
        row = AthleteCheckin(date=str(day)); db.add(row)
    row.payload_json = json.dumps(content); row.updated_at = datetime.utcnow()
    metrics = db.query(DailyMetrics).filter_by(date=str(day)).first()
    if not metrics:
        metrics = DailyMetrics(date=str(day)); db.add(metrics)
    for key in ('sleep_hours', 'stress_score'):
        if key in content:
            setattr(metrics, key, content[key])
    db.commit()
    return {'saved': True, 'checkin': content}


class Feedback(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    rpe: float = Field(ge=1, le=10)
    completion_percent: float = Field(ge=0, le=100)
    feeling: Literal['easier', 'as_expected', 'harder'] = 'as_expected'
    notes: str = Field('', max_length=2000)
    activity_id: int | None = None


def workout_row(db, workout_id):
    row = db.get(Workout, workout_id)
    if not row:
        raise HTTPException(404, 'Seduta non trovata.')
    return row


@router.get('/workouts/{workout_id}/review')
def workout_review(workout_id: int, db: Session = Depends(get_db)):
    row = workout_row(db, workout_id)
    feedback = db.query(SessionFeedback).filter_by(workout_id=workout_id).first()
    match = match_workout_activity(json.loads(row.payload_json), db.query(Activity).all(), row.date)
    suggested = None
    if match.get('matched') and match['confidence'] >= 70:
        candidate_id = match['activity_id']
        used = db.query(SessionFeedback).filter_by(activity_id=candidate_id).first()
        if not used or used.workout_id == workout_id:
            suggested = {'activity_id': candidate_id, 'confidence': match['confidence'], 'requires_confirmation': True}
    return {'feedback': json.loads(feedback.payload_json) if feedback else None,
            'suggested_match': suggested, 'auto_completed': False}


@router.put('/workouts/{workout_id}/feedback')
def save_feedback(workout_id: int, payload: Feedback, db: Session = Depends(get_db)):
    return persist_feedback(workout_id, payload, db)


def persist_feedback(workout_id: int, payload: Feedback, db: Session, *, commit: bool = True):
    """Share the ledger while allowing callers to finish one atomic evaluation."""
    row = workout_row(db, workout_id)
    if row.status in ('skipped', 'cancelled', 'superseded'):
        raise HTTPException(409, 'La seduta è stata rimossa dal piano.')
    if Date.fromisoformat(row.date) > athlete_today():
        raise HTTPException(409, 'Puoi valutare la seduta dal giorno programmato.')
    planned = json.loads(row.payload_json)
    actual = {'duration_min': row.duration_min * payload.completion_percent / 100, 'rpe': payload.rpe}
    source = 'self_reported'
    if payload.activity_id is not None:
        activity = db.get(Activity, payload.activity_id)
        if not activity:
            raise HTTPException(404, 'Attività non trovata.')
        if sport_key(activity.sport) != sport_key(row.sport):
            raise HTTPException(422, 'La disciplina non corrisponde alla seduta.')
        if abs((activity_day(activity.start_time) - Date.fromisoformat(row.date)).days) > 2:
            raise HTTPException(422, 'La data dell’attività è troppo distante dalla seduta.')
        used = db.query(SessionFeedback).filter_by(activity_id=payload.activity_id).first()
        if used and used.workout_id != workout_id:
            raise HTTPException(409, 'Attività già associata a un’altra seduta.')
        actual = {**activity_to_actual(activity), 'rpe': payload.rpe}; source = 'activity'
    result = evaluate_workout(planned, actual)
    if payload.feeling == 'harder' or payload.rpe >= 8 or result['completion'] < 70:
        result['adaptation'] = 'reduce_next_similar'
    content = {**payload.model_dump(), 'evaluation': result, 'actual': actual,
               'source': source, 'planned_duration_min': row.duration_min,
               'intensity_comparison': 'unavailable_without_step_data'}
    feedback = db.query(SessionFeedback).filter_by(workout_id=workout_id).first()
    if not feedback:
        feedback = SessionFeedback(workout_id=workout_id); db.add(feedback)
    feedback.activity_id = payload.activity_id; feedback.payload_json = json.dumps(content)
    feedback.updated_at = datetime.utcnow()
    # One evaluation/adaptation per workout: saving edits never inflates the trend.
    db.query(WorkoutEvaluation).filter_by(workout_id=workout_id).delete()
    db.query(AthleteAdaptation).filter_by(source_workout_id=workout_id).delete()
    db.add(WorkoutEvaluation(workout_id=workout_id, activity_id=payload.activity_id,
                             rpe=payload.rpe, payload_json=json.dumps(result)))
    db.add(AthleteAdaptation(sport=row.sport, adaptation=result['adaptation'], source_workout_id=workout_id,
                             confidence=75 if source == 'activity' else 50, note=payload.notes or result['note']))
    row.status = 'completed'
    if commit:
        db.commit()
    else:
        db.flush()
    return content


@router.get('/summary')
def weekly_summary(days: int = Query(7, ge=1, le=90), end: Date | None = None, db: Session = Depends(get_db)):
    end = end or athlete_today()
    if end > athlete_today() or end < Date(1970, 1, 1):
        raise HTTPException(422, 'Scegli una data dal 1970 a oggi.')
    start = end - timedelta(days=days - 1)
    rows = db.query(Activity).filter(Activity.start_time >= day_start_utc(start),
                                    Activity.start_time < day_start_utc(end + timedelta(days=1)),
                                    Activity.start_time <= datetime.utcnow()).all()
    sessions = db.query(Workout).filter(Workout.date >= str(start), Workout.date <= str(end)).all()
    sports = {}
    def category(sport):
        key = sport_key(sport)
        if key in ('swim', 'bike', 'run', 'strength'): return key
        if key in ('gym', 'palestra', 'weightlifting', 'strengthtraining', 'crossfit'): return 'strength'
        if key in ('walk', 'walking', 'hike', 'hiking'): return 'walk'
        return 'other'
    for key, name in [('swim', 'Nuoto'), ('bike', 'Bici'), ('run', 'Corsa'), ('strength', 'Palestra'), ('walk', 'Camminata / escursione'), ('other', 'Altro')]:
        activities = [a for a in rows if category(a.sport) == key]
        planned = [w for w in sessions if category(w.sport) == key and w.status not in ('cancelled', 'superseded', 'skipped')]
        if key not in ('swim', 'bike', 'run') and not activities and not planned: continue
        sports[key] = {'label': name, 'sessions': len(activities),
                       'minutes': round(sum(a.duration_seconds or 0 for a in activities) / 60, 1),
                       'distance_km': round(sum(a.distance_meters or 0 for a in activities) / 1000, 2),
                       'planned_minutes': round(sum(w.duration_min for w in planned), 1),
                       'completed_workouts': sum(w.status == 'completed' for w in planned)}
    feedbacks = db.query(SessionFeedback).filter(SessionFeedback.workout_id.in_([w.id for w in sessions])).all()
    hard = sum(json.loads(f.payload_json).get('feeling') == 'harder' for f in feedbacks)
    return {'start': str(start), 'end': str(end), 'sports': sports,
            'review': f'{len(rows)} attività registrate. {len(feedbacks)} feedback, di cui {hard} con fatica superiore alle attese.',
            'note': 'Volumi e feedback descrivono il periodo; non sono una previsione di prestazione o rischio.'}


class Preparation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nutrition: bool = False
    hydration: bool = False
    equipment: bool = False
    pacing: bool = False
    notes: str = Field('', max_length=4000)


@router.get('/race-preparation')
def get_preparation(db: Session = Depends(get_db)):
    row = db.get(RacePreparation, 1)
    return json.loads(row.payload_json) if row else Preparation().model_dump()


@router.put('/race-preparation')
def save_preparation(payload: Preparation, db: Session = Depends(get_db)):
    row = db.get(RacePreparation, 1)
    if not row:
        row = RacePreparation(id=1); db.add(row)
    row.payload_json = payload.model_dump_json(); db.commit()
    return payload


class ImportedActivity(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    source_id: str = Field(min_length=1, max_length=200)
    sport: Literal['cycling', 'running', 'swimming']
    name: str = Field('', max_length=200)
    start_time: datetime
    duration_seconds: float = Field(gt=0, le=172800)
    distance_meters: float | None = Field(None, ge=0, le=1000000)
    avg_hr: float | None = Field(None, ge=20, le=250)
    avg_power: float | None = Field(None, ge=0, le=3000)
    normalized_power: float | None = Field(None, ge=0, le=3000)


class ActivityImport(BaseModel):
    source: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    activities: list[ImportedActivity] = Field(min_length=1, max_length=500)


@router.post('/import-activities')
def import_activities(payload: ActivityImport, db: Session = Depends(get_db)):
    from app.db.experience_models import ActivityIdentity
    created = matched = 0
    for item in payload.activities:
        data = item.model_dump()
        # API imports require unambiguous timezone; store canonical UTC naive for legacy engine.
        stamp = data['start_time']
        if stamp.tzinfo is None:
            raise HTTPException(422, 'start_time deve includere il fuso orario, es. 2026-10-17T08:00:00+02:00.')
        data['start_time'] = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    for item in payload.activities:
        data = item.model_dump(); data['start_time'] = data['start_time'].astimezone(timezone.utc).replace(tzinfo=None)
        identity = f'{payload.source}:{item.source_id}'
        known = db.query(ActivityIdentity).filter_by(source_key=identity).first()
        before = db.query(Activity).count()
        data['source'] = payload.source
        row = save_activity(db, data)
        if row is None:
            raise HTTPException(409, 'Identità attività non valida; ricontrolla il database.')
        created += int(db.query(Activity).count() > before); matched += int(db.query(Activity).count() == before)
    db.commit()
    return {'created': created, 'matched': matched, 'source': payload.source, 'received_at': datetime.utcnow().isoformat() + 'Z'}


@router.post('/import-fit')
async def import_fit_upload(request: Request, db: Session = Depends(get_db)):
    from hashlib import sha256
    try:
        from garmin_fit_sdk import Decoder, Stream
    except ImportError:
        raise HTTPException(503, 'SDK FIT non installato sul server. Installa garmin-fit-sdk nelle dipendenze web.')
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(413, 'Il file FIT supera 20 MB.')
    if len(content) < 12 or content[8:12] != b'.FIT':
        raise HTTPException(422, 'Il file non ha un’intestazione FIT valida.')
    try:
        messages, errors = Decoder(Stream.from_byte_array(content)).read(enable_crc_check=True)
        if errors:
            raise ValueError('Controllo integrità FIT fallito.')
        file_ids = messages.get('file_id_mesgs') or []
        if not file_ids or file_ids[0].get('type') != 'activity':
            raise ValueError('Serve un file di attività, non un allenamento programmato.')
        records = messages.get('session_mesgs') or []
        if not records or len(records) > 100:
            raise ValueError('Numero di sessioni non valido.')
        digest = sha256(content).hexdigest()
        activities = []
        for index, fields in enumerate(records):
            sport = str(fields.get('sport', 'unknown'))
            if sport not in ('cycling', 'running', 'swimming'):
                continue
            timestamp = fields.get('start_time')
            if not isinstance(timestamp, datetime):
                raise ValueError('Data della sessione assente.')
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            activities.append(ImportedActivity(source_id=f'{digest}:{index}', sport=sport,
                name=f'{sport} · {activity_day(timestamp)}', start_time=timestamp,
                duration_seconds=fields.get('total_timer_time') or fields.get('total_elapsed_time'),
                distance_meters=fields.get('total_distance'), avg_hr=fields.get('avg_heart_rate'),
                avg_power=fields.get('avg_power'), normalized_power=fields.get('normalized_power')))
        if not activities:
            raise ValueError('Nessuna sessione nuoto, bici o corsa supportata nel file.')
    except Exception as exc:
        raise HTTPException(422, f'File FIT non importabile: {str(exc)[:200]}') from exc
    result = import_activities(ActivityImport(source='fit', activities=activities), db)
    result['sessions_in_file'] = len(records)
    result['sessions_ignored'] = len(records) - len(activities)
    result['file_sha256'] = digest
    return result
