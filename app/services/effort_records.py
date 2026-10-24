"""Source-bound historical power curves; GET reads never analyze or fetch.

Only derived curves are cacheable. Original activity/observation rows are never
modified and remotely fetched raw samples are never returned for persistence.
"""
from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import json
import math
import re
from types import SimpleNamespace

from sqlalchemy import func

from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.integrations import intervals
from app.services.activity_effort import parse_streams, number
from app.services.athlete_clock import (activity_day, as_utc_naive, athlete_today,
    day_start_utc, utc_now_naive, ATHLETE_TIMEZONE)
from app.services.execution_evidence import safe_object
from app.services.power_duration import best_power_efforts, DURATIONS
from app.services.training_load import sport_key

ALGORITHM_VERSION = VERSION = 'raw-power-duration-1'
BATCH_SIZE = 500
MAX_OBSERVATIONS = 1000
MAX_SOURCE_CHARACTERS = 16_777_216
MAX_CACHE_CHARACTERS = 262_144
PERIODS = {'all_time': None, 'last_90_days': 90, 'last_28_days': 28}
SPORTS = {'cycling', 'running', 'swimming', 'strength', 'other'}
_SPORTS = {'bike': 'cycling', 'run': 'running', 'swim': 'swimming', 'strength': 'strength'}


class _PayloadLimit(ValueError):
    pass


def _category(sport):
    return _SPORTS.get(sport_key(sport), 'other')


def provider_available():
    """Check the current workspace connection without exposing its secret."""
    try:
        config = intervals.intervals_credentials.credentials()
        return isinstance(config, dict) and bool(config.get('api_key'))
    except (RuntimeError, OSError, ValueError, TypeError):
        return False


def _utc(stamp):
    return as_utc_naive(stamp).isoformat() + 'Z' if isinstance(stamp, datetime) else None


def _text_hash(value):
    digest = hashlib.sha256()
    for offset in range(0, len(value), 65536):
        digest.update(value[offset:offset + 65536].encode('utf-8', errors='surrogatepass'))
    return digest.hexdigest()


def _json_hash(value, *, maximum_bytes=None):
    digest = hashlib.sha256(); total = 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    for part in encoder.iterencode(value):
        encoded = part.encode('ascii'); total += len(encoded)
        if maximum_bytes is not None and total > maximum_bytes:
            raise _PayloadLimit()
        digest.update(encoded)
    return digest.hexdigest()


def _fingerprint_value(value):
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, float) and not math.isfinite(value):
        return '<invalid-number:' + str(value) + '>'
    return value


def _decode_source(raw):
    if (not isinstance(raw, str) or len(raw) > MAX_SOURCE_CHARACTERS
            or len(raw.encode('utf-8', errors='surrogatepass')) > MAX_SOURCE_CHARACTERS):
        return None, 'source_payload_limit'
    try:
        content = json.loads(raw)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return None, 'invalid_source_payload'
    return (content, None) if isinstance(content, dict) else (None, 'invalid_source_payload')


def _selection(activity, rows, *, family=None):
    """Use identity metadata only; equal loads never prove equal recordings."""
    count = len(rows)
    if count > MAX_OBSERVATIONS:
        return None, 'observation_limit'
    if family is not None:
        provider = [row for row in rows if isinstance(row.source_key, str) and row.source_key.startswith(family + ':')]
        if activity.source == family and isinstance(activity.source_id, str):
            key = family + ':' + activity.source_id.removeprefix(family + ':')
            canonical = next((row for row in provider if row.source_key == key), None)
            if canonical is not None:
                return canonical, 'canonical_identity'
        if len(provider) == 1:
            return provider[0], 'single_' + family
        return (None, 'ambiguous_identity') if provider else (None, 'no_observation')
    provider = [row for row in rows if isinstance(row.source_key, str) and row.source_key.startswith('intervals:')]
    if activity.source == 'intervals' and isinstance(activity.source_id, str):
        key = 'intervals:' + activity.source_id.removeprefix('intervals:')
        canonical = next((row for row in provider if row.source_key == key), None)
        if canonical is not None:
            return canonical, 'canonical_identity'
    if len(provider) == 1:
        return provider[0], 'single_intervals'
    if len(provider) > 1:
        return None, 'ambiguous_identity'
    return (rows[0], 'latest_non_intervals') if rows else (None, 'no_observation')


