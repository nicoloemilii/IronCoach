"""Analytical references for raw paired signals, independent of display samples."""
import json
import math

import pytest

from app.services.activity_effort import parse_streams, compare_effort, channel_statistics, number
from app.services.activity_detail import normalize_streams, _load_json, _sample


def raw(times, power, heart, extra=()):
    return [{'type':'time','data':times}, {'type':'watts','data':power},
            {'type':'heartrate','data':heart}, *extra]


def result(times, power, heart, **options):
    parsed = parse_streams(raw(times, power, heart), **options)
    return parsed, compare_effort(parsed, 'VirtualRide')


def test_linear_signals_have_analytical_half_means_and_ratio_change():
    times = list(range(0, 1201, 4))
    parsed, value = result(times, [100+t/6 for t in times], [130+t/60 for t in times], elapsed_seconds=1200)
    first, second = value['halves']
    assert value['status'] == 'available'
    assert [half['power_mean_w'] for half in value['halves']] == [150, 250]
    assert [half['heart_rate_mean_bpm'] for half in value['halves']] == [135, 145]
    assert first['paired_seconds'] == second['paired_seconds'] == 600
    assert first['coverage_pct'] == second['coverage_pct'] == 100
    assert value['change_pct'] == pytest.approx(100*((250/145)/(150/135)-1), abs=.0002)
    assert parsed.quality['gap_limit_seconds'] == 10


def test_irregular_sampling_splits_one_interval_exactly_at_midpoint():
    parsed, value = result([0,4,10], [100,140,200], [100,104,110], elapsed_seconds=10)
    assert [half['power_mean_w'] for half in value['halves']] == [125,175]
    assert [half['heart_rate_mean_bpm'] for half in value['halves']] == [102.5,107.5]
    assert value['change_pct'] is None and 'insufficient_paired_duration' in value['reasons']
    stats = channel_statistics(parsed, parsed.channels['power'])
    assert stats['time_weighted_mean'] == 150
    assert stats['sample_mean'] == pytest.approx(440/3, abs=.000001)


def test_power_zero_is_observed_and_does_not_disappear_from_paired_time():
    times = list(range(1201)); power = [0 if t <= 600 else 100 for t in times]
    _, value = result(times, power, [150]*len(times), elapsed_seconds=1200)
    first = value['halves'][0]
    assert first['power_mean_w'] == first['ratio_w_per_bpm'] == 0
    assert first['power_zero_seconds'] == first['paired_seconds'] == 600
    assert value['change_pct'] is None and 'zero_first_ratio' in value['reasons']


@pytest.mark.parametrize('missing', [None, 0, -1, float('nan'), float('inf'), True, '140'])
def test_unusable_heart_rate_never_creates_valid_paired_coverage(missing):
    _, value = result([0,1,2], [100,100,100], [140,missing,140], elapsed_seconds=2)
    assert all(half['paired_seconds'] == 0 for half in value['halves'])
    assert all(half['power_mean_w'] is None for half in value['halves'])
    assert value['change_pct'] is None


def test_missing_tail_counts_against_elapsed_coverage_but_never_fills_with_zero():
    times = list(range(601))
    _, value = result(times, [200]*len(times), [140]*len(times), elapsed_seconds=1200)
    assert value['window']['source'] == 'elapsed_seconds'
    first, second = value['halves']
    assert first['coverage_pct'] == 100 and second['coverage_pct'] == 0
    assert second['power_mean_w'] is second['heart_rate_mean_bpm'] is None
    assert value['change_pct'] is None


def test_fallback_window_is_labeled_and_leading_time_is_not_hidden():
    _, value = result(list(range(300,1201)), [200]*901, [140]*901)
    assert value['window']['source'] == 'last_timestamp'
    assert value['window']['start_s'] == 0 and value['window']['end_s'] == 1200
    assert value['halves'][0]['coverage_pct'] == 50


