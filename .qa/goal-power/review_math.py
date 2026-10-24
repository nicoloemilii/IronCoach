"""Read-only production/oracle differential QA on synthetic raw samples."""
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
os.environ['IRON_DATA_DIR'] = str(HERE / 'review-math-unused')
os.environ['IRON_AUTO_SYNC'] = '0'
sys.path.insert(0, str(ROOT))
from oracle import best_over_runs, valid_runs
from app.services.activity_effort import parse_streams
from app.services.power_duration import best_power_efforts

DURATIONS = [5, 15, 30, 60, 300, 600, 1200, 3600]
SOURCES = ['app/services/power_duration.py', 'app/services/activity_effort.py', 'app/services/activity_detail.py']


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def raw(times, powers):
    return [{'type': 'time', 'data': times}, {'type': 'watts', 'data': powers}]


def close(actual, expected, label):
    assert isinstance(actual, (float, int)) and not isinstance(actual, bool), (label, actual, expected)
    assert math.isfinite(actual), (label, actual)
    expected_float = float(expected)
    absolute_tolerance = 0.0 if 0 < abs(expected_float) < 1e-5 else 1.1e-5
    assert math.isclose(actual, expected_float, abs_tol=absolute_tolerance, rel_tol=1e-10), (label, actual, str(expected))


def check_oracle(name, times, powers, *, max_time_s=None, elapsed_seconds=None):
    parsed = parse_streams(raw(times, powers), max_time_s=max_time_s, elapsed_seconds=elapsed_seconds)
    observed = best_power_efforts(parsed)
    assert [row['duration_seconds'] for row in observed['durations']] == DURATIONS, name
    # Test oracle segmentation uses raw adjacency and the published shared gap policy.
    runs = valid_runs(parsed.times, parsed.channels['power'], parsed.quality['gap_limit_seconds'] or 0)
    count = 0
    for row in observed['durations']:
        expected = best_over_runs(runs, row['duration_seconds'])
        if expected is None:
            assert row['status'] == 'insufficient_duration', (name, row)
            assert row['mean_power_w'] is None and row['start_s'] is None and row['end_s'] is None, (name, row)
            continue
        assert row['status'] == 'available', (name, row, expected)
        close(row['mean_power_w'], expected['mean'], name + ':mean')
        close(row['start_s'], expected['start'], name + ':start')
        close(row['end_s'], expected['end'], name + ':end')
        close(row['covered_seconds'], row['duration_seconds'], name + ':covered')
        close(row['coverage_pct'], 100, name + ':coverage')
        assert row['segment_start_s'] <= row['start_s'] <= row['end_s'] <= row['segment_end_s'], (name, row)
        count += 1
    json.dumps(observed, allow_nan=False)
    return dict(name=name, available_compared=count, result=observed)


