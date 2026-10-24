"""Independent rational oracle for piecewise-linear power windows.

Private QA only. No IronCoach imports, database, provider, or mutable state.
The production implementation uses normalized prefix integrals; this oracle
uses exact Fraction arithmetic and integrates every overlapping segment anew.
"""
from bisect import bisect_right
from fractions import Fraction
import json
import math
from pathlib import Path
import random


def rational(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
        raise ValueError("not a finite numeric sample")
    if isinstance(value, Fraction):
        return value
    if not math.isfinite(value):
        raise ValueError("not a finite numeric sample")
    # Match the recorded IEEE float exactly; never round it to decimal first.
    return Fraction(value)


def value_at(times, powers, stamp):
    stamp = rational(stamp)
    if stamp < times[0] or stamp > times[-1]:
        raise ValueError("outside valid run")
    index = min(len(times) - 2, bisect_right(times, stamp) - 1)
    left, right = times[index:index + 2]
    a, b = powers[index:index + 2]
    return a + (b - a) * (stamp - left) / (right - left)


def direct_integral(times, powers, start, end):
    """Exact trapezoid integration, deliberately without a prefix accumulator."""
    total = Fraction(0)
    for index, (left, right) in enumerate(zip(times, times[1:])):
        lo, hi = max(start, left), min(end, right)
        if hi <= lo:
            continue
        a, b = powers[index:index + 2]
        slope = (b - a) / (right - left)
        p_lo, p_hi = a + slope * (lo - left), a + slope * (hi - left)
        total += (p_lo + p_hi) * (hi - lo) / 2
    return total


def continuous_best(times, powers, duration):
    """Exact optimum on one valid run, including derivative interior roots."""
    times = [rational(x) for x in times]
    powers = [rational(x) for x in powers]
    duration = rational(duration)
    if len(times) != len(powers) or len(times) < 2 or duration <= 0:
        raise ValueError("invalid oracle input")
    if any(x < 0 for x in powers) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("invalid oracle run")
    lower, upper = times[0], times[-1] - duration
    if upper < lower:
        return None
    events = sorted({lower, upper} | {
        x for t in times for x in (t, t - duration) if lower <= x <= upper
    })
    candidates = set(events)
    for left, right in zip(events, events[1:]):
        d_left = value_at(times, powers, left + duration) - value_at(times, powers, left)
        d_right = value_at(times, powers, right + duration) - value_at(times, powers, right)
        # A positive-to-negative crossing is a strict local maximum.
        if d_left > 0 and d_right < 0:
            candidates.add(left + d_left * (right - left) / (d_left - d_right))
    ranked = [(direct_integral(times, powers, start, start + duration) / duration, start)
              for start in candidates]
    mean, start = max(ranked, key=lambda row: (row[0], -row[1]))
    event_max = max(direct_integral(times, powers, start, start + duration) / duration for start in events)
    return dict(mean=mean, start=start, end=start + duration,
                event_max=event_max, candidate_count=len(candidates))


def grid_best(times, powers, duration, steps=500):
    times = [rational(x) for x in times]
    powers = [rational(x) for x in powers]
    duration = rational(duration)
    lower, upper = times[0], times[-1] - duration
    if upper < lower:
        return None
    return max(direct_integral(times, powers, lower + (upper - lower) * i / steps,
                               lower + (upper - lower) * i / steps + duration) / duration
               for i in range(steps + 1))


def valid_runs(times, powers, max_gap):
    """Independent segmentation: raw adjacency, nulls and long gaps split runs."""
    result, run_t, run_p = [], [], []
    for index in range(len(times) - 1):
        row = (times[index], times[index + 1], powers[index], powers[index + 1])
        try:
            left, right, a, b = map(rational, row)
            usable = 0 < right - left <= rational(max_gap) and a >= 0 and b >= 0
        except (ValueError, TypeError, OverflowError):
            usable = False
        if usable:
            if run_t and run_t[-1] == left:
                run_t.append(right); run_p.append(b)
            else:
                if run_t:
                    result.append((run_t, run_p))
                run_t, run_p = [left, right], [a, b]
        else:
            if run_t:
                result.append((run_t, run_p))
            run_t, run_p = [], []
    if run_t:
        result.append((run_t, run_p))
    return result


def best_over_runs(runs, duration):
    results = [continuous_best(times, powers, duration) for times, powers in runs]
    results = [row for row in results if row is not None]
    return max(results, key=lambda row: (row['mean'], -row['start'])) if results else None


def self_check():
    analytic = continuous_best([0, 4, 10], [0, 120, 0], 5)
    assert analytic['mean'] == 90 and analytic['start'] == 2
    assert analytic['event_max'] == 70, analytic
    # Zero is a measured sample, and both zeros and uneven cadence are retained.
    zero = continuous_best([0, 1, 4, 8], [0, 0, 0, 0], 5)
    assert zero['mean'] == 0 and zero['start'] == 0
    constant = continuous_best([0, 1, 4, 8], [200, 200, 200, 200], 5)
    assert constant['mean'] == 200 and constant['start'] == 0
    # Missing endpoints and long gaps never join two otherwise short runs.
    assert best_over_runs(valid_runs([0, 4, 8, 12], [200, None, 200, 200], 10), 5) is None
    assert best_over_runs(valid_runs([0, 4, 20, 24], [200, 200, 200, 200], 10), 5) is None
    assert best_over_runs(valid_runs([0, 4, 8, 12], [200, 0, 200, 200], 10), 5) is not None
    rng = random.Random(20261022)
    random_cases, interior_cases, strict_grid_cases = 0, 0, 0
    maximum_grid_deficit = Fraction(0)
    for _ in range(120):
        times = [0]
        for _ in range(rng.randint(4, 11)):
            times.append(times[-1] + rng.randint(1, 8))
        powers = [rng.randint(0, 800) for _ in times]
        duration = rng.randint(1, times[-1])
        exact = continuous_best(times, powers, duration)
        grid = grid_best(times, powers, duration, steps=180)
        assert grid <= exact['mean'], (times, powers, duration, exact, grid)
        # A lower-bound oracle can be close, but may never exceed the exact optimum.
        assert direct_integral(list(map(rational, times)), list(map(rational, powers)),
                               exact['start'], exact['end']) / duration == exact['mean']
        random_cases += 1
        interior_cases += exact['mean'] > exact['event_max']
        strict_grid_cases += grid < exact['mean']
        maximum_grid_deficit = max(maximum_grid_deficit, exact['mean'] - grid)
    return dict(status='passed', oracle='exact rational direct trapezoids; no production imports',
                analytic=dict(mean_w=float(analytic['mean']), start_s=float(analytic['start']),
                              sample_or_event_aligned_max_w=float(analytic['event_max'])),
                random_cases=random_cases, strict_interior_optimum_cases=interior_cases,
                grid_strict_lower_bound_cases=strict_grid_cases,
                max_fine_grid_deficit_w=float(maximum_grid_deficit))


if __name__ == '__main__':
    result = self_check()
    Path(__file__).with_name('oracle_selfcheck.json').write_text(
        json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(result, allow_nan=False))