def source_snapshots(db, activities, *, family=None):
    activities = list(activities)
    result = {}
    for offset in range(0, len(activities), BATCH_SIZE):
        batch = activities[offset:offset + BATCH_SIZE]
        ids = [row.id for row in batch]
        ranked = db.query(ActivityObservation.id.label('id'), ActivityObservation.activity_id.label('activity_id'),
            ActivityObservation.source_key.label('source_key'),
            func.row_number().over(partition_by=ActivityObservation.activity_id,
                                   order_by=ActivityObservation.id.desc()).label('position')).filter(
            ActivityObservation.activity_id.in_(ids)).subquery()
        metadata = defaultdict(list)
        for row in db.query(ranked.c.id, ranked.c.activity_id, ranked.c.source_key).filter(
                ranked.c.position <= MAX_OBSERVATIONS + 1).order_by(ranked.c.activity_id, ranked.c.id.desc()):
            metadata[row.activity_id].append(SimpleNamespace(id=row.id, source_key=row.source_key))
        selections = {}; selected_ids = []
        for activity in batch:
            selected, reason = _selection(activity, metadata[activity.id], family=family)
            selections[activity.id] = selected, reason
            if selected is not None:
                selected_ids.append(selected.id)
        raw_by_id = dict(db.query(ActivityObservation.id, ActivityObservation.payload_json).filter(
            ActivityObservation.id.in_(selected_ids))) if selected_ids else {}
        for activity in batch:
            selected, resolution = selections[activity.id]
            clone = SimpleNamespace(**{column.key: getattr(activity, column.key) for column in Activity.__table__.columns})
            raw = raw_by_id.get(selected.id) if selected is not None else None
            source_hash = _text_hash(raw) if isinstance(raw, str) else None
            payload, issue = _decode_source(raw) if selected is not None else ({}, None)
            identity = selected.source_key if selected is not None else (
                'intervals:' + activity.source_id.removeprefix('intervals:')
                if resolution == 'no_observation' and activity.source == 'intervals' and isinstance(activity.source_id, str) else None)
            if family is not None and selected is None:
                identity = None
            remote_id = identity.split(':', 1)[1] if isinstance(identity, str) and identity.startswith('intervals:') else None
            if remote_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', remote_id):
                remote_id = None
            state = dict(activity={key: _fingerprint_value(float(getattr(activity, key))
                if key == 'duration_seconds' and number(getattr(activity, key), nonnegative=False) is not None
                else getattr(activity, key)) for key in
                ('id', 'source', 'source_id', 'sport', 'start_time', 'duration_seconds')},
                selected=None if selected is None else dict(id=selected.id, key=selected.source_key, sha256=source_hash),
                resolution=resolution, observation_count=len(metadata[activity.id]),
                aliases=[(row.id, row.source_key) for row in metadata[activity.id]] if selected is None else None)
            if family is not None:
                state['selection_family'] = family
            result[activity.id] = dict(activity=clone, fingerprint=_json_hash(state), payload=payload or {},
                remote_id=remote_id, resolution=resolution, source_identity=identity, source_hash=source_hash,
                observation_count=len(metadata[activity.id]), source_issue=issue)
    return result


def source_snapshot(db, activity, *, family=None):
    return source_snapshots(db, [activity], family=family)[activity.id]


def _empty_analysis(snapshot, now, status, error_code=None):
    return dict(fingerprint=snapshot['fingerprint'], algorithm_version=ALGORITHM_VERSION,
        status=status, payload_json=dict(curve=None, stream_quality=None, window=None, input_partial=True,
                                        has_power=False, error_code=error_code),
        source_identity=snapshot['source_identity'], source_hash=snapshot['source_hash'], source_fetched_at=None,
        analyzed_at=now, cutoff_utc=now, error_code=error_code, stop_batch=False, provider_status=None)