def main():
    before = hashes(); started = time.perf_counter(); cases = []
    cases.append(check_oracle('interior_root_90W_at_2s', [0, 4, 10], [0, 120, 0]))
    cases.append(check_oracle('zero_is_observed', [0, 1, 3, 7, 12, 20], [0] * 6))
    cases.append(check_oracle('constant_earliest_tie', [0, 1, 3, 7, 12, 20], [200] * 6))
    cases.append(check_oracle('exact_5_second_run', [0, 2, 5], [200, 300, 200]))
    cases.append(check_oracle('null_gap_never_joined', [0, 4, 8, 12], [200, None, 200, 200]))
    cases.append(check_oracle('long_gap_never_joined', [0, 4, 20, 24], [200] * 4))
    cases.append(check_oracle('zero_inside_run_kept', [0, 4, 8, 12], [200, 0, 200, 200]))
    cases.append(check_oracle('now_clips_future_samples', list(range(21)), [100] * 13 + [1000] * 8,
                              max_time_s=12, elapsed_seconds=20))
    cases.append(check_oracle('elapsed_end_clips_samples', list(range(21)), [100] * 13 + [1000] * 8,
                              elapsed_seconds=12))
    cases.append(check_oracle('observed_end_not_moving_duration', [0, 4, 10], [0, 120, 0]))
    # Isolated short high-power run cannot erase the eligible tiny-power run.
    cases.append(check_oracle('per_run_scaling_extreme', [0, 1, 100, 105, 110],
                              [1e308, 1e308, 1e-308, 1e-308, 1e-308]))
    # Every supported duration has a full continuous candidate; measured zero still counts.
    long_times = list(range(0, 3601, 5))
    constant = best_power_efforts(parse_streams(raw(long_times, [0] * len(long_times))))
    assert all(row['status'] == 'available' and row['mean_power_w'] == 0 and row['start_s'] == 0
               for row in constant['durations']), constant
    cases.append(dict(name='all_durations_zero_3600s', available_compared=8, result=constant))
    rng = random.Random(2601022)
    for index in range(85):
        times = [0]
        for _ in range(rng.randint(5, 17)):
            times.append(times[-1] + rng.choice([1, 2, 3, 4, 7, 13]))
        powers = [rng.randint(0, 800) for _ in times]
        if index % 4 == 0:
            powers[rng.randrange(len(powers))] = None
        cases.append(check_oracle('random_%03d' % index, times, powers))
    # Non-binary decimal cadences exercise merged/shifted event advancement.
    for index in range(80):
        times = [0.0]
        for _ in range(rng.randint(8, 19)):
            times.append(times[-1] + rng.choice([.1, .2, .7, 1.3, 2.1, 4.9, 7.7]))
        powers = [rng.random() * 800 for _ in times]
        cases.append(check_oracle('fractional_%03d' % index, times, powers))

    gates = []
    def gate(name, streams, expected, **kwargs):
        result = best_power_efforts(parse_streams(streams, **kwargs))
        assert result['status'] == expected, (name, result)
        assert all(row['status'] != 'available' for row in result['durations']), (name, result)
        json.dumps(result, allow_nan=False)
        gates.append(dict(name=name, status=result['status'], reasons=result['reasons']))
    gate('missing_power', [{'type':'time', 'data': [0, 5, 10]}], 'not_applicable')
    gate('duplicate_power', raw([0, 5, 10], [200] * 3) + [{'type': 'watts', 'data': [999] * 3}], 'unavailable')
    gate('duplicate_time', raw([0, 5, 5, 10], [200] * 4), 'unavailable')
    gate('backward_time', raw([0, 5, 4, 10], [200] * 4), 'unavailable')
    gate('invalid_timestamp', raw([0, 5, None, 10], [200] * 4), 'unavailable')
    gate('nonfinite_timestamp', raw([0, 5, float('inf'), 10], [200] * 4), 'unavailable')
    gate('huge_timestamp', raw([0, 5, 10**400, 10], [200] * 4), 'unavailable')
    gate('truncated_raw', raw(list(range(250001)), [200] * 250001), 'unavailable')
    # A duplicate unrelated channel cannot invalidate otherwise clean power.
    duplicate_hr = best_power_efforts(parse_streams(raw([0, 5, 10], [200] * 3) +
        [{'type':'heartrate', 'data':[120] * 3}, {'type':'heartrate', 'data':[130] * 3}]))
    assert duplicate_hr['status'] == 'available', duplicate_hr
    for value in [None, True, -1, float('nan'), float('inf'), 10**400]:
        result = best_power_efforts(parse_streams(raw([0, 4, 8, 12], [200, value, 200, 200])))
        assert not any(row['status'] == 'available' for row in result['durations']), (value, result)
        json.dumps(result, allow_nan=False)

    # Prefix cancellation/earliest tie probe: 6,000 identical triangles.
    # Every 5s optimum is exactly 121.5 W, earliest at 2s, despite a long prefix.
    times, powers = [], []
    for cycle in range(6000):
        times.extend([cycle * 10, cycle * 10 + 4])
        powers.extend([33, 151])
    times.append(60000); powers.append(33)
    stress_started = time.perf_counter()
    repeated = best_power_efforts(parse_streams(raw(times, powers)))
    stress_seconds = time.perf_counter() - stress_started
    first = repeated['durations'][0]
    close(first['mean_power_w'], 121.5, 'repeated_equal_peaks:mean')
    close(first['start_s'], 2, 'repeated_equal_peaks:earliest_start')
    assert hashes() == before, 'source changed during independent run'
    result = dict(status='passed', production_sha256=before, source_unchanged=True,
        cases=cases, gates=gates, comparison_count=sum(case['available_compared'] for case in cases),
        repeated_equal_peaks=dict(raw_points=len(times), mean_w=first['mean_power_w'],
            start_s=first['start_s'], duration_s=first['duration_seconds'], seconds=stress_seconds),
        elapsed_seconds=time.perf_counter() - started,
        boundary='Synthetic pure calls only; no database, provider, or visual validation')
    (HERE / 'review_math.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key not in ('cases',)}, allow_nan=False))


if __name__ == '__main__':
    main()
