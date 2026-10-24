"""A reproducible rating of recorded training, independent of plan matching.

The score describes how the documented session fits the athlete's recent work.
It is not a physiological performance test. Missing intensity never becomes an
invented FTP, RPE or zero load; confidence remains separate from the score.
"""
import json
from collections import defaultdict, deque
from bisect import bisect_right
from datetime import date, datetime, timedelta
from statistics import median
from types import SimpleNamespace

from app.db.iron_models import HealthMetric, RaceGoal
from app.db.models import Activity, DailyMetrics
from app.db.planning_models import AthleteSettings
from app.services.athlete_clock import activity_day, as_utc_naive, utc_now_naive
from app.services.iron_coach import expected_benefit, grade
from app.services.training_load import sport_key
from app.services.health import usable_metric_value
from app.services.observed_load import finite_load, load_values, resolve_observed_loads

VERSION = 'recorded-session-1'


def _day_before(day, days):
    return date.fromordinal(max(1, day.toordinal() - days))


def _stamp_before(stamp, days):
    try:
        return stamp - timedelta(days=days)
    except OverflowError:
        return datetime.min


def _number(value, positive=False):
    value = finite_load(value)
    return value if value is not None and (not positive or value > 0) else None


def recorded_loads(db, activities):
    """Use the shared provider-identity policy, preserving missing/conflicting load."""
    return load_values(resolve_observed_loads(db, activities))


def _component(key, label, score, weight, reason):
    return dict(key=key, label=label, score=score, weight=weight, reason=reason)


def _dose(activity, load, history, loads):
    key = sport_key(activity.sport)
    peers = [row for row in history if sport_key(row.sport) == key]
    # Compare like with like; do not compare an activity's load to its duration.
    load_peers = [loads[row.id] for row in peers if loads[row.id] is not None and loads[row.id] > 0]
    duration_peers = [_number(row.duration_seconds, positive=True) for row in peers]
    duration_peers = [value for value in duration_peers if value is not None]
    if load is not None and load > 0 and len(load_peers) >= 3:
        value, baseline, unit = load, median(load_peers), 'punti di carico'
    elif _number(activity.duration_seconds, positive=True) is not None and len(duration_peers) >= 3:
        value, baseline, unit = activity.duration_seconds / 60, median(duration_peers) / 60, 'minuti'
    else:
        return _component('stimulus', 'Stimolo registrato', 65, 60,
                          'Seduta registrata. Mancano tre precedenti della stessa disciplina per valutarne la proporzione.'), False
    ratio = value / baseline
    # A short recovery session is not automatically a bad session. Penalize
    # unusually large doses, never the absence of a maximal effort.
    score = 85 if ratio <= 1.3 else 70 if ratio <= 1.6 else 50 if ratio <= 2 else 30
    description = ('Stimolo contenuto: compatibile con una seduta breve o di recupero.' if ratio < .5 else
                   'Stimolo proporzionato alle sedute recenti.' if ratio <= 1.3 else
                   'Stimolo superiore al solito: richiede una motivazione nel piano.' if ratio <= 1.6 else
                   'Incremento marcato della singola seduta: il recupero successivo va protetto.')
    reason = f'{value:.0f} {unit}; riferimento personale {baseline:.0f}. {description}'
    return _component('stimulus', 'Proporzione dello stimolo', score, 60, reason), True


