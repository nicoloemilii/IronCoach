"""Best elapsed distance efforts from recorded distance or explicit pool lengths.

Running distance is piecewise linear in elapsed time. Its inverse is linear
between distance vertices, so a minimum of t(x+D)-t(x) occurs at a vertex of
either inverse. At plateaus the start uses the last timestamp and the finish
the first; pauses inside a window remain included. Close candidates and the
winning bounds are certified with rational arithmetic on the recorded floats.
"""
from bisect import bisect_left
from fractions import Fraction
import math
import sys

from app.services.activity_effort import adjacent_intervals, number
from app.services.training_load import sport_key

DISTANCES = (1000, 1609.344, 5000, 10000, 21097.5, 42195)
POOL_METERS = (50, 100, 200, 400, 800, 1500)
POOL_YARDS = (50, 100, 200, 500, 1000, 1650)
MAX_POOL_LENGTHS = 20_000


class _NumericLimit(Exception):
    pass


def _difference(a, b):
    """Error-free floating difference, for translation-invariant cache keys."""
    high = a - b
    if not math.isfinite(high):
        raise _NumericLimit()
    virtual = a - high
    low = (a - (high + virtual)) + (virtual - b)
    return high, low


class _Target:
    def __init__(self, value):
        self.value = value
        self.exact = Fraction(str(value))
        self.approx = float(self.exact)
        self.error = float(abs(Fraction(self.approx) - self.exact))

    def compare(self, value, anchor, sign=1):
        """Compare a recorded distance with anchor +/- the exact target."""
        residual = math.fsum((value, -anchor, -sign * self.approx))
        uncertainty = self.error + math.ulp(residual)
        if residual > uncertainty:
            return 1
        if residual < -uncertainty:
            return -1
        exact = Fraction(value) - Fraction(anchor) - sign * self.exact
        return (exact > 0) - (exact < 0)


class _Candidate:
    def __init__(self, run, target, kind, index, lower, upper, exact_point):
        self.run = run; self.target = target; self.kind = kind; self.index = index
        self.lower = lower; self.upper = upper; self.exact_point = exact_point
        self._exact = None
        anchor = run.distances[index]
        boundary = run.last[index] if kind == 'start' else run.first[index]
        if exact_point:
            other = run.first[upper] if kind == 'start' else run.last[upper]
            slope = 0.
        else:
            left, right = run.distances[lower], run.distances[upper]
            first, last = run.last[lower], run.first[upper]
            delta = right - left
            offset = math.fsum((anchor, target.approx if kind == 'start' else -target.approx, -left))
            slope = (last - first) / delta
            other = first + (offset / delta) * (last - first)
        self.estimate = other - boundary if kind == 'start' else boundary - other
        self.error = (64 * sys.float_info.epsilon * (abs(boundary) + abs(other) + 1.)
                      + 4 * target.error * abs(slope))
        if not math.isfinite(self.estimate) or not math.isfinite(self.error):
            raise _NumericLimit()

    def exact(self):
        if self._exact is not None:
            return self._exact
        run = self.run; index = self.index; lower = self.lower; upper = self.upper
        boundary = run.last[index] if self.kind == 'start' else run.first[index]
        if self.exact_point:
            other = run.first[upper] if self.kind == 'start' else run.last[upper]
            elapsed = Fraction(other) - Fraction(boundary) if self.kind == 'start' else Fraction(boundary) - Fraction(other)
        else:
            # Error-free differences preserve the exact translated shape. A
            # repeated constant-speed recording therefore needs one rational
            # calculation per shape, without declaring near values equal.
            key = (self.kind, self.target.exact,
                   _difference(run.distances[index], run.distances[lower]),
                   _difference(run.distances[upper], run.distances[lower]),
                   _difference(run.last[lower], boundary),
                   _difference(run.first[upper], run.last[lower]))
            elapsed = run.exact_cache.get(key)
            if elapsed is None:
                query = Fraction(run.distances[index]) + (self.target.exact if self.kind == 'start' else -self.target.exact)
                other = Fraction(run.last[lower]) + (query - Fraction(run.distances[lower])) * (
                    Fraction(run.first[upper]) - Fraction(run.last[lower])) / (
                    Fraction(run.distances[upper]) - Fraction(run.distances[lower]))
                elapsed = other - Fraction(boundary) if self.kind == 'start' else Fraction(boundary) - other
                if len(run.exact_cache) >= 2048:
                    run.exact_cache.pop(next(iter(run.exact_cache)))
                run.exact_cache[key] = elapsed
        if elapsed <= 0:
            raise _NumericLimit()
        start = Fraction(boundary) if self.kind == 'start' else Fraction(boundary) - elapsed
        self._exact = elapsed, start
        return self._exact

    def better_than(self, other):
        if other is None:
            return True
        if self.estimate + self.error < other.estimate - other.error:
            return True
        if self.estimate - self.error > other.estimate + other.error:
            return False
        elapsed, start = self.exact(); old_elapsed, old_start = other.exact()
        return elapsed < old_elapsed or (elapsed == old_elapsed and start < old_start)

    def serialize(self):
        elapsed, start = self.exact(); end = start + elapsed
        anchor = Fraction(self.run.distances[self.index])
        first_distance = anchor if self.kind == 'start' else anchor - self.target.exact
        values = dict(elapsed_seconds=float(elapsed), start_s=float(start), end_s=float(end),
                      start_distance_m=float(first_distance), end_distance_m=float(first_distance + self.target.exact),
                      pace_seconds_per_km=float(elapsed * 1000 / self.target.exact))
        if (not all(math.isfinite(value) for value in values.values())
                or values['end_s'] <= values['start_s']
                or values['end_distance_m'] <= values['start_distance_m']
                or values['pace_seconds_per_km'] <= 0):
            raise _NumericLimit()
        return dict(distance_m=self.target.value, distance_value=self.target.value, distance_unit='m',
                    status='available', **values, segment_start_s=self.run.first[0],
                    segment_end_s=self.run.last[-1], coverage_pct=100.)


