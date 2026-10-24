"""Descriptive execution comparison; absent values never become zero or RPE 4."""
from __future__ import annotations

from statistics import mean

from app.services.execution_evidence import finite_number, safe_object


def evaluate_workout(planned: dict, actual: dict) -> dict:
    planned = safe_object(planned) or {}
    actual = safe_object(actual) or {}
    pd = finite_number(planned.get('duration_min'), minimum=0)
    ad = finite_number(actual.get('duration_min'), minimum=0)
    completion = None if pd is None or pd <= 0 or ad is None else min(100., ad / pd * 100.)

    errors = []
    planned_steps = planned.get('steps')
    actual_steps = actual.get('steps')
    if isinstance(planned_steps, list) and isinstance(actual_steps, list):
        for p, a in zip(planned_steps, actual_steps):
            if not isinstance(p, dict) or not isinstance(a, dict):
                continue
            target = finite_number(p.get('target_power_w'), minimum=0)
            value = finite_number(a.get('avg_power_w'), minimum=0)
            if target is not None and target > 0 and value is not None:
                error = finite_number(abs(value - target) / target * 100., minimum=0)
                if error is not None:
                    errors.append(error)
    # Average after scaling keeps valid extreme finite inputs from overflowing.
    power_error = sum(value / len(errors) for value in errors) if errors else None
    intensity = max(0., 100. - power_error * 3.) if power_error is not None else None
    rpe = finite_number(actual.get('rpe'), minimum=1, maximum=10)
    expected = finite_number(planned.get('expected_rpe'), minimum=1, maximum=10)
    rpe_score = None if rpe is None or expected is None else max(0., 100. - abs(rpe - expected) * 15.)
    components = [value for value in (completion, intensity, rpe_score) if value is not None]
    overall = round(mean(components), 1) if completion is not None else None
    if rpe is not None and rpe >= 8 and completion is not None and completion >= 80:
        adaptation = 'reduce_next_similar'
    elif rpe is not None and rpe <= 3 and completion is not None and completion >= 95:
        adaptation = 'consider_progression'
    elif power_error is not None and power_error > 8:
        adaptation = 'recalibrate_targets'
    else:
        adaptation = 'hold'
    return {
        'completion': round(completion, 1) if completion is not None else None,
        'intensity_control': round(intensity, 1) if intensity is not None else None,
        'rpe_score': round(rpe_score, 1) if rpe_score is not None else None,
        'overall': overall,
        'power_error_pct': round(power_error, 1) if power_error is not None else None,
        'adaptation': adaptation,
        'note': 'Una singola seduta genera un segnale; la modifica persistente richiede un trend ripetuto.',
    }