def analyze_snapshot(snapshot, *, now, mode='stored', refresh=False, context=None):
    now = as_utc_naive(now)
    activity = snapshot['activity']
    if mode not in ('stored', 'intervals'):
        raise ValueError('Modalità di analisi non valida.')
    if snapshot['resolution'] in ('ambiguous_identity', 'observation_limit'):
        return _empty_analysis(snapshot, now, 'unavailable', snapshot['resolution'])
    if not isinstance(activity.start_time, datetime) or as_utc_naive(activity.start_time) > now:
        return _empty_analysis(snapshot, now, 'unavailable', 'future_or_invalid_activity')
    payload = snapshot['payload']; streams = payload.get('streams')
    raw_present = isinstance(streams, list) and bool(streams)
    required_kinds = ('time', 'distance') if sport_key(activity.sport) == 'run' else ('time', 'watts')
    required_present = raw_present and all(any(isinstance(row, dict) and row.get('type') == kind
        and isinstance(row.get('data'), list) and bool(row['data']) for row in streams) for kind in required_kinds)
    fetched = False; source_hash = snapshot['source_hash']
    source = 'stored_observation_streams'
    if mode == 'intervals' and (refresh or not required_present):
        if snapshot['remote_id'] is None:
            return _empty_analysis(snapshot, now, 'unavailable', 'missing_provider_identity')
        try:
            summary = intervals.get_activity_remote(snapshot['remote_id'])
            remote = intervals.get_activity_remote(snapshot['remote_id'], '/streams.json', {'types': 'time,watts,distance'})
        except intervals.IntervalsError as error:
            code = ('provider_rate_limited' if error.status == 429 else 'provider_auth' if error.status in (401, 403)
                    else 'provider_connection' if error.status == 409 else 'provider_unavailable')
            result = _empty_analysis(snapshot, now, 'unavailable', code)
            result.update(stop_batch=error.status in (401, 403, 409, 429), provider_status=error.status)
            return result
        except (RuntimeError, OSError, ValueError, TypeError):
            return _empty_analysis(snapshot, now, 'unavailable', 'provider_unavailable')
        if not isinstance(summary, dict) or not isinstance(remote, list):
            return _empty_analysis(snapshot, now, 'unavailable', 'invalid_provider_payload')
        # A derived hash and curve are persisted; never return original samples.
        try:
            source_hash = _json_hash(dict(elapsed_time=summary.get('elapsed_time'), streams=remote),
                                     maximum_bytes=MAX_SOURCE_CHARACTERS)
        except _PayloadLimit:
            return _empty_analysis(snapshot, now, 'unavailable', 'provider_payload_limit')
        except (ValueError, TypeError, OverflowError, RecursionError):
            return _empty_analysis(snapshot, now, 'unavailable', 'invalid_provider_payload')
        payload = {'elapsed_time': summary.get('elapsed_time')}; streams = remote
        raw_present = bool(streams); fetched = True; source = 'intervals_remote_streams'
    if snapshot['source_issue'] and not fetched:
        return _empty_analysis(snapshot, now, 'unavailable', snapshot['source_issue'])
    parsed = parse_streams(streams if isinstance(streams, list) else [],
        elapsed_seconds=number(payload.get('elapsed_time')),
        max_time_s=(now - as_utc_naive(activity.start_time)).total_seconds())
    if not fetched:
        from app.services.fit_observations import apply_recording_quality
        apply_recording_quality(parsed, payload)
    if context is not None:
        context.update(fingerprint=snapshot['fingerprint'], parsed=parsed, source=source,
                       source_hash=source_hash, fetched=fetched, payload=payload)
    curve = best_power_efforts(parsed); curve['source'] = source
    has_power = any(value is not None for value in parsed.channels.get('power', []))
    status = ('missing_streams' if not raw_present else 'no_power' if curve['status'] == 'not_applicable'
              else 'available' if curve['status'] == 'available' else 'insufficient_data'
              if curve['status'] == 'insufficient_data' else 'unavailable')
    partial = (parsed.quality['truncated'] or parsed.quality['timeline_ambiguous'] or parsed.quality['clipped_timestamp_count'] > 0
               or curve['coverage']['coverage_pct'] is None or curve['coverage']['coverage_pct'] < 100)
    return dict(fingerprint=snapshot['fingerprint'], algorithm_version=ALGORITHM_VERSION, status=status,
        payload_json=dict(curve=curve, stream_quality=parsed.quality, window=parsed.window, input_partial=bool(partial), has_power=has_power),
        source_identity=snapshot['source_identity'], source_hash=source_hash,
        source_fetched_at=now if fetched else None, analyzed_at=now, cutoff_utc=now,
        error_code=None, stop_batch=False, provider_status=None)


def _cache_stamp(value):
    if isinstance(value, datetime):
        return as_utc_naive(value)
    return None


