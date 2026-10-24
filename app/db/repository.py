from datetime import timedelta

from sqlalchemy.orm import Session

from app.db.models import Activity
from app.db.experience_models import ActivityIdentity
from app.db.iron_models import DuplicateCandidate, ActivityObservation
import json
import math
from collections import defaultdict
from sqlalchemy.orm import aliased
from sqlalchemy.exc import IntegrityError
from app.services.athlete_clock import as_utc_naive, utc_now_naive
from app.services.training_load import sport_key


EXACT_DUPLICATE_FIELDS = (
    'duration_seconds', 'distance_meters', 'elevation_gain_meters', 'avg_hr', 'max_hr',
    'avg_power', 'normalized_power', 'avg_speed', 'avg_cadence', 'calories', 'load',
)
MAX_DUPLICATE_DECISIONS = 100000
MAX_NEW_DUPLICATE_CANDIDATES = 1000


def exact_duplicate_key(activity):
    values = tuple(getattr(activity, field) for field in EXACT_DUPLICATE_FIELDS)
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0) for value in values):
        return None
    if not ((activity.duration_seconds is not None and activity.duration_seconds > 0)
            or (activity.distance_meters is not None and activity.distance_meters > 0)):
        return None
    if activity.start_time is None:
        return None
    key = sport_key(activity.sport)
    if key in ('gym', 'weightlifting', 'workout'):
        key = 'strength'
    return (as_utc_naive(activity.start_time), key, *values)


def exact_duplicate_groups(activities):
    """Strong review leads, never proof that two source identities are one ride.

    A different name/provider does not prevent an exact lead. Missing and zero
    remain distinct; invalid fields and records without positive time/distance
    are deliberately excluded. Near matches retain the existing import logic.
    """
    groups = defaultdict(list)
    for activity in activities:
        key = exact_duplicate_key(activity)
        if key is not None:
            groups[key].append(activity)
    return [sorted(group, key=lambda row: row.id) for group in groups.values() if len(group) > 1]


def exact_duplicate_review(db, activities):
    """Load decisions only for the exact groups in the supplied observation range."""
    groups = exact_duplicate_groups(activities)
    membership = {row.id: index for index, group in enumerate(groups) for row in group}
    if not membership:
        return {'groups': groups, 'decisions': {}, 'decisions_truncated': False}
    first, second = aliased(Activity), aliased(Activity)
    starts = [row.start_time for group in groups for row in group]
    decisions = db.query(DuplicateCandidate).join(first, first.id == DuplicateCandidate.activity_id).join(
        second, second.id == DuplicateCandidate.other_id,
    ).filter(first.start_time >= min(starts), first.start_time <= max(starts),
             second.start_time >= min(starts), second.start_time <= max(starts)).order_by(
        DuplicateCandidate.id.asc(),
    ).limit(MAX_DUPLICATE_DECISIONS + 1).all()
    truncated = len(decisions) > MAX_DUPLICATE_DECISIONS
    result = {}
    for row in decisions[:MAX_DUPLICATE_DECISIONS]:
        if row.activity_id == row.other_id or row.activity_id not in membership or row.other_id not in membership:
            continue
        if membership[row.activity_id] != membership[row.other_id]:
            continue
        pair = tuple(sorted((row.activity_id, row.other_id)))
        # A prior explicit decision wins over an accidental reverse pending row.
        if pair not in result or result[pair] == 'pending':
            result[pair] = row.status
    return {'groups': groups, 'decisions': result, 'decisions_truncated': truncated}


def exact_duplicate_counts(review, identifiers=None):
    selected = set(identifiers) if identifiers is not None else None
    possible = 0
    for group in review['groups']:
        count = len(group) if selected is None else sum(row.id in selected for row in group)
        possible += count * (count - 1) // 2
    decisions = {pair: status for pair, status in review['decisions'].items()
                 if selected is None or all(identifier in selected for identifier in pair)}
    reviewed = sum(status != 'pending' for status in decisions.values())
    return {'possible_pairs': possible, 'unreviewed_pairs': possible - len(decisions),
            'unresolved_pairs': possible - reviewed,
            'kept_pairs': sum(status == 'kept' for status in decisions.values()),
            'pending_exact_pairs': sum(status == 'pending' for status in decisions.values())}


def duplicate_summary(db, limit=10000):
    """Read-only, bounded retrospective scan; no provider or source data changes."""
    now = utc_now_naive()
    activities = db.query(Activity).filter(Activity.start_time <= now).order_by(
        Activity.start_time.desc(), Activity.id.desc(),
    ).limit(limit + 1).all()
    truncated = len(activities) > limit
    activities = activities[:limit]
    review = exact_duplicate_review(db, activities)
    counts = exact_duplicate_counts(review)
    first, second = aliased(Activity), aliased(Activity)
    pending = db.query(DuplicateCandidate).join(first, first.id == DuplicateCandidate.activity_id).join(
        second, second.id == DuplicateCandidate.other_id,
    ).filter(DuplicateCandidate.status == 'pending').count()
    body = {
        'scanned_activities': len(activities), 'scan_limit': limit,
        'eligible_activities': sum(exact_duplicate_key(row) is not None for row in activities),
        'duplicate_groups': len(review['groups']), **counts, 'pending_candidates': pending,
        'truncated': truncated or review['decisions_truncated'],
        'summary': 'Le corrispondenze esatte sono possibili duplicati da verificare. La scansione crea solo confronti: non unisce, elimina o esclude attività dalle analisi.',
        'methodology': 'Stesso istante UTC, disciplina equivalente e tutti i valori registrati identici, con durata o distanza positiva. Nome e ID della fonte possono differire. Non cerca somiglianze approssimative.',
    }
    if body['truncated']:
        body['summary'] += ' Scansione limitata: lo storico più vecchio o parte delle decisioni non è incluso.'
    return body, review


