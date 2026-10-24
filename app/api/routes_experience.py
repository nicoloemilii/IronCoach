from __future__ import annotations
import json
from datetime import date as Date, datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Activity, DailyMetrics, Workout, WorkoutEvaluation, AthleteAdaptation
from app.db.experience_models import AthleteCheckin, SessionFeedback, RacePreparation
from app.db.repository import save_activity
from app.services.evaluation import evaluate_workout
from app.services.matching import activity_to_actual
from app.services.training_load import sport_key
from app.services.athlete_clock import athlete_today, activity_day, day_start_utc, utc_now_naive
from app.services import workout_matching
from app.services.execution_evidence import safe_object, usable_feedback

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
    activity_id: int | None = Field(None, gt=0, strict=True)
    activity_match_token: str | None = Field(None, pattern=r'^[a-f0-9]{64}$')


def workout_row(db, workout_id):
    row = db.get(Workout, workout_id)
    if not row:
        raise HTTPException(404, 'Seduta non trovata.')
    return row


@router.get('/workouts/{workout_id}/review')
def workout_review(workout_id: int, db: Session = Depends(get_db)):
    # A read transaction keeps the displayed candidate and its revision token
    # in the same SQLite snapshot, including source links and prior decisions.
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, 'Completa le modifiche in corso prima di leggere le proposte.')
    db.rollback()
    with db.begin():
        if db.get_bind().dialect.name == 'sqlite': db.connection().exec_driver_sql('BEGIN')
        db.expire_all()
        row = workout_row(db, workout_id)
        feedback = db.query(SessionFeedback).filter_by(workout_id=workout_id).first()
        payload = safe_object(row.payload_json)
        content = safe_object(feedback.payload_json) if feedback else None
        if content is not None:
            # The ledger link is authoritative even for older payloads that
            # omitted activity_id or retained a superseded value inside JSON.
            content = {**content, 'activity_id':feedback.activity_id}
        evidence = usable_feedback(row,feedback,db.get(Activity,feedback.activity_id) if feedback and feedback.activity_id else None,
            today=athlete_today(),now=workout_matching.utc_now_naive()) if feedback else None
        evidence_issue = ('L’attività abbinata ha data e ora future: verifica l’abbinamento prima di confermare.'
            if evidence and evidence['issue'] == 'future_activity' else
            'Il feedback salvato non contiene un’evidenza utilizzabile: verifica l’abbinamento o conferma nuovamente il feedback manuale.'
            if evidence and not evidence['usable'] else None)
        candidates = workout_matching.activity_candidates(db,row)
        blocked = ('Il programma contiene dati non leggibili: verifica la seduta prima di salvare.' if payload is None else
            'Puoi confermare il feedback dal giorno programmato della seduta.' if Date.fromisoformat(row.date) > athlete_today() else
            'La seduta è stata rimossa dal piano.' if row.status in ('skipped','cancelled','superseded') else None)
        preferred = next((item for item in candidates['items'] if item['available'] and item['day_delta'] == 0
                          and item['duration_delta_min'] is not None and abs(item['duration_delta_min']) <= max(10,row.duration_min*.3)), None)
        return {'feedback':content,'suggested_match':({'activity_id':preferred['id'],'requires_confirmation':True} if preferred else None),
            'activity_candidates':candidates,'auto_completed':False,
            'workout':{**candidates['workout'],'payload':payload or {'sport':row.sport,'name':row.name,'duration_min':row.duration_min,'steps':[]}},
            'feedback_available':blocked is None,
            'feedback_issue':blocked or evidence_issue}


@router.put('/workouts/{workout_id}/feedback')
def save_feedback(workout_id: int, payload: Feedback, db: Session = Depends(get_db)):
    try:
        return persist_feedback(workout_id, payload, db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'L’abbinamento è cambiato durante il salvataggio. Rileggi le proposte.') from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, 'Salvataggio non completato. Verifica i dati prima di riprovare.') from None
    except Exception:
        db.rollback()
        raise


def begin_feedback_write(db):
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, 'Completa le modifiche in corso prima di confermare la seduta.')
    db.rollback()
    if db.get_bind().dialect.name == 'sqlite': db.connection().exec_driver_sql('BEGIN IMMEDIATE')
    db.expire_all()


