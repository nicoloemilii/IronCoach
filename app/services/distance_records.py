"""Source-bound elapsed distance records. Reads only validate derived caches.

Running uses explicit time/distance samples. Pool results use stored FIT Length
observations; the JSON streams integration cannot supply explicit pool lengths.
"""
from datetime import datetime, timedelta
from fractions import Fraction
import json
import math
import re

from sqlalchemy import func

from app.db.effort_models import DistanceAnalysis
from app.db.models import Activity
from app.services import effort_records as power
from app.services.activity_effort import number, parse_streams
from app.services.athlete_clock import (activity_day, as_utc_naive, athlete_today,
    day_start_utc, utc_now_naive, ATHLETE_TIMEZONE)
from app.services.distance_efforts import best_distance_efforts, best_pool_efforts, DISTANCES, POOL_METERS, POOL_YARDS
from app.services.fit_observations import apply_recording_quality
from app.services.training_load import sport_key

VERSIONS = {'running_distance': 'raw-distance-effort-1', 'pool_distance': 'pool-distance-effort-2'}
POOL_LENGTHS = (25., 50., 22.86, 45.72)
STROKES = ('freestyle', 'backstroke', 'breaststroke', 'butterfly', 'medley', 'unknown')


def metric_for(activity):
    return {'run': 'running_distance', 'swim': 'pool_distance'}.get(sport_key(activity.sport))


def source_snapshots(db, activities):
    activities = list(activities)
    running = [row for row in activities if metric_for(row) != 'pool_distance']
    swimming = [row for row in activities if metric_for(row) == 'pool_distance']
    result = power.source_snapshots(db, running) if running else {}
    # Explicit FIT identity wins over an unrelated provider summary. Multiple
    # non-canonical FIT observations remain ambiguous, even with equal totals.
    if swimming:
        result.update(power.source_snapshots(db, swimming, family='fit'))
    return result


def source_snapshot(db, activity):
    return source_snapshots(db, [activity])[activity.id]


def _seal(payload):
    payload['digest'] = power._json_hash(payload)
    return payload


def _empty(snapshot, now, code):
    metric = metric_for(snapshot['activity'])
    return dict(fingerprint=snapshot['fingerprint'], algorithm_version=VERSIONS.get(metric, 'unsupported'),
        status='unavailable', payload_json=_seal(dict(metric=metric, curve=None, input_partial=True,
        has_data=False, stream_quality=None, window=None)), source_identity=snapshot['source_identity'],
        source_hash=snapshot['source_hash'], source_fetched_at=None, analyzed_at=now, cutoff_utc=now,
        error_code=code, stop_batch=False, provider_status=None)


def pool_evidence(snapshot, now):
    """Return bounded, cutoff-safe Length evidence for scan and activity detail."""
    payload = snapshot['payload']; evidence = payload.get('pool_lengths')
    if not isinstance(evidence, dict):
        return None, 0
    evidence = dict(evidence)
    rows = evidence.get('rows')
    if not isinstance(rows, list):
        return evidence, 0
    limit = max(0., (as_utc_naive(now) - as_utc_naive(snapshot['activity'].start_time)).total_seconds())
    elapsed = number(payload.get('elapsed_time'))
    if elapsed is not None:
        limit = min(limit, elapsed)
    clipped = 0; bounded = []
    for row in rows[:20_001]:
        end = number(row.get('end_s')) if isinstance(row, dict) else None
        if end is not None and end > limit:
            bounded.append(None); clipped += 1
        else:
            bounded.append(row)
    evidence['rows'] = bounded
    evidence['truncated'] = bool(evidence.get('truncated') or len(rows) > 20_000)
    return evidence, clipped


