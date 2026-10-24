"""Best continuous-window mean power on recorded, uninterrupted raw segments.

Search is linear in raw samples for each requested duration. Piecewise-linear
power makes the derivative p(s+D)-p(s) linear between sample/shifted-sample
events, so endpoints and its positive-to-negative roots cover every maximum.
No database, provider calls, FTP assumptions or persisted metrics.
"""
from fractions import Fraction
import math
import sys

from app.services.activity_effort import adjacent_intervals, number

DURATIONS = (5, 15, 30, 60, 300, 600, 1200, 3600)


class _NumericLimit(Exception):
    pass


def _round(value):
    if value is None or not math.isfinite(value):
        return None
    result = round(value, 6)
    return value if result == 0 and value != 0 else result


class _Candidate:
    def __init__(self, segment, duration, start, left, right, event, estimate):
        self.segment = segment; self.duration = duration; self.start = start
        self.left = left; self.right = right; self.event = event; self.estimate = estimate
        # This deliberately uses the accumulated prefix span, not merely ULPs
        # of a short-window result. Potentially indistinguishable maxima are
        # certified with exact rational integration, never declared ties by a
        # floating tolerance. Prefix construction is lazy and shared per run.
        self.error = segment.scale * (64 * sys.float_info.epsilon * max(1., segment.length / duration))
        self._exact = None

    def exact(self):
        if self._exact is not None:
            return self._exact
        run = self.segment; duration = Fraction(self.duration)
        run.ensure_exact_prefix()
        left, right = self.left, self.right
        lt, rt = Fraction(run.raw_times[left]), Fraction(run.raw_times[right])
        ld = Fraction(run.raw_times[left+1])-lt
        rd = Fraction(run.raw_times[right+1])-rt
        lp, rp = Fraction(run.raw_power[left]), Fraction(run.raw_power[right])
        ln, rn = Fraction(run.raw_power[left+1]), Fraction(run.raw_power[right+1])
        kind, index = self.event
        if kind == 'start':
            start = Fraction(run.raw_times[0])
        elif kind == 'end':
            start = Fraction(run.raw_times[-1]) - duration
        elif kind == 'left':
            start = Fraction(run.raw_times[index])
        elif kind == 'right':
            start = Fraction(run.raw_times[index]) - duration
        else:
            start = None
        # Repeated windows can have identical exact shape and interior energy.
        # Reuse only that mathematical identity, never a rounded power value or
        # a hash alone. Offsets remove the absolute activity time from the key.
        shape = (duration, ld, rd, rt-lt, lp, ln, rp, rn,
                 run.exact_prefix[right]-run.exact_prefix[left],
                 'root' if start is None else start-lt)
        cached = run.exact_candidates.get(shape)
        if cached is not None:
            self._exact = cached[0], lt+cached[1]
            return self._exact
        if start is None:
            lm = (ln-lp)/ld
            rm = (rn-rp)/rd
            if lm == rm:
                raise _NumericLimit()
            start = (lp - lm * lt - rp - rm * (duration - rt)) / (rm - lm)
        if start < Fraction(run.raw_times[0]) or start + duration > Fraction(run.raw_times[-1]):
            raise _NumericLimit()
        mean = (run.exact_integral(start + duration, self.right)
                - run.exact_integral(start, self.left)) / duration
        if mean < 0:
            raise _NumericLimit()
        self._exact = mean, start
        if len(run.exact_candidates)>=2048:
            run.exact_candidates.pop(next(iter(run.exact_candidates)))
        run.exact_candidates[shape] = mean, start-lt
        return self._exact

    def better_than(self, other):
        if other is None:
            return True
        uncertainty = self.error + other.error
        if self.estimate > other.estimate and self.estimate - other.estimate > uncertainty:
            return True
        if self.estimate < other.estimate and other.estimate - self.estimate > uncertainty:
            return False
        mean, start = self.exact(); old_mean, old_start = other.exact()
        return mean > old_mean or (mean == old_mean and start < old_start)

    def serialize(self):
        mean, start = self.exact()
        try:
            value = float(mean); first = float(start); end = float(start + self.duration)
        except (OverflowError, ValueError):
            raise _NumericLimit() from None
        if (not all(math.isfinite(item) for item in (value, first, end))
                or not math.isclose(end - first, self.duration, rel_tol=0., abs_tol=max(1e-7, self.duration * 1e-10))):
            raise _NumericLimit()
        return dict(duration_seconds=self.duration, status='available', mean_power_w=value,
                    start_s=first, end_s=end, segment_start_s=self.segment.raw_times[0],
                    segment_end_s=self.segment.raw_times[-1], covered_seconds=self.duration, coverage_pct=100.)


