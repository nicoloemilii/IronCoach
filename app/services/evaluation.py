from __future__ import annotations
from statistics import mean


def evaluate_workout(planned: dict, actual: dict) -> dict:

    pd = float(
        planned.get("duration_min") or 0
    )

    ad = float(
        actual.get("duration_min") or 0
    )

    completion = (
        100.0
        if pd <= 0
        else max(
            0.0,
            min(
                100.0,
                ad / pd * 100.0
            )
        )
    )

    targets = []
    values = []

    for p, a in zip(
        planned.get("steps", []),
        actual.get("steps", [])
    ):

        target = p.get("target_power_w")
        value = a.get("avg_power_w")

        if target is not None and value is not None:
            target = float(target)
            value = float(value)

            if target > 0:
                targets.append(target)
                values.append(value)

    if targets:
        power_errors = [
            abs(v - t) / t * 100.0
            for t, v in zip(targets, values)
        ]

        power_error = mean(power_errors)

        intensity_control = max(
            0.0,
            min(
                100.0,
                100.0 - power_error * 3.0
            )
        )
    else:
        power_error = None
        intensity_control = None

    rpe = actual.get("rpe")

    expected = float(
        planned.get("expected_rpe") or 4
    )

    rpe_score = (
        None
        if rpe is None
        else max(
            0.0,
            min(
                100.0,
                100.0
                - abs(float(rpe) - expected) * 15.0
            )
        )
    )

    scores = [
        x
        for x in (
            completion,
            intensity_control,
            rpe_score,
        )
        if x is not None
    ]

    overall = (
        round(mean(scores), 1)
        if scores
        else None
    )

    if rpe is not None and float(rpe) >= 8 and completion >= 80:
        adaptation = "reduce_next_similar"

    elif (
        rpe is not None
        and float(rpe) <= 3
        and completion >= 95
    ):
        adaptation = "consider_progression"

    elif (
        power_error is not None
        and power_error > 8
    ):
        adaptation = "recalibrate_targets"

    else:
        adaptation = "hold"

    return {
        "completion": round(completion, 1),
        "intensity_control": (
            None
            if intensity_control is None
            else round(intensity_control, 1)
        ),
        "rpe_score": (
            None
            if rpe_score is None
            else round(rpe_score, 1)
        ),
        "overall": overall,
        "power_error_pct": (
            None
            if power_error is None
            else round(power_error, 1)
        ),
        "adaptation": adaptation,
        "note": (
            "Una singola seduta genera un segnale; "
            "la modifica persistente richiede un trend ripetuto."
        ),
    }
