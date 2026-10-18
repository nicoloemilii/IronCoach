"""Conservative, explainable calendar planning; no clinical predictions.

The same persisted Workout payload is consumed by the web UI and device export.
Planning never treats a missing threshold, history or recovery signal as measured.
"""
from __future__ import annotations

import json
import hashlib
import hmac
import re
from copy import deepcopy
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import Activity, DailyMetrics, Workout
from app.db.experience_models import AthleteCheckin, SessionFeedback
from app.db.garmin_models import GarminDelivery
from app.db.planning_models import AthleteSettings, PlanRevision
from app.services.athlete_profile import load_profile
from app.services.athlete_clock import athlete_today, activity_day, day_start_utc
from app.services.training_load import sport_key
from app.services.health import series
from app.services.goal_planning import goal_context, allocation_for_goal, focus_for_sport
from app.db.iron_models import RaceGoal
from app.db.progress_models import WorkoutResolution
from app.services.time_constraints import TimeConstraint, windows, available_minutes

SPORTS = ("cycling", "running", "swimming", "strength")
LABELS = {"cycling": "Bici", "running": "Corsa", "swimming": "Nuoto", "strength": "Forza e mobilità"}
THRESHOLD_KEYS = ("cycling_ftp_watts", "running_threshold_pace_sec_per_km", "swimming_css_sec_per_100m")


def _today() -> date:
    return athlete_today()


class Threshold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: float | None = Field(None, ge=20, le=1800, allow_inf_nan=False)
    source: Literal["unknown", "estimated", "measured"] = "unknown"
    measured_on: date | None = None

    @model_validator(mode="after")
    def provenance(self):
        if self.value is not None and self.source == "unknown":
            raise ValueError("Indicare se la soglia è stimata o misurata.")
        if self.source == "measured" and self.measured_on is None:
            raise ValueError("Una soglia misurata richiede la data del test.")
        if self.measured_on and self.measured_on > _today():
            raise ValueError("La data del test non può essere futura.")
        return self


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cycling_ftp_watts: Threshold = Field(default_factory=Threshold)
    running_threshold_pace_sec_per_km: Threshold = Field(default_factory=Threshold)
    swimming_css_sec_per_100m: Threshold = Field(default_factory=Threshold)

    @model_validator(mode="after")
    def plausible_units(self):
        bounds = {"cycling_ftp_watts": (30, 700), "running_threshold_pace_sec_per_km": (120, 900), "swimming_css_sec_per_100m": (40, 360)}
        for key, (low, high) in bounds.items():
            value = getattr(self, key).value
            if value is not None and not low <= value <= high:
                raise ValueError(f"{key}: valore fuori dall'intervallo {low}–{high}.")
        return self


class PlanningProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", max_length=100)
    height_cm: float | None = Field(None, ge=50, le=250, allow_inf_nan=False)
    birth_date: date | None = None
    sex: Literal['female', 'male', 'other'] | None = None
    experience: Literal["beginner", "intermediate", "advanced"] = "beginner"
    race_distance: Literal["sprint", "olympic", "70.3", "ironman"] = "ironman"
    goal_priority: Literal["finish", "improve", "time"] = "finish"
    event_name: str = Field("", max_length=160)
    event_date: date | None = None
    target_time: str | None = Field(None, max_length=30)
    race_goal_id: int | None = Field(None, gt=0)
    weekly_minutes: int = Field(360, ge=30, le=2400)
    recent_weekly_minutes: int | None = Field(None, ge=0, le=2400)
    availability: list[int] = Field(default_factory=lambda: [0, 60, 60, 60, 45, 150, 90], min_length=7, max_length=7)
    pool_access: bool = True
    limitations: str = Field("", max_length=1200)
    excluded_sports: list[Literal["cycling", "running", "swimming", "strength"]] = Field(default_factory=list, max_length=4)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    time_constraints: list[TimeConstraint] = Field(default_factory=list, max_length=100)

    @field_validator('height_cm', mode='before')
    @classmethod
    def actual_height(cls, value):
        if isinstance(value, bool):
            raise ValueError('Indica l’altezza in centimetri.')
        return value

    @field_validator('birth_date')
    @classmethod
    def valid_birth_date(cls, value):
        if value and (value > _today() or (_today().year-value.year-((_today().month,_today().day)<(value.month,value.day))) > 120):
            raise ValueError('Indica una data di nascita valida, non futura, entro 120 anni.')
        return value

    @field_validator("availability")
    @classmethod
    def valid_availability(cls, values):
        if any(v < 0 or v > 600 for v in values):
            raise ValueError("Ogni giorno richiede da 0 a 600 minuti disponibili.")
        return values


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def load_settings(db: Session) -> dict:
    row = db.get(AthleteSettings, 1)
    if row:
        return PlanningProfile.model_validate(json.loads(row.payload_json)).model_dump(mode="json")
    legacy = load_profile()
    goals = legacy.get("goals") or {}
    # Legacy defaults are intentionally not promoted to measured thresholds.
    return PlanningProfile(name=legacy.get("name") or "", event_name=goals.get("event") or "", event_date=goals.get("event_date"), target_time=goals.get("target_time")).model_dump(mode="json")


def get_settings(db: Session) -> dict:
    row = db.get(AthleteSettings, 1)
    return {"profile": load_settings(db), "revision": row.revision if row else 0}


def save_settings(db: Session, payload: PlanningProfile | dict, expected_revision: int | None = None) -> dict:
    profile = payload if isinstance(payload, PlanningProfile) else PlanningProfile.model_validate(payload)
    if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
                                          or not 0 <= expected_revision <= 9223372036854775807):
        raise HTTPException(422, 'Revisione del profilo non valida: usare un intero non negativo.')
    content = profile.model_dump(mode="json")
    values = {"payload_json": _dump(content), "updated_at": datetime.utcnow()}
    if expected_revision is None:
        # Legacy clients retain last-write-wins behavior, while the increment
        # itself remains atomic even when two clients save concurrently.
        statement = sqlite_insert(AthleteSettings).values(id=1, revision=1, **values)
        statement = statement.on_conflict_do_update(
            index_elements=[AthleteSettings.id],
            set_={**values, "revision": AthleteSettings.revision + 1},
        )
    elif expected_revision == 0:
        # Revision zero represents the unsaved profile. Concurrent first saves
        # must not both pass a Python-level absence check.
        statement = sqlite_insert(AthleteSettings).values(id=1, revision=1, **values)
        statement = statement.on_conflict_do_nothing(index_elements=[AthleteSettings.id])
    else:
        statement = update(AthleteSettings).where(
            AthleteSettings.id == 1, AthleteSettings.revision == expected_revision,
        ).values(**values, revision=AthleteSettings.revision + 1)
    revision = db.execute(statement.returning(AthleteSettings.revision)).scalar_one_or_none()
    if revision is None:
        db.rollback()
        current = db.execute(select(AthleteSettings.revision).where(AthleteSettings.id == 1)).scalar_one_or_none()
        raise HTTPException(412, {"message": "Il profilo è stato modificato in un’altra finestra. Ricarica i dati aggiornati prima di salvare.",
                                  "current_revision": current if current is not None else 0})
    db.commit()
    return {"profile": content, "revision": revision}