def test_long_gap_is_excluded_from_both_signals_and_from_chart_lines():
    times = [0,1,2,900,901,902]
    parsed, value = result(times, [100]*6, [140]*6, elapsed_seconds=902)
    assert parsed.quality['long_gap_count'] == 1
    assert parsed.quality['gap_limit_seconds'] == 3
    assert sum(half['paired_seconds'] for half in value['halves']) == 4
    charts, _, _ = normalize_streams(raw(times,[100]*6,[140]*6), 'cycling')
    assert any(point['value'] is None and 2 < point['time_s'] < 900 for point in charts[0]['points'])


@pytest.mark.parametrize('times', [[0,10,10,20], [0,10,5,20], [0,None,10,20]])
def test_ambiguous_time_is_not_sorted_filled_or_given_a_comparison(times):
    parsed, value = result(times, [100]*4, [140]*4)
    assert parsed.quality['timeline_ambiguous']
    assert value['status'] == 'unavailable' and 'ambiguous_time' in value['reasons']
    assert value['change_pct'] is None
    if times == [0,10,10,20]:
        assert parsed.times == [0,None,None,20]


def test_duplicate_channels_and_malformed_channel_types_fail_closed():
    streams = raw([0,1], [100,100], [140,140], [{'type':'watts','data':[999,999]}, {'type':[],'data':[0]}])
    parsed = parse_streams(streams); value = compare_effort(parsed, 'cycling')
    assert 'power' not in parsed.channels
    assert value['status'] == 'unavailable' and 'ambiguous_power' in value['reasons']
    assert parsed.quality['invalid_channel_type_count'] == 1


def test_raw_truncation_is_distinct_from_reduction_for_display():
    times = list(range(250001)); parsed, value = result(times,[100]*len(times),[140]*len(times))
    assert parsed.quality['truncated'] and parsed.quality['processed_time_samples'] == 250000
    assert value['status'] == 'unavailable' and value['change_pct'] is None
    assert 'truncated_input' in value['reasons']


def test_summary_nulls_huge_numbers_and_deep_json_are_safe():
    assert number(10**1000) is None
    assert number(False) is None and number('100') is None
    assert _load_json('{"x":'+'['*2000+'0'+']'*2000+'}') == {}
    _, value = result([0,1], [10**1000,100], [140,140])
    assert value['change_pct'] is None
    json.dumps(value, allow_nan=False)


def test_future_cutoff_and_short_time_axis_also_constrain_gps():
    streams = raw([0,10,20],[100]*3,[140]*3,[{'type':'latlng','data':[[42,13],[42.1,13.1],[42.2,13.2],[42.3,13.3]]}])
    charts, route, count = normalize_streams(streams,'cycling',max_time_s=15)
    assert count == 2 and [point['time_s'] for point in route] == [0,10]
    assert all(point['time_s'] <= 15 for chart in charts for point in chart['points'])


def test_reduction_preserves_every_omitted_gap_as_a_segment_boundary():
    points = [{'time_s':t,'value':None if t%7==3 else 100+t%5} for t in range(10000)]
    reduced = _sample(points,900)
    assert len(reduced) <= 900
    for previous, current in zip(reduced,reduced[1:]):
        omitted = points[previous['time_s']+1:current['time_s']+1]
        if any(point['value'] is None for point in omitted):
            assert current['value'] is None or current.get('break_before')


def test_display_extrema_cannot_bias_raw_summary_statistics():
    power = [100 if t%100 else 1000 for t in range(10000)]
    charts,_,_ = normalize_streams(raw(list(range(10000)),power,[140]*10000),'cycling')
    chart = charts[0]; display = [p['value'] for p in chart['points'] if p['value'] is not None]
    assert len(display) < 900
    assert chart['statistics']['sample_mean'] == 109
    assert sum(display)/len(display) != pytest.approx(109)
    assert chart['stats_scope'] == 'raw_recording'


def test_non_bike_does_not_receive_an_inferred_power_heart_analysis():
    parsed = parse_streams(raw([0,1],[100,100],[140,140]))
    value = compare_effort(parsed,'running')
    assert value['status'] == 'not_applicable' and value['change_pct'] is None
