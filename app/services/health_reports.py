"""Dated symptom journal and descriptive, source-consistent antecedent comparisons.

An association is a temporal co-occurrence, never a diagnosis or proof of cause.
The athlete's own explanation is preserved separately from observed comparisons.
"""
from __future__ import annotations

import math
from datetime import date as Date, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.health_reports import HealthReport
from app.db.models import Activity
from app.services.athlete_clock import activity_day, athlete_today, day_start_utc, utc_now_naive
from app.services.health import series, usable_metric_value
from app.services.observed_load import resolve_observed_loads

KINDS = {'injury': 'Infortunio', 'contracture': 'Contrattura', 'illness': 'Malessere / malattia', 'cold': 'Raffreddore'}


class HealthReportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    date: Date = Field(default_factory=athlete_today)
    kind: Literal['injury', 'contracture', 'illness', 'cold']
    body_location: str = Field('', max_length=160)
    severity: int = Field(1, ge=1, le=5, strict=True)
    status: Literal['active', 'resolved'] = 'active'
    resolved_on: Date | None = None
    suspected_cause: str = Field('', max_length=1200)
    notes: str = Field('', max_length=2000)

    @field_validator('body_location', 'suspected_cause', 'notes')
    @classmethod
    def trim(cls, value):
        if '\x00' in value:
            raise ValueError('Il testo contiene caratteri non validi.')
        return value.strip()

    @model_validator(mode='after')
    def validate_dates_and_location(self):
        today = athlete_today()
        if self.date < Date(1970, 1, 1) or self.date > today:
            raise ValueError('La data deve essere compresa fra il 1970 e oggi.')
        if self.kind in ('injury', 'contracture') and not self.body_location:
            raise ValueError('Indica dove avverti il problema.')
        if self.status == 'resolved':
            self.resolved_on = self.resolved_on or today
            if not self.date <= self.resolved_on <= today:
                raise ValueError('La risoluzione deve essere successiva all’esordio e non futura.')
        elif self.resolved_on is not None:
            raise ValueError('Una segnalazione attiva non può avere una data di risoluzione.')
        return self


def payload(row):
    return {key: getattr(row, key) for key in ('date', 'kind', 'body_location', 'severity', 'status', 'resolved_on', 'suspected_cause', 'notes')}


def serialize(row, db=None):
    result = {'id': row.id, **payload(row), 'label': KINDS[row.kind],
              'created_at': row.created_at.isoformat(), 'updated_at': row.updated_at.isoformat()}
    if db is not None:
        result['analysis'] = report_analysis(db, row)
    return result


def get_report(db, identifier):
    row = db.get(HealthReport, identifier)
    if row is None:
        raise HTTPException(404, 'Segnalazione non trovata.')
    return row


def save_report(db, value, row=None):
    stamp = datetime.utcnow()
    row = row or HealthReport(created_at=stamp)
    for key, item in value.model_dump(mode='json').items():
        setattr(row, key, item)
    row.updated_at = stamp
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def active_restrictions(db, day=None):
    """Conservative app policy driven by explicit active reports, not inferred illness."""
    day = Date.fromisoformat(str(day)) if day is not None else athlete_today()
    rows = db.query(HealthReport).filter(HealthReport.date <= str(day)).order_by(HealthReport.date.desc(), HealthReport.id.desc()).all()
    active = [row for row in rows if row.resolved_on is None or row.resolved_on > str(day)]
    paused = any(row.kind in ('injury', 'illness', 'cold') or row.severity >= 3 for row in active)
    mode = 'rest' if paused else 'easy' if active else 'normal'
    reasons = [f"{KINDS[row.kind]}" + (f" a {row.body_location}" if row.body_location else '') + f": intensità riferita {row.severity}/5, segnalazione ancora attiva." for row in active]
    if active:
        reasons.append('La proposta automatica viene sospesa.' if paused else 'La proposta automatica resta breve e facile; non viene usata per compensare sedute perse.')
    return {'mode': mode, 'training_paused': paused, 'max_duration_min': 0 if paused else 20 if active else None,
            'max_rpe': 0 if paused else 2 if active else None, 'reasons': reasons,
            'reports': [serialize(row) for row in active]}


def _known_loads(db, activities, *, resolved=None):
    resolved = resolve_observed_loads(db, activities) if resolved is None else resolved
    return [(activity_day(row.start_time), resolved[row.id]['value']) for row in activities
            if resolved[row.id]['value'] is not None]


def _finite_median(values):
    values = sorted(values); middle = len(values) // 2
    return values[middle] if len(values) % 2 else values[middle - 1] / 2 + values[middle] / 2


def _finite_change(new, old):
    result = (new / old - 1) * 100 if old else None
    return result if result is not None and math.isfinite(result) else None