def activate_goal(db: Session, goal_id: int | None, expected_revision: int | None = None) -> dict:
    profile = load_settings(db)
    if goal_id is not None:
        goal = db.get(RaceGoal, goal_id)
        if not goal:
            raise HTTPException(404, 'Obiettivo non trovato: aggiorna l’elenco e scegli una gara disponibile.')
        profile.update(event_name=goal.name, event_date=goal.date, goal_priority=goal.priority,
                       target_time=goal.target_time)
    profile['race_goal_id'] = goal_id
    return save_settings(db, profile, expected_revision=expected_revision)


def week_start(value: date | str | None = None) -> date:
    try:
        day = value if isinstance(value, date) else date.fromisoformat(value) if value else _today()
    except (TypeError, ValueError):
        raise HTTPException(422, "Data non valida: usare YYYY-MM-DD.")
    return day - timedelta(days=day.weekday())


def _rows(db: Session, start: date):
    return db.query(Workout).filter(Workout.date >= start.isoformat(), Workout.date < (start + timedelta(days=7)).isoformat(), Workout.status != "superseded").order_by(Workout.date, Workout.id).all()


def _published(db: Session, workout_id: int) -> bool:
    return db.query(GarminDelivery).filter(GarminDelivery.workout_id == workout_id, GarminDelivery.remote_published.is_(True)).first() is not None


def _recorded_minutes(db: Session, start: date) -> Counter:
    """Count real work already done this week, even if it had no planned session."""
    rows = db.query(Activity).filter(Activity.start_time >= day_start_utc(start), Activity.start_time < day_start_utc(start + timedelta(days=7)), Activity.start_time <= datetime.utcnow()).all()
    result = Counter()
    for row in rows:
        if (row.duration_seconds or 0) >= 300 and sport_key(row.sport) in ("bike", "run", "swim", "strength"):
            result[activity_day(row.start_time).isoformat()] += row.duration_seconds / 60
    return result


def _occupancy(db: Session, start: date, rows: list[Workout]) -> Counter:
    pending, completed = Counter(), Counter()
    transitions = set()
    for row in rows:
        if row.status not in ("skipped", "superseded"):
            (completed if row.status == "completed" else pending)[row.date] += row.duration_min
            brick = json.loads(row.payload_json).get('brick', {})
            if brick.get('sequence') == 1:
                transitions.add((row.date, brick['group_id']))
    transition_minutes = Counter()
    for day, _ in transitions:
        transition_minutes[day] += 5
    recorded = _recorded_minutes(db, start)
    # Real completed work and its completed plan are one stimulus. Pending sessions
    # are still future work, even when another activity was recorded on that day.
    return Counter({day: pending[day] + max(completed[day], recorded[day]) + transition_minutes[day] for day in set(pending) | set(completed) | set(recorded)})


def serialize_workout(row: Workout) -> dict:
    return {"id": row.id, "date": row.date, "sport": row.sport, "name": row.name, "duration_min": row.duration_min, "status": row.status, "payload": json.loads(row.payload_json)}


def _snapshot(rows):
    return [serialize_workout(row) for row in rows]


def _latest(db: Session, start: date):
    return db.query(PlanRevision).filter(PlanRevision.week_start == start.isoformat()).order_by(PlanRevision.version.desc()).first()


def _has_generated_plan(db: Session, start: date) -> bool:
    # A manually added or moved session is an audit revision, not an automatically
    # generated week. Undoing the original generation also restores that distinction.
    return db.query(PlanRevision.id).filter(
        PlanRevision.week_start == start.isoformat(),
        PlanRevision.action.in_(['generate', 'regenerate']),
        PlanRevision.undone_at.is_(None),
    ).first() is not None


def _audit(db: Session, start: date, action: str, before: list, metadata: dict) -> PlanRevision:
    latest = _latest(db, start)
    row = PlanRevision(week_start=start.isoformat(), version=(latest.version + 1 if latest else 1), action=action, snapshot_json=_dump(before), metadata_json=_dump(metadata))
    db.add(row)
    db.flush()
    return row


def _phase(profile: dict, start: date) -> str:
    event = profile.get("event_date")
    if not event:
        return "base"
    days = (date.fromisoformat(event) - start).days
    if -14 <= days < 0:
        return "post_race"
    if days < -14:
        return "base"
    if days <= 7:
        return "race_week"
    if days <= 21:
        return "taper"
    if days <= 56:
        return "specific"
    return "base"


def _allocation(profile: dict) -> dict:
    """Transparent planning heuristics, never a prediction of race performance."""
    shares = {
        "sprint": dict(cycling=.35, running=.30, swimming=.25, strength=.10),
        "olympic": dict(cycling=.40, running=.30, swimming=.22, strength=.08),
        "70.3": dict(cycling=.48, running=.28, swimming=.17, strength=.07),
        "ironman": dict(cycling=.52, running=.27, swimming=.15, strength=.06),
    }[profile["race_distance"]].copy()
    detailed = allocation_for_goal(profile['_race_goal'], profile['goal_priority']) if profile.get('_race_goal') else None
    if detailed:
        shares = detailed['shares']
    multipliers = {
        "finish": dict(cycling=.92, running=.95, swimming=1.12, strength=1.25),
        "improve": dict(cycling=.98, running=1.02, swimming=1.03, strength=1.0),
        "time": dict(cycling=1.06, running=1.03, swimming=.95, strength=.85),
    }[profile["goal_priority"]]
    adjusted = {sport: share * multipliers[sport] for sport, share in shares.items()}
    total = sum(adjusted.values())
    focus = {
        "finish": "Continuità nelle tre discipline, tecnica e tolleranza del volume facile.",
        "improve": "Regolarità della risposta all'allenamento e confronto tra seduta prevista ed eseguita.",
        "time": "Regolarità del ritmo e gestione dello sforzo, con volume vincolato alla capacità recente.",
    }[profile["goal_priority"]]
    if detailed:
        focus = detailed['focus']
    return {"shares": {sport: round(value / total, 4) for sport, value in adjusted.items()}, "focus": focus, "race_distance": profile["race_distance"], "goal_priority": profile["goal_priority"], "long_session_weight": {"finish": 1.15, "improve": 1.30, "time": 1.45}[profile["goal_priority"]], "note": "Ripartizione orientativa: disponibilità, storico per disciplina e feedback prevalgono sulle percentuali.",
            **({'race_goal_id': profile['_race_goal']['id'], 'proxy': detailed['proxy']} if detailed else {})}