class _Segment:
    def __init__(self, times, power):
        self.raw_times = times; self.raw_power = power; self.origin = times[0]
        self.times = [stamp - self.origin for stamp in times]
        self.length = self.times[-1]; self.scale = max(power) or 1.
        self.power = [value / self.scale for value in power]
        self.high = [0.]; self.low = [0.]; self.areas = []; self.exact_prefix = None; self.exact_candidates = {}
        total = 0.; correction = 0.
        for index in range(len(times) - 1):
            area = (self.power[index] / 2 + self.power[index + 1] / 2) * (self.times[index + 1] - self.times[index])
            self.areas.append(area)
            updated = total + area
            correction += ((total - updated) + area) if total >= area else ((area - updated) + total)
            total = updated; self.high.append(total); self.low.append(correction)

    def ensure_exact_prefix(self):
        if self.exact_prefix is None:
            prefix = [Fraction(0)]; total = Fraction(0)
            previous_t = Fraction(self.raw_times[0]); previous_p = Fraction(self.raw_power[0])
            for raw_t, raw_p in zip(self.raw_times[1:], self.raw_power[1:]):
                current_t, current_p = Fraction(raw_t), Fraction(raw_p)
                total += (previous_p + current_p) * (current_t - previous_t) / 2
                prefix.append(total); previous_t, previous_p = current_t, current_p
            self.exact_prefix = prefix

    def exact_integral(self, stamp, index):
        self.ensure_exact_prefix()
        # The monotonic search already knows the surrounding segment. Avoid a
        # binary search comparing Fraction to float at every equal maximum.
        # Exact boundary adjustments handle floating event-position rounding.
        while index > 0 and stamp < Fraction(self.raw_times[index]):
            index -= 1
        while index < len(self.raw_times)-2 and stamp > Fraction(self.raw_times[index+1]):
            index += 1
        first = Fraction(self.raw_times[index]); distance = stamp - first
        if distance == 0:
            return self.exact_prefix[index]
        if stamp == self.raw_times[index+1]:
            return self.exact_prefix[index+1]
        if stamp == self.raw_times[-1]:
            return self.exact_prefix[-1]
        power = Fraction(self.raw_power[index])
        slope = (Fraction(self.raw_power[index + 1]) - power) / (Fraction(self.raw_times[index + 1]) - first)
        return self.exact_prefix[index] + power * distance + slope * distance * distance / 2

    def _reading(self, index, stamp):
        left, right = self.times[index], self.times[index+1]
        if stamp<=left:
            return self.power[index]
        if stamp>=right:
            return self.power[index+1]
        fraction = (stamp - left) / (right - left)
        return self.power[index] * (1 - fraction) + self.power[index + 1] * fraction

    def _partial(self, index, stamp):
        if stamp<=self.times[index]:
            return 0.
        if stamp>=self.times[index+1]:
            return self.areas[index]
        distance = stamp - self.times[index]
        return (self.power[index] / 2 + self._reading(index, stamp) / 2) * distance

    def _candidate(self, duration, start, left, right, event):
        area = math.fsum((self.high[right], self.low[right], self._partial(right, start + duration),
                          -self.high[left], -self.low[left], -self._partial(left, start)))
        mean = area / duration
        bound = 64 * sys.float_info.epsilon * max(1., self.length / duration)
        if not math.isfinite(mean) or mean < -bound or mean > 1 + bound:
            raise _NumericLimit()
        estimate = min(1., max(0., mean)) * self.scale
        return _Candidate(self, duration, start, left, right, event, estimate)

    def best(self, duration):
        if self.length < duration:
            return None
        if abs(self.length - duration) <= 8 * math.ulp(max(self.length, duration)):
            if Fraction(self.raw_times[-1]) - Fraction(self.raw_times[0]) < duration:
                return None
        # Constant recordings have an exact earliest optimum and require no
        # scanning of identical candidate windows or tie certification.
        if all(value == self.raw_power[0] for value in self.raw_power):
            candidate = _Candidate(self, duration, 0., 0, 0, ('start', 0), self.raw_power[0])
            candidate._exact = (Fraction(self.raw_power[0]), Fraction(self.raw_times[0]))
            return candidate
        maximum = self.length - duration
        left = 0; right = 0; last_segment = len(self.times) - 2
        while right < last_segment and self.times[right + 1] <= duration:
            right += 1
        best = self._candidate(duration, 0., left, right, ('start', 0))
        start = 0.
        while start < maximum:
            left_event = self.times[left + 1]
            right_event = self.times[right + 1] - duration
            stop = min(maximum, left_event, right_event)
            if stop <= start:
                raise _NumericLimit()
            g_start = self._reading(right, start + duration) - self._reading(left, start)
            g_stop = self._reading(right, stop + duration) - self._reading(left, stop)
            if g_start > 0 and g_stop < 0:
                vertex = start + (stop - start) * (g_start / (g_start - g_stop))
                candidate = self._candidate(duration, vertex, left, right, ('root', None))
                if candidate.better_than(best):
                    best = candidate
            # If the derivative is zero throughout this event interval, the
            # mean is constant and its earlier endpoint already wins any tie.
            if g_start != 0 or g_stop != 0:
                event = ('end', None) if stop == maximum else ('left', left + 1) if stop == left_event else ('right', right + 1)
                candidate = self._candidate(duration, stop, left, right, event)
                if candidate.better_than(best):
                    best = candidate
            start = stop
            while left < last_segment and self.times[left + 1] <= start:
                left += 1
            while right < last_segment and self.times[right + 1] <= start + duration:
                right += 1
        return best