def scan_exact_duplicates(db, limit=10000):
    """Persist only new review candidates, atomically and idempotently."""
    body, review = duplicate_summary(db, limit)
    if review['decisions_truncated']:
        raise ValueError('Troppi confronti nell’intervallo: riduci il numero di attività della scansione.')
    new_pairs = []
    for group in review['groups']:
        identifiers = [row.id for row in group]
        for index, first in enumerate(identifiers):
            for second in identifiers[index + 1:]:
                if (first, second) in review['decisions']:
                    continue
                new_pairs.append((second, first))
                if len(new_pairs) >= MAX_NEW_DUPLICATE_CANDIDATES:
                    break
            if len(new_pairs) >= MAX_NEW_DUPLICATE_CANDIDATES:
                break
        if len(new_pairs) >= MAX_NEW_DUPLICATE_CANDIDATES:
            break
    for first, second in new_pairs:
        db.add(DuplicateCandidate(activity_id=first, other_id=second, status='pending'))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # A concurrent scan can have created the same deterministic pair.
        refreshed, _ = duplicate_summary(db, limit)
        return {**refreshed, 'new_candidates': 0,
                'more_candidates_available': refreshed['unreviewed_pairs'] > 0}
    body['new_candidates'] = len(new_pairs)
    body['pending_candidates'] += len(new_pairs)
    body['pending_exact_pairs'] += len(new_pairs)
    body['unreviewed_pairs'] -= len(new_pairs)
    body['more_candidates_available'] = body['unreviewed_pairs'] > 0
    return body


def save_activity(db: Session, data: dict, *, commit: bool = True) -> Activity:
    identity_key = f"{data.get('source', 'unknown')}:{data['source_id']}"
    alias = db.query(ActivityIdentity).filter_by(source_key=identity_key).first()
    existing = db.get(Activity, alias.activity_id) if alias else (
        db.query(Activity)
        .filter(Activity.source_id == data["source_id"], Activity.source == data.get('source'))
        .first()
    )

    # Cross-source deduplication: the same Garmin activity may already exist
    # as a local FIT import when it arrives through Intervals.icu.
    possible_duplicates = []
    if not existing and data.get("start_time") is not None:
        start = data["start_time"]
        candidates = (
            db.query(Activity)
            .filter(
                Activity.start_time >= start - timedelta(seconds=120),
                Activity.start_time <= start + timedelta(seconds=120),
                Activity.source != data.get('source'),
            )
            .order_by(Activity.start_time.asc())
            .all()
        )
        # A similar timestamp alone is not enough to merge distinct sessions.
        for candidate in candidates:
            if sport_key(candidate.sport) != sport_key(data.get('sport')):
                continue
            duration = float(data.get('duration_seconds') or 0)
            previous = float(candidate.duration_seconds or 0)
            distance = data.get('distance_meters')
            old_distance = candidate.distance_meters
            duration_matches = duration > 0 and previous > 0 and abs(duration - previous) <= max(30, duration * .03)
            distance_matches = distance is None or old_distance is None or abs(distance - old_distance) <= max(100, distance * .03)
            if duration_matches and distance_matches:
                possible_duplicates.append(candidate)

    if existing:
        # Preserve the richer existing record but fill missing fields from the
        # remote source and remember the remote ID in source_id when possible.
        for key, value in data.items():
            if key not in {"id", "source_id"} and value is not None and getattr(existing, key, None) is None:
                setattr(existing, key, value)
        if not alias:
            db.add(ActivityIdentity(source_key=identity_key, activity_id=existing.id))
        observation = db.query(ActivityObservation).filter_by(source_key=identity_key).first()
        if not observation:
            observation = ActivityObservation(source_key=identity_key, activity_id=existing.id); db.add(observation)
        observation.payload_json = json.dumps(data, default=str)
        db.commit() if commit else db.flush()
        db.refresh(existing)
        return existing

    # Older schemas enforce global source_id uniqueness. Namespace only collisions.
    data = dict(data)
    if db.query(Activity).filter_by(source_id=data['source_id']).first():
        data['source_id'] = identity_key
    base_key = data['source_id']
    suffix = 1
    while db.query(Activity).filter_by(source_id=data['source_id']).first():
        data['source_id'] = f'{base_key}:{suffix}'
        suffix += 1
    activity = Activity(**data)

    db.add(activity)
    db.flush()
    db.add(ActivityIdentity(source_key=identity_key, activity_id=activity.id))
    db.add(ActivityObservation(source_key=identity_key, activity_id=activity.id, payload_json=json.dumps(data, default=str)))
    for candidate in possible_duplicates:
        db.add(DuplicateCandidate(activity_id=activity.id, other_id=candidate.id))
    db.commit() if commit else db.flush()
    db.refresh(activity)

    return activity