def _feedback_signals(db: Session, start: date) -> dict:
    today = _today()
    cutoff = (today - timedelta(days=14)).isoformat()
    # Session date, rather than edit time, prevents an edited old report becoming a new signal.
    rows = db.query(SessionFeedback, Workout).join(Workout, SessionFeedback.workout_id == Workout.id).filter(Workout.date >= cutoff, Workout.date <= today.isoformat()).all()
    signals = {sport: {"samples": 0, "concerning": 0, "factor": 1.0, "workout_ids": []} for sport in SPORTS}
    seen = set()
    for feedback, workout in rows:
        if workout.id in seen or workout.sport not in signals:
            continue
        seen.add(workout.id)
        content = json.loads(feedback.payload_json)
        signal = signals[workout.sport]
        signal["samples"] += 1
        completion = content.get("evaluation", {}).get("completion", content.get("completion_percent", 100))
        concerning = content.get("feeling") == "harder" or float(content.get("rpe") or 0) >= 8 or (completion is not None and float(completion) < 70)
        if concerning:
            signal["concerning"] += 1
            signal["workout_ids"].append(workout.id)
    applied = start <= today + timedelta(days=7)
    for signal in signals.values():
        # One bounded factor per sport, not a multiplication on every save/generation.
        signal["factor"] = (0.65 if signal["concerning"] >= 2 else .8 if signal["concerning"] else 1.0) if applied else 1.0
    return {"window_days": 14, "applied": applied, "sports": signals}


def _capacity(db: Session, profile: dict, start: date) -> dict:
    today = _today()
    # Future requested weeks cannot turn an absence of future activities into detraining.
    anchor = min(today, start)
    since = day_start_utc(anchor - timedelta(days=28))
    until = day_start_utc(anchor)
    rows = db.query(Activity).filter(Activity.start_time >= since, Activity.start_time < until).all()
    valid = [a for a in rows if (a.duration_seconds or 0) >= 300 and sport_key(a.sport) in ("bike", "run", "swim", "strength")]
    observed_minutes = sum(a.duration_seconds / 60 for a in valid) / 4
    reasons = []
    if len(valid) >= 6:
        # Fixed 28-day denominator avoids inflating capacity from a few active days.
        limit = int(observed_minutes * 1.05)
        confidence = min(85, 45 + len(valid))
        reasons.append(f"Capacità: {round(observed_minutes)} min/settimana osservati negli ultimi 28 giorni; incremento massimo proposto 5%.")
    else:
        fallback = {"beginner": 180, "intermediate": 240, "advanced": 300}[profile["experience"]]
        declared = profile.get("recent_weekly_minutes")
        limit = min(fallback, declared) if declared is not None else fallback
        confidence = 25 if declared is None else 40
        reasons.append("Storico insufficiente: settimana di conoscenza, intensità facile e volume prudente.")
        if declared is not None:
            reasons.append("Il volume recente dichiarato è un'autovalutazione, non un dato verificato.")
    phase = _phase(profile, start)
    if phase in ("taper", "race_week", "post_race"):
        factor = {"taper": .70, "race_week": .40, "post_race": .35}[phase]
        limit = int(limit * factor)
        reasons.append({"taper": "Avvicinamento gara: volume ridotto, nessun recupero delle sedute perse.", "race_week": "Settimana gara: solo richiami facili; gara e strategia restano da confermare.", "post_race": "Recupero dopo gara: rientro leggero e da confermare con le sensazioni."}[phase])
    if profile.get("limitations"):
        limit = int(limit * .75)
        confidence = min(confidence, 35)
        reasons.append("Limitazioni dichiarate: volume ridotto e lavoro facile; il testo libero non viene interpretato come autorizzazione clinica.")
    latest_metrics = db.query(DailyMetrics).filter(DailyMetrics.date <= today.isoformat(), DailyMetrics.date >= (today - timedelta(days=2)).isoformat()).order_by(DailyMetrics.date.desc()).first()
    recovery_points = series(db, today - timedelta(days=2), today)
    latest_recovery = {p['kind']: p['value'] for p in recovery_points}
    # A recent recovery signal only changes the current/next week, not distant plans.
    low_recovery = False
    if latest_recovery and start <= today + timedelta(days=7):
        low_recovery = ((latest_recovery.get('sleep_hours') is not None and latest_recovery['sleep_hours'] < 6) or (latest_recovery.get('body_battery') is not None and latest_recovery['body_battery'] < 30) or (latest_recovery.get('stress_score') is not None and latest_recovery['stress_score'] >= 75))
    if low_recovery:
        limit = int(limit * .7)
        reasons.append("Segnali recenti di recupero scarso: durata ridotta e nessun lavoro intenso.")
    elif not latest_recovery:
        reasons.append("Dati di recupero recenti assenti: nessuna autorizzazione automatica all'intensità.")
    stale = []
    for key, item in profile["thresholds"].items():
        if item.get("value") is not None and item.get("measured_on") and (today - date.fromisoformat(item["measured_on"])).days > 90:
            stale.append(key)
    if stale:
        reasons.append("Soglie datate oltre 90 giorni: confermare i riferimenti; usare la percezione dello sforzo come controllo.")
    blocked_dates = []
    journal_good = False
    journal = db.query(AthleteCheckin).filter(AthleteCheckin.date <= today.isoformat(), AthleteCheckin.date >= (today - timedelta(days=2)).isoformat()).order_by(AthleteCheckin.date.desc()).first()
    if journal and start <= today + timedelta(days=7):
        entry = json.loads(journal.payload_json)
        fatigue, soreness = entry.get("fatigue"), entry.get("soreness")
        sleep_quality, motivation = entry.get("sleep_quality"), entry.get("motivation")
        # Clearing a field means unknown, never either poor or good recovery.
        journal_good = (not entry.get("pain") and all(value is not None for value in (fatigue, soreness, sleep_quality, motivation)) and fatigue <= 2 and soreness <= 2 and sleep_quality >= 3 and motivation >= 3)
        journal_low = entry.get("pain") or (fatigue is not None and fatigue >= 4) or (soreness is not None and soreness >= 4) or (sleep_quality is not None and sleep_quality <= 2)
        if journal_low:
            limit = int(limit * .7)
            low_recovery = True
            reasons.append("Check-in recente con dolore, fatica o indolenzimento elevati: volume ridotto; non è un via libera all'allenamento.")
        if entry.get("pain") and journal.date == today.isoformat():
            blocked_dates.append(today.isoformat())
            reasons.append("Dolore segnalato oggi: nessuna nuova seduta prescritta oggi. Valutare il sintomo prima di riprendere.")
    feedback = _feedback_signals(db, start)
    allocation = _allocation(profile)
    feedback_factor = sum(share * feedback["sports"][sport]["factor"] for sport, share in allocation["shares"].items()) / sum(allocation["shares"].values())
    limit = int(limit * feedback_factor)
    for sport, signal in feedback["sports"].items():
        if signal["factor"] < 1:
            reasons.append(f"{LABELS[sport]}: {signal['concerning']} feedback recenti con fatica superiore alle attese o completamento ridotto; quota e durata massima ridotte, senza trasferire automaticamente il volume ad altre discipline.")
    by_sport = {}
    sport_history = {}
    for sport in SPORTS:
        aliases = {"cycling": "bike", "running": "run", "swimming": "swim", "strength": "strength"}
        sessions = [a for a in valid if sport_key(a.sport) == aliases[sport]]
        sport_history[sport] = len(sessions)
        base_cap = {"cycling": 60, "running": 30, "swimming": 30, "strength": 25}[sport]
        longest = max((a.duration_seconds / 60 for a in sessions), default=base_cap)
        absolute_cap = {"cycling": 240, "running": 120, "swimming": 90, "strength": 45}[sport]
        cap = min(absolute_cap, int(longest * 1.05)) if len(sessions) >= 2 else base_cap
        by_sport[sport] = max(15, int(cap * feedback["sports"][sport]["factor"]))
    return {"limit": max(0, limit), "confidence": confidence, "rationale": reasons, "history_sessions": len(valid), "history_by_sport": sport_history, "observed_weekly_minutes": round(observed_minutes), "phase": phase, "low_recovery": low_recovery, "good_recovery": journal_good and not low_recovery, "session_caps": by_sport, "blocked_dates": blocked_dates, "feedback": feedback, "allocation": allocation}