def persist_feedback(workout_id: int, payload: Feedback, db: Session, *, commit: bool = True):
    """Share the ledger while allowing callers to finish one atomic evaluation."""
    if commit: begin_feedback_write(db)
    row = workout_row(db, workout_id)
    if row.status in ('skipped', 'cancelled', 'superseded'):
        raise HTTPException(409, 'La seduta è stata rimossa dal piano.')
    if Date.fromisoformat(row.date) > athlete_today():
        raise HTTPException(409, 'Puoi valutare la seduta dal giorno programmato.')
    planned = safe_object(row.payload_json)
    if planned is None or workout_matching.finite(row.duration_min) is None or row.duration_min <= 0:
        raise HTTPException(409, 'Il programma non contiene dati validi per il feedback. Verifica la seduta.')
    actual = {'duration_min': row.duration_min * payload.completion_percent / 100, 'rpe': payload.rpe}
    source = 'self_reported'
    if payload.activity_id is not None:
        activity = workout_matching.validate_activity(db,row,payload.activity_id,payload.activity_match_token)
        actual = {**activity_to_actual(activity), 'rpe': payload.rpe}; source = 'activity'
    result = evaluate_workout(planned, actual)
    if payload.feeling == 'harder' or payload.rpe >= 8 or (result['completion'] is not None and result['completion'] < 70):
        result['adaptation'] = 'reduce_next_similar'
    content = {**payload.model_dump(exclude={'activity_match_token'}), 'evaluation': result, 'actual': actual,
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
    from starlette.concurrency import run_in_threadpool
    from app.db.iron_models import ActivityObservation
    from app.services.fit_observations import session_observations
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
        messages, errors = await run_in_threadpool(Decoder(Stream.from_byte_array(content)).read,
                                                  enable_crc_check=True)
        if errors:
            raise ValueError('Controllo integrità FIT fallito.')
        file_ids = messages.get('file_id_mesgs') or []
        if not file_ids or file_ids[0].get('type') != 'activity':
            raise ValueError('Serve un file di attività, non un allenamento programmato.')
        records = messages.get('session_mesgs') or []
        if not records or len(records) > 100:
            raise ValueError('Numero di sessioni non valido.')
        digest = sha256(content).hexdigest()
        observations = await run_in_threadpool(session_observations, messages)
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
    # All summaries and their observations belong to one import transaction.
    # The same file/session identity enriches an older summary-only import.
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, 'Completa le modifiche in corso prima di importare il file.')
    db.rollback()
    try:
        if db.get_bind().dialect.name == 'sqlite':
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        created = matched = 0
        for item in activities:
            data = item.model_dump()
            data['start_time'] = item.start_time.astimezone(timezone.utc).replace(tzinfo=None)
            data['source'] = 'fit'
            before = db.query(Activity).count()
            row = save_activity(db, data, commit=False)
            if row is None:
                raise HTTPException(409, 'Identità attività non valida; ricontrolla il database.')
            created_now = db.query(Activity).count() > before
            created += int(created_now)
            matched += int(not created_now)
            observation = db.query(ActivityObservation).filter_by(source_key=f'fit:{item.source_id}').one()
            index = int(item.source_id.rsplit(':', 1)[1])
            observation.payload_json = json.dumps({**data, **observations[index]}, default=str,
                                                  allow_nan=False, separators=(',', ':'))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, 'Importazione FIT non completata. Riprova dopo aver verificato i dati.') from None
    except Exception:
        db.rollback()
        raise
    result = {'created': created, 'matched': matched, 'source': 'fit',
              'received_at': datetime.utcnow().isoformat() + 'Z'}
    result['sessions_in_file'] = len(records)
    result['sessions_ignored'] = len(records) - len(activities)
    result['file_sha256'] = digest
    kept = [observations[int(item.source_id.rsplit(':', 1)[1])] for item in activities]
    result['details_preserved'] = {
        'recorded_samples': sum(item['fit_recording']['preserved_record_count'] for item in kept),
        'pool_lengths': sum(len(item['pool_lengths']['rows']) for item in kept),
        'partial_sessions': sum(bool(item['fit_recording']['truncated'] or
            item['fit_recording']['timeline_ambiguous'] or item['pool_lengths']['truncated'] or
            item['pool_lengths']['issues']) for item in kept)}
    return result