def _distribution(activity, load, history, loads):
    stamp = as_utc_naive(activity.start_time)
    # Two comparable preceding weeks and an established observation horizon
    # are required; absence of early history is not an empty training week.
    if stamp.toordinal() <= 21 or not history or as_utc_naive(history[0].start_time) > _stamp_before(stamp, 21):
        return None
    boundaries = [_stamp_before(stamp, 7*index) for index in range(4, -1, -1)]
    groups = [[], [], [], []]
    for row in history:
        slot = bisect_right(boundaries, as_utc_naive(row.start_time)) - 1
        if 0 <= slot < 4:
            groups[3-slot].append(row)
    current = groups[0] + [activity]
    use_load = load is not None and all(loads[row.id] is not None for row in history)
    def total(rows):
        if use_load:
            return sum(loads[row.id] for row in rows)
        values = [_number(row.duration_seconds) for row in rows]
        return sum(values) / 60 if all(value is not None for value in values) else None
    reference = [total(rows) for rows in groups[1:]]
    reference = [value for value in reference if value is not None and value > 0]
    current_value = total(current)
    if current_value is None or len(reference) < 2:
        return None
    baseline = median(reference)
    ratio = current_value / baseline
    score = 85 if ratio <= 1.15 else 75 if ratio <= 1.3 else 55 if ratio <= 1.5 else 30
    unit = 'punti' if use_load else 'minuti'
    label = 'Accumulo elevato: evita altro lavoro impegnativo.' if ratio > 1.3 else 'Distribuzione compatibile con il volume recente.'
    return _component('distribution', 'Accumulo negli ultimi 7 giorni', score, 25,
                      f'{current_value:.0f} {unit}, riferimento {baseline:.0f} ({(ratio-1)*100:+.0f}%). {label}')


def _recovery(activity, health):
    # Date-only wellness has no measurement time. Use only a preceding day,
    # never the same day or a measurement after the recorded session.
    day = activity_day(activity.start_time)
    earliest = str(_day_before(day, 2)); before = str(day)
    signals = []
    for kind in ('tsb', 'sleep_hours', 'hrv_ms', 'resting_hr_bpm'):
        candidates = [row for row in health.get(kind, []) if earliest <= row['date'] < before]
        if not candidates:
            continue
        latest = max(candidates, key=lambda row: (row['date'], row['source'] == 'intervals'))
        value = latest['value']
        if kind == 'tsb':
            score = 20 if value <= -30 else 40 if value <= -20 else 65 if value < -10 else 85
            signals.append((score, f'Bilancio precedente {value:.0f} punti'))
        elif kind == 'sleep_hours':
            score = 30 if value < 5 else 55 if value < 6 else 70 if value < 7 else 85
            signals.append((score, f'Sonno precedente {value:.1f} ore'))
        else:
            baseline_rows = [row['value'] for row in health[kind] if str(_day_before(day, 29)) <= row['date'] < latest['date']
                             and row['source'] == latest['source']]
            if len(baseline_rows) < 7:
                continue
            baseline = median(baseline_rows)
            if baseline <= 0:
                continue
            ratio = value / baseline
            adverse = ratio < .8 if kind == 'hrv_ms' else ratio > 1.1
            signals.append((45 if adverse else 85, ('HRV' if kind == 'hrv_ms' else 'FC a riposo') +
                            (' distante dal riferimento personale' if adverse else ' vicina al riferimento personale')))
    if not signals:
        return None
    return _component('recovery', 'Recupero documentato prima della seduta', min(score for score, _ in signals), 15,
                      '; '.join(reason for _, reason in signals) + '. Questi segnali orientano il giudizio, senza identificarne la causa.')


def _health_index(db, start=None, end=None):
    result = defaultdict(list)
    kinds = ('tsb', 'sleep_hours', 'hrv_ms', 'resting_hr_bpm')
    query = db.query(HealthMetric).filter(HealthMetric.kind.in_(kinds))
    manual = db.query(DailyMetrics)
    if start is not None:
        query = query.filter(HealthMetric.date >= str(start)); manual = manual.filter(DailyMetrics.date >= str(start))
    if end is not None:
        query = query.filter(HealthMetric.date <= str(end)); manual = manual.filter(DailyMetrics.date <= str(end))
    saved = {}
    for row in query.all():
        # TSB can be negative; validate it separately from nonnegative readings.
        value = usable_metric_value(row.kind, row.value)
        try:
            date.fromisoformat(row.date)
        except (TypeError, ValueError):
            continue
        if (value is not None and isinstance(row.source, str) and row.source.strip()
                and row.source.lower() not in ('local_estimate', 'estimated', 'estimate', 'unknown')
                and (row.kind != 'sleep_hours' or value <= 24)):
            saved[(row.date, row.kind, row.source)] = dict(date=row.date, source=row.source, value=value)
    for row in manual.all():
        try:
            date.fromisoformat(row.date)
        except (TypeError, ValueError):
            continue
        for kind in kinds[1:]:
            # A live manual row owns its fields, including a cleared value;
            # migrated copies must not resurrect readings or count twice.
            saved.pop((row.date, kind, 'manual'), None)
            value = usable_metric_value(kind, getattr(row, kind, None))
            if value is not None and (kind != 'sleep_hours' or value <= 24):
                saved[(row.date, kind, 'manual')] = dict(date=row.date, source='manual', value=value)
    for (_, kind, _), point in sorted(saved.items()):
        result[kind].append(point)
    return result


