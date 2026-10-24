"""Independent differential audit: indexed history versus retained legacy path.

Pure synthetic objects, no database connections or provider access.
"""
from datetime import datetime, timedelta
import hashlib
from itertools import groupby
import json
import os
from pathlib import Path
import random
import re
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent
os.environ['IRON_DATA_DIR'] = str(QA / 'isolated-data')
os.environ['IRON_AUTO_SYNC'] = '0'
sys.path.insert(0, str(ROOT))
from app.services import activity_rating as rating
from app.services.rating_history import HistoryContext

NOW = datetime(2026, 11, 10, 12)


def row(identifier, stamp, duration, sport='cycling'):
    return SimpleNamespace(id=identifier, sport=sport, start_time=stamp,
        duration_seconds=duration, avg_hr=130, avg_power=None, normalized_power=None)


def compare(rows, loads, health):
    rows.sort(key=lambda item: (item.start_time, item.id))
    context = HistoryContext(rows, loads, health, rating._component, rating._distribution,
                             rating._recovery, rating._stamp_before)
    count = 0
    for stamp, values in groupby(rows, key=lambda item: item.start_time):
        values = list(values); context.advance(stamp)
        for activity in values:
            original = rating._rating(activity, loads[activity.id], list(context.active), loads,
                health, None, [], NOW)
            indexed = rating._rating(activity, loads[activity.id], None, loads,
                health, None, [], NOW, history_context=context)
            assert original == indexed, {'id': activity.id, 'stamp': str(stamp),
                                          'legacy': original, 'indexed': indexed}
            count += 1
        if stamp <= NOW:
            for activity in values:
                context.add(activity)
    return count, context.fallbacks


def health_points():
    health = {kind: [] for kind in ('tsb', 'sleep_hours', 'hrv_ms', 'resting_hr_bpm')}
    for ago in range(120):
        day = str((NOW - timedelta(days=ago)).date())
        readings = [('tsb', -12 if ago % 5 == 0 else 0), ('sleep_hours', 5.5 if ago % 7 == 0 else 8),
                    ('hrv_ms', 55 if ago % 9 == 0 else 70), ('resting_hr_bpm', 58 if ago % 11 == 0 else 50)]
        for kind, value in readings:
            for source in ('garmin', 'intervals', 'manual'):
                health[kind].append({'date': day, 'source': source, 'value': value})
    for values in health.values(): values.sort(key=lambda item: (item['date'], item['source']))
    return health


def audit():
    results = []; total = 0; fallbacks = 0
    for seed in range(20):
        rng = random.Random(seed); rows = []; loads = {}
        for index in range(350):
            stamp = NOW - timedelta(hours=rng.randrange(-48, 70 * 24) // 6 * 6)
            rows.append(row(index + 1, stamp, rng.choice([None, 0, -1, 600, 1800, 3600, 5400, 3600.1, 600.3]),
                            rng.choice(['cycling', 'VirtualRide', 'running', 'Crossfit', 'swimming', 'unknown'])))
            loads[index + 1] = rng.choice([None, 0, 10, 30, 50, 65, .1, .3, 30.1, 50.2])
        count, used = compare(rows, loads, health_points()); total += count; fallbacks += used
    results.append({'matrix': 'mixed_missing_zero_ties_future_dst_health_sources', 'ratings': total, 'fallbacks': fallbacks})
    for mode in ('load', 'duration', 'large', 'decimal'):
        mode_count = 0; mode_fallbacks = 0
        for seed in range(8):
            rng = random.Random(seed + 300); rows = []; loads = {}
            for index in range(300):
                stamp = NOW - timedelta(minutes=rng.randrange(0, 70 * 24 * 60) // 30 * 30)
                duration = rng.choice([600, 1800, 3600, 600.1, 3600.3, 5400.5])
                load = rng.choice([10.1, 20.3, 30.5, 50.7, 65.2])
                if mode == 'duration': load = None
                if mode == 'large': load = rng.choice([1e15, 1e15 + .5, 2e15 + .25, 3e15 + 1.5])
                if mode == 'decimal': load = rng.choice([.1, .2, .3, .4, .5, .6, .7])
                rows.append(row(index + 1, stamp, duration, rng.choice(['cycling', 'VirtualRide', 'running'])))
                loads[index + 1] = load
            count, used = compare(rows, loads, {}); mode_count += count; mode_fallbacks += used
        total += mode_count; fallbacks += mode_fallbacks
        results.append({'matrix': mode, 'ratings': mode_count, 'fallbacks': mode_fallbacks})
    return {'ratings_compared': total, 'differences': 0, 'fallbacks': fallbacks, 'matrices': results}


def repair_checks():
    output = {}
    for label, duration in [('tiny_duration', 1e-323), ('large_duration', 1e308)]:
        rows = [row(index + 1, NOW - timedelta(days=30 - index), duration) for index in range(30)]
        loads = {activity.id: None for activity in rows}
        context = HistoryContext(rows, loads, {}, rating._component, rating._distribution,
                                 rating._recovery, rating._stamp_before)
        for activity in rows[:-1]: context.add(activity)
        legacy = rating._rating(rows[-1], None, list(context.active), loads, {}, None, [], NOW)
        indexed = rating._rating(rows[-1], None, None, loads, {}, None, [], NOW, history_context=context)
        assert legacy == indexed
        serialized = json.dumps(indexed, allow_nan=False)
        assert not re.search(r'\b(?:nan|inf|infinity)\b', serialized, flags=re.IGNORECASE)
        output[label] = {'no_exception': True, 'legacy_indexed_equal': True,
                         'finite_json_and_text': True, 'score': indexed['score']}
    rows = [row(1, NOW - timedelta(hours=2), 3600), row(2, NOW - timedelta(hours=1), 3600)]
    health = {'sleep_hours': [{'date': str((NOW - timedelta(days=1)).date()), 'source': 'intervals', 'value': 8}]}
    context = HistoryContext(rows, {1: 50, 2: 50}, health, rating._component, rating._distribution,
                             rating._recovery, rating._stamp_before)
    first, second = context.recovery(rows[0]), context.recovery(rows[1])
    assert first == second and first is not second
    first['score'] = 0
    assert second['score'] == 85
    output['recovery_results_independent'] = True
    return output


if __name__ == '__main__':
    paths = [ROOT / name for name in ('app/services/rating_history.py', 'app/services/activity_rating.py')]
    hashes = {str(path.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    started = time.perf_counter(); result = audit(); result['repair_checks'] = repair_checks()
    result.update(seconds=round(time.perf_counter() - started, 3), source_sha256=hashes,
                  personal_database_opened=False, provider_access=False,
                  source_stable=all(hashes[str(path.relative_to(ROOT)).replace('\\', '/')] == hashlib.sha256(path.read_bytes()).hexdigest() for path in paths))
    (QA / 'rating_review_final.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    assert result['source_stable']
