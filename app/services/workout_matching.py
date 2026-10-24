"""Dated, explainable candidates; never match or complete a session implicitly."""
from datetime import date, datetime, timedelta
import hashlib
import hmac
import json
import math

from fastapi import HTTPException
from sqlalchemy import func, or_

from app.db.models import Activity
from app.db.experience_models import SessionFeedback
from app.db.iron_models import DuplicateCandidate
from app.services.athlete_clock import activity_day, as_utc_naive, day_start_utc, utc_now_naive
from app.services.training_load import sport_key

MAX_SCAN = 500
MAX_CANDIDATES = 12


def finite(value, *, minimum=0):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum else None


def serial(value):
    if isinstance(value, datetime): return value.isoformat(timespec='microseconds')
    if isinstance(value, float) and not math.isfinite(value): return {'unavailable_number':repr(value)}
    return value


def row_dict(row):
    return {column.name: serial(getattr(row, column.name)) for column in row.__table__.columns}


def token_for(workout, activity, feedbacks, duplicates):
    content = {'workout':row_dict(workout), 'activity':row_dict(activity),
        'links':[row_dict(row) for row in feedbacks if row.workout_id == workout.id or row.activity_id == activity.id],
        'duplicates':[row_dict(row) for row in duplicates if activity.id in (row.activity_id,row.other_id)]}
    raw = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(',',':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def candidate_context(db, workout, identifiers):
    feedbacks = db.query(SessionFeedback).filter(or_(SessionFeedback.workout_id == workout.id,
        SessionFeedback.activity_id.in_(identifiers))).order_by(SessionFeedback.id).all()
    duplicates = db.query(DuplicateCandidate).filter(or_(DuplicateCandidate.activity_id.in_(identifiers),
        DuplicateCandidate.other_id.in_(identifiers))).order_by(DuplicateCandidate.id).all()
    return feedbacks, duplicates


def activity_candidates(db, workout):
    try:
        target = date.fromisoformat(workout.date)
        start = max(date(1970,1,1), target - timedelta(days=2))
        end = target + timedelta(days=2)
        lower, upper = day_start_utc(start), day_start_utc(end + timedelta(days=1))
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(409, 'La data della seduta non consente di cercare attività. Verifica il piano.') from None
    now = utc_now_naive()
    rows = db.query(Activity).filter(Activity.start_time >= lower, Activity.start_time < upper,
        Activity.start_time <= now).order_by(func.abs(func.julianday(Activity.start_time) - func.julianday(day_start_utc(target))),
                                           Activity.id).limit(MAX_SCAN + 1).all()
    truncated_scan = len(rows) > MAX_SCAN
    rows = rows[:MAX_SCAN]
    rows = [row for row in rows if sport_key(row.sport) == sport_key(workout.sport)]
    identifiers = [row.id for row in rows]
    feedbacks, duplicates = candidate_context(db, workout, identifiers)
    used = {row.activity_id:row.workout_id for row in feedbacks if row.activity_id is not None}
    current = next((row.activity_id for row in feedbacks if row.workout_id == workout.id), None)
    planned = finite(workout.duration_min)
    items = []
    for row in rows:
        duration = finite(row.duration_seconds)
        invalid_duration = row.duration_seconds is not None and duration is None
        available = not invalid_duration and (row.id not in used or used[row.id] == workout.id)
        reason = 'Durata registrata non valida: verifica la fonte.' if invalid_duration else (
            'Attività già associata a un’altra seduta.' if not available else None)
        day = activity_day(row.start_time)
        minutes = duration / 60 if duration is not None else None
        items.append({'id':row.id,'name':row.name,'source':row.source,'source_id':row.source_id,
            'start_time':serial(row.start_time),'activity_date':str(day),'sport':row.sport,
            'duration_min':round(minutes,2) if minutes is not None else None,
            'day_delta':(day-target).days,
            'duration_delta_min':round(minutes-planned,2) if minutes is not None and planned is not None else None,
            'available':available,'unavailable_reason':reason,'current':row.id == current,
            'duplicate_warning':any(value.status == 'pending' and row.id in (value.activity_id,value.other_id) for value in duplicates),
            'match_token':token_for(workout,row,feedbacks,duplicates) if available else None})
    items.sort(key=lambda row:(not row['current'], not row['available'], abs(row['day_delta']),
        abs(row['duration_delta_min']) if row['duration_delta_min'] is not None else float('inf'), row['id']))
    return {'workout':{'id':workout.id,'date':workout.date,'sport':workout.sport,'name':workout.name,
                      'duration_min':planned,'status':workout.status},
        'window':{'start':str(start),'end':str(end),'timezone':'Europe/Rome'},
        'items':items[:MAX_CANDIDATES],
        'counts':{'eligible':sum(row['available'] for row in items),'returned':min(len(items),MAX_CANDIDATES),
                  'total':len(items),'truncated':truncated_scan or len(items)>MAX_CANDIDATES},
        'limits':['Le proposte confrontano disciplina, giorno e durata: non provano che la seduta sia stata eseguita.',
                  'Scegli un’attività e salva il feedback per confermare. Le attività future sono escluse.',
                  'Una durata mancante resta sconosciuta e non produce aderenza alla durata.',
                  f'Massimo {MAX_CANDIDATES} proposte da {MAX_SCAN} registrazioni vicine; non è una ricerca su tutto lo storico.']}


def validate_activity(db, workout, identifier, expected_token=None):
    activity = db.get(Activity, identifier)
    if not activity: raise HTTPException(404, 'Attività non trovata. Rileggi le proposte.')
    if as_utc_naive(activity.start_time) > utc_now_naive():
        raise HTTPException(422, 'Non puoi confermare un’attività con data e ora future.')
    if sport_key(activity.sport) != sport_key(workout.sport):
        raise HTTPException(422, 'La disciplina non corrisponde alla seduta.')
    if abs((activity_day(activity.start_time) - date.fromisoformat(workout.date)).days) > 2:
        raise HTTPException(422, 'La data dell’attività è troppo distante dalla seduta.')
    if activity.duration_seconds is not None and finite(activity.duration_seconds) is None:
        raise HTTPException(422, 'La durata dell’attività non è valida. Verifica la fonte prima di associarla.')
    feedbacks, duplicates = candidate_context(db, workout, [identifier])
    if any(row.activity_id == identifier and row.workout_id != workout.id for row in feedbacks):
        raise HTTPException(409, 'Attività già associata a un’altra seduta. Rileggi le proposte.')
    if expected_token and not hmac.compare_digest(expected_token,token_for(workout,activity,feedbacks,duplicates)):
        raise HTTPException(409, 'Attività o seduta cambiate dopo il confronto. Rileggi le proposte e conferma di nuovo.')
    return activity