def _current_goal(db):
    settings = db.get(AthleteSettings, 1)
    try:
        from app.services.execution_evidence import safe_object
        content = safe_object(settings.payload_json) if settings else None
        identifier = content.get('race_goal_id') if content else None
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            return None, []
        goal = db.get(RaceGoal, identifier) if identifier else None
        legs = json.loads(goal.legs_json) if goal and len(goal.legs_json) <= 524288 else None
        if not isinstance(legs, list) or len(legs) > 100 or any(not isinstance(leg, dict) or not isinstance(leg.get('sport'), str) for leg in legs):
            return None, []
        return goal, legs
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        return None, []


def _rating(activity, load, history, loads, health, goal, legs, now):
    base = dict(activity_id=activity.id, scope='recorded_session', score=None, judgment='Dati non utilizzabili',
                tone='unknown', level=None, confidence_percent=0, components=[], evidence=[], actions=[],
                limits=['Il voto descrive l’adeguatezza della seduta registrata: non misura la prestazione assoluta né gli adattamenti.'],
                benefits=expected_benefit(activity.sport), rules_version=VERSION)
    duration = _number(activity.duration_seconds, positive=True)
    if as_utc_naive(activity.start_time) > now:
        base['summary'] = base['explanation'] = 'La seduta è futura: il Coach valuta soltanto lavoro già registrato.'
        return base
    if duration is None and not (load is not None and load > 0):
        base['summary'] = base['explanation'] = 'Mancano una durata valida e un carico positivo. Non attribuisco un voto inventato.'
        return base
    components = []
    dose, comparable = _dose(activity, load, history, loads)
    components.append(dose)
    distribution = _distribution(activity, load, history, loads)
    if distribution:
        components.append(distribution)
    recovery = _recovery(activity, health)
    if recovery:
        components.append(recovery)
    total_weight = sum(item['weight'] for item in components)
    score = round(sum(item['score'] * item['weight'] for item in components) / total_weight)
    level = 5 if score >= 80 else 4 if score >= 65 else 3 if score >= 50 else 2 if score >= 35 else 1
    evidence = [item['reason'] for item in components]
    if duration is not None:
        evidence.insert(0, f'Durata {duration/60:.0f} minuti.' + (f' Carico {load:.0f} punti.' if load is not None else ''))
    elif load is not None:
        evidence.insert(0, f'Carico registrato {load:.0f} punti; durata non disponibile.')
    intensity = any(_number(getattr(activity, key, None), positive=True) is not None
                    for key in ('avg_hr', 'avg_power', 'normalized_power'))
    if intensity:
        readings = [('FC media', activity.avg_hr, 'bpm'), ('Potenza media', activity.avg_power, 'W'),
                    ('Potenza normalizzata', activity.normalized_power, 'W')]
        evidence.append(' · '.join(f'{label} {value:.0f} {unit}' for label, value, unit in readings
                                   if _number(value, positive=True) is not None) + '.')
    confidence = min(90, 25 + 15*(duration is not None) + 15*(load is not None) + 10*intensity
                     + 15*comparable + 5*(distribution is not None) + 5*(recovery is not None))
    if not comparable:
        base['limits'].append('Voto provvisorio: lo storico comparabile della disciplina è ancora breve.')
    if not intensity:
        base['limits'].append('Intensità non verificata: durata e carico non descrivono l’esecuzione delle singole fasi.')
    if goal and any(sport_key(leg.get('sport')) == sport_key(activity.sport) for leg in legs):
        # Current goal is context, not retrospective proof of this session's intent.
        evidence.append(f'Per l’obiettivo attuale {goal.name}, questa disciplina è specifica. La singola seduta non certifica la distanza gara.')
    action = ('Proteggi il recupero: evita di sommare un’altra seduta impegnativa allo stesso accumulo.' if score < 55 else
              'Mantieni controllato il prossimo lavoro; verifica la risposta prima di aumentare durata o intensità.' if score < 70 else
              'Conserva questa proporzione tra stimolo e recupero; aumenta solo quando il contesto della settimana lo consente.')
    summary = ('Stimolo eccessivo rispetto al contesto documentato.' if score < 40 else
               'Seduta poco equilibrata: serve maggiore controllo del carico.' if score < 55 else
               'Seduta accettabile, con margini da verificare.' if score < 70 else
               'Seduta ben proporzionata al lavoro recente.' if score < 85 else
               'Seduta ben inserita nel contesto di allenamento.')
    return {**base, 'score': score, 'judgment': grade(score), 'tone': {1:'critical',2:'caution',3:'watch',4:'positive',5:'good'}[level],
            'level': level, 'confidence_percent': confidence, 'summary': summary, 'explanation': summary,
            'components': components, 'evidence': evidence, 'actions': [action]}


