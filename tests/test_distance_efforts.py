"""Independent time/distance references and explicit FIT-length edge cases."""
from fractions import Fraction
import json
import math
import random

import pytest

from app.services.activity_effort import parse_streams
from app.services.distance_efforts import DISTANCES, best_distance_efforts, best_pool_efforts


def running(times, distances, **options):
    return best_distance_efforts(parse_streams([
        {'type': 'time', 'data': times}, {'type': 'distance', 'data': distances}], **options))


def oracle(times, distances, target):
    """Exhaustive rational inverse at every possible slope-change event."""
    times, distances = list(map(Fraction, times)), list(map(Fraction, distances))
    target = Fraction(str(target))
    if distances[-1] - distances[0] < target:
        return None

    def inverse(query, *, departure):
        exact = [t for t, d in zip(times, distances) if d == query]
        if exact:
            return max(exact) if departure else min(exact)
        for left in range(len(times) - 1):
            d0, d1 = distances[left:left + 2]
            if d0 < query < d1:
                t0, t1 = times[left:left + 2]
                return t0 + (query - d0) * (t1 - t0) / (d1 - d0)
        raise AssertionError('Reference query outside recorded distance')

    starts = {d for d in distances} | {d - target for d in distances}
    choices = []
    for distance in starts:
        if distances[0] <= distance <= distances[-1] - target:
            start = inverse(distance, departure=True)
            end = inverse(distance + target, departure=False)
            choices.append((end - start, start, end, distance))
    return min(choices)


@pytest.mark.parametrize('seed', range(12))
def test_running_matches_independent_rational_oracle_for_all_standard_distances(seed):
    rng = random.Random(seed)
    times, distances = [0.], [float(seed * 100000)]
    for _ in range(105):
        times.append(times[-1] + rng.choice([1, 2, 4, 8]))
        distances.append(distances[-1] + rng.choice([0., 97.125, 500.25, 800.75, 1000.]))
    result = running(times, distances)
    for row in result['distances']:
        expected = oracle(times, distances, row['distance_m'])
        if expected is None:
            assert row['status'] == 'insufficient_distance'
        else:
            duration, start, end, distance = expected
            assert row['status'] == 'available'
            assert row['elapsed_seconds'] == float(duration)
            assert row['start_s'] == float(start)
            assert row['end_s'] == float(end)
            assert row['start_distance_m'] == float(distance)
    json.dumps(result, allow_nan=False)


def test_steady_recording_contains_all_six_exact_distances_and_earliest_tie():
    times = list(range(0, 15001, 5))
    result = running(times, [3 * t for t in times])
    assert tuple(row['distance_m'] for row in result['distances']) == DISTANCES
    for row in result['distances']:
        assert row['status'] == 'available'
        assert row['start_s'] == 0
        assert row['elapsed_seconds'] == float(Fraction(str(row['distance_m'])) / 3)
        assert row['pace_seconds_per_km'] == float(Fraction(1000, 3))


def test_pause_inside_distance_is_included_but_boundary_plateaus_are_excluded():
    times = list(range(0, 351))
    distance = [0 if t <= 10 else (t - 10) * 4 if t <= 135 else
                500 if t <= 185 else min(1000, 500 + (t - 185) * 4) for t in times]
    result = running(times, distance)
    row = result['distances'][0]
    assert (row['start_s'], row['end_s'], row['elapsed_seconds']) == (10, 310, 300)
    assert result['method']['pauses'] == 'included'


@pytest.mark.parametrize('break_kind', ['gap', 'reset', 'missing'])
def test_running_never_bridges_discontinuous_short_segments(break_kind):
    times, distances = list(range(101)), [6 * t for t in range(101)]
    if break_kind == 'gap':
        times += list(range(300, 401)); distances += [600 + 6 * t for t in range(101)]
    elif break_kind == 'reset':
        times += list(range(101, 202)); distances += [6 * t for t in range(101)]
    else:
        times += list(range(101, 203)); distances += [None] + [600 + 6 * t for t in range(101)]
    result = running(times, distances)
    assert result['status'] == 'insufficient_data'
    assert result['coverage']['segments_count'] == 2
    assert result['coverage']['longest_segment_m'] == 600
    assert all(row['elapsed_seconds'] is None for row in result['distances'])


@pytest.mark.parametrize('times', [[0, 1, 1, 2], [0, 2, 1, 3], [0, None, 2, 3]])
def test_ambiguous_running_time_fails_closed(times):
    result = running(times, [0, 1000, 2000, 3000])
    assert result['status'] == 'unavailable' and 'ambiguous_time' in result['reasons']


def test_missing_and_duplicate_distance_do_not_become_observed_zero():
    for streams, reason in [([{'type': 'time', 'data': [0, 1]}], 'missing_distance'),
        ([{'type': 'time', 'data': [0, 1]}, {'type': 'distance', 'data': [0, 1000]},
          {'type': 'distance', 'data': [0, 2000]}], 'ambiguous_distance')]:
        result = best_distance_efforts(parse_streams(streams))
        assert result['status'] == 'unavailable' and reason in result['reasons']
        assert all(row['elapsed_seconds'] is None for row in result['distances'])


def test_running_future_samples_cannot_supply_a_distance():
    result = running([0, 1, 2], [0, 600, 1200], max_time_s=1)
    assert result['status'] == 'insufficient_data'
    assert result['coverage']['longest_segment_m'] == 600


def length(index, start, duration=20, *, stroke='freestyle', kind='active', **changes):
    return dict(index=index, start_s=start, end_s=start + duration,
                elapsed_s=duration, timer_s=duration, type=kind, stroke=stroke, **changes)


