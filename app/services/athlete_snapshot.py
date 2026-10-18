"""A live, observed athlete profile, without manufacturing personal measurements."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import math

from app.db.models import Activity
from app.services.athlete_clock import activity_day, athlete_today, day_start_utc, utc_now_naive
from app.services.goal_planning import goal_context
from app.services.health import effective_metrics, health_response
from app.services.health_reports import active_restrictions, _known_loads
from app.services.planning import load_settings
from app.services.training_load import sport_key

LABELS = {'bike': 'Bici', 'run': 'Corsa', 'swim': 'Nuoto', 'strength': 'Forza', 'other': 'Altro'}

RECORD_FIELDS = {
    'longest_distance_km': ('distance_meters', .001, 'km', 'Distanza massima'),
    'longest_duration_min': ('duration_seconds', 1/60, 'min', 'Durata massima'),
    'most_elevation_m': ('elevation_gain_meters', 1, 'm', 'Dislivello massimo'),
    'highest_avg_power_w': ('avg_power', 1, 'W', 'Potenza media massima di un’attività'),
    'highest_avg_speed_kmh': ('avg_speed', 3.6, 'km/h', 'Velocità media massima di un’attività'),
    'highest_normalized_power_w': ('normalized_power', 1, 'W', 'Potenza normalizzata massima di un’attività'),
}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _records(rows):
    result = {}
    for key, (attribute, scale, unit, label) in RECORD_FIELDS.items():
        candidates = [row for row in rows if _number(getattr(row, attribute)) and
                      (getattr(row, attribute) > 0 or attribute == 'elevation_gain_meters')]
        if not candidates:
            result[key] = None
            continue
        row = max(candidates, key=lambda item: (getattr(item, attribute), item.start_time, item.id))
        result[key] = {'value': round(getattr(row, attribute) * scale, 2), 'unit': unit, 'label': label,
                       'activity_id': row.id, 'activity_name': row.name or label, 'date': str(activity_day(row.start_time)),
                       'source': row.source, 'scope': 'whole_activity'}
    return result


def _totals(db, rows):
    def observed(attribute, scale=1):
        values = [getattr(row, attribute) for row in rows if _number(getattr(row, attribute))]
        return round(sum(values) * scale, 2) if values or not rows else None
    loads = _known_loads(db, rows)
    return {'activities': len(rows), 'hours': observed('duration_seconds', 1/3600),
            'distance_km': observed('distance_meters', .001), 'elevation_m': observed('elevation_gain_meters'),
            'calories': observed('calories'), 'load': round(sum(value for _, value in loads), 1) if loads or not rows else None,
            'coverage': {attribute: sum(_number(getattr(row, attribute)) for row in rows)
                         for attribute in ('duration_seconds', 'distance_meters', 'elevation_gain_meters', 'calories')}}


def _sport(value):
    key = sport_key(value)
    if key in ('gym', 'weightlifting', 'workout'):
        return 'strength'
    return key if key in LABELS else 'other'


def derived_profile(db, day=None):
    day = date.fromisoformat(str(day)) if day is not None else athlete_today()
    start = day - timedelta(days=27)
    previous_start = start - timedelta(days=28)
    activities = db.query(Activity).filter(Activity.start_time < day_start_utc(day + timedelta(days=1)), Activity.start_time <= utc_now_naive()).order_by(Activity.start_time.asc()).all()
    recent = [row for row in activities if start <= activity_day(row.start_time) <= day]
    previous = [row for row in activities if previous_start <= activity_day(row.start_time) < start]
    ninety = [row for row in activities if activity_day(row.start_time) >= day - timedelta(days=89)]
    settings, goal, goal_warnings = goal_context(db, load_settings(db))
    current_metrics = effective_metrics(db, day)
    health_start = date.fromordinal(max(1, day.toordinal() - 364))
    observed_health = health_response(db, health_start, day)
    weight = next(item for item in observed_health['metrics'] if item['kind'] == 'weight_kg')
    weight_recent = [point for point in weight['points'] if point['date'] >= str(start) and
                     weight['latest'] and point['source'] == weight['latest']['source']]
    weight = {**weight, 'change_28d_kg': round(weight_recent[-1]['value'] - weight_recent[0]['value'], 2) if len(weight_recent) >= 2 else None}
    # Aggregate observed load only; never estimate from a default FTP.
    known_loads = _known_loads(db, recent)
    recent_minutes = sum(max(0, row.duration_seconds or 0) / 60 for row in recent)
    previous_minutes = sum(max(0, row.duration_seconds or 0) / 60 for row in previous)
    frequency = Counter(_sport(row.sport) for row in recent)
    sports = []
    for key, label in LABELS.items():
        rows = [row for row in recent if _sport(row.sport) == key]
        all_rows = [row for row in activities if _sport(row.sport) == key]
        long_rows = [row for row in ninety if _sport(row.sport) == key]
        duration = [row.duration_seconds / 60 for row in rows if row.duration_seconds is not None and row.duration_seconds >= 0]
        distance = [row.distance_meters / 1000 for row in rows if row.distance_meters is not None and row.distance_meters >= 0]
        sports.append({'sport': key, 'label': label, 'activities': len(rows), 'weekly_sessions': round(len(rows) / 4, 1),
                       'minutes': round(sum(duration), 1), 'weekly_minutes': round(sum(duration) / 4, 1),
                       'distance_km': round(sum(distance), 1), 'longest_90d_min': round(max((row.duration_seconds / 60 for row in long_rows if row.duration_seconds is not None and row.duration_seconds >= 0), default=0), 1),
                       'totals': {'all_time': _totals(db, all_rows), 'days_90': _totals(db, long_rows), 'days_28': _totals(db, rows)},
                       'records': {'all_time': _records(all_rows), 'days_90': _records(long_rows)}})
    active_weeks = len({(activity_day(row.start_time).isocalendar().year, activity_day(row.start_time).isocalendar().week) for row in recent})
    strengths = []
    priorities = []
    if recent:
        dominant = frequency.most_common(1)[0][0]
        strengths.append(f"{LABELS[dominant]} è la disciplina più presente: {frequency[dominant]} attività nelle ultime quattro settimane.")
        if active_weeks >= 4:
            strengths.append('Attività registrate in almeno quattro settimane: una base di continuità osservabile.')
    else:
        priorities.append('Nessuna attività recente registrata: sincronizza lo storico prima di aumentare il programma.')
    if goal:
        names = {'cycling': 'bike', 'running': 'run', 'swimming': 'swim'}
        for discipline in goal['disciplines']:
            key = names[discipline]
            if not frequency.get(key):
                priorities.append(f"{LABELS[key]} richiesta da {goal['name']}, ma assente nelle ultime quattro settimane: ricostruisci continuità nel piano.")
    if previous_minutes > 0 and recent_minutes < previous_minutes * .7:
        priorities.append('Il volume registrato è diminuito di oltre il 30% rispetto alle quattro settimane precedenti: verifica se lo scarico era programmato.')
    restrictions = active_restrictions(db, day)
    priorities[:0] = restrictions['reasons']
    thresholds = {key: value for key, value in settings['thresholds'].items() if value.get('value') is not None}
    count = len(activities)
    first_day = activity_day(activities[0].start_time) if activities else None
    historical_days = (day - first_day).days + 1 if first_day else 0
    headline = ('Profilo da costruire con lo storico' if not recent else
                'Profilo multisport con continuità da consolidare' if sum(frequency.get(key, 0) > 0 for key in ('swim', 'bike', 'run')) > 1 else
                f"Profilo attuale concentrato su {LABELS[frequency.most_common(1)[0][0]].lower()}")
    return {'date': str(day), 'generated_at': utc_now_naive().isoformat() + 'Z', 'name': settings['name'] or None,
            'window': {'start': str(start), 'end': str(day), 'days': 28}, 'headline': headline,
            'activities': len(recent), 'total_activities': count, 'history_days': historical_days,
            'weekly_minutes': round(recent_minutes / 4, 1), 'previous_weekly_minutes': round(previous_minutes / 4, 1),
            'weekly_load': round(sum(value for _, value in known_loads) / 4, 1) if known_loads else None,
            'fitness': {key: round(current_metrics[key], 1) for key in ('ctl', 'atl', 'tsb', 'sleep_hours', 'hrv_ms', 'resting_hr_bpm') if current_metrics.get(key) is not None},
            'health': {'from': str(health_start), 'to': str(day), 'metrics': [item for item in observed_health['metrics'] if item['latest']], 'weight': weight},
            'totals': {'all_time': _totals(db, activities), 'days_90': _totals(db, ninety), 'days_28': _totals(db, recent)},
            'records_note': 'Record calcolati sulle attività intere registrate. Potenza e velocità medie non sono record su segmenti, curve di potenza o soglie FTP.',
            'sports': sports, 'goal': goal, 'strengths': strengths, 'priorities': priorities,
            'restrictions': restrictions, 'thresholds': thresholds, 'goal_warnings': goal_warnings,
            'unknown_fields': ['age', 'height_cm'] + ([] if weight['latest'] else ['weight_kg']),
            'manual_fields_preserved': True,
            'manual_preferences': {'weekly_minutes': settings['weekly_minutes'], 'availability': settings['availability'],
                                   'pool_access': settings['pool_access'], 'limitations': settings['limitations'], 'excluded_sports': settings['excluded_sports']}}
