from app.db.health_reports import HealthReport
import json
import math
from datetime import date as Date, datetime, timedelta
from types import SimpleNamespace
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Activity, WorkoutEvaluation
from app.db.experience_models import ActivityIdentity, SessionFeedback
from app.db.iron_models import RaceGoal, SyncRun, DuplicateCandidate, ActivityObservation
from app.services.athlete_clock import athlete_today, activity_day, as_utc_naive, utc_now_naive
from app.services.health import health_response
from app.services.iron_coach import assess, overview
from app.services.race_gap import gap_analysis
from app.api.routes_sync import local_only

router = APIRouter(tags=['Iron Coach'])


class Requirements(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    weekly_hours: float | None = Field(None, gt=0, le=168)
    longest_distance_km: float | None = Field(None, gt=0, le=2000)
    longest_elevation_m: float | None = Field(None, gt=0, le=30000)
    terrain_confirmed: bool | None = None
    altitude_confirmed: bool | None = None


class Leg(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    sport: Literal['swimming', 'cycling', 'running']
    terrain: Literal['road', 'trail', 'mixed', 'water'] = 'road'
    distance_km: float = Field(gt=0, le=2000)
    elevation_gain_m: float = Field(0, ge=0, le=30000)
    elevation_loss_m: float = Field(0, ge=0, le=30000)
    altitude_min_m: float | None = Field(None, ge=-500, le=9000)
    altitude_max_m: float | None = Field(None, ge=-500, le=9000)
    requirements: Requirements = Field(default_factory=Requirements)

    @model_validator(mode='after')
    def altitude_order(self):
        if self.altitude_min_m is not None and self.altitude_max_m is not None and self.altitude_min_m > self.altitude_max_m:
            raise ValueError('La quota minima supera la quota massima.')
        return self


class GoalInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=150)
    date: Date
    priority: Literal['finish', 'improve', 'time'] = 'finish'
    target_time: str | None = Field(None, pattern=r'^\d{1,3}:[0-5]\d:[0-5]\d$')
    legs: list[Leg] = Field(min_length=1, max_length=10)


class GoalPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(None, min_length=1, max_length=150)
    date: Date | None = None
    priority: Literal['finish', 'improve', 'time'] | None = None
    target_time: str | None = Field(None, pattern=r'^\d{1,3}:[0-5]\d:[0-5]\d$')
    legs: list[Leg] | None = Field(None, min_length=1, max_length=10)


def goal_row(db, identifier):
    row = db.get(RaceGoal, identifier)
    if not row: raise HTTPException(404, 'Obiettivo non trovato.')
    return row


def goal_dict(row):
    return {'id': row.id, 'name': row.name, 'date': row.date, 'priority': row.priority,
            'target_time': row.target_time, 'legs': json.loads(row.legs_json)}


@router.get('/goals')
def goals(db: Session = Depends(get_db)):
    return [goal_dict(r) for r in db.query(RaceGoal).order_by(RaceGoal.date).all()]


@router.post('/goals', status_code=201, dependencies=[Depends(local_only)])
def create_goal(payload: GoalInput, db: Session = Depends(get_db)):
    content = payload.model_dump(mode='json'); legs = content.pop('legs')
    row = RaceGoal(**content, legs_json=json.dumps(legs)); db.add(row); db.commit(); db.refresh(row)
    return goal_dict(row)


@router.get('/goals/{identifier}')
def read_goal(identifier: int, db: Session = Depends(get_db)):
    return goal_dict(goal_row(db, identifier))


@router.patch('/goals/{identifier}', dependencies=[Depends(local_only)])
def update_goal(identifier: int, payload: GoalPatch, db: Session = Depends(get_db)):
    row = goal_row(db, identifier); content = goal_dict(row); content.pop('id')
    content.update(payload.model_dump(mode='json', exclude_unset=True))
    try: validated = GoalInput.model_validate(content).model_dump(mode='json')
    except ValueError: raise HTTPException(422, 'Configurazione obiettivo non valida.') from None
    row.legs_json = json.dumps(validated.pop('legs'))
    for key, value in validated.items(): setattr(row, key, value)
    db.commit(); return goal_dict(row)


@router.delete('/goals/{identifier}', status_code=204, dependencies=[Depends(local_only)])
def delete_goal(identifier: int, db: Session = Depends(get_db)):
    goal = goal_row(db, identifier)
    from app.db.progress_models import GoalPreparationItem
    from app.db.planning_models import AthleteSettings
    settings = db.get(AthleteSettings, 1)
    if settings:
        profile = json.loads(settings.payload_json)
        if profile.get('race_goal_id') == identifier:
            profile.update(race_goal_id=None, event_name='', event_date=None, target_time=None)
            settings.payload_json = json.dumps(profile); settings.revision += 1; settings.updated_at = datetime.utcnow()
    db.query(GoalPreparationItem).filter_by(goal_id=identifier).delete(synchronize_session=False)
    db.delete(goal); db.commit()


@router.get('/goals/{identifier}/gap-analysis')
def gap(identifier: int, db: Session = Depends(get_db)):
    return gap_analysis(db, goal_row(db, identifier))


@router.get('/health/metrics')
def health_metrics(start: Date | None = Query(None, alias='from'), end: Date | None = Query(None, alias='to'), db: Session = Depends(get_db)):
    end = end or athlete_today(); start = start or end - timedelta(days=27)
    if start > end or (end - start).days > 3650: raise HTTPException(422, 'Intervallo non valido (massimo 10 anni).')
    return health_response(db, start, end)


@router.get('/coach/overview')
def coach_overview(db: Session = Depends(get_db)):
    return dashboard(db=db)['coach']


@router.get('/activities/{identifier}/assessment')
def activity_assessment(identifier: int, db: Session = Depends(get_db)):
    row = db.get(Activity, identifier)
    if not row: raise HTTPException(404, 'Attività non trovata.')
    from app.services.activity_rating import activity_ratings
    # Keep execution adherence distinct from the rating of recorded training.
    return {**activity_ratings(db)[identifier], 'execution_assessment': assess(db, row)}


@router.get('/dashboard/home')
def dashboard(day: Date | None = None, db: Session = Depends(get_db)):
    today = day or athlete_today()
    if today > athlete_today() or today < Date(1970, 1, 1):
        raise HTTPException(422, 'Scegli una data dal 1970 a oggi.')
    monday = today - timedelta(days=today.weekday())
    now = utc_now_naive()
    rows = [a for a in db.query(Activity).all()
            if as_utc_naive(a.start_time) <= now and activity_day(a.start_time) <= today]
    def source_load(activity):
        observation = db.query(ActivityObservation).filter(ActivityObservation.activity_id == activity.id, ActivityObservation.source_key.like('intervals:%')).first()
        # An explicit missing remote load must not revive an older canonical value.
        return json.loads(observation.payload_json).get('load') if observation else activity.load

    loads = {a.id: source_load(a) for a in rows}
    def summary(start, end):
        selected = [a for a in rows if start <= activity_day(a.start_time) <= end]
        def total(key, divisor=1):
            vals = [getattr(a, key) for a in selected]
            return round(sum(vals) / divisor, 2) if all(v is not None for v in vals) else None
        # Intervals observations take precedence for training load after confirmed merges.
        load_values = [loads[a.id] for a in selected]
        ascent = [a.elevation_gain_meters for a in selected if a.elevation_gain_meters is not None]
        return {'hours': total('duration_seconds', 3600), 'activities': len(selected),
                'elevation_m': sum(ascent),
                'elevation_known_m': round(sum(ascent),1) if ascent else None,
                'elevation_known_activities':len(ascent), 'elevation_missing_activities':len(selected)-len(ascent),
                'load': round(sum(load_values), 1) if all(v is not None for v in load_values) else None}
    current = summary(monday, today); previous = summary(monday - timedelta(days=7), today - timedelta(days=7))
    trend = (current['load'] / previous['load'] - 1) * 100 if current['load'] is not None and previous['load'] else None
    health = health_response(db, today - timedelta(days=27), today)
    # Keep local estimates visibly separate from imported observations.
    from app.services.training_load import fitness_metrics, sport_key
    def known_load(a):
        value = loads[a.id]
        if value is not None:
            return math.isfinite(value) and value >= 0
        return (sport_key(a.sport) == 'bike' and a.duration_seconds is not None
                and a.duration_seconds > 0 and a.normalized_power is not None and a.normalized_power > 0)

    # The legacy load engine treats unknown loads as zero. Only use its estimates
    # when the complete available activity history has usable load evidence.
    estimate_rows = [SimpleNamespace(start_time=a.start_time, sport=a.sport,
                     duration_seconds=a.duration_seconds, normalized_power=a.normalized_power,
                     load=loads[a.id]) for a in rows]
    estimates = (fitness_metrics(sorted(estimate_rows, key=lambda a: as_utc_naive(a.start_time)), 170, end_date=today)
                 if rows and all(known_load(a) for a in rows) else {})
    condition = []
    for metric in health['metrics']:
        if metric['kind'] in ('ctl', 'atl', 'tsb', 'sleep_hours', 'hrv_ms', 'resting_hr_bpm'):
            if not metric['latest'] and metric['kind'] in ('ctl', 'atl', 'tsb') and estimates.get('days', 0) > 0:
                metric = dict(metric, latest={'value': estimates.get(metric['kind']), 'source': 'local_estimate', 'date': str(today)}, stale=False,
                              explanation=metric['explanation'] + ' Stima locale con FTP legacy 170 W, non un dato Intervals.')
            condition.append(metric)
    coach = overview(db, day=today)
    from app.services.health import recovery_response
    recovery = recovery_response(db, today, rows, health['metrics'])
    history = [dict(start=str(monday-timedelta(days=7*i)), end=str(today-timedelta(days=7*i)),
                    **summary(monday-timedelta(days=7*i),today-timedelta(days=7*i))) for i in range(1,5)]
    from app.services.dashboard_coach import weekly_assessments, recorded_overview
    assessments = weekly_assessments(current, history, condition)
    from app.services.dashboard_coach import contextual_assessment
    from app.services.planning import load_settings
    from app.services.goal_planning import goal_context
    settings, goal, _ = goal_context(db, load_settings(db))
    from app.services.training_load import sport_key
    current['sports'] = {key:sum(1 for a in rows if monday <= activity_day(a.start_time) <= today and sport_key(a.sport) == key) for key in ('bike','run','swim')}
    from app.db.experience_models import AthleteCheckin
    checkin = db.query(AthleteCheckin).filter_by(date=str(today)).first()
    current['subjective'] = json.loads(checkin.payload_json) if checkin else {}
    from app.services.health_reports import active_restrictions
    current['restrictions'] = active_restrictions(db, today)
    current['date'] = str(today)
    current['sessions_3d'] = sum(1 for a in rows if today-timedelta(days=2) <= activity_day(a.start_time) <= today)
    current['recent_heavy_session'] = any((loads[a.id] or 0) >= 180 and today-timedelta(days=1) <= activity_day(a.start_time) <= today for a in rows)
    coach['training_assessment'], metric_assessments = contextual_assessment(current, assessments, condition, recovery, goal or settings)
    from app.integrations import strava
    from app.db.sync_models import IntervalsState
    intervals = db.get(IntervalsState, 1)
    try:
        tokens = strava.read_tokens()
        if not isinstance(tokens, dict): raise ValueError()
        connected = bool(tokens.get('refresh_token'))
        strava_status = {'status': 'connected' if connected else 'disconnected', 'connected': connected}
    except (OSError, ValueError, RuntimeError):
        strava_status = {'status': 'unavailable', 'connected': False}
    return {'date': str(today), 'week_start': str(monday), 'condition': condition, 'week': current,
            'recovery': recovery, 'historical': today < athlete_today(),
            'week_history':history, 'week_assessments':assessments, 'metric_assessments':metric_assessments,
            'previous_same_period': previous, 'load_trend_percent': round(trend, 1) if trend is not None else None,
            'coach': coach, 'sources': {'intervals': {'last_success_at': intervals.last_success_at if intervals else None},
            'strava': strava_status},
            'pending_duplicates': db.query(DuplicateCandidate).filter_by(status='pending').count()}


@router.get('/sync/runs/{identifier}')
def sync_run(identifier: int, db: Session = Depends(get_db)):
    row = db.get(SyncRun, identifier)
    if not row: raise HTTPException(404, 'Sincronizzazione non trovata.')
    return {'id': row.id, 'source': row.source, 'days': row.days, 'resources': row.resources, 'status': row.status,
            'started_at': row.started_at, 'finished_at': row.finished_at,
            'result': json.loads(row.result_json) if row.result_json else None}


@router.get('/activities/duplicates/pending')
def duplicates(db: Session = Depends(get_db)):
    def detail(a):
        return {'id': a.id, 'name': a.name, 'source': a.source, 'start_time': a.start_time,
                'duration_seconds': a.duration_seconds, 'distance_meters': a.distance_meters}
    return [{'id': r.id, 'activity': detail(db.get(Activity, r.activity_id)), 'other': detail(db.get(Activity, r.other_id))}
            for r in db.query(DuplicateCandidate).filter_by(status='pending').all()
            if db.get(Activity, r.activity_id) and db.get(Activity, r.other_id)]


@router.post('/activities/duplicates/{identifier}/{decision}', dependencies=[Depends(local_only)])
def resolve_duplicate(identifier: int, decision: Literal['merge', 'keep'], db: Session = Depends(get_db)):
    row = db.get(DuplicateCandidate, identifier)
    if not row or row.status != 'pending': raise HTTPException(404, 'Confronto non disponibile.')
    if decision == 'keep':
        row.status = 'kept'; db.commit(); return {'status': 'kept'}
    keep = db.get(Activity, row.other_id); remove = db.get(Activity, row.activity_id)
    if not keep or not remove: raise HTTPException(409, 'Attività non più disponibili.')
    left = db.query(SessionFeedback).filter_by(activity_id=keep.id).first()
    right = db.query(SessionFeedback).filter_by(activity_id=remove.id).first()
    if left and right: raise HTTPException(409, 'Entrambe le attività hanno feedback: risolvi gli abbinamenti prima della fusione.')
    if not db.query(ActivityIdentity).filter_by(activity_id=remove.id).first():
        # Legacy imports may predate the identity table; retain their remote ID.
        key = f'{remove.source}:{remove.source_id}'
        alias = db.query(ActivityIdentity).filter_by(source_key=key).first()
        if alias and alias.activity_id != keep.id:
            raise HTTPException(409, 'Identità esterna già collegata a un’altra attività.')
        if not alias: db.add(ActivityIdentity(source_key=key, activity_id=keep.id))

    def remap_payload(value):
        if isinstance(value, dict):
            return {key: keep.id if key in ('activity_id', 'matched_activity_id') and item == remove.id
                    else remap_payload(item) for key, item in value.items()}
        if isinstance(value, list): return [remap_payload(item) for item in value]
        return value

    for alias in db.query(ActivityIdentity).filter_by(activity_id=remove.id): alias.activity_id = keep.id
    for observation in db.query(ActivityObservation).filter_by(activity_id=remove.id): observation.activity_id = keep.id
    for evaluation in db.query(WorkoutEvaluation).filter_by(activity_id=remove.id):
        evaluation.activity_id = keep.id
        evaluation.payload_json = json.dumps(remap_payload(json.loads(evaluation.payload_json)))
    if right:
        right.activity_id = keep.id
        content = remap_payload(json.loads(right.payload_json)); content['activity_id'] = keep.id; right.payload_json = json.dumps(content)
    for field in ('duration_seconds', 'distance_meters', 'elevation_gain_meters', 'avg_hr', 'max_hr', 'avg_power', 'normalized_power', 'avg_speed', 'avg_cadence', 'calories', 'load'):
        if getattr(keep, field) is None: setattr(keep, field, getattr(remove, field))
    # Preserve comparisons involving a third activity, including explicit keep
    # decisions. Dropping those rows loses review work and future duplicate leads.
    candidates = db.query(DuplicateCandidate).filter((DuplicateCandidate.activity_id == remove.id) | (DuplicateCandidate.other_id == remove.id)).all()
    for candidate in candidates:
        first = keep.id if candidate.activity_id == remove.id else candidate.activity_id
        second = keep.id if candidate.other_id == remove.id else candidate.other_id
        if first == second:
            db.delete(candidate)
            continue
        existing = db.query(DuplicateCandidate).filter(
            ((DuplicateCandidate.activity_id == first) & (DuplicateCandidate.other_id == second)) |
            ((DuplicateCandidate.activity_id == second) & (DuplicateCandidate.other_id == first))).first()
        if existing and existing.id != candidate.id:
            if candidate.status == 'kept': existing.status = 'kept'
            db.delete(candidate)
        else:
            candidate.activity_id = first; candidate.other_id = second
    db.flush()
    db.delete(remove); db.commit()
    return {'status': 'merged', 'activity_id': keep.id}