def analyze_snapshot(snapshot, *, now, mode='stored', refresh=False, context=None, power_result=None):
    now = as_utc_naive(now); activity = snapshot['activity']; metric = metric_for(activity)
    if mode not in ('stored', 'intervals'):
        raise ValueError('Modalità di analisi non valida.')
    if metric is None:
        return _empty(snapshot, now, 'unsupported_sport')
    if snapshot['resolution'] in ('ambiguous_identity', 'observation_limit'):
        return _empty(snapshot, now, snapshot['resolution'])
    if not isinstance(activity.start_time, datetime) or as_utc_naive(activity.start_time) > now:
        return _empty(snapshot, now, 'future_or_invalid_activity')
    if metric == 'pool_distance':
        if snapshot['source_issue']:
            return _empty(snapshot, now, snapshot['source_issue'])
        evidence, clipped = pool_evidence(snapshot, now)
        curve = best_pool_efforts(evidence, activity.sport)
        curve['source'] = 'stored_fit_lengths'
        coverage = curve['coverage']
        has_data = coverage['eligible_lengths'] > 0
        partial = bool(clipped or curve['status'] in ('unavailable', 'not_applicable')
            or coverage['invalid_lengths'] or coverage['boundary_breaks'] or coverage['paused_lengths'])
        quality = dict(clipped_timestamp_count=clipped)
        window = None; fetched = False; source_hash = snapshot['source_hash']
        raw_present = isinstance(evidence, dict) and bool(evidence.get('rows'))
    else:
        shared = context if context and context.get('fingerprint') == snapshot['fingerprint'] else None
        if shared is None and power_result and power_result.get('error_code') and mode == 'intervals':
            code = power_result['error_code']
            if str(code).startswith('provider_') or code in ('invalid_provider_payload', 'missing_provider_identity'):
                result = _empty(snapshot, now, code)
                result.update(stop_batch=power_result.get('stop_batch', False), provider_status=power_result.get('provider_status'))
                return result
        if shared is None:
            shared = {}
            outcome = power.analyze_snapshot(snapshot, now=now, mode=mode, refresh=refresh, context=shared)
            if not shared:
                result = _empty(snapshot, now, outcome.get('error_code') or 'missing_distance')
                result.update(stop_batch=outcome.get('stop_batch', False), provider_status=outcome.get('provider_status'))
                return result
        parsed = shared['parsed']; fetched = shared['fetched']; source_hash = shared['source_hash']
        curve = best_distance_efforts(parsed, activity.sport); curve['source'] = shared['source']
        has_data = any(value is not None for value in parsed.channels.get('distance', []))
        quality = parsed.quality; window = parsed.window
        percentage = curve['coverage']['coverage_pct']
        partial = bool(quality['truncated'] or quality['timeline_ambiguous'] or quality['clipped_timestamp_count']
                       or percentage is None or percentage < 100)
        raw_present = bool(parsed.times) or bool(parsed.channels)
    status = ('available' if curve['status'] == 'available' else 'insufficient_data' if curve['status'] == 'insufficient_data'
              else 'missing_streams' if not raw_present else 'no_lengths' if metric == 'pool_distance' and curve['status'] == 'not_applicable'
              else 'no_distance' if curve['status'] == 'not_applicable' else 'unavailable')
    payload = _seal(dict(metric=metric, curve=curve, input_partial=partial, has_data=has_data,
                         stream_quality=quality, window=window))
    return dict(fingerprint=snapshot['fingerprint'], algorithm_version=VERSIONS[metric], status=status,
        payload_json=payload, source_identity=snapshot['source_identity'], source_hash=source_hash,
        source_fetched_at=now if fetched else None, analyzed_at=now, cutoff_utc=now,
        error_code=None, stop_batch=False, provider_status=None)


def _rows(group, unit):
    values = POOL_YARDS if unit == 'yd' else POOL_METERS
    return [(float(Fraction(value) * (Fraction('0.9144') if unit == 'yd' else 1)), value) for value in values]