def _make_workout(profile: dict, sport: str, minutes: int, priority: str, phase: str, version: int, start: date, short=False) -> dict:
    warm = min(10, max(3, round(minutes * .2)))
    cool = min(5, max(2, round(minutes * .1)))
    main = minutes - warm - cool
    purpose = {"cycling": "Costruire continuità aerobica in bici senza accumulare fatica inutile.", "running": "Consolidare la corsa facile con uno sforzo controllato.", "swimming": "Curare tecnica e continuità del gesto a intensità facile.", "strength": "Curare controllo del movimento e mobilità senza arrivare a cedimento."}[sport]
    steps = []
    for typ, duration, rpe, cue in (("warmup", warm, 2, "Inizia progressivamente, respirazione comoda."), ("aerobic" if sport != "strength" else "technique", main, 3, "Sforzo facile e sostenibile; riduci se le sensazioni peggiorano."), ("cooldown", cool, 2, "Rallenta gradualmente e termina con margine.")):
        step = {"type": typ, "duration_min": duration, "target_rpe": rpe, "instruction": cue}
        ftp = profile["thresholds"]["cycling_ftp_watts"]["value"]
        if sport == "cycling" and ftp:
            fraction = .55 if typ != "aerobic" else .65
            step.update(target_power_w=round(ftp * fraction), target_power_pct_ftp=round(fraction * 100))
        running_pace = profile["thresholds"]["running_threshold_pace_sec_per_km"]["value"]
        css = profile["thresholds"]["swimming_css_sec_per_100m"]["value"]
        if sport == "running" and running_pace:
            step["reference_pace_sec_per_km"] = round(running_pace * (1.20 if typ == "aerobic" else 1.30))
        if sport == "swimming" and css:
            step["reference_pace_sec_per_100m"] = round(css + (15 if typ == "aerobic" else 25))
        steps.append(step)
    if sport == "strength":
        steps = [{"type": "warmup", "duration_min": warm, "target_rpe": 2, "instruction": "Cammino sul posto, circonduzioni delle spalle e mobilità dolce di caviglie e anche."}]
        movements = ["Alzati e siediti da una sedia stabile: 6–8 ripetizioni lente, poi riposo.", "Ponte glutei a terra: 6–8 ripetizioni controllate, poi riposo.", "Bird-dog in quadrupedia: 4–6 alternanze per lato, senza inarcare la schiena.", "Sollevamenti sui polpacci con appoggio stabile: 8 ripetizioni lente, poi riposo."]
        used = 0
        for index, movement in enumerate(movements):
            block = round(main / 4, 1) if index < 3 else round(main - used, 1)
            steps.append({"type": "technique", "duration_min": block, "target_rpe": 3, "instruction": movement + " Ripeti con ampio recupero solo se il movimento è già conosciuto e confortevole; termina con margine."})
            used += block
        steps.append({"type": "cooldown", "duration_min": cool, "target_rpe": 2, "instruction": "Cammina lentamente e riprendi una respirazione comoda; mobilità dolce senza forzare."})
    allocation = _allocation(profile)
    goal_focus = focus_for_sport(profile['_race_goal'], sport) if profile.get('_race_goal') else None
    if goal_focus:
        purpose += ' ' + goal_focus['explanation']
        for step in steps:
            if step['type'] in ('aerobic', 'technique'):
                step['instruction'] += ' ' + goal_focus['explanation']
            if sport == 'running' and (goal_focus['terrain'] in ('trail', 'mixed') or goal_focus['elevation_gain_m'] > 0):
                step.pop('reference_pace_sec_per_km', None)
    return {"sport": sport, "name": f"{LABELS[sport]} · {'resistenza facile' if priority == 'key' else 'tecnica e continuità'}", "duration_min": minutes, "expected_rpe": 3, "priority": priority, "purpose": purpose, "reason": purpose, "phase": phase, "focus": allocation["focus"], "race_distance": profile["race_distance"], "goal_priority": profile["goal_priority"], "adaptation_mode": "conservative_aerobic", "steps": steps, "short_version": {"duration_min": max(15, int(minutes * .65)), "instruction": "Conserva riscaldamento e defaticamento, riduci il blocco centrale."}, "planning": {"managed": True, "week_start": start.isoformat(), "version": version, "rule": "conservative_aerobic_v2", "shortened": short}, "equipment": {"swimming": "Piscina o ambiente acquatico adeguato; conferma le condizioni.", "strength": "Sedia stabile, spazio libero, tappetino facoltativo e movimenti già conosciuti."}.get(sport, "Dispositivo facoltativo: usa lo sforzo percepito."),
            **({'goal_focus': goal_focus} if goal_focus else {})}


def _schedule_brick(db, profile, capacity, created, occupied, start, version):
    def omitted(reason):
        return {"scheduled": False, "reason": "Combinato omesso: " + reason}
    if profile.get('_race_goal') and not {'cycling', 'running'}.issubset(profile['_race_goal']['disciplines']):
        return omitted('l’obiettivo attivo non prevede entrambe le frazioni bici e corsa.')
    if profile["experience"] == "beginner":
        return omitted("prima consolidiamo la continuità nelle singole discipline.")
    if capacity["phase"] not in ("base", "specific"):
        return omitted("la fase di gara, scarico o recupero richiede priorità diverse.")
    if profile["limitations"] or any(s in profile["excluded_sports"] for s in ("cycling", "running")):
        return omitted("rispettiamo le limitazioni e le discipline escluse.")
    if any(capacity["history_by_sport"][sport] < 4 for sport in ("cycling", "running")):
        return omitted("servono almeno quattro attività recenti di bici e quattro di corsa per confermare la continuità.")
    if not capacity["good_recovery"]:
        return omitted("manca un check-in recente favorevole, oppure ci sono segnali di recupero scarso.")
    if any(capacity["feedback"]["sports"][sport]["concerning"] for sport in ("cycling", "running")):
        return omitted("i feedback recenti richiedono prima una risposta più regolare alle sedute singole.")
    bikes = sorted((w for w in created if w.sport == "cycling" and w.duration_min >= 30), key=lambda w: -w.duration_min)
    runs = sorted((w for w in created if w.sport == "running"), key=lambda w: w.duration_min)
    for bike in bikes:
        for run in runs:
            run_minutes = min(20, int(run.duration_min))
            other_minutes = sum(w.duration_min for w in created if w.date == bike.date and w.id != run.id)
            if occupied[bike.date] + other_minutes + run_minutes + 5 > profile["availability"][date.fromisoformat(bike.date).weekday()]:
                continue
            group = f"{start.isoformat()}:v{version}:brick1"
            bike_payload = json.loads(bike.payload_json)
            bike_payload["brick"] = {"group_id": group, "sequence": 1, "partner_workout_id": run.id, "transition_minutes": 5, "original_purpose": bike_payload["purpose"], "original_name": bike.name}
            bike_payload["purpose"] += " Segue una corsa breve molto facile per familiarizzare con il cambio di gesto."
            bike.payload_json = _dump(bike_payload)
            run_payload = _make_workout(profile, "running", run_minutes, "support", capacity["phase"], version, start)
            run_payload.update(name="Corsa · transizione facile", purpose="Familiarizzare con il passaggio bici-corsa, senza cercare velocità o aggiungere volume.", expected_rpe=2)
            run_payload["reason"] = run_payload["purpose"]
            original_run = json.loads(run.payload_json)
            run_payload["brick"] = {"group_id": group, "sequence": 2, "partner_workout_id": bike.id, "transition_minutes": 5, "original_purpose": original_run["purpose"], "original_name": run.name}
            for step in run_payload["steps"]:
                step["target_rpe"] = 2
                step["instruction"] = "Corsa molto facile dopo la bici; puoi alternare cammino. Interrompi se il gesto o le sensazioni peggiorano."
                if "reference_pace_sec_per_km" in step:
                    step["reference_pace_sec_per_km"] = round(profile["thresholds"]["running_threshold_pace_sec_per_km"]["value"] * 1.30)
            run.date = bike.date
            run.duration_min = run_minutes
            run.name = run_payload["name"]
            run.payload_json = _dump(run_payload)
            return {"scheduled": True, "group_id": group, "date": bike.date, "workout_ids": [bike.id, run.id], "reason": "Combinato facile: una corsa già prevista viene accorciata e collegata alla bici; nessun volume aggiuntivo, cinque minuti riservati al cambio."}
    return omitted("durata delle sedute o disponibilità del giorno non consentono bici, corsa e cambio senza sovraccaricare il calendario.")


