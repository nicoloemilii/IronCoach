"""Bounded, descriptive calculations on recorded samples before chart reduction.

No database, provider calls, physiological assessment or persisted metrics.
The gap and coverage gates are transparent data-quality policy choices.
"""
from collections import Counter
from dataclasses import dataclass
import math

from app.services.training_load import sport_key

MAX_RAW_POINTS = 250_000
MAX_STREAM_ROWS = 64
STREAMS = {
    'watts': ('power', 'Potenza', 'W'),
    'heartrate': ('heart_rate', 'Frequenza cardiaca', 'bpm'),
    'cadence': ('cadence', 'Cadenza', '/min'),
    'velocity_smooth': ('speed', 'Velocità', 'm/s'),
    'altitude': ('altitude', 'Altitudine', 'm'),
    'distance': ('distance', 'Distanza', 'm'),
    'temp': ('temperature', 'Temperatura', '°C'),
}


def number(value, *, nonnegative=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and (not nonnegative or value >= 0) else None


def _rounded(value):
    value = number(value, nonnegative=False)
    if value is None:
        return None
    rounded = round(value, 6)
    return value if rounded == 0 and value != 0 else rounded


def _median(values):
    if not values:
        return None
    values = sorted(values); midpoint = len(values) // 2
    return values[midpoint] if len(values) % 2 else values[midpoint - 1] / 2 + values[midpoint] / 2


@dataclass
class ParsedStreams:
    times: list
    channels: dict
    gps: dict | None
    has_time_channel: bool
    quality: dict
    window: dict


def parse_streams(raw, *, max_time_s=None, elapsed_seconds=None):
    """Keep raw index alignment; reject ambiguity instead of choosing a value."""
    rows = {}; counts = Counter(); invalid_types = 0
    truncated = isinstance(raw, list) and len(raw) > MAX_STREAM_ROWS
    for row in raw[:MAX_STREAM_ROWS] if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        kind = row.get('type')
        if not isinstance(kind, str):
            invalid_types += 1
            continue
        if kind not in ('time', 'latlng') and kind not in STREAMS:
            continue
        counts[kind] += 1
        if isinstance(row.get('data'), list):
            rows[kind] = row
            truncated = truncated or len(row['data']) > MAX_RAW_POINTS
            if kind == 'latlng' and isinstance(row.get('data2'), list):
                truncated = truncated or len(row['data2']) > MAX_RAW_POINTS
    duplicates = sorted(kind for kind, count in counts.items() if count > 1)
    for kind in duplicates:
        rows.pop(kind, None)
    raw_times = rows.get('time', {}).get('data', [])
    cutoff = number(max_time_s, nonnegative=False) if max_time_s is not None else None
    known_elapsed = number(elapsed_seconds)
    limit = known_elapsed
    if cutoff is not None:
        limit = min(limit, cutoff) if limit is not None else cutoff
    numeric = []; invalid = 0; clipped = 0
    for value in raw_times[:MAX_RAW_POINTS]:
        stamp = number(value)
        if stamp is None:
            invalid += 1
        elif limit is not None and stamp > limit:
            clipped += 1; stamp = None
        numeric.append(stamp)
    repeated = Counter(value for value in numeric if value is not None)
    duplicate_groups = sum(count > 1 for count in repeated.values())
    duplicate_samples = sum(count for count in repeated.values() if count > 1)
    times = []; high_water = -1; out_of_order = 0
    for stamp in numeric:
        if stamp is None:
            times.append(None)
            continue
        backwards = stamp < high_water
        if backwards:
            out_of_order += 1
        high_water = max(high_water, stamp)
        times.append(None if backwards or repeated[stamp] > 1 else stamp)
    intervals = [right - left for left, right in zip(times, times[1:])
                 if left is not None and right is not None and right > left]
    median_interval = _median(intervals)
    gap_limit = min(10., median_interval * 3) if median_interval is not None else None
    observed_end = max((stamp for stamp in numeric if stamp is not None), default=0.)
    window_end = min(known_elapsed, max(0., cutoff)) if known_elapsed is not None and cutoff is not None else known_elapsed if known_elapsed is not None else observed_end
    window = dict(start_s=0., end_s=window_end, midpoint_s=window_end / 2,
                  source='elapsed_seconds' if known_elapsed is not None else 'last_timestamp',
                  coverage_scope='activity_elapsed' if known_elapsed is not None else 'observed_time_axis')
    channels = {}
    for remote_kind, (kind, _, _) in STREAMS.items():
        if remote_kind not in rows:
            continue
        data = rows[remote_kind]['data']
        channels[kind] = [number(data[index], nonnegative=kind not in ('altitude', 'temperature'))
                          if index < len(data) else None for index in range(len(times))]
    quality = dict(raw_time_samples=len(raw_times), processed_time_samples=len(times),
                   valid_time_samples=sum(stamp is not None for stamp in times),
                   invalid_timestamp_count=invalid, out_of_order_count=out_of_order,
                   duplicate_timestamp_count=duplicate_samples, duplicate_timestamp_groups=duplicate_groups,
                   clipped_timestamp_count=clipped, duplicate_channel_types=duplicates,
                   invalid_channel_type_count=invalid_types, truncated=bool(truncated),
                   raw_point_limit=MAX_RAW_POINTS, stream_row_limit=MAX_STREAM_ROWS,
                   median_sample_interval_seconds=_rounded(median_interval), gap_limit_seconds=_rounded(gap_limit),
                   long_gap_count=sum(delta > gap_limit for delta in intervals) if gap_limit is not None else 0,
                   timeline_ambiguous=bool(invalid or out_of_order or duplicate_groups or 'time' in duplicates))
    return ParsedStreams(times, channels, rows.get('latlng'), bool(counts['time']), quality, window)


def adjacent_intervals(parsed):
    gap_limit = parsed.quality['gap_limit_seconds']
    if gap_limit is None:
        return
    for index in range(len(parsed.times) - 1):
        left, right = parsed.times[index:index + 2]
        if left is not None and right is not None and 0 < right - left <= gap_limit:
            yield index, left, right


class _Average:
    def __init__(self):
        self.mean = None; self.weight = 0.

    def add(self, value, weight):
        if weight <= 0:
            return
        total = self.weight + weight
        if not math.isfinite(total):
            return
        if self.mean is None:
            self.mean = value
        else:
            self.mean = self.mean * (self.weight / total) + value * (weight / total)
        self.weight = total


def channel_statistics(parsed, values):
    samples = [(stamp, values[index]) for index, stamp in enumerate(parsed.times) if stamp is not None]
    sample_average = _Average(); weighted = _Average(); minimum = None; maximum = None
    for _, value in samples:
        if value is not None:
            sample_average.add(value, 1.)
            minimum = value if minimum is None else min(minimum, value)
            maximum = value if maximum is None else max(maximum, value)
    for index, left, right in adjacent_intervals(parsed):
        first, second = values[index:index + 2]
        if first is not None and second is not None:
            weighted.add(first / 2 + second / 2, right - left)
    end = parsed.window['end_s']
    return dict(sample_mean=_rounded(sample_average.mean), min=minimum, max=maximum,
                valid_samples=int(sample_average.weight), sample_count=len(samples),
                missing_samples=len(samples) - int(sample_average.weight),
                time_weighted_mean=_rounded(weighted.mean), covered_seconds=_rounded(weighted.weight),
                coverage_pct=_rounded(min(100., weighted.weight / end * 100.)) if end > 0 else None,
                window_start_s=0., window_end_s=end,
                sample_start_s=samples[0][0] if samples else None,
                sample_end_s=samples[-1][0] if samples else None,
                window_source=parsed.window['source'])


def chart_points(parsed, values):
    """Explicit nulls stop lines across discarded indices or unsupported gaps."""
    points = []; previous_index = None; previous_stamp = None
    gap_limit = parsed.quality['gap_limit_seconds']
    for index, stamp in enumerate(parsed.times):
        if stamp is None:
            continue
        if previous_index is not None and (index != previous_index + 1 or gap_limit is None or stamp - previous_stamp > gap_limit):
            points.append(dict(time_s=previous_stamp / 2 + stamp / 2, value=None))
        points.append(dict(time_s=stamp, value=values[index]))
        previous_index, previous_stamp = index, stamp
    if points and points[0]['time_s'] > 0:
        points.insert(0, dict(time_s=0., value=None))
    if points and points[-1]['time_s'] < parsed.window['end_s']:
        points.append(dict(time_s=parsed.window['end_s'], value=None))
    return points


def _interpolate(first, second, fraction):
    return first * (1 - fraction) + second * fraction


def compare_effort(parsed, sport):
    window = parsed.window; end = window['end_s']; midpoint = window['midpoint_s']
    halves = [dict(key=key, start_s=start, end_s=stop, duration_seconds=stop - start,
                   paired_seconds=0., coverage_pct=0. if stop > start else None,
                   heart_rate_mean_bpm=None, power_mean_w=None, ratio_w_per_bpm=None, power_zero_seconds=0.)
              for key, start, stop in [('first', 0., midpoint), ('second', midpoint, end)]]
    result = dict(status='insufficient_data', reasons=[], window=dict(window), halves=halves,
                  change_pct=None, difference=dict(power_w=None, heart_rate_bpm=None),
                  method=dict(version='raw-paired-1', weighting='trapezoidal',
                      gap_limit_seconds=parsed.quality['gap_limit_seconds'],
                      median_sample_interval_seconds=parsed.quality['median_sample_interval_seconds'],
                      min_coverage_pct=80., min_paired_seconds_per_half=300., zero_power='included',
                      zero_power_definition='both_interval_endpoints_zero', halves='equal_elapsed_time',
                      raw_point_limit=MAX_RAW_POINTS),
                  limits=['Confronto descrittivo dei campioni registrati: non misura forma fisica, efficienza fisiologica o rischio.',
                          'Le metà seguono il tempo trascorso; riscaldamento, terreno, intensità e tratti a potenza zero possono cambiarne il rapporto.',
                          'I tratti a potenza zero restano inclusi. Non vengono identificati automaticamente pause o movimento.',
                          'Interpolazione lineare soltanto tra campioni adiacenti validi; nessuna estensione oltre i campioni o attraverso vuoti.',
                          'Il limite dei vuoti e le soglie di copertura/durata sono criteri tecnici dell’app, non soglie fisiologiche.'])
    reasons = result['reasons']
    if sport_key(sport) != 'bike':
        result.update(status='not_applicable', reasons=['unsupported_sport'])
        return result
    if not parsed.times:
        reasons.append('missing_time')
    if parsed.quality['timeline_ambiguous']:
        reasons.append('ambiguous_time')
    if end <= 0:
        reasons.append('invalid_window')
    if parsed.quality['truncated']:
        reasons.append('truncated_input')
    for kind, remote, label in [('power', 'watts', 'power'), ('heart_rate', 'heartrate', 'heart_rate')]:
        if kind not in parsed.channels:
            reasons.append(('ambiguous_' if remote in parsed.quality['duplicate_channel_types'] else 'missing_') + label)
    power = parsed.channels.get('power'); heart = parsed.channels.get('heart_rate')
    accumulators = [(_Average(), _Average()), (_Average(), _Average())]
    if power is not None and heart is not None:
        for index, left, right in adjacent_intervals(parsed):
            p0, p1 = power[index:index + 2]; h0, h1 = heart[index:index + 2]
            if p0 is None or p1 is None or h0 is None or h1 is None or h0 <= 0 or h1 <= 0:
                continue
            for half, (power_average, heart_average) in zip(halves, accumulators):
                start, stop = max(left, half['start_s']), min(right, half['end_s'])
                if stop <= start:
                    continue
                first_fraction, last_fraction = (start - left) / (right - left), (stop - left) / (right - left)
                p_start = _interpolate(p0, p1, first_fraction); p_stop = _interpolate(p0, p1, last_fraction)
                h_start = _interpolate(h0, h1, first_fraction); h_stop = _interpolate(h0, h1, last_fraction)
                power_average.add(p_start / 2 + p_stop / 2, stop - start)
                heart_average.add(h_start / 2 + h_stop / 2, stop - start)
                if p0 == 0 and p1 == 0:
                    half['power_zero_seconds'] += stop - start
        for half, (power_average, heart_average) in zip(halves, accumulators):
            half['paired_seconds'] = _rounded(power_average.weight)
            half['coverage_pct'] = _rounded(min(100., power_average.weight / half['duration_seconds'] * 100.)) if half['duration_seconds'] > 0 else None
            half['power_mean_w'] = _rounded(power_average.mean)
            half['heart_rate_mean_bpm'] = _rounded(heart_average.mean)
            half['power_zero_seconds'] = _rounded(half['power_zero_seconds'])
            if heart_average.mean is not None and heart_average.mean > 0 and power_average.mean is not None:
                half['ratio_w_per_bpm'] = _rounded(power_average.mean / heart_average.mean)
    if any(half['coverage_pct'] is None or half['coverage_pct'] < 80 for half in halves):
        reasons.append('insufficient_coverage')
    if any(half['paired_seconds'] < 300 for half in halves):
        reasons.append('insufficient_paired_duration')
    first_ratio, second_ratio = [half['ratio_w_per_bpm'] for half in halves]
    if first_ratio is not None and first_ratio <= 0:
        reasons.append('zero_first_ratio')
    if all(half['paired_seconds'] > 0 for half in halves) and (first_ratio is None or second_ratio is None):
        reasons.append('numeric_limit')
    for target, source in [('power_w', 'power_mean_w'), ('heart_rate_bpm', 'heart_rate_mean_bpm')]:
        first, second = [half[source] for half in halves]
        if first is not None and second is not None:
            result['difference'][target] = _rounded(second - first)
    if not reasons:
        change = _rounded((second_ratio / first_ratio - 1) * 100.)
        if change is None:
            reasons.append('numeric_limit')
        else:
            result.update(status='available', change_pct=change)
    if reasons and any(reason in reasons for reason in ('missing_time', 'missing_power', 'missing_heart_rate', 'ambiguous_power', 'ambiguous_heart_rate', 'ambiguous_time', 'truncated_input', 'invalid_window')):
        result['status'] = 'unavailable'
    return result