class _Run:
    def __init__(self, times, distances):
        self.distances = []; self.first = []; self.last = []; self.exact_cache = {}
        for stamp, distance in zip(times, distances):
            if self.distances and distance == self.distances[-1]:
                self.last[-1] = stamp
            else:
                self.distances.append(distance); self.first.append(stamp); self.last.append(stamp)

    def _candidate(self, target, kind, index):
        anchor = self.distances[index]; sign = 1 if kind == 'start' else -1
        query = math.fsum((anchor, sign * target.approx))
        upper = min(bisect_left(self.distances, query), len(self.distances) - 1)
        # Query rounding can place an exact event on an adjacent float. The
        # comparison below uses the original anchor and exact target instead.
        while upper > 0 and target.compare(self.distances[upper - 1], anchor, sign) >= 0:
            upper -= 1
        while upper < len(self.distances) - 1 and target.compare(self.distances[upper], anchor, sign) < 0:
            upper += 1
        comparison = target.compare(self.distances[upper], anchor, sign)
        if comparison < 0 or (upper == 0 and comparison > 0):
            raise _NumericLimit()
        exact_point = comparison == 0
        return _Candidate(self, target, kind, index, max(0, upper - 1), upper, exact_point)

    def best(self, target):
        if target.compare(self.distances[-1], self.distances[0]) < 0:
            return None
        best = None
        for index, distance in enumerate(self.distances):
            if target.compare(self.distances[-1], distance) >= 0:
                candidate = self._candidate(target, 'start', index)
                if candidate.better_than(best):
                    best = candidate
            if target.compare(distance, self.distances[0]) >= 0:
                candidate = self._candidate(target, 'end', index)
                if candidate.better_than(best):
                    best = candidate
        return best


def _distance_runs(parsed, distance):
    runs = []; beginning = None; last = None; resets = 0
    for index, _, _ in adjacent_intervals(parsed):
        first, second = distance[index:index + 2]
        if first is None or second is None:
            continue
        if second < first:
            resets += 1
            continue
        if last is None or index != last + 1:
            if beginning is not None:
                runs.append((beginning, last + 1))
            beginning = index
        last = index
    if beginning is not None:
        runs.append((beginning, last + 1))
    return runs, resets


