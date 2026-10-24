"""One identity-aware policy for recorded loads; no estimates or data writes."""
from itertools import groupby
import json
import math
from types import SimpleNamespace

from sqlalchemy import and_

from app.db.iron_models import ActivityObservation
from app.db.models import Activity

BATCH_SIZE = 500
MAX_PAYLOAD_CHARACTERS = 16_777_216
MAX_OBSERVATIONS_PER_ACTIVITY = 1_000


def finite_load(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _payload_load(payload):
    if not isinstance(payload, str) or len(payload) > MAX_PAYLOAD_CHARACTERS:
        return None, 'invalid', 'invalid_payload'
    try:
        content = json.loads(payload)
    except (ValueError, TypeError, RecursionError, OverflowError):
        return None, 'invalid', 'invalid_payload'
    if not isinstance(content, dict):
        return None, 'invalid', 'invalid_payload'
    if 'load' not in content:
        return None, 'missing', 'missing_load'
    if content['load'] is None:
        return None, 'missing', 'explicit_null'
    value = finite_load(content['load'])
    return (value, 'available', None) if value is not None else (None, 'invalid', 'invalid_load')


def _canonical_key(activity):
    if getattr(activity, 'source', None) != 'intervals':
        return None
    identifier = getattr(activity, 'source_id', None)
    if not isinstance(identifier, str) or not identifier:
        return None
    return 'intervals:' + identifier.removeprefix('intervals:')


def select_observation(activity, observations):
    """Select by provider identity, or agree only on validated load values.

    ``equivalent_loads`` does not imply equivalent payloads. A caller displaying
    an entire observation must require canonical_identity or single_observation.
    """
    observations = list(observations)
    count = len(observations)
    base = dict(observation=None, observation_count=count, observation_id=None,
                source_key=None, conflict=False, reason='no_intervals_observation', value_reason=None)
    if not observations:
        raw = getattr(activity, 'load', None); value = finite_load(raw)
        return {**base, 'value': value, 'source': 'canonical_activity',
                'status': 'available' if value is not None else 'missing' if raw is None else 'invalid',
                'value_reason': None if value is not None else 'missing_load' if raw is None else 'invalid_load'}
    selected = None; key = _canonical_key(activity)
    if key is not None:
        selected = next((row for row in observations if row.source_key == key), None)
    if selected is not None:
        reason = 'canonical_identity'
    elif count == 1:
        selected = observations[0]; reason = 'single_observation'
    else:
        values = [_payload_load(row.payload_json) for row in observations]
        if all(status in ('available', 'missing') for _, status, _ in values) and len({(value, status) for value, status, _ in values}) == 1:
            selected = min(observations, key=lambda row: row.id); reason = 'equivalent_loads'
        else:
            return {**base, 'value': None, 'source': 'conflict', 'status': 'conflict', 'conflict': True,
                    'reason': 'conflicting_observations', 'value_reason': 'conflicting_observations'}
    value, status, value_reason = _payload_load(selected.payload_json)
    return {**base, 'observation': selected, 'value': value, 'status': status,
            'source': 'intervals_observation', 'reason': reason, 'value_reason': value_reason,
            'observation_id': selected.id, 'source_key': selected.source_key}


def _metadata(activity, rows, count=None):
    if count is not None and count > MAX_OBSERVATIONS_PER_ACTIVITY:
        return dict(value=None, status='conflict', source='conflict', observation_id=None, source_key=None,
                    observation_count=count, conflict=True, reason='observation_limit', value_reason='observation_limit')
    result = select_observation(activity, rows)
    result.pop('observation')
    return result


def resolve_observed_loads(db, activities=None, *, batch_size=BATCH_SIZE):
    """Resolve requested IDs in bounded queries; optional global streamed read.

    The global form supports the existing request-scoped fitness snapshot with
    one streamed join. It never materializes every observation payload at once.
    """
    if activities is None:
        query = db.query(Activity.id, Activity.source, Activity.source_id, Activity.load,
                         ActivityObservation.id, ActivityObservation.source_key, ActivityObservation.payload_json).outerjoin(
            ActivityObservation, and_(ActivityObservation.activity_id == Activity.id,
                                     ActivityObservation.source_key.like('intervals:%'))).order_by(Activity.id, ActivityObservation.id)
        result = {}
        for identifier, grouped in groupby(query.yield_per(BATCH_SIZE), key=lambda row: row[0]):
            rows = []; count = 0; activity = None
            for row in grouped:
                if activity is None:
                    activity = SimpleNamespace(id=row[0], source=row[1], source_id=row[2], load=row[3])
                if row[4] is not None:
                    count += 1
                    if len(rows) < MAX_OBSERVATIONS_PER_ACTIVITY:
                        rows.append(SimpleNamespace(id=row[4], source_key=row[5], payload_json=row[6]))
            result[identifier] = _metadata(activity, rows, count)
        return result
    activity_by_id = {row.id: row for row in activities}
    result = {identifier: _metadata(row, []) for identifier, row in activity_by_id.items()}
    identifiers = list(activity_by_id)
    batch_size = min(BATCH_SIZE, max(1, batch_size))
    for start in range(0, len(identifiers), batch_size):
        query = db.query(ActivityObservation.id, ActivityObservation.activity_id, ActivityObservation.source_key,
                         ActivityObservation.payload_json).filter(ActivityObservation.activity_id.in_(identifiers[start:start + batch_size]),
                         ActivityObservation.source_key.like('intervals:%')).order_by(ActivityObservation.activity_id, ActivityObservation.id)
        for identifier, grouped in groupby(query.yield_per(BATCH_SIZE), key=lambda row: row[1]):
            rows = []; count = 0
            for row in grouped:
                count += 1
                if len(rows) < MAX_OBSERVATIONS_PER_ACTIVITY:
                    rows.append(SimpleNamespace(id=row[0], source_key=row[2], payload_json=row[3]))
            result[identifier] = _metadata(activity_by_id[identifier], rows, count)
    return result


def load_values(resolved):
    return {identifier: value['value'] for identifier, value in resolved.items()}