def _validated_cache(row, snapshot, now):
    now = as_utc_naive(now)
    if row.fingerprint != snapshot['fingerprint'] or row.algorithm_version != ALGORITHM_VERSION:
        return None
    analyzed = _cache_stamp(row.analyzed_at); cutoff = _cache_stamp(row.cutoff_utc)
    fetched = _cache_stamp(row.source_fetched_at)
    if (analyzed is None or cutoff is None or analyzed > now or cutoff > now or cutoff > analyzed
            or (row.source_fetched_at is not None and fetched is None)
            or (fetched is not None and (fetched > analyzed or fetched > now))
            or row.source_identity != snapshot['source_identity']):
        return None
    if not isinstance(row.payload_json, str) or len(row.payload_json) > MAX_CACHE_CHARACTERS:
        return None
    payload = safe_object(row.payload_json)
    if payload is None or type(payload.get('input_partial')) is not bool or type(payload.get('has_power')) is not bool:
        return None
    if row.status not in ('available', 'insufficient_data', 'missing_streams', 'no_power', 'unavailable'):
        return None
    curve = payload.get('curve')
    if curve is None:
        return payload if (row.status == 'unavailable' and isinstance(row.error_code, str) and row.error_code
            and payload['input_partial'] and not payload['has_power']
            and row.source_hash == snapshot['source_hash'] and fetched is None) else None
    if (not isinstance(curve, dict) or curve.get('metric') != 'power' or curve.get('unit') != 'W'
            or curve.get('scope') != 'activity_valid_segments' or not isinstance(curve.get('method'), dict)
            or curve['method'].get('version') != ALGORITHM_VERSION):
        return None
    expected_method = {'weighting': 'trapezoidal', 'interpolation': 'linear',
                       'search': 'continuous_window', 'zero_power': 'included'}
    if any(curve['method'].get(key) != value for key, value in expected_method.items()):
        return None
    quality = payload.get('stream_quality')
    if (not isinstance(quality, dict) or type(quality.get('truncated')) is not bool
            or type(quality.get('timeline_ambiguous')) is not bool
            or type(quality.get('clipped_timestamp_count')) is not int or quality['clipped_timestamp_count'] < 0):
        return None
    source = curve.get('source')
    if source == 'stored_observation_streams':
        if row.source_hash != snapshot['source_hash'] or fetched is not None:
            return None
    elif source == 'intervals_remote_streams':
        if fetched is None or not isinstance(row.source_hash, str) or not re.fullmatch('[0-9a-f]{64}', row.source_hash):
            return None
    else:
        return None
    values = curve.get('durations'); window = payload.get('window')
    if not isinstance(values, list) or len(values) != len(DURATIONS) or not isinstance(window, dict):
        return None
    window_end = number(window.get('end_s')); coverage = curve.get('coverage')
    if window_end is None or window.get('start_s') != 0 or not isinstance(coverage, dict):
        return None
    seconds = number(coverage.get('covered_seconds')); window_seconds = number(coverage.get('window_seconds'))
    longest = number(coverage.get('longest_segment_seconds')); percentage = coverage.get('coverage_pct')
    segments = coverage.get('segments_count')
    if (seconds is None or window_seconds != window_end or longest is None or seconds > window_end + 1e-6
            or longest > seconds + 1e-6 or type(segments) is not int or segments < 0
            or (percentage is not None and (number(percentage) is None or percentage > 100))):
        return None
    if (percentage is not None and window_end > 0
            and not math.isclose(percentage, min(100., seconds / window_end * 100.), abs_tol=2e-6)):
        return None
    if not payload['input_partial'] and (percentage is None or percentage < 100
            or quality['truncated'] or quality['timeline_ambiguous'] or quality['clipped_timestamp_count'] > 0):
        return None
    try:
        end_stamp = as_utc_naive(snapshot['activity'].start_time) + timedelta(seconds=window_end)
    except (TypeError, ValueError, OverflowError):
        return None
    if end_stamp > now or end_stamp > cutoff:
        return None
    available = 0
    for duration, value in zip(DURATIONS, values):
        if not isinstance(value, dict) or type(value.get('duration_seconds')) is not int or value['duration_seconds'] != duration:
            return None
        if value.get('status') == 'available':
            fields = [number(value.get(key)) for key in ('mean_power_w', 'start_s', 'end_s', 'segment_start_s', 'segment_end_s', 'covered_seconds', 'coverage_pct')]
            if any(item is None for item in fields):
                return None
            mean, start, end, segment_start, segment_end, covered, coverage = fields
            tolerance = max(1e-7, duration * 1e-10)
            if (not math.isclose(end - start, duration, rel_tol=0., abs_tol=tolerance)
                    or start + tolerance < segment_start or end > segment_end + tolerance or end > window_end + tolerance
                    or covered != duration or coverage != 100 or longest + tolerance < duration
                    or segments == 0 or percentage is None
                    or quality['truncated'] or quality['timeline_ambiguous']):
                return None
            available += 1
        elif value.get('status') not in ('insufficient_duration', 'numeric_limit') or any(value.get(key) is not None for key in ('mean_power_w', 'start_s', 'end_s')):
            return None
    if (row.status == 'available') != bool(available):
        return None
    if (available and (curve.get('status') != 'available' or not payload['has_power'])
            or (row.status == 'insufficient_data' and curve.get('status') != 'insufficient_data')
            or (row.status in ('missing_streams', 'no_power') and curve.get('status') != 'not_applicable')
            or (row.status == 'unavailable' and curve.get('status') != 'unavailable')
            or (row.status in ('missing_streams', 'no_power') and payload['has_power'])):
        return None
    return payload


