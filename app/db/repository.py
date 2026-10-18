from datetime import timedelta

from sqlalchemy.orm import Session

from app.db.models import Activity
from app.db.experience_models import ActivityIdentity
from app.db.iron_models import DuplicateCandidate, ActivityObservation
import json
from app.services.training_load import sport_key


def save_activity(db: Session, data: dict) -> Activity:
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
        db.commit()
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
    db.commit()
    db.refresh(activity)

    return activity