def _preview(profile, start, target):
    preview = []
    factors = {"taper": .70, "race_week": .40, "post_race": .35}
    current_factor = factors.get(_phase(profile, start), 1.0)
    for index in range(4):
        monday = start + timedelta(weeks=index)
        phase = _phase(profile, monday)
        factor = min(1.0, factors.get(phase, 1.0) / current_factor) * (.75 if index == 3 else 1.0)
        preview.append({"start": monday.isoformat(), "phase": phase, "focus": "Scarico e verifica delle sensazioni" if index == 3 else "Continuità e tecnica" if phase == "base" else "Preparazione specifica" if phase == "specific" else "Recupero e freschezza", "indicative_minutes": round(target * factor), "provisional": True, "note": "Anteprima indicativa: nessuna progressione automatica senza dati aggiornati."})
    return preview


def get_week(db: Session, value=None) -> dict:
    start = week_start(value)
    profile, race_goal, goal_warnings = goal_context(db, load_settings(db))
    rows = _rows(db, start)
    revision = _latest(db, start)
    metadata = json.loads(revision.metadata_json) if revision else {}
    capacity = _capacity(db, profile, start)
    recorded = _recorded_minutes(db, start)
    days = []
    summary = {sport: {"sessions": 0, "minutes": 0, "completed_minutes": 0} for sport in SPORTS}
    for index in range(7):
        day = (start + timedelta(days=index)).isoformat()
        sessions = [serialize_workout(row) for row in rows if row.date == day]
        for session in sessions:
            if session['status'] == 'planned' and day >= _today().isoformat():
                window = windows(profile, day)
                if window['restricted']:
                    session['payload']['time_window'] = window
        sessions.sort(key=lambda row: (row["payload"].get("brick", {}).get("group_id", ""), row["payload"].get("brick", {}).get("sequence", 0), row["id"]))
        active = [row for row in sessions if row["status"] != "skipped"]
        for row in active:
            bucket = summary.setdefault(row["sport"], {"sessions": 0, "minutes": 0, "completed_minutes": 0})
            bucket["sessions"] += 1
            bucket["minutes"] += row["duration_min"]
            if row["status"] == "completed":
                bucket["completed_minutes"] += row["duration_min"]
        planned = sum(r["duration_min"] for r in active)
        transition = 5 * len({r["payload"]["brick"]["group_id"] for r in active if r["payload"].get("brick", {}).get("sequence") == 1})
        completed = sum(r["duration_min"] for r in active if r["status"] == "completed")
        required = planned - completed + max(completed, recorded[day]) + transition
        days.append({"date": day, "available_minutes": available_minutes(profile, day), "time_window": windows(profile, day), "planned_minutes": planned, "transition_minutes": transition, "required_minutes": required, "recorded_minutes": round(recorded[day], 1), "sessions": sessions})
    total = sum(day["planned_minutes"] for day in days)
    reasons = list(metadata.get("rationale") or capacity["rationale"]) + goal_warnings
    signature = race_goal['signature'] if race_goal else None
    goal_changed = metadata.get('race_goal_signature') != signature
    needs_review = bool(revision and (goal_changed or metadata.get("profile_revision") != get_settings(db)["revision"] or metadata.get("feedback_adaptation", capacity["feedback"]) != capacity["feedback"] or capacity["limit"] < metadata.get("target_minutes", 0)))
    if revision and goal_changed:
        reasons.append('Le specifiche dell’obiettivo sono cambiate: rivedi e ricalcola le sedute future; quelle esistenti non vengono riscritte automaticamente.')
    return {"start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(), "version": revision.version if revision else 0, "generated": _has_generated_plan(db, start), "days": days, "summary": summary, "total_minutes": total, "target_minutes": metadata.get("target_minutes", min(profile["weekly_minutes"], capacity["limit"])), "confidence": metadata.get("confidence", capacity["confidence"]), "phase": capacity["phase"], "rationale": reasons, "adaptation_mode": "conservative_aerobic", "allocation": metadata.get("allocation", capacity["allocation"]), "feedback_adaptation": metadata.get("feedback_adaptation", capacity["feedback"]), "brick_status": metadata.get("brick_status", {"scheduled": False, "reason": "Il combinato viene valutato alla generazione del piano."}), "needs_review": needs_review, "race_goal": race_goal, "goal_warnings": goal_warnings,
            "block_preview": _preview(profile, start, total or min(profile["weekly_minutes"], capacity["limit"])), "can_undo": bool(db.query(PlanRevision).filter(PlanRevision.week_start == start.isoformat(), PlanRevision.undone_at.is_(None), PlanRevision.action != "undo").first())}