def report_analysis(db, report):
    onset = Date.fromisoformat(report.date)
    recent_start = onset - timedelta(days=3)
    baseline_start = recent_start - timedelta(days=28)
    points = series(db, baseline_start, onset - timedelta(days=1))
    points = [point for point in points if point['source'].lower() not in ('local_estimate', 'estimated', 'estimate', 'unknown')]
    comparisons = []
    labels = {'sleep_hours': ('Sonno', 'ore'), 'hrv_ms': ('HRV', 'ms'), 'resting_hr_bpm': ('FC a riposo', 'bpm')}
    for kind, (label, unit) in labels.items():
        recent = [point for point in points if point['kind'] == kind and str(recent_start) <= point['date'] < str(onset)
                  and usable_metric_value(kind, point['value']) is not None]
        if not recent:
            continue
        chosen_source = recent[-1]['source']
        recent_values = [point['value'] for point in recent if point['source'] == chosen_source]
        baseline = [point['value'] for point in points if point['kind'] == kind and point['source'] == chosen_source and point['date'] < str(recent_start)
                    and usable_metric_value(kind, point['value']) is not None]
        if len(recent_values) < 2 or len(baseline) < 7:
            continue
        old, new = _finite_median(baseline), _finite_median(recent_values)
        percent = _finite_change(new, old)
        noteworthy = (kind == 'sleep_hours' and new < old * .9) or (kind == 'hrv_ms' and new < old * .85) or (kind == 'resting_hr_bpm' and new >= old + 4)
        comparisons.append({'kind': kind, 'label': label, 'unit': unit, 'recent_value': round(new, 1), 'baseline_value': round(old, 1),
                            'change_percent': round(percent, 1) if percent is not None else None, 'noteworthy': noteworthy,
                            'observation': f'{label}: {new:.1f} {unit} prima dei sintomi, rispetto a {old:.1f} {unit} nel periodo precedente.'})
    activities = db.query(Activity).filter(Activity.start_time >= day_start_utc(baseline_start), Activity.start_time < day_start_utc(onset), Activity.start_time <= utc_now_naive()).all()
    resolved = resolve_observed_loads(db, activities)
    loads = _known_loads(db, activities, resolved=resolved)
    load_coverage = {status: sum(item['status'] == status for item in resolved.values())
                     for status in ('available', 'missing', 'invalid', 'conflict')}
    load_coverage.update(activities=len(activities), complete=len(loads) == len(activities), scope='recorded_activities_only')
    recent = [value for day, value in loads if recent_start <= day < onset]
    baseline = [value for day, value in loads if baseline_start <= day < recent_start]
    if recent and len(baseline) >= 3 and load_coverage['complete']:
        # Scale before summing to avoid an intermediate overflow from otherwise
        # finite data. An unrepresentable aggregate remains unavailable.
        old = sum(value / 28 for value in baseline)
        new = sum(value / 3 for value in recent)
        change = _finite_change(new, old) if math.isfinite(new) and math.isfinite(old) else None
        if not math.isfinite(old) or not math.isfinite(new):
            old = new = None
    else:
        old = new = change = None
    if old is not None and new is not None:
        comparisons.append({'kind': 'load', 'label': 'Carico registrato al giorno', 'unit': 'punti/giorno',
                            'recent_value': round(new, 1), 'baseline_value': round(old, 1), 'change_percent': round(change, 1) if change is not None else None,
                            'noteworthy': bool(old > 0 and new > old * 1.25),
                            'observation': f'Carico registrato: {new:.1f} punti/giorno nei tre giorni precedenti, rispetto a {old:.1f} nei 28 giorni prima.'})
    associations = [item for item in comparisons if item['noteworthy']]
    if associations:
        summary = 'Prima della segnalazione emergono questi cambiamenti: ' + ', '.join(item['label'].lower() for item in associations) + '. Sono associazioni temporali: non dimostrano la causa del problema.'
    elif comparisons:
        summary = 'Nei dati confrontabili non emergono gli scostamenti previsti dalle regole di confronto. Questo non esclude il problema segnalato e non ne identifica la causa.'
    else:
        summary = 'Lo storico precedente non basta per un confronto attendibile: servono almeno due letture recenti e sette precedenti dello stesso metodo, oppure un carico registrato confrontabile.'
    return {'summary': summary, 'associations': associations, 'comparisons': comparisons,
            'athlete_hypothesis': report.suspected_cause, 'hypothesis_label': 'La tua ipotesi, non una causa confermata',
            'recent_start': str(recent_start), 'recent_end': str(onset - timedelta(days=1)), 'baseline_start': str(baseline_start),
            'restriction': active_restrictions(db, onset), 'load_coverage': load_coverage,
            'method': 'preceding-3d-vs-prior-28d-1'}
