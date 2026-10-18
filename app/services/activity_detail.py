"""Observed activity summaries and streams; no synthetic GPS or time axis."""
import hashlib
import json
import math
import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from types import SimpleNamespace

from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.integrations import intervals, intervals_credentials
from app.services.activity_rating import activity_ratings, recorded_loads
from app.services.athlete_clock import as_utc_naive, utc_now_naive
from app.services.training_load import sport_key

STREAMS = {
    'watts': ('power', 'Potenza', 'W'),
    'heartrate': ('heart_rate', 'Frequenza cardiaca', 'bpm'),
    'cadence': ('cadence', 'Cadenza', '/min'),
    'velocity_smooth': ('speed', 'Velocità', 'm/s'),
    'altitude': ('altitude', 'Altitudine', 'm'),
    'distance': ('distance', 'Distanza', 'm'),
    'temp': ('temperature', 'Temperatura', '°C'),
}
MAX_RAW_POINTS = 250000
MAX_CHART_POINTS = 900
MAX_ROUTE_POINTS = 3000
_CACHE = OrderedDict()
_CACHE_LOCK = Lock()


def _number(value, *, nonnegative=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if not nonnegative or value >= 0 else None


def _load_json(value):
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def local_observation(db, activity):
    observations = db.query(ActivityObservation).filter_by(activity_id=activity.id).order_by(ActivityObservation.id.desc()).all()
    intervals_rows = [row for row in observations if row.source_key.startswith('intervals:')]
    row = intervals_rows[0] if intervals_rows else observations[0] if observations else None
    remote_id = intervals_rows[0].source_key.split(':', 1)[1] if intervals_rows else (
        activity.source_id.removeprefix('intervals:') if activity.source == 'intervals' else None)
    if not isinstance(remote_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', remote_id):
        remote_id = None
    return (_load_json(row.payload_json) if row else {}), remote_id


def _cached_remote(remote_id):
    """Short, bounded in-memory cache, scoped to the protected account key."""
    try:
        configuration = intervals_credentials.credentials()
        key = configuration.get('api_key')
    except (RuntimeError, OSError, ValueError):
        return {}, [], None, 'Collegamento Intervals.icu da verificare.', False
    if not key:
        return {}, [], None, 'Collega Intervals.icu per caricare percorso e registrazioni complete.', False
    fingerprint = hashlib.sha256((str(configuration.get('athlete_id')) + '\0' + key).encode()).hexdigest()
    cache_key = (fingerprint, remote_id)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > time.monotonic():
            _CACHE.move_to_end(cache_key)
            return cached[1]
    summary = {}; streams = []; route = None; messages = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        summary_job = executor.submit(intervals.get_activity_remote, remote_id)
        streams_job = executor.submit(intervals.get_activity_remote, remote_id, '/streams.json',
                                      {'types': 'time,watts,heartrate,cadence,velocity_smooth,altitude,distance,temp,latlng'})
        for job, kind in ((summary_job, 'summary'), (streams_job, 'streams')):
            try:
                value = job.result()
                if kind == 'summary' and isinstance(value, dict):
                    summary = value
                elif kind == 'streams' and isinstance(value, list):
                    streams = value
                else:
                    messages.append('Il dettaglio ricevuto da Intervals.icu non contiene dati utilizzabili.')
            except intervals.IntervalsError as error:
                # Provider error messages are sanitized by the protected client.
                messages.append(error.message)
    # The map endpoint can contain GPS even when a latlng stream is absent.
    if streams and not normalize_streams(streams, 'unknown')[1]:
        try:
            value = intervals.get_activity_remote(remote_id, '/map')
            if isinstance(value, dict):
                route = value.get('latlngs')
        except intervals.IntervalsError:
            pass  # An indoor session legitimately has no route.
    result = (summary, streams, route, ' '.join(dict.fromkeys(messages)) or None, True)
    with _CACHE_LOCK:
        _CACHE[cache_key] = (time.monotonic() + (30 if messages else 300), result)
        _CACHE.move_to_end(cache_key)
        while len(_CACHE) > 16:
            _CACHE.popitem(last=False)
    return result


def _sample(points, limit):
    """Preserve extrema and explicit gaps rather than dropping peaks by stride."""
    if len(points) <= limit:
        return points
    result = [points[0]]
    buckets = max(1, (limit-2)//3)
    size = (len(points)-2) / buckets
    for index in range(buckets):
        bucket = points[1+int(index*size):1+int((index+1)*size)]
        valid = [point for point in bucket if point['value'] is not None]
        selected = ([min(valid, key=lambda point: point['value']), max(valid, key=lambda point: point['value'])] if valid else [])
        gap = next((point for point in bucket if point['value'] is None), None)
        if gap:
            selected.append(gap)
        seen = set()
        for point in sorted(selected, key=lambda point: point['time_s']):
            if point['time_s'] not in seen:
                result.append(point); seen.add(point['time_s'])
    result.append(points[-1])
    return result


def normalize_streams(raw, sport, *, max_time_s=None):
    if not isinstance(raw, list):
        return [], [], 0
    by_type = {row.get('type'): row for row in raw if isinstance(row, dict) and isinstance(row.get('data'), list)}
    times = by_type.get('time', {}).get('data', [])[:MAX_RAW_POINTS]
    normalized_times = []; last = -1
    for value in times:
        current = _number(value)
        if current is None or current < last or (max_time_s is not None and current > max_time_s):
            normalized_times.append(None)
        else:
            normalized_times.append(current); last = current
    charts = []
    for remote_kind, (kind, label, unit) in STREAMS.items():
        data = by_type.get(remote_kind, {}).get('data', [])[:MAX_RAW_POINTS]
        points = [{'time_s': stamp, 'value': _number(value, nonnegative=kind not in ('altitude', 'temperature'))}
                  for stamp, value in zip(normalized_times, data) if stamp is not None]
        if not any(point['value'] is not None for point in points):
            continue
        charts.append(dict(kind=kind, label=label, unit=unit, points=_sample(points, MAX_CHART_POINTS),
                           sample_count=len(points), downsampled=len(points)>MAX_CHART_POINTS))
        if kind == 'speed' and sport_key(sport) in ('run', 'swim'):
            distance = 100 if sport_key(sport) == 'swim' else 1000
            pace = [{'time_s': point['time_s'], 'value': distance/point['value'] if point['value'] is not None and point['value'] > 0 else None}
                    for point in points]
            charts.append(dict(kind='pace', label='Passo', unit='s/100m' if distance == 100 else 's/km',
                               points=_sample(pace, MAX_CHART_POINTS), sample_count=len(points),
                               downsampled=len(points)>MAX_CHART_POINTS))
    gps = by_type.get('latlng', {})
    data = gps.get('data', [])[:MAX_RAW_POINTS]
    longitude = gps.get('data2') if isinstance(gps.get('data2'), list) else []
    coordinates = []
    for index, point in enumerate(data):
        if normalized_times and index < len(normalized_times) and normalized_times[index] is None:
            continue
        lat, lon = (point[:2] if isinstance(point, list) and len(point) >= 2 else (point, longitude[index] if index < len(longitude) else None))
        lat, lon = _number(lat, nonnegative=False), _number(lon, nonnegative=False)
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            coordinates.append(dict(lat=lat, lon=lon, time_s=normalized_times[index] if index < len(normalized_times) else None))
    return charts, coordinates, sum(value is not None for value in normalized_times)


def _route_points(raw):
    if not isinstance(raw, list):
        return []
    points = []
    for pair in raw[:MAX_RAW_POINTS]:
        if not isinstance(pair, list) or len(pair) < 2:
            continue
        lat, lon = _number(pair[0], nonnegative=False), _number(pair[1], nonnegative=False)
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            points.append(dict(lat=lat, lon=lon, time_s=None))
    return points


def _observed_rating(db, activity, summary):
    """Rate the values shown in this detail without modifying persisted rows."""
    rows = db.query(Activity).all()
    fresh = SimpleNamespace(**{column.key: getattr(activity, column.key) for column in Activity.__table__.columns})
    for key in ('duration_seconds', 'distance_meters', 'elevation_gain_meters', 'avg_hr', 'max_hr',
                'avg_power', 'normalized_power', 'avg_speed', 'avg_cadence', 'calories', 'load'):
        setattr(fresh, key, summary[key])
    observed = [fresh if row.id == activity.id else row for row in rows]
    loads = recorded_loads(db, observed)
    loads[activity.id] = summary['load']
    return activity_ratings(db, observed, loads=loads)[activity.id]


def activity_detail(db, activity, *, fetch_remote=True):
    payload, remote_id = local_observation(db, activity)
    raw_streams = payload.get('streams', [])
    map_points = payload.get('latlngs')
    remote_message = None; fetched = False; attempted = False
    if remote_id and fetch_remote:
        remote_summary, remote_streams, remote_route, remote_message, attempted = _cached_remote(remote_id)
        payload.update(remote_summary)
        if remote_streams:
            raw_streams = remote_streams
        if remote_route:
            map_points = remote_route
        fetched = bool(remote_summary or remote_streams)
    def reading(keys, fallback=None):
        for key in keys:
            if key in payload:
                return _number(payload[key])
        return _number(fallback)
    summary = {
        'duration_seconds': reading(('moving_time', 'duration_seconds', 'duration'), activity.duration_seconds),
        'elapsed_seconds': reading(('elapsed_time',)),
        'distance_meters': reading(('distance', 'distance_meters'), activity.distance_meters),
        'elevation_gain_meters': reading(('total_elevation_gain', 'elevation_gain', 'elevation_gain_meters'), activity.elevation_gain_meters),
        'avg_hr': reading(('average_heartrate', 'avg_hr'), activity.avg_hr),
        'max_hr': reading(('max_heartrate', 'max_hr'), activity.max_hr),
        'avg_power': reading(('icu_average_watts', 'average_power', 'avg_power'), activity.avg_power),
        'normalized_power': reading(('icu_weighted_avg_watts', 'icu_np', 'normalized_power'), activity.normalized_power),
        'max_power': reading(('max_watts', 'max_power')),
        'avg_speed': reading(('average_speed', 'avg_speed'), activity.avg_speed),
        'max_speed': reading(('max_speed',)),
        'avg_cadence': reading(('average_cadence', 'avg_cadence'), activity.avg_cadence),
        'calories': reading(('calories',), activity.calories),
        'load': reading(('icu_training_load', 'training_load', 'load'), activity.load),
    }
    derived = []
    if summary['avg_speed'] is None and summary['distance_meters'] is not None and summary['duration_seconds']:
        summary['avg_speed'] = summary['distance_meters'] / summary['duration_seconds']
        derived.append('avg_speed')
    summary['avg_pace_seconds_per_km'] = 1000 / summary['avg_speed'] if summary['avg_speed'] else None
    summary['avg_pace_seconds_per_100m'] = 100 / summary['avg_speed'] if summary['avg_speed'] else None
    if summary['avg_speed']:
        derived += ['avg_pace_seconds_per_km', 'avg_pace_seconds_per_100m']
    elapsed_to_now = (utc_now_naive() - as_utc_naive(activity.start_time)).total_seconds()
    charts, coordinates, count = normalize_streams(raw_streams, activity.sport, max_time_s=elapsed_to_now)
    if not coordinates:
        coordinates = _route_points(map_points)
    if elapsed_to_now < 0:
        charts = []; coordinates = []; count = 0
    route_count = len(coordinates)
    if route_count > MAX_ROUTE_POINTS:
        indices = sorted(set(round(i*(route_count-1)/(MAX_ROUTE_POINTS-1)) for i in range(MAX_ROUTE_POINTS)))
        coordinates = [coordinates[index] for index in indices]
    status = 'partial' if attempted and remote_message else 'complete' if charts else 'summary_only'
    message = remote_message or (None if charts else 'Sono disponibili i riepiloghi; questa attività non contiene serie temporali utilizzabili.')
    return dict(activity_id=activity.id, name=activity.name, sport=activity.sport, start_time=activity.start_time,
                summary=summary, derived_fields=derived, streams=charts,
                route=dict(available=len(coordinates)>=2, points=coordinates, sample_count=route_count,
                           downsampled=route_count>MAX_ROUTE_POINTS),
                provider_url=f'https://intervals.icu/activities/{remote_id}' if remote_id else None,
                availability=dict(status=status, message=message, remote_fetched=fetched, remote_attempted=attempted, time_samples=count),
                coach_rating=_observed_rating(db, activity, summary))