def generate_week(db: Session, value=None, regenerate=False) -> dict:
    start = week_start(value)
    today = _today()
    if start + timedelta(days=6) < today:
        raise HTTPException(422, "Non è possibile generare una settimana interamente passata.")
    if _has_generated_plan(db, start) and not regenerate:
        return get_week(db, start)
    profile, race_goal, goal_warnings = goal_context(db, load_settings(db))
    capacity = _capacity(db, profile, start)
    rows = _rows(db, start)
    profile['availability'] = [available_minutes(profile, start + timedelta(days=i)) for i in range(7)]
    before = _snapshot(rows)
    next_version = (_latest(db, start).version + 1) if _latest(db, start) else 1
    # Replacing a generated week never deletes manual or completed sessions.
    for row in rows:
        payload = json.loads(row.payload_json)
        if row.status != "completed" and row.date >= today.isoformat() and payload.get("planning", {}).get("managed"):
            if _published(db, row.id):
                raise HTTPException(409, "Una seduta è già pubblicata su Garmin: serve una riconciliazione prima di rigenerare il piano.")
    for row in rows:
        payload = json.loads(row.payload_json)
        if row.status != "completed" and row.date >= today.isoformat() and payload.get("planning", {}).get("managed"):
            # Keep stable IDs and delivery history; never recycle a remote reference.
            row.status = "superseded"
    db.flush()
    retained = _rows(db, start)
    occupied = _occupancy(db, start, retained)
    remaining = max(0, min(profile["weekly_minutes"], capacity["limit"], sum(profile["availability"])) - sum(occupied.values()))
    excluded = set(profile["excluded_sports"])
    if not profile["pool_access"]:
        excluded.add("swimming")
    race_sports = race_goal['disciplines'] if race_goal else SPORTS
    eligible = [s for s in SPORTS if s not in excluded and (s in race_sports or s == 'strength')]
    candidates = []
    event_date = date.fromisoformat(profile["event_date"]) if profile.get("event_date") else None
    for index, available in enumerate(profile["availability"]):
        day = start + timedelta(days=index)
        # Do not backfill past days or prescribe an extra session on race day.
        spare = available - occupied[day.isoformat()]
        if day >= today and spare >= 15 and day != event_date and day.isoformat() not in capacity["blocked_dates"]:
            candidates.append((index, spare))
    if len(candidates) == 7:
        # One rest day even when the athlete lists availability every day.
        candidates.remove(min(candidates, key=lambda item: item[1]))
    # Allocate the longest available slot to bike, then rotate the disciplines.
    candidates.sort(key=lambda item: (-item[1], item[0]))
    pattern = ["cycling", "running", "swimming", "cycling", "running", "swimming"]
    if race_goal:
        disciplines = sorted((s for s in eligible if s != 'strength'), key=lambda sport: -capacity['allocation']['shares'][sport])
        pattern = disciplines or (['strength'] if 'strength' in eligible else [None])
    pattern = [s if s in eligible else ("strength" if "strength" in eligible else eligible[0] if eligible else None) for s in pattern]
    assignments = [{"index": index, "spare": spare, "sport": pattern[position % len(pattern)]} for position, (index, spare) in enumerate(candidates)]
    strength_reason = "Forza di supporto omessa: budget o disponibilità insufficienti per mantenerla senza aggiungere carico."
    if "strength" in eligible and not profile["limitations"] and remaining >= 120 and len(assignments) >= 3 and not any(a["sport"] == "strength" for a in assignments):
        partner_day = next((a for a in assignments if a["sport"] == "swimming" and a["spare"] >= 45), None)
        if partner_day is None:
            partner_day = next((a for a in assignments if a["sport"] == "cycling" and a["spare"] >= 60), None)
        if partner_day is None and race_goal:
            partner_day = next((a for a in assignments if a['sport'] == 'running' and a['spare'] >= 45), None)
        if partner_day:
            assignments.append({"index": partner_day["index"], "spare": partner_day["spare"], "sport": "strength"})
            strength_reason = "Forza di supporto inclusa nel budget esistente: movimenti a corpo libero già conosciuti, senza cedimento."
    elif any(a["sport"] == "strength" for a in assignments):
        strength_reason = "Forza di supporto prevista al posto delle discipline non disponibili, sempre entro il budget."
    created = []
    assigned_counts = Counter(a["sport"] for a in assignments)
    weights = [capacity["allocation"]["shares"].get(a["sport"], 0) * capacity["feedback"]["sports"].get(a["sport"], {}).get("factor", 1.0) / assigned_counts[a["sport"]] for a in assignments]
    if assignments and assignments[0]["sport"] == "cycling":
        weights[0] *= capacity["allocation"]["long_session_weight"]
    allocated = Counter()
    for position, assignment in enumerate(assignments):
        if remaining < 15 or not eligible:
            break
        index, sport = assignment["index"], assignment["sport"]
        spare = assignment["spare"] - allocated[index]
        if spare < 15:
            continue
        slots_left = min(len(assignments) - position, max(1, int(remaining // 15)))
        remaining_weight = sum(weights[position:position + slots_left])
        if remaining_weight <= 0:
            continue
        share = weights[position] / remaining_weight
        duration = min(spare, max(15, int(remaining * share)), remaining - (slots_left - 1) * 15, capacity["session_caps"][sport])
        duration = int(duration // 5 * 5)
        if duration < 15:
            continue
        priority = "key" if sport == "cycling" and position == 0 else "support"
        payload = _make_workout(profile, sport, duration, priority, capacity["phase"], next_version, start)
        row = Workout(date=(start + timedelta(days=index)).isoformat(), sport=sport, name=payload["name"], duration_min=duration, payload_json=_dump(payload), status="planned")
        db.add(row)
        created.append(row)
        remaining -= duration
        allocated[index] += duration
    db.flush()
    brick = _schedule_brick(db, profile, capacity, created, occupied, start, next_version)
    if not any(row.sport == "strength" for row in created):
        strength_reason = "Forza di supporto omessa: budget, limitazioni o disponibilità non permettono una seduta separata senza aggiungere carico."
    rationale = list(capacity["rationale"]) + goal_warnings
    rationale.append((f"Obiettivo {race_goal['name']}: " if race_goal else f"Distanza {profile['race_distance']}: ") + capacity["allocation"]["focus"])
    if race_goal:
        rationale.extend(race_goal['constraints'])
    rationale.append(strength_reason)
    rationale.append(brick["reason"])
    rationale.append("Adattamento prudente: lavoro aerobico e tecnico, nessuna progressione intensa automatica o recupero forzato delle sedute saltate.")
    if profile.get("target_time"):
        rationale.append("Il tempo gara desiderato è un obiettivo dichiarato: non aumenta da solo volume o intensità e non costituisce una previsione di risultato.")
    if not profile["pool_access"]:
        rationale.append("Piscina non disponibile: nessuna seduta di nuoto prescritta; mobilità/forza come supporto.")
    if profile["excluded_sports"]:
        rationale.append("Discipline escluse rispettate: " + ", ".join(profile["excluded_sports"]) + ".")
    if capacity["limit"] < profile["weekly_minutes"]:
        rationale.append("Il tempo disponibile è un tetto: lo storico e il recupero possono richiedere un volume inferiore.")
    if not created:
        rationale.append("Nessuna nuova seduta: capacità, disponibilità o esclusioni non consentono una proposta adeguata.")
    _audit(db, start, "regenerate" if regenerate else "generate", before, {"rationale": rationale, "confidence": capacity["confidence"], "target_minutes": min(profile["weekly_minutes"], capacity["limit"]), "profile_revision": get_settings(db)["revision"], "rule_version": "conservative_aerobic_v2", "allocation": capacity["allocation"], "feedback_adaptation": capacity["feedback"], "brick_status": brick,
           'race_goal_signature': race_goal['signature'] if race_goal else None})
    db.commit()
    return get_week(db, start)


def _prepare_workout_change(db: Session, workout_id: int, action: str, new_date: date | None = None, duration_min: int | None = None, reason: str = "") -> dict:
    """Validate and describe edits without assigning to ORM objects or auditing."""
    row = db.get(Workout, workout_id)
    if not row:
        raise HTTPException(404, "Allenamento non trovato.")
    if row.status == "completed":
        raise HTTPException(409, "Una seduta completata non può essere modificata.")
    if row.status in ("skipped", "superseded", "cancelled"):
        raise HTTPException(409, "Una seduta saltata può essere ripristinata con Annulla.")
    if _published(db, row.id):
        raise HTTPException(409, "Seduta già pubblicata su Garmin: la modifica richiede una riconciliazione esplicita.")
    if row.date < _today().isoformat() and action != 'skip':
        raise HTTPException(409, 'La seduta è nel passato: registra il riscontro o segnala il mancato svolgimento, senza riscriverne la programmazione.')
    start = week_start(row.date)
    before = _snapshot(_rows(db, start))
    profile, race_goal, _ = goal_context(db, load_settings(db))
    revised_row = serialize_workout(row)
    changed = {row.id: revised_row}
    payload = json.loads(row.payload_json)
    linked = None
    if payload.get("brick"):
        candidate = db.get(Workout, payload["brick"].get("partner_workout_id"))
        if candidate and candidate.status not in ("skipped", "superseded", "cancelled"):
            candidate_payload = json.loads(candidate.payload_json)
            if candidate_payload.get("brick", {}).get("group_id") == payload["brick"]["group_id"]:
                linked = candidate
        if linked and (linked.status == "completed" or _published(db, linked.id)):
            raise HTTPException(409, "Una parte del combinato è completata o pubblicata: serve una revisione esplicita prima di modificarlo.")
        if linked and (linked.date != row.date or week_start(linked.date) != start):
            raise HTTPException(409, 'Le parti del combinato non sono allineate: riconcilia il gruppo prima di modificarlo.')
    if action == "move":
        if not new_date:
            raise HTTPException(422, "Indicare il nuovo giorno.")
        if new_date < _today():
            raise HTTPException(422, "Non è possibile spostare una seduta nel passato.")
        if week_start(new_date) != start:
            raise HTTPException(422, "Scegliere un giorno nella stessa settimana.")
        if profile.get('event_date') == new_date.isoformat():
            raise HTTPException(409, 'Il giorno della gara è riservato: scegli un altro giorno per la seduta.')
        if new_date.isoformat() in _capacity(db, profile, start)['blocked_dates']:
            raise HTTPException(409, 'Il giorno scelto è sospeso dai segnali di recupero: rivedi prima il check-in.')
        group_ids = {row.id, linked.id} if linked else {row.id}
        other = _occupancy(db, start, [r for r in _rows(db, start) if r.id not in group_ids])[new_date.isoformat()]
        required = row.duration_min + (linked.duration_min + 5 if linked else 0)
        if other + required > available_minutes(profile, new_date):
            raise HTTPException(409, "Il giorno scelto non ha abbastanza minuti disponibili.")
        revised_row['date'] = new_date.isoformat()
        if linked:
            changed[linked.id] = {**serialize_workout(linked), 'date': new_date.isoformat()}
    elif action == "shorten":
        if duration_min is None or duration_min < 15 or duration_min >= row.duration_min:
            raise HTTPException(422, "La durata deve essere almeno 15 minuti e inferiore a quella attuale.")
        if payload.get('source') == 'manual' and payload.get('exercises'):
            raise HTTPException(409, 'La scheda palestra richiede una revisione di serie ed esercizi: crea una nuova scheda con il programma desiderato.')
        if payload.get('source') == 'manual':
            # Preserve the athlete's own phases/targets instead of substituting
            # an unrelated automatic template under the same session name.
            steps = deepcopy(payload.get('steps') or [])
            if not steps or any(not isinstance(step.get('duration_min'), (int, float)) or step['duration_min'] <= 0 for step in steps):
                raise HTTPException(422, 'Le fasi manuali non hanno durate valide per la riduzione.')
            original = sum(step['duration_min'] for step in steps)
            for step in steps:
                step['duration_min'] = round(step['duration_min'] * duration_min / original, 2)
            steps[-1]['duration_min'] = round(duration_min - sum(step['duration_min'] for step in steps[:-1]), 2)
            payload.update(duration_min=duration_min, steps=steps)
        elif row.sport in SPORTS:
            revised = _make_workout(profile, row.sport, duration_min, payload.get("priority", "support"), payload.get("phase", "base"), (_latest(db, start).version if _latest(db, start) else 0) + 1, start, short=True)
            payload = {**payload, **revised}
            if payload.get("brick"):
                if payload["brick"]["sequence"] == 2:
                    payload.update(name="Corsa · transizione facile", purpose="Familiarizzare con il passaggio bici-corsa a sforzo molto facile.", expected_rpe=2)
                    for step in payload["steps"]:
                        step["target_rpe"] = 2
                        if "reference_pace_sec_per_km" in step:
                            step["reference_pace_sec_per_km"] = round(profile["thresholds"]["running_threshold_pace_sec_per_km"]["value"] * 1.30)
                else:
                    payload["purpose"] += " Segue una corsa breve molto facile."
        else:
            raise HTTPException(422, "Disciplina non supportata per la riduzione automatica.")
        revised_row['duration_min'] = duration_min
        revised_row['name'] = payload['name']
    elif action == "skip":
        revised_row['status'] = 'skipped'
        if linked:
            linked_payload = json.loads(linked.payload_json)
            original = linked_payload.pop("brick")
            linked_payload["purpose"] = original.get("original_purpose", linked_payload["purpose"])
            linked_payload["name"] = original.get("original_name", linked_payload["name"])
            changed[linked.id] = {**serialize_workout(linked), 'name': linked_payload['name'], 'payload': linked_payload}
        payload.pop("brick", None)
    else:
        raise HTTPException(422, "Azione non supportata.")
    payload.setdefault("planning", {})["last_change"] = {"action": action, "reason": reason, "at": datetime.utcnow().isoformat() + "Z"}
    revised_row['payload'] = payload
    latest = _latest(db, start)
    metadata = json.loads(latest.metadata_json) if latest else {}
    rationale = list(metadata.get("rationale") or [])
    rationale.append({"move": "Seduta spostata nei minuti disponibili, senza aumentare il volume.", "shorten": "Fasi manuali ridotte in proporzione, conservando obiettivi e istruzioni." if payload.get('source') == 'manual' else "Blocco centrale ridotto, mantenendo riscaldamento e defaticamento.", "skip": "Seduta saltata: nessun debito da recuperare automaticamente."}[action])
    metadata.update(rationale=rationale, workout_id=workout_id, reason=reason)
    if linked and action == "move":
        rationale.append("Le due parti del combinato sono state spostate insieme, riservando anche il tempo per il cambio.")
        if metadata.get("brick_status", {}).get("scheduled"):
            metadata["brick_status"]["date"] = revised_row['date']
    if linked and action == "skip":
        rationale.append("Combinato interrotto: la parte rimasta è una seduta singola e non viene recuperato volume.")
        metadata["brick_status"] = {"scheduled": False, "reason": "Combinato interrotto dopo aver saltato una delle due parti."}
    return {'start': start, 'before': before, 'changes': changed, 'metadata': metadata,
            'rationale': rationale[len(json.loads(latest.metadata_json).get('rationale') or []):] if latest else rationale}


def _change_preview(db, workout_id, prepared):
    before = prepared['before']
    after = [prepared['changes'].get(item['id'], item) for item in before]
    old = {item['id']: item for item in before}
    profile, race_goal, _ = goal_context(db, load_settings(db))
    recorded = _recorded_minutes(db, prepared['start'])

    def day_totals(rows, day):
        active = [item for item in rows if item['date'] == day and item['status'] not in ('skipped', 'superseded', 'cancelled')]
        minutes = sum(item['duration_min'] for item in active)
        completed = sum(item['duration_min'] for item in active if item['status'] == 'completed')
        transition = 5 * len({item['payload']['brick']['group_id'] for item in active if item['payload'].get('brick', {}).get('sequence') == 1})
        return minutes, minutes - completed + max(completed, recorded[day]) + transition

    days = []
    for index in range(7):
        day = (prepared['start'] + timedelta(days=index)).isoformat()
        old_minutes, old_required = day_totals(before, day)
        new_minutes, new_required = day_totals(after, day)
        days.append({'date': day, 'before_minutes': old_minutes, 'after_minutes': new_minutes,
                     'before_required_minutes': old_required, 'after_required_minutes': new_required,
                     'available_minutes': profile['availability'][index]})
    total_before = sum(day['before_minutes'] for day in days)
    total_after = sum(day['after_minutes'] for day in days)
    result = {'before': old[workout_id], 'after': prepared['changes'][workout_id],
              'affected_workouts': [{'before': old[identifier], 'after': item} for identifier, item in prepared['changes'].items()],
              'impact': {'week_start': prepared['start'].isoformat(), 'before_minutes': total_before,
                         'after_minutes': total_after, 'delta_minutes': total_after - total_before, 'days': days},
              'rationale': prepared['rationale'], 'can_apply': True,
              'note': 'Anteprima senza salvataggio: disponibilità e stato delle sedute saranno ricontrollati alla conferma.'}
    material = deepcopy({key: result[key] for key in ('before', 'after', 'affected_workouts', 'impact', 'rationale')})
    for item in [material['after']] + [pair['after'] for pair in material['affected_workouts']]:
        item.get('payload', {}).get('planning', {}).get('last_change', {}).pop('at', None)
    latest = _latest(db, prepared['start'])
    material.update(profile_revision=get_settings(db)['revision'], race_goal_signature=race_goal['signature'] if race_goal else None,
                    week_version=latest.version if latest else 0)
    result['preview_signature'] = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return result


def preview_workout(db: Session, workout_id: int, action: str, new_date: date | None = None, duration_min: int | None = None, reason: str = '') -> dict:
    # No autoflush: even an unrelated pending session change must not turn a
    # read-only preview into a database write.
    with db.no_autoflush:
        prepared = _prepare_workout_change(db, workout_id, action, new_date, duration_min, reason)
        return _change_preview(db, workout_id, prepared)


def modify_workout(db: Session, workout_id: int, action: str, new_date: date | None = None, duration_min: int | None = None, reason: str = "", preview_signature: str | None = None) -> dict:
    with db.no_autoflush:
        if preview_signature is not None and not re.fullmatch(r'[a-fA-F0-9]{64}', preview_signature):
            raise HTTPException(422, 'Firma dell’anteprima non valida: richiedi una nuova anteprima.')
        try:
            prepared = _prepare_workout_change(db, workout_id, action, new_date, duration_min, reason)
        except HTTPException as error:
            if preview_signature is not None and error.status_code in (404, 409, 422):
                raise HTTPException(409, 'L’anteprima non è più applicabile. Riaprila per verificare lo stato aggiornato delle sedute.') from None
            raise
        if preview_signature is not None:
            current = _change_preview(db, workout_id, prepared)['preview_signature']
            if not hmac.compare_digest(current, preview_signature.lower()):
                raise HTTPException(409, 'Il piano o i dati dell’obiettivo sono cambiati. Riapri l’anteprima prima di confermare la modifica.')
    for identifier, item in prepared['changes'].items():
        row = db.get(Workout, identifier)
        for key in ('date', 'duration_min', 'name', 'status'):
            setattr(row, key, item[key])
        row.payload_json = _dump(item['payload'])
    _audit(db, prepared['start'], action, prepared['before'], prepared['metadata'])
    db.commit()
    return {"workout": serialize_workout(db.get(Workout, workout_id)), "week": get_week(db, prepared['start'])}


def undo_week(db: Session, value=None) -> dict:
    start = week_start(value)
    revision = db.query(PlanRevision).filter(PlanRevision.week_start == start.isoformat(), PlanRevision.undone_at.is_(None), PlanRevision.action != "undo").order_by(PlanRevision.version.desc()).first()
    if not revision:
        raise HTTPException(409, "Nessuna modifica da annullare.")
    rows = _rows(db, start)
    before = _snapshot(rows)
    saved = {item["id"]: item for item in json.loads(revision.snapshot_json)}
    current = {row.id: row for row in rows}
    reconciled = {item.workout_id for item in db.query(WorkoutResolution).filter(WorkoutResolution.undone_at.is_(None)).all()}
    protected = []
    for row in rows:
        if row.id in reconciled or row.status == "completed" or _published(db, row.id):
            protected.append(row.id)
            continue
        if row.id not in saved:
            row.status = "superseded"
    for identifier, item in saved.items():
        if identifier in reconciled:
            if identifier not in protected:
                protected.append(identifier)
            continue
        row = current.get(identifier)
        if row is not None and (row.status == "completed" or _published(db, row.id)):
            continue
        if row is None:
            duplicate_completed = any(r.status == "completed" and r.date == item["date"] and r.sport == item["sport"] for r in rows)
            if duplicate_completed and item["status"] != "completed" and item["payload"].get("planning", {}).get("managed"):
                protected.append(identifier)
                continue
            # An ID outside this week might have been moved externally: never overwrite it.
            elsewhere = db.get(Workout, identifier)
            if elsewhere is not None and (elsewhere.date < start.isoformat() or elsewhere.date >= (start + timedelta(days=7)).isoformat() or elsewhere.status == "completed" or _published(db, identifier)):
                protected.append(identifier)
                continue
            if elsewhere is not None:
                row = elsewhere
            else:
                row = Workout(id=identifier)
                db.add(row)
        for key in ("date", "sport", "name", "duration_min", "status"):
            setattr(row, key, item[key])
        row.payload_json = _dump(item["payload"])
    revision.undone_at = datetime.utcnow()
    previous = db.query(PlanRevision).filter(PlanRevision.week_start == start.isoformat(), PlanRevision.version < revision.version).order_by(PlanRevision.version.desc()).first()
    metadata = json.loads(previous.metadata_json) if previous else {}
    metadata["rationale"] = list(metadata.get("rationale") or []) + ["Ultima modifica annullata. Sedute completate e decisioni di riposo o mancato svolgimento confermate sono state conservate."]
    metadata.update(restored_from_version=revision.version, protected_workout_ids=protected)
    _audit(db, start, "undo", before, metadata)
    db.commit()
    return get_week(db, start)
