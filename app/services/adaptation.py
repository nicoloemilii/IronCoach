from __future__ import annotations

from statistics import mean
from collections import defaultdict


def adaptation_context(adaptations, sport: str | None = None, limit: int = 8) -> dict:
    rows = [
        a for a in adaptations
        if sport is None or a.sport == sport
    ]

    rows = sorted(
        rows,
        key=lambda x: x.created_at or 0,
        reverse=True
    )[:limit]

    if not rows:
        return {
            "action": "none",
            "confidence": 0.0,
            "sample_size": 0,
            "trend": "none",
        }

    counts = defaultdict(int)

    for row in rows:
        counts[row.adaptation] += 1

    action = max(counts, key=counts.get)

    # Consideriamo solo i segnali che supportano l'azione dominante.
    action_rows = [
        row for row in rows
        if row.adaptation == action
    ]

    action_confidences = [
        float(row.confidence or 0)
        for row in action_rows
    ]

    repeated = counts[action] >= 2

    confidence = mean(action_confidences)

    # Un singolo segnale non può produrre un adattamento persistente.
    if not repeated:
        confidence *= 0.5

    confidence = round(confidence, 1)

    trend = "persistent" if repeated else "single_signal"

    return {
        "action": action,
        "confidence": confidence,
        "sample_size": len(action_rows),
        "trend": trend,
    }