"""Manual plans and gym checklists. Planning/checking never creates an Activity."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import Workout
from app.db.training_models import GymProgress
from app.services.athlete_clock import athlete_today


class ManualStep(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    duration_min: int = Field(ge=1, le=600, strict=True)
    target_rpe: int = Field(ge=1, le=10, strict=True)
    instruction: str = Field('', max_length=1000)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('Indica il nome della fase.')
        return value.strip()


class GymExercise(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=120)
    sets: int = Field(ge=1, le=20, strict=True)
    reps: int | None = Field(None, ge=1, le=100, strict=True)
    work_seconds: int | None = Field(None, ge=5, le=600, strict=True)
    weight_kg: float | None = Field(None, ge=0, le=1000)
    rest_seconds: int = Field(60, ge=0, le=600, strict=True)
    notes: str = Field('', max_length=1000)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('Indica il nome dell’esercizio.')
        return value.strip()

    @model_validator(mode='after')
    def effort(self):
        if (self.reps is None) == (self.work_seconds is None):
            raise ValueError('Ogni esercizio richiede ripetizioni oppure secondi di lavoro.')
        return self


class ManualWorkoutInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    date: date
    sport: Literal['cycling', 'running', 'swimming', 'strength']
    name: str = Field(min_length=1, max_length=160)
    duration_min: int = Field(ge=5, le=600, strict=True)
    purpose: str = Field('', max_length=1500)
    target_rpe: int = Field(4, ge=1, le=10, strict=True)
    steps: list[ManualStep] = Field(default_factory=list, max_length=40)
    exercises: list[GymExercise] = Field(default_factory=list, max_length=30)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('Indica il nome della seduta.')
        return value.strip()

    @model_validator(mode='after')
    def consistent_program(self):
        if self.sport == 'strength':
            if not self.exercises or self.steps:
                raise ValueError('La scheda palestra richiede almeno un esercizio, senza fasi aerobiche.')
        elif self.exercises:
            raise ValueError('Gli esercizi appartengono alle schede palestra.')
        if self.steps and sum(step.duration_min for step in self.steps) != self.duration_min:
            raise ValueError('La somma delle fasi deve corrispondere alla durata della seduta.')
        return self


def create_manual(db, payload: ManualWorkoutInput):
    today = athlete_today()
    if not today <= payload.date <= today + timedelta(days=730):
        raise HTTPException(422, 'Pianifica da oggi ai prossimi due anni. Le attività svolte si importano nello storico.')
    from app.services.planning import load_settings, _audit, _snapshot, _latest, week_start, serialize_workout, _occupancy
    settings = load_settings(db)
    start = week_start(payload.date)
    rows = db.query(Workout).filter(Workout.date >= str(start), Workout.date < str(start + timedelta(days=7))).all()
    warnings = []
    if payload.sport in settings['excluded_sports']:
        warnings.append('La disciplina è esclusa nelle preferenze: questa scelta manuale non modifica il profilo.')
    if payload.sport == 'swimming' and not settings['pool_access']:
        warnings.append('Nel profilo non risulta accesso alla piscina: verifica dove svolgere il nuoto.')
    occupancy = _occupancy(db, start, rows)
    from app.services.time_constraints import available_minutes, windows
    available = available_minutes(settings, payload.date, payload.sport)
    if windows(settings, payload.date, payload.sport)['conflict']:
        warnings.append('Il giorno o questa disciplina sono in pausa nelle disponibilità temporanee: scegli una data compatibile prima di eseguire la seduta.')
    if occupancy[str(payload.date)] + payload.duration_min > available:
        warnings.append(f'Il totale del giorno supera i {available} minuti di disponibilità indicati nel profilo.')
    if sum(occupancy.values()) + payload.duration_min > settings['weekly_minutes']:
        warnings.append('Il totale della settimana supera il budget indicato nel profilo.')
    if payload.date == today:
        from app.services.health_reports import active_restrictions
        restrictions = active_restrictions(db, today)
        if restrictions.get('mode') != 'normal':
            warnings.append('La segnalazione fisica attiva richiede prudenza: la scheda manuale non è un via libera del Coach.')

    exercises = [exercise.model_dump() for exercise in payload.exercises]
    if exercises:
        # The calendar keeps the user's time budget; set-by-set execution has its own timer.
        fractions = [exercise['sets'] * ((exercise['work_seconds'] or exercise['reps'] * 3) + exercise['rest_seconds']) for exercise in exercises]
        durations = [round(payload.duration_min * part / sum(fractions), 2) for part in fractions]
        durations[-1] = round(payload.duration_min - sum(durations[:-1]), 2)
        steps = [{'type': 'strength', 'name': exercise['name'], 'duration_min': duration,
                  'target_rpe': payload.target_rpe,
                  'instruction': f"{exercise['sets']} serie · " + (f"{exercise['reps']} ripetizioni" if exercise['reps'] else f"{exercise['work_seconds']} secondi") + f" · recupero {exercise['rest_seconds']} s. {exercise['notes']}"}
                 for exercise, duration in zip(exercises, durations)]
    else:
        steps = [{'type': 'manual', **step.model_dump()} for step in payload.steps] or [
            {'type': 'manual', 'name': payload.name, 'duration_min': payload.duration_min,
             'target_rpe': payload.target_rpe, 'instruction': payload.purpose}]
    content = {'source': 'manual', 'sport': payload.sport, 'name': payload.name,
               'duration_min': payload.duration_min, 'expected_rpe': payload.target_rpe,
               'reason': payload.purpose, 'purpose': payload.purpose, 'steps': steps,
               'exercises': exercises, 'manual_warnings': warnings}
    latest = _latest(db, start)
    metadata = json.loads(latest.metadata_json) if latest else {}
    _audit(db, start, 'manual_create', _snapshot(rows), {**metadata, 'date': str(payload.date), 'sport': payload.sport})
    row = Workout(date=str(payload.date), sport=payload.sport, name=payload.name,
                  duration_min=payload.duration_min, status='planned',
                  payload_json=json.dumps(content, ensure_ascii=False, allow_nan=False))
    db.add(row)
    db.commit()
    db.refresh(row)
    return {**serialize_workout(row), 'warnings': warnings}


class CompletedSet(BaseModel):
    model_config = ConfigDict(extra='forbid')
    exercise_index: int = Field(ge=0, strict=True)
    set_index: int = Field(ge=0, strict=True)


class GymTimer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    remaining_seconds: int = Field(0, ge=0, le=600, strict=True)
    running: bool = False
    ends_at: datetime | None = None

    @model_validator(mode='after')
    def valid_timer(self):
        if self.running:
            if not self.ends_at or self.ends_at.tzinfo is None or self.remaining_seconds == 0:
                raise ValueError('Un timer avviato richiede scadenza con fuso orario e tempo residuo.')
        elif self.ends_at is not None:
            raise ValueError('Un timer in pausa non deve avere una scadenza.')
        return self


class GymProgressInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0, lt=2**63 - 1, strict=True)
    completed_sets: list[CompletedSet] = Field(default_factory=list, max_length=600)
    active_exercise: int = Field(0, ge=0, strict=True)
    timer: GymTimer = Field(default_factory=GymTimer)
    notes: str = Field('', max_length=2000)


def gym_workout(db, workout_id):
    row = db.get(Workout, workout_id)
    if not row:
        raise HTTPException(404, 'Scheda palestra non trovata.')
    if row.status in ('skipped', 'superseded', 'cancelled'):
        raise HTTPException(409, 'La scheda non è più attiva nel piano.')
    content = json.loads(row.payload_json)
    if row.sport != 'strength' or not content.get('exercises'):
        raise HTTPException(422, 'Questa seduta non contiene una scheda palestra per serie.')
    return row, content


def validate_progress_for_plan(progress: dict, exercises: list):
    value = GymProgressInput.model_validate(progress)
    if value.active_exercise >= len(exercises):
        raise ValueError('Esercizio selezionato inesistente.')
    pairs = [(item.exercise_index, item.set_index) for item in value.completed_sets]
    if len(pairs) != len(set(pairs)) or any(index >= len(exercises) or number >= exercises[index]['sets'] for index, number in pairs):
        raise ValueError('Serie duplicate o non presenti nella scheda.')
    return value


def read_progress(db, workout_id):
    workout, content = gym_workout(db, workout_id)
    row = db.query(GymProgress).filter_by(workout_id=workout_id).first()
    progress = json.loads(row.payload_json) if row else {'completed_sets': [], 'active_exercise': 0,
        'timer': {'remaining_seconds': 0, 'running': False, 'ends_at': None}, 'notes': ''}
    # Expired wall-clock timers stay expired after a refresh or a sleeping tab.
    timer = progress['timer']
    if timer.get('running'):
        until = datetime.fromisoformat(timer['ends_at'].replace('Z', '+00:00'))
        seconds = max(0, min(600, int((until - datetime.now(timezone.utc)).total_seconds() + .999)))
        timer['remaining_seconds'] = seconds
        if seconds == 0:
            timer.update(running=False, ends_at=None)
    total = sum(exercise['sets'] for exercise in content['exercises'])
    return {'workout_id': workout.id, 'date': workout.date, 'name': workout.name,
            'exercises': content['exercises'], 'revision': row.revision if row else 0,
            'progress': progress, 'completed_count': len(progress['completed_sets']), 'total_sets': total,
            'finished': len(progress['completed_sets']) == total, 'activity_created': False}


def save_progress(db, workout_id, payload: GymProgressInput):
    workout, content = gym_workout(db, workout_id)
    if date.fromisoformat(workout.date) > athlete_today():
        raise HTTPException(409, 'Puoi seguire la scheda dal giorno programmato.')
    try:
        validate_progress_for_plan(payload.model_dump(), content['exercises'])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if payload.timer.running:
        until = payload.timer.ends_at.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if not now - timedelta(days=1) <= until <= now + timedelta(seconds=601):
            raise HTTPException(422, 'La scadenza del recupero non è valida.')
    progress = payload.model_dump(mode='json', exclude={'expected_revision'})
    values = {'payload_json': json.dumps(progress, ensure_ascii=False), 'updated_at': datetime.utcnow()}
    if payload.expected_revision == 0:
        statement = sqlite_insert(GymProgress).values(workout_id=workout_id, revision=1, **values)
        statement = statement.on_conflict_do_nothing(index_elements=[GymProgress.workout_id])
    else:
        statement = update(GymProgress).where(GymProgress.workout_id == workout_id, GymProgress.revision == payload.expected_revision).values(**values, revision=GymProgress.revision + 1)
    revision = db.execute(statement.returning(GymProgress.revision)).scalar_one_or_none()
    if revision is None:
        db.rollback()
        raise HTTPException(412, 'Scheda modificata in un’altra finestra. Riaprila per caricare il progresso aggiornato.')
    db.commit()
    return read_progress(db, workout_id)
