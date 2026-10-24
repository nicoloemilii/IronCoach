"""Shared, bounded checks for evidence of a planned session's execution.

Stored JSON and status labels are not proof of execution. Callers load the
activity explicitly; this module performs no database access or mutation.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime

from app.services.athlete_clock import activity_day, as_utc_naive
from app.services.training_load import sport_key

MAX_JSON_CHARACTERS = 524_288
MAX_JSON_NODES = 20_000
MAX_JSON_DEPTH = 24


def finite_number(value, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def safe_object(payload):
    """Return a finite JSON object within explicit size/depth/node bounds."""
    if isinstance(payload, str):
        if len(payload) > MAX_JSON_CHARACTERS:
            return None
        try:
            content = json.loads(payload)
        except (ValueError, TypeError, RecursionError, OverflowError):
            return None
    else:
        content = payload
    if not isinstance(content, dict):
        return None
    pending = [(content, 0)]; nodes = 0; characters = 0
    while pending:
        value, depth = pending.pop(); nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return None
        if isinstance(value, dict):
            if len(value) + nodes > MAX_JSON_NODES:
                return None
            for key, item in value.items():
                if not isinstance(key, str):
                    return None
                characters += len(key)
                pending.append((item, depth + 1))
        elif isinstance(value, list):
            if len(value) + nodes > MAX_JSON_NODES:
                return None
            pending.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            characters += len(value)
        elif value is None or isinstance(value, bool):
            pass
        elif isinstance(value, (int, float)):
            if finite_number(value) is None:
                return None
        else:
            return None
        if characters > MAX_JSON_CHARACTERS:
            return None
    return content


def usable_feedback(workout, feedback, activity=None, *, today, now):
    """Validate the canonical link or manual duration without inventing values.

    An activity with unknown duration confirms occurrence, but supplies neither
    minutes nor adherence. Legacy linked feedback without ``actual`` is valid.
    """
    result = dict(usable=False, issue=None, minutes=None, source=None,
                  activity_id=None, content=None, planned=None)

    def unavailable(issue):
        result['issue'] = issue
        return result

    if not feedback:
        return unavailable('missing_feedback')
    try:
        planned_day = date.fromisoformat(workout.date)
    except (ValueError, TypeError, AttributeError):
        return unavailable('invalid_workout_date')
    if planned_day > today:
        return unavailable('future_workout')
    planned = safe_object(workout.payload_json)
    content = safe_object(feedback.payload_json)
    if planned is None:
        return unavailable('invalid_plan_payload')
    if content is None:
        return unavailable('invalid_feedback_payload')
    result.update(content=content, planned=planned)
    if planned.get('duration_min') is not None:
        declared_minutes = finite_number(planned['duration_min'], minimum=0)
        if declared_minutes is None or declared_minutes <= 0:
            return unavailable('invalid_plan_payload')
    if planned.get('steps') is not None and not isinstance(planned['steps'], list):
        return unavailable('invalid_plan_payload')
    if content.get('evaluation') is not None and not isinstance(content['evaluation'], dict):
        return unavailable('invalid_feedback_payload')
    planned_minutes = finite_number(workout.duration_min, minimum=0)
    if planned_minutes is None or planned_minutes <= 0:
        return unavailable('invalid_planned_duration')
    for field, lower, upper in [('rpe', 1, 10), ('completion_percent', 0, 100)]:
        if content.get(field) is not None and finite_number(content[field], minimum=lower, maximum=upper) is None:
            return unavailable('invalid_' + field)
    if content.get('actual') is not None and not isinstance(content['actual'], dict):
        return unavailable('invalid_actual_payload')
    if feedback.activity_id is not None:
        if activity is None or activity.id != feedback.activity_id:
            return unavailable('missing_activity')
        if not isinstance(activity.start_time, datetime):
            return unavailable('invalid_activity_time')
        try:
            recorded_day = activity_day(activity.start_time)
            if as_utc_naive(activity.start_time) > as_utc_naive(now) or recorded_day > today:
                return unavailable('future_activity')
        except (ValueError, TypeError, OverflowError):
            return unavailable('invalid_activity_time')
        if (not isinstance(activity.sport, str) or not isinstance(workout.sport, str)
                or sport_key(activity.sport) != sport_key(workout.sport)):
            return unavailable('activity_sport_mismatch')
        if abs((recorded_day - planned_day).days) > 2:
            return unavailable('activity_date_mismatch')
        duration = finite_number(activity.duration_seconds, minimum=0)
        if activity.duration_seconds is not None and duration is None:
            return unavailable('invalid_activity_duration')
        result.update(usable=True, minutes=duration / 60 if duration is not None else None,
                      source='activity', activity_id=activity.id)
    else:
        actual = content.get('actual') or {}
        duration = finite_number(actual.get('duration_min'), minimum=0)
        if duration is None:
            return unavailable('missing_or_invalid_manual_duration')
        result.update(usable=True, minutes=duration, source='self_reported')
    return result