def best_distance_efforts(parsed, sport='running'):
    rows = [dict(distance_m=value, distance_value=value, distance_unit='m', status='insufficient_distance',
                 elapsed_seconds=None, pace_seconds_per_km=None, start_s=None, end_s=None,
                 start_distance_m=None, end_distance_m=None, segment_start_s=None,
                 segment_end_s=None, coverage_pct=None) for value in DISTANCES]
    result = dict(status='insufficient_data', metric='distance', unit='s', scope='activity_valid_segments',
                  distances=rows, reasons=[],
                  method=dict(version='raw-distance-effort-1', interpolation='linear_cumulative_distance',
                              search='continuous_distance_window', pauses='included',
                              gap_limit_seconds=parsed.quality['gap_limit_seconds']),
                  coverage=dict(covered_seconds=0., window_seconds=parsed.window['end_s'], coverage_pct=None,
                                segments_count=0, longest_segment_m=0., distance_resets=0),
                  limits=['Tempi ricavati dalla distanza registrata, con interpolazione tra campioni adiacenti validi: non sono risultati di gara certificati.',
                          'Il tempo trascorso include le pause interne al tratto; non viene usato il tempo in movimento.',
                          'Vuoti, valori mancanti e azzeramenti della distanza interrompono i tratti; nessuna distanza viene ricostruita dal GPS.',
                          'I risultati descrivono soltanto i tratti validi disponibili in questa attività.'])
    if sport_key(sport) != 'run':
        result.update(status='not_applicable', reasons=['unsupported_sport'])
        return result
    quality = parsed.quality; reasons = result['reasons']
    if 'distance' in quality['duplicate_channel_types']:
        result.update(status='unavailable', reasons=['ambiguous_distance'])
        return result
    distance = parsed.channels.get('distance')
    if distance is None:
        result.update(status='unavailable', reasons=['missing_distance'])
        return result
    if not parsed.times:
        reasons.append('missing_time')
    if quality['timeline_ambiguous']:
        reasons.append('ambiguous_time')
    if quality['truncated']:
        reasons.append('truncated_input')
    if parsed.window['end_s'] <= 0:
        reasons.append('invalid_window')
    if reasons:
        result['status'] = 'unavailable'
        return result
    runs, resets = _distance_runs(parsed, distance)
    covered = math.fsum(parsed.times[last] - parsed.times[first] for first, last in runs)
    result['coverage'].update(covered_seconds=covered, segments_count=len(runs), distance_resets=resets,
        longest_segment_m=max((distance[last] - distance[first] for first, last in runs), default=0.),
        coverage_pct=min(100., covered / parsed.window['end_s'] * 100.))
    if resets:
        reasons.append('distance_resets')
    best = {value: None for value in DISTANCES}; numeric = set()
    for first, last in runs:
        run = _Run(parsed.times[first:last + 1], distance[first:last + 1])
        for value in DISTANCES:
            if value in numeric:
                continue
            try:
                candidate = run.best(_Target(value))
                if candidate is not None and candidate.better_than(best[value]):
                    best[value] = candidate
            except (ArithmeticError, ValueError, _NumericLimit):
                numeric.add(value)
    for index, value in enumerate(DISTANCES):
        if value not in numeric and best[value] is not None:
            try:
                rows[index] = best[value].serialize()
            except (ArithmeticError, ValueError, _NumericLimit):
                numeric.add(value)
        if value in numeric:
            rows[index]['status'] = 'numeric_limit'
    if numeric:
        reasons.append('numeric_limit')
    if any(row['status'] == 'available' for row in rows):
        result['status'] = 'available'
    else:
        reasons.append('insufficient_distance')
    return result


def _pool_target(value, unit):
    distance = Fraction(value) * (Fraction('0.9144') if unit == 'yd' else 1)
    return dict(distance_m=float(distance), distance_value=value, distance_unit=unit,
                status='insufficient_distance', elapsed_seconds=None, pace_seconds_per_100m=None,
                start_s=None, end_s=None, first_length_index=None, last_length_index=None,
                lengths_count=None, coverage_pct=None), distance


