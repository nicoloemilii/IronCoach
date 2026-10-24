"""Independent rational-arithmetic oracle + actual synthetic detail HTTP API.

Application routing is imported directly, never app.main. Every database here is
SQLite in-memory. Provider access raises immediately. No personal DB is opened.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent
os.environ['IRON_DATA_DIR'] = str(QA / 'review-api-isolated-unused')
os.environ['IRON_AUTO_SYNC'] = '0'
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api.routes_activity_detail import router
from app.db.database import Base, get_db
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.services import activity_detail

NOW = datetime(2026, 10, 22, 12)


def raw(times, power, heart_rate):
    return [{'type': 'time', 'data': times}, {'type': 'watts', 'data': power},
            {'type': 'heartrate', 'data': heart_rate}]


def oracle(times, power, heart_rate, end, max_gap):
    """Integrate linear interpolants exactly, separately from application code."""
    midpoint = Fraction(end) / 2
    halves = []
    for start, stop in ((Fraction(0), midpoint), (midpoint, Fraction(end))):
        seconds = Fraction(0); p_integral = Fraction(0); h_integral = Fraction(0)
        zero_seconds = Fraction(0)
        for index in range(len(times) - 1):
            t0, t1 = Fraction(times[index]), Fraction(times[index + 1])
            if not 0 < t1 - t0 <= max_gap:
                continue
            left, right = max(start, t0), min(stop, t1)
            if right <= left:
                continue
            values = [power[index], power[index + 1], heart_rate[index], heart_rate[index + 1]]
            if any(not isinstance(value, (int, float)) or isinstance(value, bool)
                   or not math.isfinite(value) for value in values):
                continue
            p0, p1, h0, h1 = map(Fraction, values)
            if min(p0, p1) < 0 or min(h0, h1) <= 0:
                continue
            def area(v0, v1):
                at_left = v0 + (v1 - v0) * (left - t0) / (t1 - t0)
                at_right = v0 + (v1 - v0) * (right - t0) / (t1 - t0)
                return (at_left + at_right) * (right - left) / 2
            seconds += right - left
            p_integral += area(p0, p1); h_integral += area(h0, h1)
            if p0 == p1 == 0:
                zero_seconds += right - left
        halves.append({'paired_seconds': float(seconds),
                       'coverage_pct': float(seconds / (stop - start) * 100) if stop > start else 0,
                       'heart_rate_mean_bpm': float(h_integral / seconds) if seconds else None,
                       'power_mean_w': float(p_integral / seconds) if seconds else None,
                       'ratio_w_per_bpm': float(p_integral / h_integral) if h_integral else None,
                       'power_zero_seconds': float(zero_seconds)})
    first, second = (half['ratio_w_per_bpm'] for half in halves)
    eligible = all(half['coverage_pct'] >= 80 and half['paired_seconds'] >= 300 for half in halves)
    change = (second / first - 1) * 100 if eligible and first is not None and first > 0 and second is not None else None
    return {'halves': halves, 'change_pct': change, 'available': eligible and first is not None and first > 0}


def scenarios():
    regular = list(range(0, 1201, 5))
    irregular = [0]
    for step in [1, 2, 3, 4] * 120:
        irregular.append(irregular[-1] + step)
    result = {}
    def case(name, times, power, hr, *, end=None, **extra):
        end = times[-1] if end is None else end
        dts = [b - a for a, b in zip(times, times[1:]) if b > a]
        gap = min(10, 3 * statistics.median(dts))
        result[name] = {'streams': raw(times, power, hr), 'elapsed': end,
                        'expected': oracle(times, power, hr, end, gap), **extra}
    case('irregular_linear', irregular, [100 + stamp / 6 for stamp in irregular],
         [120 + stamp / 60 for stamp in irregular])
    midpoint = list(range(0, 1000, 3))
    case('midpoint_inside_interval', midpoint, [100 + stamp / 6 for stamp in midpoint],
         [120 + stamp / 60 for stamp in midpoint], end=1001)
    case('power_zero_is_observed', regular, [0 if stamp <= 300 else 200 for stamp in regular], [120] * len(regular))
    case('heart_rate_zero_is_missing', regular, [200] * len(regular),
         [0 if 200 <= stamp <= 300 else 120 for stamp in regular])
    case('explicit_missing_values', regular, [None if 200 <= stamp <= 300 else 200 for stamp in regular],
         [120] * len(regular))
    case('elapsed_tail_uncovered', regular, [200] * len(regular), [120] * len(regular), end=2400)
    case('exact_coverage_boundary', regular, [None if stamp < 120 else 200 for stamp in regular], [120] * len(regular))
    case('under_coverage_boundary', regular, [None if stamp < 125 else 200 for stamp in regular], [120] * len(regular))
    case('exact_duration_boundary', list(range(0, 601, 5)), [200] * 121, [120] * 121)
    case('under_duration_boundary', list(range(0, 600, 5)), [200] * 120, [120] * 120)
    implicit = list(range(0, 401, 5)) + list(range(800, 1201, 5))
    case('implicit_long_gap', implicit, [200] * len(implicit), [120] * len(implicit))
    case('first_ratio_zero', regular, [0 if stamp <= 600 else 200 for stamp in regular], [120] * len(regular))
    case('moving_duration_not_window', regular, [200] * len(regular), [120] * len(regular),
         elapsed_missing=True, moving_seconds=50)
    case('now_caps_window', regular, [200] * len(regular), [120] * len(regular), end=1000,
         payload_elapsed=2400, activity_age=1000)
    for name, change in [('duplicate_timestamp', lambda values: values.insert(121, values[120])),
                          ('backward_timestamp', lambda values: values.__setitem__(121, values[120] - 1))]:
        times = regular.copy(); power = [200] * len(times); hr = [120] * len(times)
        change(times)
        if len(times) > len(power): power.insert(121, 200); hr.insert(121, 120)
        result[name] = {'streams': raw(times, power, hr), 'elapsed': 1200, 'blocked': True}
    result['duplicate_channel'] = {'streams': raw(regular, [200] * len(regular), [120] * len(regular))
        + [{'type': 'watts', 'data': [300] * len(regular)}], 'elapsed': 1200, 'blocked': True}
    result['unhashable_type'] = {'streams': [{'type': [], 'data': [1, 2]}], 'elapsed': 1200, 'blocked': True}
    result['huge_integer'] = {'streams': raw(regular, [10**400] * len(regular), [120] * len(regular)),
                             'elapsed': 1200, 'blocked': True}
    result['nonfinite_values'] = {'streams': raw(regular, [float('inf')] * len(regular), [120] * len(regular)),
                                 'elapsed': 1200, 'blocked': True}
    result['raw_truncated'] = {'streams': raw(list(range(250001)), [200] * 250001, [120] * 250001),
                               'elapsed': 250000, 'activity_age': 300000, 'blocked': True}
    result['running_not_applicable'] = {'streams': raw(regular, [200] * len(regular), [120] * len(regular)),
                                         'elapsed': 1200, 'sport': 'running', 'not_applicable': True}
    return result


def db_snapshot(engine):
    with engine.connect() as connection:
        return {table.name: [list(row) for row in connection.execute(text(f'SELECT * FROM "{table.name}" ORDER BY 1'))]
                for table in Base.metadata.sorted_tables}


@contextmanager
def fixture(case):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI(); app.include_router(router)
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    payload = {'streams': case['streams']}
    if not case.get('elapsed_missing'):
        payload['elapsed_time'] = case.get('payload_elapsed', case['elapsed'])
    with Session(engine) as db:
        activity = Activity(source='fit', source_id='effort-synthetic', sport=case.get('sport', 'cycling'),
            name='Bici sintetica <img src=x onerror=alert(1)>',
            start_time=NOW - timedelta(seconds=case.get('activity_age', 10000)),
            duration_seconds=case.get('moving_seconds', 1200), distance_meters=30000, avg_hr=140,
            avg_power=200, load=50)
        db.add(activity); db.flush()
        db.add(ActivityObservation(activity_id=activity.id, source_key='fit:effort-synthetic',
                                   payload_json=json.dumps(payload)))
        db.commit()
    def forbidden(*args, **kwargs):
        raise AssertionError('Provider access is forbidden in this audit')
    with patch.object(activity_detail, 'utc_now_naive', lambda: NOW), \
         patch.object(activity_detail.intervals_credentials, 'credentials', forbidden), \
         patch.object(activity_detail.intervals, 'get_activity_remote', forbidden):
        with TestClient(app, client=('127.0.0.1', 50000), raise_server_exceptions=False) as client:
            yield client, engine
    engine.dispose()


def close(actual, expected, tolerance=.02):
    if expected is None:
        assert actual is None, (actual, expected)
    else:
        assert isinstance(actual, (int, float)) and abs(actual - expected) <= tolerance, (actual, expected)


if __name__ == '__main__':
    paths = [ROOT / name for name in ['app/services/activity_effort.py', 'app/services/activity_detail.py',
                                      'app/api/routes_activity_detail.py']]
    hashes = {str(path.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = {'scope': 'Actual HTTP detail router with synthetic in-memory SQLite and independent Fraction integration oracle',
              'source_sha256': hashes, 'personal_database_opened': False, 'provider_access': False,
              'visual_verification': False, 'fixtures': {}, 'cases': {}, 'checks': {}}
    for name, case in scenarios().items():
        with fixture(case) as (client, engine):
            before = db_snapshot(engine); statements = []
            def capture(conn, cursor, sql, parameters, context, executemany):
                verb = sql.lstrip().split(' ', 1)[0].upper()
                if verb in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'DROP', 'ALTER'):
                    statements.append(verb)
            event.listen(engine, 'before_cursor_execute', capture)
            try:
                response = client.get('/activities/1/detail')
            finally:
                event.remove(engine, 'before_cursor_execute', capture)
            assert response.status_code == 200, (name, response.status_code, response.text[:300])
            assert response.headers.get('cache-control') == 'private, no-store'
            body = response.json(); json.dumps(body, allow_nan=False)
            assert before == db_snapshot(engine) and not statements, name
            value = body['effort_comparison']
            if case.get('not_applicable'):
                assert value['status'] == 'not_applicable'
            elif case.get('blocked'):
                assert value['status'] != 'available' and value['change_pct'] is None, (name, value)
            else:
                expected = case['expected']
                assert (value['status'] == 'available') == expected['available'], (name, value, expected)
                close(value['window']['end_s'], case['elapsed'])
                assert len(value['halves']) == 2
                for observed, wanted in zip(value['halves'], expected['halves']):
                    for key in wanted:
                        close(observed[key], wanted[key], tolerance=.02 if key != 'ratio_w_per_bpm' else .0001)
                close(value['change_pct'], expected['change_pct'])
            report['cases'][name] = {'status': value['status'], 'read_only': True,
                'comparison': value, 'expected': case.get('expected')}
            if name in ('irregular_linear', 'explicit_missing_values', 'elapsed_tail_uncovered', 'first_ratio_zero',
                        'raw_truncated', 'running_not_applicable', 'duplicate_timestamp', 'power_zero_is_observed'):
                report['fixtures'][name] = body
    report['checks'] = {'all_case_assertions': True, 'all_gets_readonly': all(case['read_only'] for case in report['cases'].values()),
        'sources_stable': all(hashes[str(path.relative_to(ROOT)).replace('\\', '/')] == hashlib.sha256(path.read_bytes()).hexdigest() for path in paths)}
    (QA / 'review_api.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert all(report['checks'].values())
    print(json.dumps({'passed': True, 'cases': len(report['cases']), 'checks': report['checks']}, ensure_ascii=False))
