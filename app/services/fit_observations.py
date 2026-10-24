"""Bounded observations from decoded FIT messages, without invented samples.

Garmin defines summary spans by start_time + total_elapsed_time. Pool length
is always in metres; pool_length_unit is a display unit, including yards.
https://developer.garmin.com/fit/articles/cookbook/decoding_activity_files.html
https://developer.garmin.com/fit/articles/cookbook/encoding_activity_files.html
"""
from datetime import datetime, timezone
import json
import math

MAX_RECORDS = 250_000
MAX_LENGTHS = 20_000
MAX_PAYLOAD_BYTES = 16_000_000


def _number(value, *, signed=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and (signed or value >= 0) else None


def _stamp(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _span(row):
    start = _stamp(row.get('start_time'))
    elapsed = _number(row.get('total_elapsed_time'))
    if start is None or elapsed is None or not 0 < elapsed <= 172800:
        return None
    return start, elapsed


def _issue(container, issue):
    if issue not in container['issues']:
        container['issues'].append(issue)


def _streams(rows, start):
    kinds = {'distance': ('distance',), 'watts': ('power',), 'heartrate': ('heart_rate',),
             'cadence': ('cadence',), 'velocity_smooth': ('enhanced_speed', 'speed'),
             'altitude': ('enhanced_altitude', 'altitude'), 'temp': ('temperature',)}
    output = [{'type': 'time', 'data': [(_stamp(row.get('timestamp')) - start).total_seconds()
               if _stamp(row.get('timestamp')) else None for row in rows]}]
    for kind, keys in kinds.items():
        if not any(any(key in row for key in keys) for row in rows):
            continue
        values = []
        for row in rows:
            value = next((row[key] for key in keys if _number(row.get(key), signed=kind in ('altitude', 'temp')) is not None), None)
            values.append(_number(value, signed=kind in ('altitude', 'temp')))
        output.append({'type': kind, 'data': values})
    if any('position_lat' in row or 'position_long' in row for row in rows):
        coordinates = []
        for row in rows:
            lat = _number(row.get('position_lat'), signed=True)
            lon = _number(row.get('position_long'), signed=True)
            # The SDK leaves position fields in FIT semicircles.
            if lat is None or lon is None or not -2**30 <= lat <= 2**30 or not -2**31 <= lon < 2**31:
                coordinates.append(None)
            else:
                coordinates.append([lat * 180 / 2**31, lon * 180 / 2**31])
        output.append({'type': 'latlng', 'data': coordinates})
    return output if rows else []


def session_observations(messages):
    """Return one observation per session index, retaining source ordering.

    Multi-session attribution uses all sessions, including unsupported sports.
    Overlapping or absent bounds never cause a sample to be silently assigned.
    Limits apply to the entire uploaded file, not independently to 100 sessions.
    """
    sessions = messages.get('session_mesgs') or []
    if not isinstance(sessions, list) or len(sessions) > 100:
        raise ValueError('Numero di sessioni non valido.')
    sessions = [row if isinstance(row, dict) else {} for row in sessions]
    spans = [_span(row) for row in sessions]
    records = messages.get('record_mesgs') or []
    lengths = messages.get('length_mesgs') or []
    if not isinstance(records, list) or not isinstance(lengths, list):
        raise ValueError('Messaggi FIT non validi.')
    output = {}
    assigned = {index: [] for index in range(len(sessions))}
    for index, session in enumerate(sessions):
        pool = session.get('sport') == 'swimming' and session.get('sub_sport') == 'lap_swimming'
        pool_length = _number(session.get('pool_length'))
        output[index] = {
            'elapsed_time': spans[index][1] if spans[index] else None,
            'sub_sport': session.get('sub_sport') if isinstance(session.get('sub_sport'), str) else None,
            'streams': [],
            'fit_recording': {'schema_version': 1, 'total_file_record_count': len(records),
                'source_record_count': 0, 'preserved_record_count': 0,
                'source_count_complete': len(records) <= MAX_RECORDS,
                'truncated': len(records) > MAX_RECORDS, 'timeline_ambiguous': False,
                'timestamp_resolution_s': 1, 'issues': []},
            'pool_lengths': {'schema_version': 1, 'status': 'unavailable' if pool else 'not_applicable',
                'pool_length_m': pool_length, 'pool_length_unit': session.get('pool_length_unit')
                    if session.get('pool_length_unit') in ('metric', 'statute') else None,
                'sub_sport': session.get('sub_sport') if isinstance(session.get('sub_sport'), str) else None,
                'rows': [], 'truncated': len(lengths) > MAX_LENGTHS,
                'timestamp_resolution_s': 1, 'issues': []}}
        if not spans[index]:
            _issue(output[index]['fit_recording'], 'invalid_session_span')
            output[index]['fit_recording']['timeline_ambiguous'] = True
        if pool and (pool_length is None or pool_length <= 0):
            _issue(output[index]['pool_lengths'], 'missing_pool_length')

    def owners(start, elapsed=0):
        if start is None or any(span is None for span in spans):
            return []
        return [i for i, (origin, duration) in enumerate(spans)
                if 0 <= (start - origin).total_seconds() and
                   (start - origin).total_seconds() + elapsed <= duration + 1e-9]

    for row in records[:MAX_RECORDS]:
        row = row if isinstance(row, dict) else {}
        stamp = _stamp(row.get('timestamp'))
        indexes = owners(stamp)
        if stamp is None and len(sessions) == 1 and spans[0]:
            indexes = [0]  # Retain a null time in sequence, so no gap is bridged.
        if len(indexes) != 1:
            if len(indexes) > 1 or stamp is None or any(span is None for span in spans):
                for index in indexes or output:
                    quality = output[index]['fit_recording']
                    quality['timeline_ambiguous'] = True
                    _issue(quality, 'ambiguous_record_session')
            continue
        index = indexes[0]
        assigned[index].append(row)
        output[index]['fit_recording']['source_record_count'] += 1

    for row in lengths[:MAX_LENGTHS]:
        row = row if isinstance(row, dict) else {}
        span = _span(row)
        indexes = owners(*span) if span else []
        if len(indexes) != 1:
            for index in indexes or output:
                if output[index]['pool_lengths']['status'] != 'not_applicable':
                    _issue(output[index]['pool_lengths'], 'ambiguous_length_session')
            continue
        index = indexes[0]
        pool = output[index]['pool_lengths']
        if pool['status'] == 'not_applicable':
            continue
        start = (span[0] - spans[index][0]).total_seconds()
        stamp = _stamp(row.get('timestamp'))
        raw_index = row.get('message_index')
        entry = {'index': raw_index if isinstance(raw_index, int) and not isinstance(raw_index, bool) and 0 <= raw_index < 32768 else None,
                 'start_s': start, 'end_s': start + span[1], 'elapsed_s': span[1],
                 'timer_s': _number(row.get('total_timer_time')),
                 'timestamp_s': (stamp - spans[index][0]).total_seconds() if stamp else None,
                 'type': row.get('length_type') if row.get('length_type') in ('active', 'idle') else 'unknown',
                 'stroke': row.get('swim_stroke') if isinstance(row.get('swim_stroke'), str) else None}
        pool['rows'].append(entry)

    for index, payload in output.items():
        span = spans[index]
        payload['streams'] = _streams(assigned[index], span[0]) if span else []
        quality = payload['fit_recording']
        quality['preserved_record_count'] = len(assigned[index])
        if quality['truncated']:
            _issue(quality, 'record_limit')
        pool = payload['pool_lengths']
        if pool['truncated']:
            _issue(pool, 'length_limit')
        if pool['status'] != 'not_applicable':
            if not pool['rows']:
                _issue(pool, 'missing_length_messages')
            if pool['rows'] and not pool['issues']:
                pool['status'] = 'available'
        # JSON overhead can exceed the point budget for densely populated files.
        # Keep a prefix and advertise the loss; never silently downsample evidence.
        while len(json.dumps(payload, allow_nan=False, separators=(',', ':')).encode('utf-8')) > MAX_PAYLOAD_BYTES:
            quality['truncated'] = True
            _issue(quality, 'payload_limit')
            count = quality['preserved_record_count'] // 2
            if quality['preserved_record_count']:
                for stream in payload['streams']:
                    stream['data'] = stream['data'][:count]
                quality['preserved_record_count'] = count
            elif pool['rows']:
                pool['rows'] = pool['rows'][:len(pool['rows']) // 2]
                pool['truncated'] = True
                pool['status'] = 'unavailable'
                _issue(pool, 'payload_limit')
            else:
                raise ValueError('Limite dati FIT non valido.')
    return output


def apply_recording_quality(parsed, payload):
    """Carry import-time loss into later raw-data calculations and their coverage."""
    quality = payload.get('fit_recording') if isinstance(payload, dict) else None
    if not isinstance(quality, dict):
        return parsed
    parsed.quality['truncated'] = bool(parsed.quality.get('truncated') or quality.get('truncated'))
    parsed.quality['timeline_ambiguous'] = bool(parsed.quality.get('timeline_ambiguous') or quality.get('timeline_ambiguous'))
    issues = quality.get('issues')
    parsed.quality['fit_import_issues'] = [code for code in issues if isinstance(code, str)][:100] if isinstance(issues, list) else []
    return parsed