def pool(rows, *, meters=25, unit='metric', **options):
    return best_pool_efforts(dict(schema_version=1, status='available', rows=rows,
        pool_length_m=meters, pool_length_unit=unit, truncated=False), **options)


def available(value):
    return [row for group in value['groups'] for row in group['distances'] if row['status'] == 'available']


def test_pool_uses_fastest_complete_consecutive_lengths_and_earliest_ties():
    rows = [length(0, 0, 30), length(1, 30, 20), length(2, 50, 20), length(3, 70, 30)]
    result = pool(rows)
    short, long = available(result)
    assert (short['distance_m'], short['elapsed_seconds'], short['start_s'], short['end_s']) == (50, 40, 30, 70)
    assert (short['first_length_index'], short['last_length_index']) == (1, 2)
    assert long['elapsed_seconds'] == 100
    tie = pool([length(i, i * 20) for i in range(4)])
    assert available(tie)[0]['start_s'] == 0


@pytest.mark.parametrize('unit,meters,target,distance', [('metric', 25, 50, 50), ('statute', 22.86, 50, 45.72)])
def test_pool_yards_and_meters_preserve_distinct_targets(unit, meters, target, distance):
    row = available(pool([length(0, 0), length(1, 20)], meters=meters, unit=unit))[0]
    assert (row['distance_value'], row['distance_m']) == (target, distance)
    assert row['distance_unit'] == ('yd' if unit == 'statute' else 'm')
    assert row['elapsed_seconds'] == 40


def test_pool_requires_whole_lengths_and_keeps_strokes_separate():
    rows = [length(0, 0), length(1, 20), length(2, 40, stroke='breaststroke'),
            length(3, 60, stroke='breaststroke')]
    value = pool(rows)
    assert {group['stroke'] for group in value['groups']} == {'freestyle', 'breaststroke'}
    assert len(available(value)) == 2
    assert {row['distance_m'] for row in available(value)} == {50}
    incompatible = pool(rows, meters=33)
    assert not available(incompatible)
    assert all(row['status'] == 'incompatible_pool_length' for group in incompatible['groups'] for row in group['distances'])


@pytest.mark.parametrize('break_kind', ['idle', 'pause', 'gap', 'index_gap', 'invalid'])
def test_pool_breaks_on_explicit_pause_rest_or_missing_continuity(break_kind):
    rows = [length(0, 0), length(1, 20), length(2, 40), length(3, 60)]
    if break_kind == 'idle': rows[1]['type'] = 'idle'
    if break_kind == 'pause': rows[1]['timer_s'] = 12
    if break_kind == 'gap': rows[2].update(start_s=41, end_s=60, elapsed_s=19, timer_s=19)
    if break_kind == 'index_gap':
        rows[2]['index'] = 3; rows[3]['index'] = 4
    if break_kind == 'invalid': rows[1]['elapsed_s'] = None
    result = pool(rows)
    assert result['method']['pauses'] == 'split'
    assert all(row['distance_m'] == 50 for row in available(result))
    assert result['coverage']['segments_count'] == 2


@pytest.mark.parametrize('rows,reason', [
    ([length(0, 0), length(1, 10), length(2, 30)], 'overlapping_pool_lengths'),
    ([length(0, 0), length(0, 20)], 'ambiguous_pool_order'),
    ([length(2, 0), None, length(1, 40), length(3, 60)], 'ambiguous_pool_order'),
    ([length(0, 20), None, length(1, 0), length(2, 20)], 'overlapping_pool_lengths')])
def test_overlaps_and_ambiguous_pool_order_do_not_create_standalone_records(rows, reason):
    result = pool(rows, meters=50)
    assert result['status'] == 'unavailable' and result['reasons'] == [reason]
    assert not available(result)


@pytest.mark.parametrize('unit', [None, True, False, 'nautical', 'unknown'])
def test_missing_or_ambiguous_pool_units_are_not_assumed_metric(unit):
    result = pool([length(0, 0), length(1, 20)], unit=unit)
    assert result['status'] == 'unavailable' and result['reasons'] == ['unknown_pool_unit']


def test_pool_future_boundary_keeps_only_complete_lengths():
    rows = [length(i, i * 20) for i in range(4)]
    result = pool(rows, max_time_s=70)
    assert result['coverage']['clipped_lengths'] == 1
    assert 'future_lengths_excluded' in result['reasons']
    assert [row['distance_m'] for row in available(result)] == [50]
    assert all(row['end_s'] <= 70 for row in available(result))
    assert available(pool(rows, max_time_s=-1)) == []
    assert pool(rows, max_time_s=float('nan'))['status'] == 'unavailable'


def test_pool_missing_lengths_or_elapsed_never_derive_from_summary_or_timer():
    assert best_pool_efforts(None)['status'] == 'not_applicable'
    rows = [length(0, 0), length(1, 20)]
    rows[0]['elapsed_s'] = None
    result = pool(rows)
    assert available(result) == []
    assert result['coverage']['invalid_lengths'] == 1


def test_pool_missing_timer_still_uses_explicit_elapsed_with_no_inferred_repair():
    rows = [length(0, 0), length(1, 20)]
    for row in rows: row['timer_s'] = None
    assert available(pool(rows))[0]['elapsed_seconds'] == 40
    rows[1]['timer_s'] = -1
    assert available(pool(rows)) == []


def test_pool_truncation_is_not_claimed_complete():
    value = best_pool_efforts(dict(schema_version=1, status='available',
        rows=[length(0, 0), length(1, 20)], pool_length_m=25,
        pool_length_unit='metric', truncated=True))
    assert value['status'] == 'unavailable' and value['reasons'] == ['truncated_input']