def _runs(parsed, power):
    runs = []; beginning = None; last = None
    for index, left, right in adjacent_intervals(parsed):
        if power[index] is None or power[index + 1] is None:
            continue
        if last is None or index != last + 1:
            if beginning is not None:
                runs.append((beginning, last + 1))
            beginning = index
        last = index
    if beginning is not None:
        runs.append((beginning, last + 1))
    return runs


def best_power_efforts(parsed):
    rows = [dict(duration_seconds=duration, status='insufficient_duration', mean_power_w=None, start_s=None,
                 end_s=None, segment_start_s=None, segment_end_s=None, covered_seconds=0., coverage_pct=None)
            for duration in DURATIONS]
    result = dict(status='insufficient_data', metric='power', unit='W', scope='activity_valid_segments',
                  durations=rows, reasons=[],
                  method=dict(version='raw-power-duration-1', weighting='trapezoidal', interpolation='linear',
                              search='continuous_window', gap_limit_seconds=parsed.quality['gap_limit_seconds'], zero_power='included'),
                  coverage=dict(covered_seconds=0., window_seconds=parsed.window['end_s'], coverage_pct=None,
                                segments_count=0, longest_segment_seconds=0.),
                  limits=['Le migliori medie riguardano soltanto i tratti continui validi disponibili di questa attività, non i record personali sullo storico.',
                          'La ricerca considera anche finestre che iniziano tra due campioni, con interpolazione lineare della potenza.',
                          'Gli zeri registrati sono inclusi; i dati assenti non vengono sostituiti con zeri e i vuoti interrompono le finestre.',
                          'Nessuna stima di FTP o classificazione della forma fisica deriva da queste medie.'])
    reasons = result['reasons']; quality = parsed.quality
    if 'watts' in quality['duplicate_channel_types']:
        result.update(status='unavailable', reasons=['ambiguous_power'])
        return result
    power = parsed.channels.get('power')
    if power is None:
        result.update(status='not_applicable', reasons=['missing_power'])
        return result
    runs = _runs(parsed, power)
    lengths = [parsed.times[last] - parsed.times[first] for first, last in runs]
    covered = math.fsum(lengths)
    window = parsed.window['end_s']
    result['coverage'].update(covered_seconds=_round(covered), segments_count=len(runs),
        longest_segment_seconds=_round(max(lengths, default=0.)),
        coverage_pct=_round(min(100., covered / window * 100.)) if window > 0 else None)
    if not parsed.times:
        reasons.append('missing_time')
    if quality['timeline_ambiguous']:
        reasons.append('ambiguous_time')
    if quality['truncated']:
        reasons.append('truncated_input')
    if window <= 0:
        reasons.append('invalid_window')
    if reasons:
        result['status'] = 'unavailable'
        return result
    best = {duration: None for duration in DURATIONS}; numeric = set()
    for (first, last), length in zip(runs, lengths):
        if length < DURATIONS[0]:
            continue
        segment = _Segment(parsed.times[first:last + 1], power[first:last + 1])
        for duration in DURATIONS:
            if duration in numeric or length < duration:
                continue
            try:
                candidate = segment.best(duration)
                if candidate is not None and candidate.better_than(best[duration]):
                    best[duration] = candidate
            except (OverflowError, ValueError, ZeroDivisionError, _NumericLimit):
                numeric.add(duration)
    available = 0
    for index, duration in enumerate(DURATIONS):
        if duration not in numeric and best[duration] is not None:
            try:
                result['durations'][index] = best[duration].serialize(); available += 1
            except (OverflowError, ValueError, ZeroDivisionError, _NumericLimit):
                numeric.add(duration)
        if duration in numeric:
            rows[index]['status'] = 'numeric_limit'
    if numeric:
        reasons.append('numeric_limit')
    if available:
        result['status'] = 'available'
    else:
        reasons.append('insufficient_duration')
    return result