def cache_valid(row, snapshot, now):
    """Validate a derived cache without computing a curve or contacting a provider."""
    return row is not None and _validated_cache(row, snapshot, now) is not None


def records(db, *, period='all_time', sport='cycling', now=None):
    from app.db.effort_models import EffortAnalysis
    if period not in PERIODS or sport not in SPORTS:
        raise ValueError('Periodo o disciplina non validi.')
    now = as_utc_naive(now or utc_now_naive()); today = athlete_today(now)
    length = PERIODS[period]
    start = today - timedelta(days=min(length - 1, today.toordinal() - 1)) if length else None
    connection = db.connection().connection.driver_connection
    connection.create_function('iron_effort_category', 1, _category, deterministic=True)
    if not connection.in_transaction:
        connection.execute('BEGIN')
    query = db.query(Activity).filter(Activity.start_time <= now, func.iron_effort_category(Activity.sport) == sport)
    if start is not None:
        query = query.filter(Activity.start_time >= day_start_utc(start))
    coverage = dict(total=0, indexed=0, with_power=0, missing_streams=0, no_power=0, unavailable=0,
                    stale=0, pending=0, complete_index=False, input_partial=False)
    rows = {duration: dict(duration_seconds=duration, status='insufficient_data', mean_power_w=None, start_s=None,
        end_s=None, activity=None, analysis=None, candidate_coverage=dict(available=0, total=0)) for duration in DURATIONS}
    winners = {}; first_day = None

    def consume(batch):
        nonlocal first_day
        snapshots = source_snapshots(db, batch)
        cached = {row.activity_id: row for row in db.query(EffortAnalysis).filter(EffortAnalysis.activity_id.in_([item.id for item in batch]))}
        for activity in batch:
            coverage['total'] += 1
            day = activity_day(activity.start_time)
            first_day = min(first_day, day) if first_day is not None else day
            cache = cached.get(activity.id)
            if cache is None:
                coverage['pending'] += 1; coverage['input_partial'] = True
                continue
            payload = _validated_cache(cache, snapshots[activity.id], now)
            if payload is None:
                coverage['stale'] += 1; coverage['input_partial'] = True
                continue
            coverage['indexed'] += 1
            coverage['with_power'] += payload['has_power']
            coverage['input_partial'] = coverage['input_partial'] or payload['input_partial']
            if cache.status in ('missing_streams', 'no_power'):
                coverage[cache.status] += 1
            elif cache.status in ('unavailable', 'insufficient_data'):
                coverage['unavailable'] += 1
            curve = payload.get('curve')
            if not curve:
                continue
            for candidate in curve['durations']:
                if candidate['status'] != 'available':
                    continue
                duration = candidate['duration_seconds']; row = rows[duration]
                row['candidate_coverage']['available'] += 1
                key = (-candidate['mean_power_w'], as_utc_naive(activity.start_time), candidate['start_s'], activity.id)
                if duration in winners and winners[duration] <= key:
                    continue
                winners[duration] = key
                row.update(status='available', mean_power_w=candidate['mean_power_w'], start_s=candidate['start_s'], end_s=candidate['end_s'],
                    activity=dict(id=activity.id, name=activity.name, date=str(day), sport=activity.sport, source=activity.source),
                    analysis=dict(source=curve.get('source'), analyzed_at=_utc(cache.analyzed_at), fetched_at=_utc(cache.source_fetched_at),
                                  source_identity=cache.source_identity, source_hash=cache.source_hash))

    batch = []
    for activity in query.order_by(Activity.id).yield_per(BATCH_SIZE):
        batch.append(activity)
        if len(batch) == BATCH_SIZE:
            consume(batch); batch = []
    if batch:
        consume(batch)
    coverage['complete_index'] = coverage['stale'] == 0 and coverage['pending'] == 0
    for row in rows.values():
        row['candidate_coverage']['total'] = coverage['total']
    return dict(period=dict(key=period, start=str(start or first_day) if start or first_day else None, end=str(today), timezone=ATHLETE_TIMEZONE),
        sport=sport, as_of_utc=_utc(now), status='available' if winners else 'insufficient_data', coverage=coverage, records=list(rows.values()),
        limits=['Record tra le attività analizzate e i loro tratti continui validi disponibili; una copertura parziale non dimostra un record assoluto.',
                'Le discipline restano separate. Nessuna stima di FTP o valutazione fisiologica viene dedotta da queste medie.'])