def _validated_cache(row, snapshot, now):
    metric = metric_for(snapshot['activity']); now = as_utc_naive(now)
    if metric not in VERSIONS or row.fingerprint != snapshot['fingerprint'] or row.algorithm_version != VERSIONS[metric]:
        return None
    analyzed = power._cache_stamp(row.analyzed_at); cutoff = power._cache_stamp(row.cutoff_utc)
    fetched = power._cache_stamp(row.source_fetched_at)
    if (analyzed is None or cutoff is None or analyzed > now or cutoff > analyzed or cutoff > now
            or row.source_identity != snapshot['source_identity']
            or (row.source_fetched_at is not None and fetched is None)
            or (fetched is not None and (fetched > analyzed or fetched > now))):
        return None
    if not isinstance(row.payload_json, str) or len(row.payload_json) > power.MAX_CACHE_CHARACTERS:
        return None
    try:
        payload = json.loads(row.payload_json)
        if not isinstance(payload, dict):
            return None
        digest = payload.pop('digest', None)
        if digest != power._json_hash(payload):
            return None
        payload['digest'] = digest
    except (ValueError, TypeError, OverflowError, RecursionError):
        return None
    if (payload.get('metric') != metric or type(payload.get('input_partial')) is not bool
            or type(payload.get('has_data')) is not bool
            or row.status not in ('available', 'insufficient_data', 'missing_streams', 'no_distance', 'no_lengths', 'unavailable')):
        return None
    curve = payload.get('curve')
    if curve is None:
        return payload if (row.status == 'unavailable' and row.error_code and payload['input_partial']
                           and not payload['has_data'] and row.source_hash == snapshot['source_hash'] and fetched is None) else None
    if (not isinstance(curve, dict) or curve.get('unit') != 's' or not isinstance(curve.get('method'), dict)
            or curve['method'].get('version') != VERSIONS[metric]):
        return None
    source = curve.get('source')
    if source in ('stored_observation_streams', 'stored_fit_lengths'):
        if fetched is not None or row.source_hash != snapshot['source_hash']:
            return None
    elif source == 'intervals_remote_streams' and metric == 'running_distance':
        if fetched is None or not isinstance(row.source_hash, str) or not re.fullmatch('[0-9a-f]{64}', row.source_hash):
            return None
    else:
        return None
    quality = payload.get('stream_quality')
    if not isinstance(quality, dict) or type(quality.get('clipped_timestamp_count')) is not int or quality['clipped_timestamp_count'] < 0:
        return None
    try:
        limit = (cutoff - as_utc_naive(snapshot['activity'].start_time)).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None
    if limit < 0:
        return None
    if metric == 'running_distance':
        window = payload.get('window'); coverage = curve.get('coverage')
        if (curve.get('metric') != 'distance' or curve.get('scope') != 'activity_valid_segments'
                or curve['method'].get('pauses') != 'included' or not isinstance(window, dict)
                or window.get('start_s') != 0 or number(window.get('end_s')) is None or window['end_s'] > limit
                or not isinstance(coverage, dict)):
            return None
        limit = window['end_s']; groups = [curve]; specifications = [(float(value), value) for value in DISTANCES]
        if quality.get('truncated') or quality.get('timeline_ambiguous'):
            if row.status == 'available':
                return None
    else:
        if (curve.get('metric') != 'pool_distance' or curve.get('scope') != 'activity_explicit_lengths'
                or curve['method'].get('pauses') != 'split' or source != 'stored_fit_lengths'
                or not isinstance(curve.get('groups'), list) or len(curve['groups']) > 32):
            return None
        groups = curve['groups']; seen = set()
        for group in groups:
            if not isinstance(group, dict) or number(group.get('pool_length_m')) in (None, 0) or group.get('distance_unit') not in ('m', 'yd'):
                return None
            key = (group['pool_length_m'], group.get('distance_unit'), group.get('stroke'))
            if not isinstance(key[2], str) or key in seen:
                return None
            seen.add(key)
    available = 0
    for group in groups:
        values = group.get('distances')
        if metric == 'pool_distance':
            specifications = _rows(group, group['distance_unit'])
        if not isinstance(values, list) or len(values) != len(specifications):
            return None
        for value, (distance, declared) in zip(values, specifications):
            if (not isinstance(value, dict) or value.get('distance_m') != distance or value.get('distance_value') != declared
                    or value.get('distance_unit') != ('m' if metric == 'running_distance' else group['distance_unit'])):
                return None
            if value.get('status') != 'available':
                if value.get('status') not in ('insufficient_distance', 'numeric_limit', 'incompatible_pool_length') or any(value.get(key) is not None for key in ('elapsed_seconds', 'start_s', 'end_s')):
                    return None
                continue
            elapsed, start, end = [number(value.get(key)) for key in ('elapsed_seconds', 'start_s', 'end_s')]
            if (None in (elapsed, start, end) or elapsed <= 0 or end <= start or end > limit + 1e-6
                    or not math.isclose(end - start, elapsed, rel_tol=1e-12, abs_tol=1e-6)
                    or value.get('coverage_pct') != 100):
                return None
            if metric == 'pool_distance':
                count = value.get('lengths_count'); first = value.get('first_length_index'); last = value.get('last_length_index')
                if (type(count) is not int or count <= 0 or type(first) is not int or type(last) is not int
                        or first < 0 or last - first + 1 != count or not math.isclose(count * group['pool_length_m'], distance, abs_tol=1e-8)):
                    return None
            available += 1
    if (row.status == 'available') != bool(available) or bool(available) != (curve.get('status') == 'available'):
        return None
    if available and not payload['has_data']:
        return None
    return payload


def cache_valid(row, snapshot, now):
    return row is not None and _validated_cache(row, snapshot, now) is not None


