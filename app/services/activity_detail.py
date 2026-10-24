"""Observed activity summaries and streams; no synthetic GPS or time axis."""
import hashlib
import json
import math
import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from app.db.database import WORKSPACE
from threading import Lock
from types import SimpleNamespace

from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.integrations import intervals, intervals_credentials
from app.services.activity_rating import activity_ratings, recorded_loads
from app.services.activity_effort import (number, parse_streams, channel_statistics, chart_points, compare_effort)
from app.services.athlete_clock import as_utc_naive, utc_now_naive
from app.services.training_load import sport_key
from app.services.power_duration import best_power_efforts
from app.services.distance_efforts import best_distance_efforts, best_pool_efforts
from app.services.fit_observations import apply_recording_quality
from app.services.observed_load import select_observation, MAX_OBSERVATIONS_PER_ACTIVITY

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
    return number(value, nonnegative=nonnegative)


def _load_json(value):
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError, RecursionError):
        return {}


def _local_observation_details(db, activity):
    observations = db.query(ActivityObservation).filter_by(activity_id=activity.id).order_by(
        ActivityObservation.id.desc()).limit(MAX_OBSERVATIONS_PER_ACTIVITY + 1).all()
    intervals_rows = [row for row in observations if row.source_key.startswith('intervals:')]
    resolved = select_observation(activity, intervals_rows)
    ambiguous = bool(intervals_rows and resolved['reason'] not in ('canonical_identity', 'single_observation'))
    if len(observations) > MAX_OBSERVATIONS_PER_ACTIVITY:
        ambiguous = True
        resolved.update(status='conflict', reason='observation_limit', value=None)
    selected = None if ambiguous else resolved['observation']
    if not intervals_rows and not ambiguous:
        selected = observations[0] if observations else None
    content = _load_json(selected.payload_json) if selected else {}
    if intervals_rows or ambiguous:
        # The normalized observation's load is authoritative even when missing.
        # Equivalent loads do not authorize choosing another alias's whole payload.
        content.pop('icu_training_load', None); content.pop('training_load', None)
        content['load'] = resolved['value']
    remote_id = selected.source_key.split(':', 1)[1] if selected and selected.source_key.startswith('intervals:') else None
    if not intervals_rows and activity.source == 'intervals' and isinstance(activity.source_id, str):
        remote_id = activity.source_id.removeprefix('intervals:')
    if ambiguous or not isinstance(remote_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', remote_id):
        remote_id = None
    resolution = {key: resolved[key] for key in ('source','status','reason','value_reason','conflict','observation_count')}
    resolution['ambiguous_identity'] = ambiguous
    return content, remote_id, resolution


def local_observation(db, activity):
    content, remote_id, _ = _local_observation_details(db, activity)
    return content, remote_id


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
    cache_key = (WORKSPACE.get(), fingerprint, remote_id)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > time.monotonic():
            _CACHE.move_to_end(cache_key)
            return cached[1]
    summary = {}; streams = []; route = None; messages = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        summary_job = executor.submit(copy_context().run, intervals.get_activity_remote, remote_id)
        streams_job = executor.submit(copy_context().run, intervals.get_activity_remote, remote_id, '/streams.json',
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
    if len(points) <= limit:
        return points
    # Reserve extrema and a gap per bucket. Mark every omitted interruption on
    # the next retained point, so a display limit can never bridge a raw gap.
    selected = {0, len(points)-1}
    buckets = max(1, (limit-2)//3)
    size = (len(points)-2) / buckets
    for index in range(buckets):
        indices = range(1+int(index*size), 1+int((index+1)*size))
        valid = [item for item in indices if points[item]['value'] is not None]
        if valid:
            selected.update((min(valid, key=lambda item: points[item]['value']),
                             max(valid, key=lambda item: points[item]['value'])))
        gap = next((item for item in indices if points[item]['value'] is None), None)
        if gap is not None:
            selected.add(gap)
    breaks = [0]
    for point in points:
        breaks.append(breaks[-1] + (point['value'] is None or point.get('break_before', False)))
    result = []; previous = None
    for index in sorted(selected):
        point = dict(points[index])
        if previous is not None and breaks[index+1] > breaks[previous+1]:
            point['break_before'] = True
        result.append(point); previous = index
    return result


def _normalized_streams(parsed, sport):
    charts = []
    for kind, label, unit in STREAMS.values():
        values = parsed.channels.get(kind)
        if values is None:
            continue
        points = chart_points(parsed, values)
        if not any(point['value'] is not None for point in points):
            continue
        stats = channel_statistics(parsed, values)
        charts.append(dict(kind=kind, label=label, unit=unit, points=_sample(points, MAX_CHART_POINTS),
                           sample_count=stats['sample_count'], downsampled=len(points)>MAX_CHART_POINTS,
                           statistics=stats, stats_scope='raw_recording'))
        if kind == 'speed' and sport_key(sport) in ('run', 'swim'):
            distance = 100 if sport_key(sport) == 'swim' else 1000
            pace = [number(distance/value) if value is not None and value > 0 else None for value in values]
            pace_points = chart_points(parsed, pace)
            charts.append(dict(kind='pace', label='Passo', unit='s/100m' if distance == 100 else 's/km',
                               points=_sample(pace_points, MAX_CHART_POINTS), sample_count=stats['sample_count'],
                               downsampled=len(pace_points)>MAX_CHART_POINTS,
                               statistics=channel_statistics(parsed, pace), stats_scope='raw_recording'))
    gps = parsed.gps or {}
    data = gps.get('data', [])[:MAX_RAW_POINTS]
    longitude = gps.get('data2') if isinstance(gps.get('data2'), list) else []
    coordinates = []
    for index, point in enumerate(data):
        # A supplied time channel is authoritative, including invalid/short tails.
        if parsed.has_time_channel and (index >= len(parsed.times) or parsed.times[index] is None):
            continue
        lat, lon = (point[:2] if isinstance(point, list) and len(point) >= 2 else (point, longitude[index] if index < len(longitude) else None))
        lat, lon = _number(lat, nonnegative=False), _number(lon, nonnegative=False)
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            coordinates.append(dict(lat=lat, lon=lon, time_s=parsed.times[index] if index < len(parsed.times) else None))
    return charts, coordinates, parsed.quality['valid_time_samples']


def normalize_streams(raw, sport, *, max_time_s=None, elapsed_seconds=None):
    return _normalized_streams(parse_streams(raw, max_time_s=max_time_s, elapsed_seconds=elapsed_seconds), sport)


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
    return activity_ratings(db, observed, loads=loads, target_ids={activity.id})[activity.id]


def activity_detail(db, activity, *, fetch_remote=True):
    payload, remote_id, resolution = _local_observation_details(db, activity)
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
        summary['avg_speed'] = _number(summary['distance_meters'] / summary['duration_seconds'])
        if summary['avg_speed'] is not None:
            derived.append('avg_speed')
    summary['avg_pace_seconds_per_km'] = _number(1000 / summary['avg_speed']) if summary['avg_speed'] else None
    summary['avg_pace_seconds_per_100m'] = _number(100 / summary['avg_speed']) if summary['avg_speed'] else None
    if summary['avg_speed']:
        derived += ['avg_pace_seconds_per_km', 'avg_pace_seconds_per_100m']
    elapsed_to_now = (utc_now_naive() - as_utc_naive(activity.start_time)).total_seconds()
    parsed = parse_streams(raw_streams, max_time_s=elapsed_to_now, elapsed_seconds=summary['elapsed_seconds'])
    if not (fetched and remote_streams):
        apply_recording_quality(parsed, payload)
    charts, coordinates, count = _normalized_streams(parsed, activity.sport)
    effort = compare_effort(parsed, activity.sport)
    effort['source'] = 'intervals_remote_streams' if fetched and remote_streams else 'stored_observation_streams'
    best_efforts = best_power_efforts(parsed)
    best_efforts['source'] = effort['source']
    distance_efforts = best_distance_efforts(parsed, activity.sport)
    distance_efforts['source'] = effort['source']
    pool_payload = payload.get('pool_lengths')
    if sport_key(activity.sport) == 'swim':
        # FIT lengths have their own source identity; never substitute a step
        # distance stream or choose arbitrarily among multiple imported files.
        from app.services.effort_records import source_snapshot
        pool_snapshot = source_snapshot(db, activity, family='fit')
        if pool_snapshot['source_identity'] is not None:
            pool_payload = pool_snapshot['payload'].get('pool_lengths')
        elif pool_snapshot['resolution'] in ('ambiguous_identity', 'observation_limit'):
            pool_payload = {'status': 'unavailable', 'issues': [pool_snapshot['resolution']]}
    pool_efforts = best_pool_efforts(pool_payload, activity.sport, max_time_s=elapsed_to_now)
    pool_efforts['source'] = 'stored_fit_lengths'
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
    if resolution['ambiguous_identity']:
        status = 'partial'
        message = 'Questa attività è associata a più registrazioni della fonte: mostro i riepiloghi conservati senza scegliere arbitrariamente un altro dettaglio.'
    references = {}
    if sport_key(activity.sport) == 'bike' and any(chart['kind'] == 'power' for chart in charts):
        from app.services.planning import load_settings
        threshold = load_settings(db)['thresholds']['cycling_ftp_watts']
        if threshold['value']:
            references['power'] = {'lower': round(threshold['value'] * .56, 1), 'upper': round(threshold['value'] * .75, 1),
                                   'label': 'Z2 potenza · riferimento endurance',
                                   'note': 'FTP attuale ' + ('stimata' if threshold['source'] == 'estimated' else 'misurata') + '; non è l’obiettivo di ogni seduta',
                                   'source_url': 'https://www.trainingpeaks.com/blog/power-training-levels/'}
    return dict(activity_id=activity.id, name=activity.name, sport=activity.sport, start_time=activity.start_time,
                summary=summary, derived_fields=derived, streams=charts, chart_references=references,
                effort_comparison=effort, best_efforts=best_efforts, distance_efforts=distance_efforts,
                pool_efforts=pool_efforts, stream_quality=parsed.quality,
                route=dict(available=len(coordinates)>=2, points=coordinates, sample_count=route_count,
                           downsampled=route_count>MAX_ROUTE_POINTS),
                provider_url=f'https://intervals.icu/activities/{remote_id}' if remote_id else None,
                availability=dict(status=status, message=message, remote_fetched=fetched, remote_attempted=attempted, time_samples=count, source_resolution=resolution),
                coach_rating=_observed_rating(db, activity, summary))