def best_pool_efforts(pool_lengths, sport='swimming', *, max_time_s=None):
    result = dict(status='insufficient_data', metric='pool_distance', unit='s', scope='activity_explicit_lengths',
                  groups=[], reasons=[], method=dict(version='pool-distance-effort-2', interpolation='none',
                    search='consecutive_whole_active_lengths', time='elapsed', pauses='split'),
                  coverage=dict(total_lengths=0, active_lengths=0, eligible_lengths=0, idle_lengths=0,
                                paused_lengths=0, invalid_lengths=0, boundary_breaks=0, segments_count=0,
                                clipped_lengths=0),
                  limits=['Solo vasche complete esplicitamente registrate: nessuna vasca viene ricostruita dalla distanza cumulativa.',
                          'Le serie rispettano lunghezza della piscina e stile; pause, riposo e confini temporali non coincidenti le interrompono.',
                          'I tempi sono quelli trascorsi registrati. La precisione dei confini FIT può impedire di collegare vasche consecutive.',
                          'I risultati descrivono i dati disponibili e non equivalgono a primati agonistici certificati.'])
    if sport_key(sport) != 'swim':
        result.update(status='not_applicable', reasons=['unsupported_sport'])
        return result
    cutoff = number(max_time_s, nonnegative=False) if max_time_s is not None else None
    if max_time_s is not None and cutoff is None:
        result.update(status='unavailable', reasons=['invalid_window'])
        return result
    if not isinstance(pool_lengths, dict):
        result.update(status='not_applicable', reasons=['missing_pool_lengths'])
        return result
    if pool_lengths.get('status') != 'available':
        result.update(status='unavailable' if pool_lengths.get('status') == 'unavailable' else 'not_applicable',
                      reasons=list(pool_lengths.get('issues') or ['missing_pool_lengths']))
        return result
    rows = pool_lengths.get('rows'); pool_length = number(pool_lengths.get('pool_length_m'))
    if pool_length is None or pool_length <= 0:
        result.update(status='unavailable', reasons=['invalid_pool_length'])
        return result
    if (pool_lengths.get('schema_version') != 1 or not isinstance(rows, list)
            or pool_lengths.get('truncated') or len(rows) > MAX_POOL_LENGTHS):
        result.update(status='unavailable', reasons=['truncated_input' if pool_lengths.get('truncated') or isinstance(rows, list) and len(rows) > MAX_POOL_LENGTHS else 'invalid_pool_lengths'])
        return result
    raw_unit = pool_lengths.get('pool_length_unit')
    unit = None if isinstance(raw_unit, bool) else 'yd' if raw_unit in ('statute', 'yard', 'yards', 'yd', 1) else 'm' if raw_unit in ('metric', 'meter', 'meters', 'm', 0) else None
    if unit is None:
        result.update(status='unavailable', reasons=['unknown_pool_unit'])
        return result
    # FIT encodes pool_length to hundredths of a metre. Decimal conversion
    # respects explicit 22.86/45.72 metre yard pools without float remainder.
    exact_length = Fraction(str(pool_length))
    targets = [_pool_target(value, unit) for value in (POOL_YARDS if unit == 'yd' else POOL_METERS)]
    compatible = [(template, distance, distance / exact_length) for template, distance in targets
                  if (distance / exact_length).denominator == 1]
    coverage = result['coverage']; coverage['total_lengths'] = len(rows)
    seen = set(); runs = []; run = []; previous = None; strokes = set()
    last_index = None; last_end = None
    for row in rows:
        if not isinstance(row, dict):
            coverage['invalid_lengths'] += 1; previous = None
            if run: runs.append(run); run = []
            continue
        index = row.get('index'); start = number(row.get('start_s')); end = number(row.get('end_s'))
        if cutoff is not None and end is not None and end > cutoff:
            coverage['clipped_lengths'] += 1; previous = None
            if run: runs.append(run); run = []
            continue
        # Ambiguous ordering or overlapping explicit intervals cannot be
        # repaired by splitting them into apparently valid standalone records.
        # Retain this check across malformed rows so they cannot hide a reset.
        if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
            if index in seen or last_index is not None and index <= last_index:
                result.update(status='unavailable', reasons=['ambiguous_pool_order'])
                return result
            seen.add(index); last_index = index
        if start is not None and end is not None and end > start:
            if last_end is not None and start < last_end:
                result.update(status='unavailable', reasons=['overlapping_pool_lengths'])
                return result
            last_end = end
        elapsed = number(row.get('elapsed_s')); timer = number(row.get('timer_s'))
        kind = row.get('type'); stroke = row.get('stroke')
        stroke = stroke if isinstance(stroke, str) and stroke else 'unknown'
        valid = (isinstance(index, int) and not isinstance(index, bool) and index >= 0
                 and start is not None and end is not None and end > start
                 and elapsed is not None and elapsed > 0
                 and math.isclose(end - start, elapsed, rel_tol=1e-12, abs_tol=1e-9))
        if not valid:
            coverage['invalid_lengths'] += 1; previous = None
            if run: runs.append(run); run = []
            continue
        if previous and (index <= previous['index'] or start < previous['start_s']):
            result.update(status='unavailable', reasons=['ambiguous_pool_order'])
            return result
        paused = timer is not None and timer < elapsed and not math.isclose(timer, elapsed, rel_tol=1e-12, abs_tol=1e-9)
        invalid_timer = (row.get('timer_s') is not None and timer is None or
            timer is not None and timer > elapsed and not math.isclose(timer, elapsed, rel_tol=1e-12, abs_tol=1e-9))
        if kind == 'active':
            coverage['active_lengths'] += 1; strokes.add(stroke)
        if kind == 'idle': coverage['idle_lengths'] += 1
        if paused: coverage['paused_lengths'] += 1
        if kind not in ('active', 'idle') or invalid_timer: coverage['invalid_lengths'] += 1
        eligible = kind == 'active' and not paused and not invalid_timer
        boundary = previous is not None and (start != previous['end_s'] or index != previous['index'] + 1)
        if boundary: coverage['boundary_breaks'] += 1
        if run and (not eligible or boundary or stroke != run[-1]['stroke']):
            runs.append(run); run = []
        if eligible:
            coverage['eligible_lengths'] += 1
            run.append(dict(index=index, start_s=start, end_s=end, stroke=stroke))
        previous = dict(index=index, start_s=start, end_s=end)
    if run: runs.append(run)
    coverage['segments_count'] = len(runs)
    if coverage['boundary_breaks']: result['reasons'].append('pool_boundary_breaks')
    if coverage['paused_lengths']: result['reasons'].append('paused_lengths')
    if coverage['invalid_lengths']: result['reasons'].append('invalid_lengths')
    if coverage['clipped_lengths']: result['reasons'].append('future_lengths_excluded')
    if not compatible: result['reasons'].append('no_compatible_standard_distance')
    for stroke in sorted(strokes):
        group_rows = [dict(template) for template, _ in targets]
        for row in group_rows:
            distance = Fraction(str(row['distance_m']))
            if (distance / exact_length).denominator != 1:
                row['status'] = 'incompatible_pool_length'
        for template, distance, length_count in compatible:
            length_count = int(length_count); best = None
            for run in runs:
                if run[0]['stroke'] != stroke or len(run) < length_count:
                    continue
                for index in range(len(run) - length_count + 1):
                    first, last = run[index], run[index + length_count - 1]
                    elapsed = Fraction(last['end_s']) - Fraction(first['start_s'])
                    candidate = (elapsed, first['start_s'], first['index'], last['end_s'], last['index'])
                    if best is None or candidate < best: best = candidate
            if best is not None:
                row = next(row for row in group_rows if row['distance_value'] == template['distance_value'])
                elapsed, start, first_index, end, last_index = best
                row.update(status='available', elapsed_seconds=float(elapsed), pace_seconds_per_100m=float(elapsed * 100 / distance),
                           start_s=start, end_s=end, first_length_index=first_index, last_length_index=last_index,
                           lengths_count=length_count, coverage_pct=100.)
        result['groups'].append(dict(pool_length_m=pool_length, pool_length_unit=raw_unit,
                                     distance_unit=unit, stroke=stroke, distances=group_rows))
    if any(row['status'] == 'available' for group in result['groups'] for row in group['distances']):
        result['status'] = 'available'
    else:
        result['reasons'].append('insufficient_distance')
    return result
