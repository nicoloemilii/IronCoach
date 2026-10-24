"""Differential checks against the retained chronological reference rules."""
from collections import deque
from datetime import datetime, timedelta, timezone
from random import Random
from types import SimpleNamespace

import pytest

from app.services import activity_rating as rating
from app.services.athlete_clock import as_utc_naive
from app.services.rating_history import HistoryContext


NOW = datetime(2026, 10, 22, 12)


def compare(rows, loads, health=None):
    health = health or {}
    ordered = sorted(rows, key=lambda row: (as_utc_naive(row.start_time), row.id))
    context = HistoryContext(ordered, loads, health, rating._component,
                             rating._distribution, rating._recovery, rating._stamp_before)
    history = deque()
    expected = {}
    actual = {}
    # Deliberately retain the former per-row scans, without using HistoryContext.
    for row in ordered:
        stamp = as_utc_naive(row.start_time)
        while history and as_utc_naive(history[0].start_time) < rating._stamp_before(stamp, 42):
            history.popleft()
        prior = [item for item in history if as_utc_naive(item.start_time) < stamp]
        expected[row.id] = rating._rating(row, loads[row.id], prior, loads, health, None, [], NOW)
        if stamp <= NOW:
            history.append(row)
    from itertools import groupby
    for stamp, group in groupby(ordered, key=lambda row: as_utc_naive(row.start_time)):
        group = list(group)
        context.advance(stamp)
        for row in group:
            actual[row.id] = rating._rating(row, loads[row.id], None, loads, health,
                                           None, [], NOW, history_context=context)
        if stamp <= NOW:
            for row in group:
                context.add(row)
    assert actual == expected
    return context


def row(identifier, stamp, duration=1800, sport='cycling'):
    return SimpleNamespace(id=identifier, start_time=stamp, duration_seconds=duration,
                           sport=sport, avg_hr=130, avg_power=None, normalized_power=None)


@pytest.mark.parametrize('seed', range(12))
def test_random_dense_sparse_equal_stamps_nullable_fractional_history(seed):
    random = Random(seed)
    rows = [row(index + 1, NOW - timedelta(hours=random.randrange(-24, 2400)),
                random.choice([None, 0, 1, 1200, 1800.1, 3600.5]),
                random.choice(['Ride', 'cycling', 'running', 'swimming', 'Crossfit', 'Workout']))
            for index in range(280)]
    loads = {item.id: random.choice([None, 0, 0.1, 1 / 3, 20, 40.5, 130.05]) for item in rows}
    health = {kind: [dict(date=str((NOW - timedelta(days=day)).date()),
                         source=source, value=value)
                     for day in range(-1, 132) for source in ['intervals', 'manual']]
              for kind, value in [('tsb', -12), ('sleep_hours', 6.5),
                                  ('hrv_ms', 45), ('resting_hr_bpm', 55)]}
    compare(rows, loads, health)


@pytest.mark.parametrize('ratio', [1.15, 1.3, 1.5, 1.155, 1.305, 1.505])
def test_distribution_rounding_and_score_boundaries_use_legacy_fallback(ratio):
    rows = [row(index + 1, NOW - timedelta(days=days))
            for index, days in enumerate([42, 28, 21, 14, 7, 0])]
    loads = {item.id: .1 for item in rows}
    # Three preceding weeks total .1 each; the current week includes day 7.
    loads[rows[-1].id] = .1 * (ratio - 1)
    context = compare(rows, loads)
    if ratio == 1.3:
        assert context.fallbacks > 0


@pytest.mark.parametrize('magnitude', [1e-300, 1e300])
def test_extreme_finite_values_preserve_legacy_explanations(magnitude):
    rows = [row(index + 1, NOW - timedelta(hours=12 * (120 - index)), magnitude)
            for index in range(121)]
    compare(rows, {item.id: magnitude * (1 + item.id % 3 / 10) for item in rows})


def test_aware_timestamps_and_earliest_supported_dates():
    rows = [row(1, datetime(1, 1, 1)), row(2, datetime(1, 1, 22)),
            row(3, datetime(2026, 10, 22, 14, tzinfo=timezone(timedelta(hours=2)))),
            row(4, NOW), row(5, NOW + timedelta(seconds=1))]
    compare(rows, {item.id: 20 for item in rows})


@pytest.mark.parametrize('duration,load', [(1e-323,None), (1e308,None), (1800,1e308)])
def test_unrepresentable_comparisons_never_crash_or_emit_nonfinite_explanations(duration,load):
    rows=[row(index+1,NOW-timedelta(days=35-index),duration) for index in range(36)]
    loads={item.id:load for item in rows}
    compare(rows,loads)
    result=rating._rating(rows[-1],load,rows[:-1],loads,{},None,[],NOW)
    text=' '.join(result['evidence']).lower()
    assert ' inf ' not in text and 'nan' not in text
    if duration==1e-323:
        assert result['score'] is None


def test_daily_recovery_components_do_not_share_mutable_dictionaries():
    rows=[row(1,NOW),row(2,NOW+timedelta(seconds=1))]
    health={'sleep_hours':[dict(date='2026-10-21',source='intervals',value=8)]}
    context=compare(rows,{1:20,2:20},health)
    first=context.recovery(rows[0]);first['reason']='changed by a consumer'
    assert context.recovery(rows[1])['reason']!='changed by a consumer'