def records(db, *, metric='running_distance', period='all_time', pool_length_m=25., stroke='freestyle', now=None):
    if metric not in VERSIONS or period not in power.PERIODS or pool_length_m not in POOL_LENGTHS or stroke not in STROKES:
        raise ValueError('Filtri dei risultati non validi.')
    now = as_utc_naive(now or utc_now_naive()); today = athlete_today(now)
    length = power.PERIODS[period]; start = today - timedelta(days=min(length - 1, today.toordinal() - 1)) if length else None
    sport = 'running' if metric == 'running_distance' else 'swimming'; pool = metric == 'pool_distance'
    connection = db.connection().connection.driver_connection
    connection.create_function('iron_effort_category', 1, power._category, deterministic=True)
    if not connection.in_transaction:
        connection.execute('BEGIN')
    query = db.query(Activity).filter(Activity.start_time <= now, func.iron_effort_category(Activity.sport) == sport)
    if start is not None:
        query = query.filter(Activity.start_time >= day_start_utc(start))
    with_key = 'with_lengths' if pool else 'with_distance'; missing_key = 'no_lengths' if pool else 'no_distance'
    coverage = dict(total=0, indexed=0, missing_streams=0, unavailable=0, stale=0, pending=0,
                    complete_index=False, input_partial=False, **{with_key: 0, missing_key: 0})
    unit = 'yd' if pool and pool_length_m in (22.86, 45.72) else 'm'
    targets = _rows(None, unit) if pool else [(float(value), value) for value in DISTANCES]
    rows = {distance: dict(distance_meters=distance, distance_value=value, distance_unit=unit,
        status='insufficient_data', elapsed_seconds=None, start_s=None, end_s=None, activity=None, analysis=None,
        candidate_coverage=dict(available=0, total=0)) for distance, value in targets}
    winners = {}; first_day = None

    def consume(batch):
        nonlocal first_day
        snapshots = source_snapshots(db, batch)
        cached = {row.activity_id: row for row in db.query(DistanceAnalysis).filter(DistanceAnalysis.activity_id.in_([item.id for item in batch]))}
        for activity in batch:
            coverage['total'] += 1; day = activity_day(activity.start_time)
            first_day = min(first_day, day) if first_day is not None else day
            cache = cached.get(activity.id)
            if cache is None:
                coverage['pending'] += 1; coverage['input_partial'] = True; continue
            payload = _validated_cache(cache, snapshots[activity.id], now)
            if payload is None:
                coverage['stale'] += 1; coverage['input_partial'] = True; continue
            coverage['indexed'] += 1; coverage['input_partial'] |= payload['input_partial']
            curve = payload.get('curve'); group = curve
            if pool:
                group = next((value for value in (curve or {}).get('groups', [])
                    if value['pool_length_m'] == pool_length_m and value['distance_unit'] == unit and value['stroke'] == stroke), None)
            coverage[with_key] += int(payload['has_data'] and (not pool or group is not None))
            if cache.status == 'missing_streams':
                coverage['missing_streams'] += 1
            elif cache.status == missing_key or pool and group is None and cache.status != 'unavailable':
                coverage[missing_key] += 1
            elif cache.status in ('unavailable', 'insufficient_data'):
                coverage['unavailable'] += 1
            for candidate in (group or {}).get('distances', []):
                if candidate['status'] != 'available':
                    continue
                distance = candidate['distance_m']; row = rows[distance]
                row['candidate_coverage']['available'] += 1
                key = (candidate['elapsed_seconds'], as_utc_naive(activity.start_time), candidate['start_s'], activity.id)
                if distance in winners and winners[distance] <= key:
                    continue
                winners[distance] = key
                row.update(status='available', elapsed_seconds=candidate['elapsed_seconds'], start_s=candidate['start_s'], end_s=candidate['end_s'],
                    activity=dict(id=activity.id, name=activity.name, date=str(day), sport=activity.sport, source=activity.source),
                    analysis=dict(source=curve.get('source'), analyzed_at=power._utc(cache.analyzed_at), fetched_at=power._utc(cache.source_fetched_at),
                                  source_identity=cache.source_identity, source_hash=cache.source_hash))

    batch = []
    for activity in query.order_by(Activity.id).yield_per(power.BATCH_SIZE):
        batch.append(activity)
        if len(batch) == power.BATCH_SIZE:
            consume(batch); batch = []
    if batch:
        consume(batch)
    coverage['complete_index'] = coverage['stale'] == 0 and coverage['pending'] == 0
    for row in rows.values():
        row['candidate_coverage']['total'] = coverage['total']
    return dict(metric=metric, sport=sport, pool_length_m=pool_length_m if pool else None, stroke=stroke if pool else None,
        period=dict(key=period, start=str(start or first_day) if start or first_day else None, end=str(today), timezone=ATHLETE_TIMEZONE),
        as_of_utc=power._utc(now), status='empty' if coverage['total'] == 0 else 'available' if winners else 'insufficient_data',
        coverage=coverage, records=list(rows.values()), limits=[
            'Migliori segmenti registrati tra le attività analizzate, non primati ufficiali di gara.',
            'Le pause interne alle serie di corsa sono incluse. In piscina riposi, pause e confini non coincidenti interrompono le serie.'
            if pool else 'Tempo trascorso con pause interne comprese; vuoti e azzeramenti interrompono i segmenti.',
            'Le vasche richiedono messaggi Length FIT già importati. Il recupero delle serie Intervals non fornisce questi messaggi.'
            if pool else 'I confini sono interpolati sulla distanza cumulativa osservata; nessuna distanza deriva da medie o GPS.'])
