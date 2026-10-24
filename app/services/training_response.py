"""Read-only associations between recorded load and dated wellness changes.

Calendar dates cannot establish measurement order or isolate a training effect.
All reads use the supplied workspace session; no provider or local-file access.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import math

from sqlalchemy import or_
from sqlalchemy.orm import aliased

from app.db.iron_models import ActivityObservation, DuplicateCandidate, HealthMetric
from app.db.models import Activity, DailyMetrics
from app.db.repository import exact_duplicate_groups, MAX_DUPLICATE_DECISIONS
from app.services.athlete_clock import (
    ATHLETE_TIMEZONE, activity_day, as_utc_naive, athlete_today, day_start_utc,
    utc_now_naive,
)
from app.services.health import METRICS, series, usable_metric_value
from app.services.training_insights import _recorded_loads


VERSION = 'observed-training-response-1'
MAX_ACTIVITIES = 50000
MAX_OBSERVATIONS = 50000
MAX_WELLNESS = 20000
MIN_PAIRS = 5
KINDS = ('hrv_ms', 'resting_hr_bpm', 'sleep_hours')
SOURCES = {'intervals': 'Intervals.icu', 'manual': 'Inserimento manuale',
           'local_estimate': 'Stima locale',
           'unavailable': 'Dati non disponibili'}


def _quantile(values, fraction):
    """Weighted interpolation avoids overflowing an even-sample median."""
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return values[low]
    part = position - low
    value = values[low] * (1 - part) + values[high] * part
    return value if math.isfinite(value) else None


def _wellness_value(kind, point):
    if point is None or point['source'] == 'local_estimate':
        return None
    value = usable_metric_value(kind, point['value'])
    if kind == 'sleep_hours' and value is not None and value > 24:
        return None
    return value


def _raw_value(point):
    value = point['value'] if point else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _duplicate_dates(db, activities, start_utc, end_utc):
    """Include exact leads and persisted near matches, even across the window.

    Outer joins retain pending candidates whose other endpoint is orphaned.
    A prior explicit kept/merged decision wins over reverse pending legacy rows.
    """
    by_id = {row.id: activity_day(row.start_time) for row in activities}
    first, second = aliased(Activity), aliased(Activity)
    decisions = db.query(DuplicateCandidate).outerjoin(
        first, first.id == DuplicateCandidate.activity_id,
    ).outerjoin(second, second.id == DuplicateCandidate.other_id).filter(or_(
        (first.start_time >= start_utc) & (first.start_time < end_utc),
        (second.start_time >= start_utc) & (second.start_time < end_utc),
    )).order_by(DuplicateCandidate.id.asc()).limit(MAX_DUPLICATE_DECISIONS + 1).all()
    truncated = len(decisions) > MAX_DUPLICATE_DECISIONS
    resolved, pending = set(), set()
    for row in decisions[:MAX_DUPLICATE_DECISIONS]:
        if row.activity_id == row.other_id:
            continue
        pair = tuple(sorted((row.activity_id, row.other_id)))
        if row.status in ('kept', 'merged'):
            resolved.add(pair)
        else:
            pending.add(pair)
    pending -= resolved
    dates = {by_id[identifier] for pair in pending for identifier in pair if identifier in by_id}
    groups = exact_duplicate_groups(activities)
    membership = {row.id: index for index, group in enumerate(groups) for row in group}
    reviewed_by_group = defaultdict(int)
    for first_id, second_id in resolved:
        index = membership.get(first_id)
        if index is not None and membership.get(second_id) == index:
            reviewed_by_group[index] += 1
    unresolved_exact = 0
    for index, group in enumerate(groups):
        reviewed = reviewed_by_group[index]
        unresolved = len(group) * (len(group) - 1) // 2 - reviewed
        if unresolved:
            dates.update(by_id[row.id] for row in group)
            unresolved_exact += unresolved
    return dates, truncated, len(pending), unresolved_exact


def _groups(rows, incomplete):
    eligible = [row for row in rows if row['eligible']]
    split = _quantile([row['load'] for row in eligible], .5)
    groups = []
    for key, label in [('lower', 'Carico fino alla mediana'), ('higher', 'Carico sopra la mediana')]:
        selected = [row for row in eligible if (row['load'] <= split if key == 'lower' else row['load'] > split)]
        changes = [row['change'] for row in selected]
        loads = [row['load'] for row in selected]
        groups.append({'key': key, 'label': label, 'count': len(selected),
                       'load_min': min(loads) if loads else None, 'load_max': max(loads) if loads else None,
                       'median_change': _quantile(changes, .5),
                       'q25_change': _quantile(changes, .25), 'q75_change': _quantile(changes, .75)})
    available = not incomplete and all(group['count'] >= MIN_PAIRS for group in groups)
    difference = groups[1]['median_change'] - groups[0]['median_change'] if available else None
    if difference is not None and not math.isfinite(difference):
        available = False
        difference = None
        reason = 'La differenza supera l’intervallo numerico rappresentabile.'
    elif incomplete:
        reason = 'Dati o controlli incompleti: il confronto è sospeso.'
    elif available:
        reason = 'Differenza descrittiva tra mediane; non dimostra un effetto del carico.'
    elif split is not None and not groups[1]['count']:
        reason = 'I carichi sono identici o troppo concentrati sulla mediana: manca il gruppo superiore.'
    else:
        reason = 'Servono almeno cinque coppie utilizzabili in ciascun gruppo, della stessa fonte.'
    return len(eligible), split, groups, {'available': available, 'reason': reason,
                                       'higher_minus_lower': difference, 'min_pairs_per_group': MIN_PAIRS}


def training_response(db, as_of: date | None = None, days=84, lag=1, *, now: datetime | None = None):
    now = as_utc_naive(now if now is not None else utc_now_naive())
    today = athlete_today(now)
    day = as_of or today
    if not isinstance(day, date) or isinstance(day, datetime) or not date(1970, 1, 1) <= day <= today:
        raise ValueError('Scegli una data dal 1 gennaio 1970 a oggi.')
    if isinstance(days, bool) or not isinstance(days, int) or not 28 <= days <= 180:
        raise ValueError('Scegli un periodo da 28 a 180 giorni.')
    if isinstance(lag, bool) or lag not in (1, 2):
        raise ValueError('Scegli una distanza di uno o due giorni di calendario.')
    end = day - timedelta(days=lag)
    start = end - timedelta(days=days - 1)
    start_utc, end_utc = day_start_utc(start), day_start_utc(day)
    all_activities = db.query(Activity).filter(
        Activity.start_time >= start_utc, Activity.start_time < end_utc,
        Activity.start_time <= now,
    ).order_by(Activity.start_time.asc(), Activity.id.asc()).limit(MAX_ACTIVITIES + 1).all()
    activity_truncated = len(all_activities) > MAX_ACTIVITIES
    all_activities = all_activities[:MAX_ACTIVITIES]
    training = [row for row in all_activities if activity_day(row.start_time) <= end]
    by_day = defaultdict(list)
    for row in all_activities:
        by_day[activity_day(row.start_time)].append(row)
    observations = db.query(ActivityObservation.id).join(Activity, Activity.id == ActivityObservation.activity_id).filter(
        Activity.start_time >= start_utc, Activity.start_time < end_utc, Activity.start_time <= now,
        ActivityObservation.source_key.like('intervals:%'),
    ).limit(MAX_OBSERVATIONS + 1).all()
    observations_truncated = len(observations) > MAX_OBSERVATIONS
    loads = (_recorded_loads(db, all_activities, start_utc, end_utc, now)
             if not observations_truncated else {row.id: None for row in all_activities})
    health_count = db.query(HealthMetric.id).filter(HealthMetric.date >= str(start), HealthMetric.date <= str(day)).limit(MAX_WELLNESS + 1).all()
    manual_count = db.query(DailyMetrics.id).filter(DailyMetrics.date >= str(start), DailyMetrics.date <= str(day)).limit(MAX_WELLNESS + 1).all()
    wellness_truncated = len(health_count) > MAX_WELLNESS or len(manual_count) > MAX_WELLNESS
    points = series(db, start, day) if not wellness_truncated else []
    points = [point for point in points if point['kind'] in KINDS]
    point_map = {(point['kind'], point['date']): point for point in points}
    duplicate_days, review_truncated, pending_pairs, exact_pairs = _duplicate_dates(db, training, start_utc, day_start_utc(end + timedelta(days=1)))
    truncated = activity_truncated or observations_truncated or wellness_truncated or review_truncated
    training_days = sorted({activity_day(row.start_time) for row in training})
    day_records = {}
    exclusions = {'missing_load': [], 'load_overflow': [], 'duplicate_review': [], 'dataset_truncated': [],
                  'days_without_activities': [str(start + timedelta(days=index)) for index in range(days)
                                              if start + timedelta(days=index) not in training_days]}
    for training_day in training_days:
        records = by_day[training_day]
        values = [loads[row.id] for row in records]
        missing = any(value is None for value in values)
        load, overflow = None, False
        if not missing:
            try:
                load = math.fsum(values)
                overflow = not math.isfinite(load)
            except OverflowError:
                overflow = True
            if overflow:
                load = None
        reasons = [reason for condition, reason in [(missing, 'missing_load'), (overflow, 'load_overflow'),
                   (training_day in duplicate_days, 'duplicate_review'), (truncated, 'dataset_truncated')] if condition]
        for reason in reasons:
            exclusions[reason].append(str(training_day))
        intervening = [training_day + timedelta(days=index) for index in range(1, lag)
                       if by_day[training_day + timedelta(days=index)]]
        day_records[training_day] = {'training_date': str(training_day),
            'response_date': str(training_day + timedelta(days=lag)), 'load': load,
            'activities': len(records), 'reasons': reasons,
            'intervening_activities': sum(len(by_day[item]) for item in intervening),
            'intervening_dates': [str(item) for item in intervening]}
    metrics = []
    for kind in KINDS:
        sources = sorted({point['source'] for point in points if point['kind'] == kind}) or ['unavailable']
        output = []
        for source in sources:
            rows = []
            for training_day in training_days:
                record = day_records[training_day]
                before = point_map.get((kind, record['training_date']))
                after = point_map.get((kind, record['response_date']))
                before_value, after_value = _wellness_value(kind, before), _wellness_value(kind, after)
                reasons = list(record['reasons'])
                for point, value, label in [(before, before_value, 'before'), (after, after_value, 'after')]:
                    if point is None:
                        reasons.append('missing_' + label)
                    elif point['source'] == 'local_estimate':
                        reasons.append('estimated_' + label)
                    elif value is None:
                        reasons.append('invalid_' + label)
                if (before and before['source'] != source) or (after and after['source'] != source):
                    reasons.append('source_mismatch')
                change = (after_value - before_value if before_value is not None and after_value is not None
                          and before['source'] == after['source'] == source else None)
                if change is not None and not math.isfinite(change):
                    change = None
                    reasons.append('change_overflow')
                rows.append({key: value for key, value in record.items() if key != 'reasons'} | {
                    'before': _raw_value(before), 'after': _raw_value(after),
                    'before_source': before['source'] if before else None,
                    'after_source': after['source'] if after else None,
                    'change': change, 'eligible': not reasons,
                    'exclusion_reason': reasons[0] if reasons else None, 'exclusion_reasons': reasons})
            paired, split, groups, comparison = _groups(rows, truncated)
            output.append({'source': source, 'source_label': SOURCES.get(source, source), 'rows': rows,
                           'paired_days': paired, 'load_split_median': split,
                           'groups': groups, 'comparison': comparison})
        metrics.append({'kind': kind, 'label': METRICS[kind]['label'], 'unit': METRICS[kind]['unit'], 'series': output})
    return {'version': VERSION, 'timezone': ATHLETE_TIMEZONE, 'lag_days': lag,
        'period': {'start': str(start), 'end': str(end), 'days': days, 'as_of': str(day),
                   'response_start': str(start + timedelta(days=lag)), 'response_end': str(day),
                   'includes_partial_response_day': day == today},
        'coverage': {'recorded_training_days': len(training_days),
                     'complete_load_days': sum(record['load'] is not None for record in day_records.values()),
                     'eligible_training_days': sum(not record['reasons'] for record in day_records.values()),
                     'missing_load_days': len(exclusions['missing_load']), 'load_overflow_days': len(exclusions['load_overflow']),
                     'duplicate_days': len(duplicate_days), 'days_without_activities': len(exclusions['days_without_activities']),
                     'intervening_activity_days': sum(bool(record['intervening_dates']) for record in day_records.values())},
        'metrics': metrics,
        'quality': {'truncated': truncated, 'activity_limit': MAX_ACTIVITIES,
                    'activity_truncated': activity_truncated, 'observations_truncated': observations_truncated,
                    'wellness_truncated': wellness_truncated, 'review_truncated': review_truncated,
                    'pending_pairs': pending_pairs, 'unresolved_exact_pairs': exact_pairs, 'exclusions': exclusions,
                    'summary': ('Dati o controlli oltre i limiti di lettura: confronti sospesi e conteggi parziali.' if truncated else
                                'Sono confrontate solo coppie della stessa fonte con carico completo e senza duplicati da chiarire. Le altre righe restano visibili.')},
        'methodology': [
            'Analisi descrittiva del carico registrato, senza punteggio di salute, previsione, causalità o raccomandazione automatica.',
            'Le giornate di allenamento sono concluse nel calendario Europe/Rome. La distanza è di uno o due giorni di calendario, non di 24 o 48 ore misurate.',
            'Prima e dopo sono valori etichettati con le due date: non è verificato che le misure precedano o seguano la seduta. Il sonno è attribuito alla data riportata dalla fonte.',
            'as_of filtra le date di osservazione nei dati attualmente archiviati: non ricostruisce quali dati fossero disponibili in passato. L’ultima giornata di risposta può essere ancora in corso.',
            'La stessa fonte identifica il fornitore o l’inserimento manuale, non certifica identico dispositivo, protocollo o orario di misura.',
            'Le osservazioni contrassegnate come stime locali restano consultabili ma non entrano nelle coppie o nei confronti.',
            'Il carico somma solo valori registrati finiti e non negativi, con precedenza Intervals anche quando il valore è mancante. Nessuna FTP predefinita o carico stimato.',
            'Carichi di fonti e discipline diverse possono usare metodi diversi: la somma e le differenze tra gruppi non rappresentano una misura fisiologica standardizzata.',
            'Una data senza attività registrate è sconosciuta: non viene trasformata in riposo o carico zero. Uno zero di carico realmente registrato è utilizzabile.',
            'Per ogni metrica e fonte la mediana dei carichi delle coppie utilizzabili separa i gruppi: fino alla mediana e sopra la mediana. Tutte le parità restano nel gruppo inferiore.',
            'Il confronto richiede cinque coppie per gruppo. Quartili e differenza delle mediane restano nelle unità originali e non indicano valori ottimali.',
            'I controlli escludono corrispondenze esatte e candidati duplicati ancora da chiarire, inclusi quelli con un’altra attività fuori periodo. Una scelta esplicita di mantenerle distinte viene rispettata.',
            'Le coppie possono sovrapporsi e condividere letture: non sono campioni indipendenti. A due giorni eventuali attività intermedie sono indicate, senza isolare il loro effetto o quello di sonno e vita quotidiana.',
        ]}