def activity_ratings(db, activities=None, *, loads=None, now=None, target_ids=None):
    """Bounded chronological pass; no query is executed per activity."""
    now = as_utc_naive(now or utc_now_naive())
    target_ids = set(target_ids) if target_ids is not None else None
    if activities is None and target_ids is not None:
        targets = db.query(Activity).filter(Activity.id.in_(target_ids)).all() if target_ids else []
        if not targets:
            return {}
        first = min(as_utc_naive(row.start_time) for row in targets)
        last = max(as_utc_naive(row.start_time) for row in targets)
        prior = db.query(Activity).filter(Activity.start_time >= _stamp_before(first, 42),
            Activity.start_time <= min(last, now)).all()
        activities = list({row.id: row for row in prior + targets}.values())
    else:
        activities = list(activities) if activities is not None else db.query(Activity).all()
    loads = loads if loads is not None else recorded_loads(db, activities)
    observed_days = [activity_day(row.start_time) for row in activities if as_utc_naive(row.start_time) <= now
                     and (target_ids is None or row.id in target_ids)]
    health = _health_index(db, _day_before(min(observed_days), 29), _day_before(max(observed_days), 1)) if observed_days else {}
    goal, legs = _current_goal(db)
    # Rating repeatedly reads a bounded recent history. Plain immutable-by-use
    # snapshots avoid hundreds of thousands of SQLAlchemy attribute lookups;
    # they never mutate or detach the persisted Activity objects.
    activities = [SimpleNamespace(**{key: getattr(row, key) for key in
                  ('id', 'sport', 'start_time', 'duration_seconds', 'avg_hr', 'avg_power', 'normalized_power')})
                  for row in activities]
    history = deque(); result = {}
    for activity in sorted(activities, key=lambda row: (as_utc_naive(row.start_time), row.id)):
        stamp = as_utc_naive(activity.start_time)
        while history and as_utc_naive(history[0].start_time) < _stamp_before(stamp, 42):
            history.popleft()
        if target_ids is None or activity.id in target_ids:
            prior = [row for row in history if as_utc_naive(row.start_time) < stamp]
            result[activity.id] = _rating(activity, loads[activity.id], prior, loads, health, goal, legs, now)
        if stamp <= now:
            history.append(activity)
    return result
