"""Analytical maxima and data-quality boundaries for continuous power windows."""
from copy import deepcopy
import json
import math

import pytest

from app.services.activity_effort import parse_streams
from app.services.power_duration import best_power_efforts, DURATIONS


def calculate(times, power, *, extra=(), **options):
    raw=[{'type':'time','data':times},{'type':'watts','data':power},*extra]
    before=deepcopy(raw)
    value=best_power_efforts(parse_streams(raw,**options))
    assert raw==before
    json.dumps(value,allow_nan=False)
    return value


def effort(value,duration):
    return next(row for row in value['durations'] if row['duration_seconds']==duration)


def test_continuous_optimum_inside_two_samples_is_not_replaced_by_an_aligned_window():
    value=calculate([0,4,10],[0,120,0])
    best=effort(value,5)
    assert value['status']=='available'
    assert best['mean_power_w']==90
    assert best['start_s']==2 and best['end_s']==7
    assert best['coverage_pct']==100 and best['covered_seconds']==5
    assert effort(value,15)['status']=='insufficient_duration'


@pytest.mark.parametrize('power',[0,200,1e-308,1e308])
def test_constant_signals_preserve_zero_tiny_and_huge_observations(power):
    value=calculate(list(range(0,3601,5)),[power]*721)
    assert [row['duration_seconds'] for row in value['durations']]==list(DURATIONS)
    for row in value['durations']:
        assert row['status']=='available' and row['mean_power_w']==power
        assert row['start_s']==0 and row['end_s']==row['duration_seconds']


def test_linear_ramp_has_analytical_last_window_mean():
    value=calculate(list(range(61)),[100+stamp*2 for stamp in range(61)])
    assert effort(value,5)['mean_power_w']==215
    assert effort(value,5)['start_s']==55
    assert effort(value,60)['mean_power_w']==160


def test_equal_maxima_choose_the_earliest_interval():
    times=list(range(241));powers=[0,120,0,0]*60+[0]
    value=calculate(times,powers)
    assert effort(value,5)['start_s']==.5
    assert effort(value,5)['mean_power_w']==42


def test_missing_samples_and_long_gaps_split_windows_without_filling_zero():
    times=list(range(91))+list(range(1000,1101))
    value=calculate(times,[100]*91+[200]*101,elapsed_seconds=1100)
    assert value['coverage']['segments_count']==2
    assert value['coverage']['covered_seconds']==190
    assert value['coverage']['coverage_pct']==pytest.approx(190/1100*100,abs=1e-6)
    assert effort(value,60)['mean_power_w']==200
    assert effort(value,60)['start_s']==1000
    assert effort(value,300)['mean_power_w'] is None
    missing=calculate(list(range(31)),[100]*10+[None]+[200]*20)
    assert missing['coverage']['segments_count']==2
    assert effort(missing,30)['status']=='insufficient_duration'


@pytest.mark.parametrize('bad',[None,-1,True,'200',float('inf')])
def test_unusable_power_breaks_a_window(bad):
    value=calculate([0,1,2,3,4,5,6],[200,200,200,bad,200,200,200])
    assert value['status']=='insufficient_data'
    assert all(row['mean_power_w'] is None for row in value['durations'])


@pytest.mark.parametrize('times',[[0,1,1,3,4,5,6],[0,2,1,3,4,5,6],[0,None,2,3,4,5,6]])
def test_ambiguous_time_is_unavailable_instead_of_sorted_or_repaired(times):
    value=calculate(times,[200]*len(times))
    assert value['status']=='unavailable' and 'ambiguous_time' in value['reasons']
    assert all(row['mean_power_w'] is None for row in value['durations'])


def test_duplicate_power_is_explicitly_unavailable_and_missing_power_is_not_applicable():
    value=calculate(list(range(11)),[100]*11,extra=[{'type':'watts','data':[999]*11}])
    assert value['status']=='unavailable' and value['reasons']==['ambiguous_power']
    missing=best_power_efforts(parse_streams([{'type':'time','data':list(range(11))}]))
    assert missing['status']=='not_applicable'


def test_raw_limit_does_not_present_a_prefix_as_a_complete_curve():
    value=calculate(list(range(250001)),[200]*250001)
    assert value['status']=='unavailable' and 'truncated_input' in value['reasons']
    assert all(row['mean_power_w'] is None for row in value['durations'])


def test_future_and_elapsed_limits_are_obeyed_without_using_moving_time():
    value=calculate(list(range(21)),[100]*13+[1000]*8,max_time_s=12,elapsed_seconds=20)
    assert effort(value,5)['mean_power_w']==100 and effort(value,5)['end_s']<=12
    assert effort(value,15)['status']=='insufficient_duration'
    future=calculate(list(range(21)),[1000]*21,max_time_s=-1,elapsed_seconds=20)
    assert future['status']=='unavailable'
    assert all(row['mean_power_w'] is None for row in future['durations'])


def test_separate_segments_use_their_own_numeric_scale():
    value=calculate([0,1,100,105,110],[1e308,1e308,1e-308,1e-308,1e-308])
    assert effort(value,5)['mean_power_w']==1e-308
    assert effort(value,5)['start_s']==100


def test_offset_timestamps_and_fractional_samples_keep_absolute_bounds():
    offset=1000000.25
    value=calculate([offset,offset+4,offset+10],[0,120,0])
    best=effort(value,5)
    assert best['mean_power_w']==90
    assert best['start_s']==offset+2 and best['end_s']==offset+7
    assert math.isfinite(best['mean_power_w'])
