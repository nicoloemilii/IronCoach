"""Observed training patterns, with comparable dates and visible missing data.

This is a description of imported activities, not an adherence assessment or a
readiness model. A date without an activity is not evidence of rest. All reads
use the caller's SQLAlchemy session, including the hosted workspace session.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import math

from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.db.repository import exact_duplicate_review, exact_duplicate_counts
from app.services.athlete_clock import (
    ATHLETE_TIMEZONE, ATHLETE_TZ, activity_day, as_utc_naive, athlete_today,
    day_start_utc, local_stamp_to_utc_naive, utc_now_naive,
)
from app.services.training_load import sport_key


VERSION = 'observed-training-patterns-1'
PERIOD_DAYS = 28
MIN_AS_OF = date(1970, 1, 1)
SPORT_LABELS = {'bike': 'Bici', 'run': 'Corsa', 'swim': 'Nuoto', 'strength': 'Palestra', 'other': 'Altro'}
SPORT_KEYS = {'bike': 'cycling', 'run': 'running', 'swim': 'swimming', 'strength': 'strength', 'other': 'other'}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _sport(value):
    key = sport_key(value)
    if key in ('gym', 'weightlifting', 'workout'):
        key = 'strength'
    return key if key in SPORT_LABELS else 'other'


def _round(value, digits=1):
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _observed(values, total, scale=1):
    """Keep an explicit zero; an entirely unknown aggregate remains unknown."""
    known = [value for value in values if value is not None]
    aggregation_error = False
    try:
        value = math.fsum(value * scale for value in known) if known or total == 0 else None
    except OverflowError:
        value = None
        aggregation_error = True
    if value is not None and not math.isfinite(value):
        value = None
        aggregation_error = True
    return _round(value, 2), {
        'known': len(known), 'total': total,
        'percent': round(len(known) / total * 100, 1) if total else None,
        'complete': len(known) == total and not aggregation_error,
        'aggregation_error': aggregation_error,
    }


def _totals(rows, loads):
    result = {'activities': len(rows), 'coverage': {}}
    fields = (
        ('minutes', 'duration_seconds', 1 / 60, 'minutes'),
        ('distance_km', 'distance_meters', .001, 'distance'),
        ('elevation_m', 'elevation_gain_meters', 1, 'elevation'),
    )
    for key, attribute, scale, coverage_key in fields:
        result[key], result['coverage'][coverage_key] = _observed(
            [_number(getattr(row, attribute)) for row in rows], len(rows), scale,
        )
    result['load'], result['coverage']['load'] = _observed([loads[row.id] for row in rows], len(rows))
    return result


def _recorded_loads(db, rows, start_utc, end_utc, now):
    """One bounded observation query; an Intervals null overrides an old value.

    Never estimate a missing load from an unconfirmed/default FTP. Observations
    are ordered so a duplicate legacy association has a deterministic winner.
    """
    loads = {row.id: _number(row.load) for row in rows}
    if not rows:
        return loads
    observations = db.query(ActivityObservation.activity_id, ActivityObservation.payload_json).join(
        Activity, Activity.id == ActivityObservation.activity_id,
    ).filter(
        Activity.start_time >= start_utc, Activity.start_time < end_utc,
        Activity.start_time <= now, ActivityObservation.source_key.like('intervals:%'),
    ).order_by(ActivityObservation.id.asc()).all()
    for identifier, raw in observations:
        if identifier not in loads:
            continue
        try:
            loads[identifier] = _number(json.loads(raw).get('load'))
        except (ValueError, TypeError, AttributeError):
            loads[identifier] = None
    return loads


def _period(start, end, cutoff, partial):
    return {
        'start': str(start), 'end': str(end), 'days': PERIOD_DAYS,
        'includes_partial_day': partial,
        'cutoff_utc': cutoff.isoformat() + 'Z',
    }


def _day_rows(rows):
    result = {}
    for row in rows:
        result.setdefault(activity_day(row.start_time), []).append(row)
    return result


def _continuity(rows, previous, start):
    by_day = _day_rows(rows)
    days = []
    longest = current_gap = 0
    active_blocks = set()
    for index in range(PERIOD_DAYS):
        day = start + timedelta(days=index)
        records = by_day.get(day, [])
        minutes, coverage = _observed([_number(row.duration_seconds) for row in records], len(records), 1 / 60)
        if records:
            current_gap = 0
            active_blocks.add(index // 7)
        else:
            current_gap += 1
            longest = max(longest, current_gap)
        days.append({'date': str(day), 'activities': len(records), 'minutes': minutes,
                     'has_training': bool(records), 'known_duration_activities': coverage['known']})
    active = len(by_day)
    previous_active = len(_day_rows(previous))
    return {
        'active_days': active, 'previous_active_days': previous_active,
        'active_weeks': len(active_blocks), 'total_weeks': 4,
        'longest_gap_days': longest, 'current_gap_days': current_gap,
        'days': days,
        'summary': f'{active} giorni con attività registrate su 28; {len(active_blocks)} settimane su 4 con almeno una registrazione.',
        'gap_note': 'I giorni senza registrazioni possono essere riposo oppure attività non importate. Non sono sedute saltate accertate.',
    }


def _volume(current, previous, quality):
    comparable = (current['coverage']['minutes']['complete'] and previous['coverage']['minutes']['complete']
                  and current['minutes'] is not None and previous['minutes'] is not None
                  and not quality['current_possible_duplicates'] and not quality['previous_possible_duplicates']
                  and not quality['review_truncated'])
    change = _round(current['minutes'] - previous['minutes'], 2) if comparable else None
    percent = _round(change / previous['minutes'] * 100) if comparable and previous['minutes'] > 0 else None
    if not current['activities'] and not previous['activities']:
        summary = 'Nessuna attività registrata nei due periodi. Importa lo storico per leggere le tendenze.'
    elif quality['current_possible_duplicates'] or quality['previous_possible_duplicates'] or quality['review_truncated']:
        summary = 'Possibili duplicati nello storico: i totali conservano tutte le attività, mentre le variazioni attendono la verifica dei confronti.'
    elif not comparable:
        summary = 'La durata manca in alcune attività: il volume mostra i minuti noti e la variazione percentuale resta non disponibile.'
    elif previous['minutes'] == 0:
        summary = f"{current['minutes']:g} minuti registrati; nessun minuto nel periodo precedente. La variazione percentuale non è definita."
    else:
        suffix = f' ({percent:+g}%)' if percent is not None else ''
        summary = f"{current['minutes']:g} minuti registrati contro {previous['minutes']:g} nei 28 giorni precedenti{suffix}."
    return {'current': current, 'previous': previous, 'minutes_change': change,
            'minutes_change_pct': percent, 'comparable': comparable, 'summary': summary}


def _sports(rows, previous, loads, volume):
    result = []
    def known_seconds(selected):
        try:
            value = math.fsum(value for row in selected if (value := _number(row.duration_seconds)) is not None)
        except OverflowError:
            return None
        return value if math.isfinite(value) else None

    current_seconds = known_seconds(rows)
    previous_seconds = known_seconds(previous)
    for key, label in SPORT_LABELS.items():
        current_rows = [row for row in rows if _sport(row.sport) == key]
        previous_rows = [row for row in previous if _sport(row.sport) == key]
        if not current_rows and not previous_rows:
            continue
        current = _totals(current_rows, loads)
        prior = _totals(previous_rows, loads)

        def share(part, whole):
            if part is None or whole is None or whole <= 0:
                return None
            return _round(part / whole * 100)

        current_share = share(known_seconds(current_rows) if current['minutes'] is not None else None, current_seconds)
        previous_share = share(known_seconds(previous_rows) if prior['minutes'] is not None else None, previous_seconds)
        share_change = (_round(current_share - previous_share) if volume['comparable']
                        and current_share is not None and previous_share is not None else None)
        summary = (f"{current['minutes']:g} minuti registrati" if current['minutes'] is not None else 'Durata non disponibile')
        if current_share is not None:
            summary += f' · {current_share:g}% dei minuti noti'
        if share_change is not None:
            summary += f' · {share_change:+g} punti percentuali sul periodo precedente'
        result.append({
            'key': SPORT_KEYS[key], 'label': label, 'activities': current['activities'], 'previous_activities': prior['activities'],
            'current_minutes': current['minutes'], 'previous_minutes': prior['minutes'],
            'current_share_pct': current_share, 'previous_share_pct': previous_share,
            'share_change_pp': share_change, 'coverage': current['coverage']['minutes'],
            'previous_coverage': prior['coverage']['minutes'], 'summary': summary + '.',
        })
    return result


def training_insights(db, as_of: date | None = None, *, now: datetime | None = None):
    now = as_utc_naive(now if now is not None else utc_now_naive())
    today = athlete_today(now)
    day = as_of or today
    if not isinstance(day, date) or isinstance(day, datetime) or day < MIN_AS_OF or day > today:
        raise ValueError('Scegli una data dal 1 gennaio 1970 a oggi.')
    start = day - timedelta(days=PERIOD_DAYS - 1)
    previous_start = start - timedelta(days=PERIOD_DAYS)
    previous_end = start - timedelta(days=1)
    partial = day == today
    end_utc = day_start_utc(day + timedelta(days=1))
    start_utc = day_start_utc(previous_start)
    cutoff = now if partial else end_utc
    if partial:
        wall_clock = now.replace(tzinfo=timezone.utc).astimezone(ATHLETE_TZ).timetz().replace(tzinfo=None)
        prior_cutoff = local_stamp_to_utc_naive(datetime.combine(previous_end, wall_clock))
    else:
        prior_cutoff = day_start_utc(start)
    all_rows = db.query(Activity).filter(
        Activity.start_time >= start_utc, Activity.start_time < end_utc, Activity.start_time <= now,
    ).order_by(Activity.start_time.asc(), Activity.id.asc()).all()
    rows = [row for row in all_rows if start <= activity_day(row.start_time) <= day]
    previous = [row for row in all_rows if previous_start <= activity_day(row.start_time) <= previous_end
                and (as_utc_naive(row.start_time) <= prior_cutoff if partial
                     else as_utc_naive(row.start_time) < prior_cutoff)]
    loads = _recorded_loads(db, all_rows, start_utc, end_utc, now)
    review = exact_duplicate_review(db, rows + previous)
    current_duplicates = exact_duplicate_counts(review, [row.id for row in rows])['unresolved_pairs']
    previous_duplicates = exact_duplicate_counts(review, [row.id for row in previous])['unresolved_pairs']
    quality = {
        'current_possible_duplicates': current_duplicates, 'previous_possible_duplicates': previous_duplicates,
        'review_truncated': review['decisions_truncated'],
        'summary': ('Possibili registrazioni duplicate nei periodi confrontati. Verifica lo storico prima di leggere le variazioni; nessuna attività è stata esclusa.'
                    if current_duplicates or previous_duplicates or review['decisions_truncated']
                    else 'Nessuna corrispondenza esatta non risolta nei due periodi. Questo controllo non esclude duplicati con valori diversi.'),
    }
    volume = _volume(_totals(rows, loads), _totals(previous, loads), quality)
    weeks = []
    for index in range(8):
        week_start = previous_start + timedelta(days=7 * index)
        week_end = week_start + timedelta(days=6)
        period_rows = previous if index < 4 else rows
        selected = [row for row in period_rows if week_start <= activity_day(row.start_time) <= week_end]
        totals = _totals(selected, loads)
        weeks.append({
            'start': str(week_start), 'end': str(week_end), 'days': 7,
            'period': 'previous' if index < 4 else 'current',
            'partial_day': partial and index in (3, 7),
            **totals, 'load_coverage_pct': totals['coverage']['load']['percent'],
        })
    sources_current = Counter(row.source for row in rows)
    sources_previous = Counter(row.source for row in previous)
    return {
        'version': VERSION, 'as_of': str(day), 'timezone': ATHLETE_TIMEZONE,
        'generated_at': now.isoformat() + 'Z',
        'status': 'available' if rows or previous else 'empty',
        'period': _period(start, day, cutoff, partial),
        'comparison_period': _period(previous_start, previous_end, prior_cutoff, partial),
        'continuity': _continuity(rows, previous, start), 'volume': volume,
        'sports': _sports(rows, previous, loads, volume), 'weeks': weeks,
        'coverage': {'current': volume['current']['coverage'], 'previous': volume['previous']['coverage']},
        'quality': quality,
        'methodology': [
            'Due periodi consecutivi di 28 giorni, sugli stessi giorni della settimana. Le date sono inclusive nel calendario Europe/Rome.',
            ('Oggi è in corso: anche l’ultimo giorno del confronto termina alla stessa ora locale. Le due barre settimanali corrispondenti sono parziali.'
             if partial else 'Entrambi i periodi comprendono giornate concluse.'),
            'Le otto barre sono blocchi consecutivi di sette giorni: quattro precedenti e quattro attuali. Non sono settimane ISO.',
            'Un giorno con almeno un’attività registrata conta una sola volta nella continuità. Una settimana è un blocco di sette giorni con almeno una registrazione.',
            'I totali sommano solo valori noti e non negativi. N/D significa dato mancante; uno zero registrato rimane zero. Le quote delle discipline usano solo i minuti noti.',
            'Il carico è quello registrato nell’attività; la fonte Intervals prevale, anche quando segnala un valore mancante. Non si calcola un TSS da FTP predefinite.',
            'Le variazioni di volume e quota richiedono durate complete in entrambi i periodi. Un riferimento di zero non produce una percentuale di variazione.',
            'Corrispondenze esatte non risolte sospendono le variazioni: le attività restano nei totali finché non scegli se siano distinte o duplicate.',
            'La copertura misura i campi presenti nelle attività importate, non la completezza degli allenamenti realmente svolti. Nessun obiettivo ottimale o giudizio fisiologico è dedotto.',
        ],
        'provenance': {
            'scope': 'registered_activities_only', 'recorded_load_only': True,
            'future_activities_excluded': True, 'external_calls': False,
            'sources': [{'source': source, 'current_activities': sources_current[source],
                         'previous_activities': sources_previous[source]}
                        for source in sorted(set(sources_current) | set(sources_previous))],
        },
    }
