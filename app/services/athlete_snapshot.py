"""A live, observed athlete profile, without manufacturing personal measurements."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import math

from app.db.models import Activity
from app.services.athlete_clock import activity_day, athlete_today, day_start_utc, utc_now_naive
from app.services.goal_planning import goal_context
from app.services.health import effective_metrics, health_response
from app.services.health_reports import active_restrictions
from app.services.observed_load import resolve_observed_loads
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
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _scaled(value, scale=1):
    value = _number(value)
    if value is None:
        return None
    result = value * scale
    return result if math.isfinite(result) else None


def _coverage(values, scale=1, unit=''):
    """Keep complete totals separate from the subtotal of available values."""
    values = list(values)
    known = [number for value in values if (number := _scaled(value, scale)) is not None]
    missing = sum(value is None for value in values)
    try:
        subtotal = math.fsum(known) if known or not values else None
    except OverflowError:
        subtotal = None
    if subtotal is not None and not math.isfinite(subtotal):
        subtotal = None
    complete = len(known) == len(values) and subtotal is not None
    status = ('empty' if not values else 'complete' if complete else
              'partial' if known and subtotal is not None else 'unavailable')
    result = {'known': len(known), 'expected': len(values), 'missing': missing,
              'invalid': len(values) - missing - len(known), 'complete': complete,
              'status': status, 'known_value': round(subtotal, 2) if subtotal is not None else None,
              'unit': unit}
    if known and subtotal is None:
        result['reason'] = 'aggregate_overflow'
    return result


def _complete_value(coverage):
    return coverage['known_value'] if coverage['complete'] else None


def _duration_coverage(rows):
    result = _coverage((row.duration_seconds for row in rows), 1 / 60, 'min')
    return {**result, 'known_minutes': result['known_value']}


def _weekly(value):
    return round(value / 4, 1) if value is not None else None


def _records(rows):
    result = {}
    for key, (attribute, scale, unit, label) in RECORD_FIELDS.items():
        coverage = _coverage((getattr(row, attribute) for row in rows), scale, unit)
        candidates = [(row, value) for row in rows
                      if (value := _scaled(getattr(row, attribute), scale)) is not None and
                      (value > 0 or attribute == 'elevation_gain_meters')]
        if not candidates:
            result[key] = None
            continue
        row, value = max(candidates, key=lambda item: (item[1], item[0].start_time, item[0].id))
        result[key] = {'value': round(value, 2), 'unit': unit, 'label': label,
                       'activity_id': row.id, 'activity_name': row.name or label, 'date': str(activity_day(row.start_time)),
                       'source': row.source, 'scope': 'whole_activity',
                       'candidate_coverage': {key: coverage[key] for key in ('known', 'expected', 'missing', 'invalid')} |
                                             {'eligible': len(candidates)}}
    return result


def _totals(rows, loads):
    fields = {'hours': ('duration_seconds', 1 / 3600, 'ore'),
              'distance_km': ('distance_meters', .001, 'km'),
              'elevation_m': ('elevation_gain_meters', 1, 'm'), 'calories': ('calories', 1, 'kcal')}
    coverage = {key: _coverage((getattr(row, attribute) for row in rows), scale, unit)
                for key, (attribute, scale, unit) in fields.items()}
    coverage['load'] = _coverage((loads[row.id]['value'] for row in rows), unit='punti')
    coverage['load']['conflicting'] = sum(loads[row.id]['status'] == 'conflict' for row in rows)
    coverage['load']['missing'] = sum(loads[row.id]['status'] == 'missing' for row in rows)
    coverage['load']['invalid'] = sum(loads[row.id]['status'] in ('invalid', 'conflict') for row in rows)
    return {'activities': len(rows), **{key: _complete_value(value) for key, value in coverage.items()},
            'coverage': {attribute: sum(_number(getattr(row, attribute)) is not None for row in rows)
                         for attribute in ('duration_seconds', 'distance_meters', 'elevation_gain_meters', 'calories')},
            'coverage_summary': coverage}


def _sport(value):
    key = sport_key(value)
    return key if key in LABELS else 'other'


def _volume_comparison(day, start, previous_start, current, previous):
    reasons = []
    for label, coverage in [('current', current), ('previous', previous)]:
        if not coverage['complete']:
            reasons.append(label + '_duration_incomplete')
    status = 'insufficient_data' if reasons else 'available'
    current_minutes, previous_minutes = _complete_value(current), _complete_value(previous)
    change = delta = None
    if not reasons:
        if previous_minutes <= 0:
            status = 'no_baseline'; reasons.append('previous_volume_zero')
        else:
            change = (current_minutes / previous_minutes - 1) * 100
            delta = current_minutes - previous_minutes
            if not math.isfinite(change) or not math.isfinite(delta):
                change = delta = None
                status = 'insufficient_data'; reasons.append('comparison_overflow')
    def window(first, last, coverage):
        return {'start': str(first), 'end': str(last), 'days': 28,
                'total_minutes': _complete_value(coverage), 'known_minutes': coverage['known_minutes'],
                'known_activities': coverage['known'], 'expected_activities': coverage['expected'],
                'complete': coverage['complete'], 'coverage_status': coverage['status']}
    return {'scope': 'registered_activity_duration', 'status': status, 'reasons': reasons,
            'current': window(start, day, current),
            'previous': window(previous_start, start - timedelta(days=1), previous),
            'known_activity_counts': {'current': current['known'], 'previous': previous['known']},
            'expected_activity_counts': {'current': current['expected'], 'previous': previous['expected']},
            'change_pct': round(change, 1) if change is not None else None,
            'delta_minutes': round(delta, 2) if delta is not None else None}


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
    from app.services.body_metrics import body_mass_index
    body_mass = body_mass_index(settings, weight, day)
    # Resolve provenance once, then reuse it for every window and discipline.
    # Missing fields remain unknown; available subtotals carry explicit coverage.
    loads = resolve_observed_loads(db, activities)
    duration_coverage = _duration_coverage(recent)
    previous_duration_coverage = _duration_coverage(previous)
    recent_minutes = _complete_value(duration_coverage)
    previous_minutes = _complete_value(previous_duration_coverage)
    volume_comparison = _volume_comparison(day, start, previous_start, duration_coverage, previous_duration_coverage)
    totals = {'all_time': _totals(activities, loads), 'days_90': _totals(ninety, loads),
              'days_28': _totals(recent, loads)}
    frequency = Counter(_sport(row.sport) for row in recent)
    sports = []
    for key, label in LABELS.items():
        rows = [row for row in recent if _sport(row.sport) == key]
        all_rows = [row for row in activities if _sport(row.sport) == key]
        long_rows = [row for row in ninety if _sport(row.sport) == key]
        duration = _duration_coverage(rows)
        distance = _coverage((row.distance_meters for row in rows), .001, 'km')
        records = {'all_time': _records(all_rows), 'days_90': _records(long_rows), 'days_28': _records(rows)}
        longest = records['days_90']['longest_duration_min']
        sports.append({'sport': key, 'label': label, 'activities': len(rows), 'weekly_sessions': round(len(rows) / 4, 1),
                       'minutes': _complete_value(duration), 'weekly_minutes': _weekly(_complete_value(duration)),
                       'duration_coverage': duration, 'distance_coverage': distance,
                       'distance_km': _complete_value(distance), 'longest_90d_min': longest['value'] if longest else None,
                       'totals': {'all_time': _totals(all_rows, loads), 'days_90': _totals(long_rows, loads),
                                  'days_28': _totals(rows, loads)}, 'records': records})
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
    if volume_comparison['status'] == 'available' and volume_comparison['change_pct'] < -30:
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
            'weekly_minutes': _weekly(recent_minutes), 'previous_weekly_minutes': _weekly(previous_minutes),
            'duration_coverage': duration_coverage, 'previous_duration_coverage': previous_duration_coverage,
            'volume_comparison': volume_comparison,
            'weekly_load': _weekly(totals['days_28']['load']),
            'fitness': {key: round(current_metrics[key], 1) for key in ('ctl', 'atl', 'tsb', 'sleep_hours', 'hrv_ms', 'resting_hr_bpm') if current_metrics.get(key) is not None},
            'health': {'from': str(health_start), 'to': str(day), 'metrics': [item for item in observed_health['metrics'] if item['latest']], 'weight': weight},
            'body_mass': body_mass, 'personal_details': {'height_cm': settings['height_cm'], 'birth_date': settings['birth_date'], 'sex': settings['sex']},
            'totals': totals,
            'records_note': 'Record calcolati sulle attività intere registrate. Potenza e velocità medie non sono record su segmenti, curve di potenza o soglie FTP.',
            'sports': sports, 'goal': goal, 'strengths': strengths, 'priorities': priorities,
            'restrictions': restrictions, 'thresholds': thresholds, 'goal_warnings': goal_warnings,
            'unknown_fields': ([] if settings['birth_date'] else ['age']) + ([] if settings['height_cm'] else ['height_cm']) + ([] if settings['sex'] else ['sex']) + ([] if weight['latest'] else ['weight_kg']),
            'manual_fields_preserved': True,
            'manual_preferences': {'weekly_minutes': settings['weekly_minutes'], 'availability': settings['availability'],
                                   'pool_access': settings['pool_access'], 'limitations': settings['limitations'], 'excluded_sports': settings['excluded_sports']}}
