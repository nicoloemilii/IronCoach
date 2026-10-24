"""Longitudinal observations preserve source, missingness, review and tenancy."""
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_training_response, routes_iron, routes_duplicate_review
from app.db import database
from app.db.database import Base, get_db
from app.db.iron_models import ActivityObservation, DuplicateCandidate, HealthMetric
from app.db.models import Activity, DailyMetrics
from app.services import training_response as response_service


TODAY = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, 20)


def _merge_with_preview(client, path):
    preview = client.get(path.rsplit('/', 1)[0] + '/preview')
    if preview.status_code != 200:
        return preview
    return client.post(path, json={'state_token': preview.json()['state_token']})


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(response_service, 'utc_now_naive', lambda: NOW)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(routes_training_response.router)
    app.include_router(routes_duplicate_review.router); app.include_router(routes_iron.router)
    def session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        yield client, engine
    engine.dispose()


def activity(db, key, day=None, load=20, stamp=None, **changes):
    values = dict(source='fit', source_id=key, sport='cycling', name='Sintetica',
                  start_time=stamp or datetime.combine(day or TODAY - timedelta(days=1), datetime.min.time()) + timedelta(hours=8),
                  duration_seconds=3600, distance_meters=20000, load=load)
    values.update(changes)
    row = Activity(**values); db.add(row); db.flush()
    return row


def wellness(db, day, value, kind='hrv_ms', source='intervals'):
    db.add(HealthMetric(date=str(day), kind=kind, value=value, source=source,
                        unit={'hrv_ms':'ms', 'resting_hr_bpm':'bpm', 'sleep_hours':'ore'}[kind],
                        observed_at=str(day), synced_at=NOW.isoformat() + 'Z'))


def pairs(db, loads, changes=None, kind='hrv_ms', source='intervals', lag=1, base=50):
    for index, load in enumerate(loads):
        day = TODAY - timedelta(days=lag + (lag + 1) * index)
        activity(db, f'{kind}-{source}-{index}', day, load)
        wellness(db, day, base, kind, source)
        wellness(db, day + timedelta(days=lag), base + (changes[index] if changes else index), kind, source)


def get(client, **params):
    result = client.get('/dashboard/training-response', params=params)
    assert result.status_code == 200, result.text
    return result.json()


def series(body, kind='hrv_ms', source='intervals'):
    metric = next(item for item in body['metrics'] if item['kind'] == kind)
    return next(item for item in metric['series'] if item['source'] == source)


def test_empty_dates_and_missingness_do_not_invent_rest_or_a_health_score(api):
    client, _ = api
    body = get(client)
    assert body['period'] == {'start':'2026-08-03', 'end':'2026-10-25', 'days':84, 'as_of':'2026-10-26',
                              'response_start':'2026-08-04', 'response_end':'2026-10-26', 'includes_partial_response_day':True}
    assert body['timezone'] == 'Europe/Rome' and body['lag_days'] == 1
    assert body['coverage']['recorded_training_days'] == 0
    assert body['coverage']['days_without_activities'] == 84
    assert len(body['quality']['exclusions']['days_without_activities']) == 84
    for metric in body['metrics']:
        item = metric['series'][0]
        assert item['source'] == 'unavailable' and item['rows'] == []
        assert item['paired_days'] == 0 and item['load_split_median'] is None
        assert not item['comparison']['available'] and item['comparison']['higher_minus_lower'] is None
    assert 'readiness' not in body and 'score' not in body
    assert any('non ricostruisce' in note for note in body['methodology'])


@pytest.mark.parametrize('kind', ['hrv_ms', 'resting_hr_bpm', 'sleep_hours'])
def test_local_estimates_remain_visible_but_never_count_as_observed_pairs(api, kind):
    client, engine = api
    with Session(engine) as db:
        pairs(db, list(range(10, 110, 10)), [1] * 10, kind=kind, source='local_estimate',
              base=7 if kind == 'sleep_hours' else 50)
        db.commit()
    item = series(get(client), kind=kind, source='local_estimate')
    assert item['source_label'] == 'Stima locale'
    assert item['paired_days'] == 0
    assert not item['comparison']['available']
    assert all(row['before'] is not None and row['after'] is not None for row in item['rows'])
    assert all(row['change'] is None and not row['eligible'] for row in item['rows'])
    assert all({'estimated_before', 'estimated_after'} <= set(row['exclusion_reasons']) for row in item['rows'])


def test_real_zero_load_is_known_but_missing_load_is_not_estimated_from_power(api):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [0, None], [1, 2])
        db.query(Activity).filter_by(load=None).one().normalized_power = 300
        db.commit()
    body = get(client)
    rows = series(body)['rows']
    zero = next(row for row in rows if row['load'] == 0)
    missing = next(row for row in rows if row['load'] is None)
    assert zero['eligible'] and zero['change'] == 1
    assert not missing['eligible'] and 'missing_load' in missing['exclusion_reasons']
    assert body['coverage']['complete_load_days'] == 1 and body['coverage']['missing_load_days'] == 1
    assert body['coverage']['days_without_activities'] == 82


def test_group_split_and_difference_are_source_specific_and_require_five_each(api):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [10] * 5 + [30] * 5, [1] * 5 + [-2] * 5)
        db.commit()
    item = series(get(client))
    assert item['paired_days'] == 10 and item['load_split_median'] == 20
    assert [group['count'] for group in item['groups']] == [5, 5]
    assert [group['median_change'] for group in item['groups']] == [1, -2]
    assert item['groups'][0]['q25_change'] == item['groups'][0]['q75_change'] == 1
    assert item['comparison']['available'] and item['comparison']['higher_minus_lower'] == -3
    # Removing one usable pair never leaves an apparently sufficient comparison.
    with Session(engine) as db:
        db.query(HealthMetric).filter_by(date=str(TODAY), kind='hrv_ms').one().value = 0
        db.commit()
    assert not series(get(client))['comparison']['available']


@pytest.mark.parametrize('loads,counts', [([10] * 10, [10, 0]), ([10] * 9 + [20], [9, 1])])
def test_exact_median_ties_are_never_rank_split_to_fill_groups(api, loads, counts):
    client, engine = api
    with Session(engine) as db:
        pairs(db, loads); db.commit()
    item = series(get(client))
    assert item['load_split_median'] == 10
    assert [group['count'] for group in item['groups']] == counts
    assert not item['comparison']['available']


def test_effective_source_precedence_and_source_switches_do_not_create_pairs(api):
    client, engine = api
    day = TODAY - timedelta(days=1)
    with Session(engine) as db:
        activity(db, 'source-switch', day)
        wellness(db, day, 60, source='manual')
        wellness(db, day, 50, source='intervals')
        wellness(db, TODAY, 55, source='manual')
        db.commit()
    body = get(client)
    for source in ('intervals', 'manual'):
        row = series(body, source=source)['rows'][0]
        assert row['before'] == 50 and row['before_source'] == 'intervals'
        assert row['after'] == 55 and row['after_source'] == 'manual'
        assert not row['eligible'] and row['change'] is None
        assert 'source_mismatch' in row['exclusion_reasons']


def test_cleared_live_manual_value_does_not_resurrect_a_migrated_reading(api):
    client, engine = api
    day = TODAY - timedelta(days=1)
    with Session(engine) as db:
        activity(db, 'cleared-manual', day)
        db.add(DailyMetrics(date=str(day), hrv_ms=None))
        wellness(db, day, 50, source='manual')
        wellness(db, TODAY, 55, source='manual')
        db.commit()
    row = series(get(client), source='manual')['rows'][0]
    assert row['before'] is None and row['exclusion_reason'] == 'missing_before'


@pytest.mark.parametrize('kind,value,eligible', [('hrv_ms',0,False), ('resting_hr_bpm',0,False),
    ('sleep_hours',0,True), ('sleep_hours',24,True), ('sleep_hours',25,False), ('sleep_hours',-1,False)])
def test_invalid_readings_stay_visible_but_real_sleep_zero_remains_usable(api, kind, value, eligible):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [20], [0], kind=kind, base=value)
        db.commit()
    row = series(get(client), kind)['rows'][0]
    assert row['before'] == row['after'] == value
    assert row['eligible'] is eligible
    assert ('invalid_before' in row['exclusion_reasons']) is not eligible


@pytest.mark.parametrize('raw', ['{"load":null}', '[1]', 'null', 'not-json', '{"load":true}',
    '{"load":-1}', '{"load":"Infinity"}', '[' * 2000 + '1' + ']' * 2000])
def test_authoritative_missing_or_malformed_load_does_not_reuse_the_activity_field(api, raw):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [90], [1])
        row = db.query(Activity).one()
        db.add(ActivityObservation(activity_id=row.id, source_key='intervals:observed', payload_json=raw))
        db.commit()
    result = series(get(client))['rows'][0]
    assert result['load'] is None and not result['eligible']
    with Session(engine) as db:
        assert db.query(Activity).one().load == 90
        assert db.query(ActivityObservation).one().payload_json == raw


def test_multiple_activities_require_all_loads_and_preserve_observed_zero_override(api):
    client, engine = api
    day = TODAY - timedelta(days=1)
    with Session(engine) as db:
        pairs(db, [10], [1])
        row = activity(db, 'second', day, 90, stamp=datetime.combine(day, datetime.min.time()) + timedelta(hours=9))
        db.add(ActivityObservation(activity_id=row.id, source_key='intervals:zero', payload_json='{"load":0}'))
        db.commit()
    row = series(get(client))['rows'][0]
    assert row['load'] == 10 and row['activities'] == 2 and row['eligible']


def test_exact_and_near_duplicate_days_excluded_until_explicit_keep_or_merge(api):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [20], [1])
        original = db.query(Activity).one()
        duplicate = activity(db, 'duplicate', stamp=original.start_time, load=20, source='intervals')
        candidate = DuplicateCandidate(activity_id=duplicate.id, other_id=original.id, status='pending')
        db.add(candidate); db.commit(); identifier = candidate.id
    body = get(client)
    assert body['coverage']['duplicate_days'] == 1
    assert 'duplicate_review' in series(body)['rows'][0]['exclusion_reasons']
    assert client.post(f'/activities/duplicates/{identifier}/keep').status_code == 200
    assert series(get(client))['rows'][0]['eligible']
    with Session(engine) as db:
        db.get(DuplicateCandidate, identifier).status = 'pending'; db.commit()
    assert _merge_with_preview(client, f'/activities/duplicates/{identifier}/merge').status_code == 200
    row = series(get(client))['rows'][0]
    assert row['eligible'] and row['activities'] == 1 and row['load'] == 20


def test_unscanned_exact_duplicates_are_detected_without_creating_candidates(api):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [20], [1])
        original = db.query(Activity).one()
        activity(db, 'unreviewed', stamp=original.start_time, load=20)
        db.commit()
    assert get(client)['coverage']['duplicate_days'] == 1
    with Session(engine) as db:
        assert db.query(DuplicateCandidate).count() == 0


@pytest.mark.parametrize('counterpart', ['outside', 'orphan', 'midnight'])
def test_persisted_near_duplicates_include_outside_or_orphan_counterparts(api, counterpart):
    client, engine = api
    with Session(engine) as db:
        if counterpart == 'midnight':
            original = activity(db, 'boundary', stamp=datetime(2026,10,24,21,59), load=20)
            other = activity(db, 'other', stamp=datetime(2026,10,24,22,0), load=21)
        else:
            original = activity(db, 'near', TODAY - timedelta(days=1), load=20)
            other = activity(db, 'other', TODAY - timedelta(days=100), load=21) if counterpart == 'outside' else SimpleNamespace(id=99999)
        db.add(DuplicateCandidate(activity_id=original.id, other_id=other.id, status='pending'))
        db.commit()
    body = get(client)
    assert body['coverage']['duplicate_days'] == (2 if counterpart == 'midnight' else 1)
    assert body['quality']['pending_pairs'] == 1


def test_kept_reverse_decision_wins_over_pending_legacy_pair(api):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [20], [1])
        first = db.query(Activity).one()
        second = activity(db, 'near', load=21)
        db.add_all([DuplicateCandidate(activity_id=first.id,other_id=second.id,status='pending'),
                    DuplicateCandidate(activity_id=second.id,other_id=first.id,status='kept')])
        db.commit()
    assert get(client)['coverage']['duplicate_days'] == 0


def test_calendar_dates_across_dst_are_not_exact_hour_delays_and_today_training_is_excluded(api):
    client, engine = api
    with Session(engine) as db:
        # Rome dates: Oct24 23:59CEST, Oct25 00:00CEST, Oct26 00:00CET.
        activity(db, 'last-before-midnight', stamp=datetime(2026,10,24,21,59))
        activity(db, 'first-oct25', stamp=datetime(2026,10,24,22))
        activity(db, 'today', stamp=datetime(2026,10,25,23))
        activity(db, 'future', stamp=NOW + timedelta(days=1))
        for ago in (0,1,2):
            wellness(db, TODAY - timedelta(days=ago), 50)
        wellness(db, TODAY + timedelta(days=1), 999)
        db.commit()
    body = get(client, lag='1')
    assert {row['training_date'] for row in series(body)['rows']} == {'2026-10-24','2026-10-25'}
    assert body['coverage']['recorded_training_days'] == 2
    historical = get(client, as_of='2026-10-25', lag='1')
    assert [row['training_date'] for row in series(historical)['rows']] == ['2026-10-24']
    assert historical['period']['includes_partial_response_day'] is False


def test_lag_two_reports_intervening_activities_without_claiming_independence(api):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [20], [2], lag=2)
        activity(db, 'intervening', TODAY - timedelta(days=1), 90)
        db.commit()
    body = get(client, lag='2')
    row = series(body)['rows'][0]
    assert row['training_date'] == '2026-10-24' and row['response_date'] == '2026-10-26'
    assert row['change'] == 2 and row['eligible']
    assert row['intervening_activities'] == 1 and row['intervening_dates'] == ['2026-10-25']
    assert body['coverage']['intervening_activity_days'] == 1
    assert any('non sono campioni indipendenti' in note for note in body['methodology'])


def test_group_split_is_recomputed_for_each_metric_and_source(api):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [10,20,30,40], [1,2,3,4])
        rows = db.query(Activity).order_by(Activity.start_time).all()
        for row in rows[:2]:
            day = row.start_time.date()
            wellness(db, day, 60, 'resting_hr_bpm', 'manual')
            wellness(db, day + timedelta(days=1), 61, 'resting_hr_bpm', 'manual')
        db.commit()
    body = get(client)
    assert series(body)['load_split_median'] == 25
    assert series(body,'resting_hr_bpm','manual')['load_split_median'] == 35


def test_finite_daily_load_sum_overflow_is_explicit_and_json_stays_finite(api):
    client, engine = api
    with Session(engine) as db:
        pairs(db, [1e308], [1])
        activity(db, 'huge-second', load=1e308, stamp=datetime(2026,10,25,9))
        db.commit()
    body = get(client)
    row = series(body)['rows'][0]
    assert row['load'] is None and 'load_overflow' in row['exclusion_reasons']
    assert body['coverage']['load_overflow_days'] == 1
    json.dumps(body, allow_nan=False)


def test_even_medians_and_unrepresentable_group_difference_never_emit_infinity(api):
    client, engine = api
    with Session(engine) as db:
        for index in range(10):
            day = TODAY - timedelta(days=1 + index * 2)
            activity(db,str(index),day,1e307 if index < 5 else 1e308)
            wellness(db,day,1e308 if index < 5 else 1)
            wellness(db,day+timedelta(days=1),1 if index < 5 else 1e308)
        db.commit()
    body = get(client)
    item = series(body)
    assert item['load_split_median'] is not None
    assert not item['comparison']['available'] and item['comparison']['higher_minus_lower'] is None
    assert 'numerico' in item['comparison']['reason']
    json.dumps(body, allow_nan=False)


@pytest.mark.parametrize('params', [{'as_of':'1969-12-31'}, {'as_of':'2026-10-27'}, {'as_of':'9999-12-31'},
    {'as_of':'2026-02-30'}, {'days':27}, {'days':181}, {'lag':0}, {'lag':3}, {'lag':'1.5'}])
def test_invalid_or_future_requests_are_rejected(api, params):
    client, _ = api
    assert client.get('/dashboard/training-response',params=params).status_code == 422


def test_earliest_date_and_aware_now_are_supported_without_future_data(api):
    _, engine = api
    with Session(engine) as db:
        result = response_service.training_response(db,date(1970,1,1),now=NOW)
        assert result['period']['as_of'] == '1970-01-01'
        result = response_service.training_response(db,now=datetime(2026,10,27,0,30,tzinfo=timezone(timedelta(hours=1))))
        assert result['period']['as_of'] == '2026-10-27'


@pytest.mark.parametrize('limit', ['MAX_ACTIVITIES', 'MAX_OBSERVATIONS', 'MAX_WELLNESS', 'MAX_DUPLICATE_DECISIONS'])
def test_truncated_reads_fail_closed_with_visible_quality_flag(api, monkeypatch, limit):
    client, engine = api
    with Session(engine) as db:
        pairs(db,[10],[1])
        row = db.query(Activity).one()
        db.add(ActivityObservation(activity_id=row.id,source_key='intervals:limit',payload_json='{"load":10}'))
        db.add(DuplicateCandidate(activity_id=row.id,other_id=99999,status='kept'))
        db.commit()
    monkeypatch.setattr(response_service,limit,0)
    body = get(client)
    assert body['quality']['truncated']
    assert all(not item['comparison']['available'] for metric in body['metrics'] for item in metric['series'])
    assert body['coverage']['eligible_training_days'] == 0


def test_get_is_read_only_with_bounded_queries_and_no_hidden_duplicate_scan_writes(api):
    client, engine = api
    with Session(engine) as db:
        pairs(db,[10,20,30],[1,2,3]); db.commit()
        original = [(row.id,row.load) for row in db.query(Activity).order_by(Activity.id)]
    statements=[]
    def capture(connection,cursor,statement,parameters,context,executemany):
        statements.append(statement)
    event.listen(engine,'before_cursor_execute',capture)
    try:
        get(client)
    finally:
        event.remove(engine,'before_cursor_execute',capture)
    assert len(statements) <= 9
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
    with Session(engine) as db:
        assert original == [(row.id,row.load) for row in db.query(Activity).order_by(Activity.id)]
        assert db.query(DuplicateCandidate).count() == 0


def test_hosted_workspaces_never_share_response_pairs(tmp_path, monkeypatch):
    from app.commercial.middleware import ProductBoundary
    from app.commercial.accounts import COOKIE
    monkeypatch.setattr(database,'DATA_DIR',tmp_path)
    monkeypatch.setattr(response_service,'utc_now_naive',lambda:NOW)
    identifiers=[uuid4().hex,uuid4().hex]
    class Accounts:
        def authenticate(self,cookie):
            if cookie not in ('a','b'):return None
            return {'id':identifiers[0 if cookie=='a' else 1],'csrf':'test'}
        def limit(self,*args):return None
    app=FastAPI();app.include_router(routes_training_response.router)
    app.add_middleware(ProductBoundary,settings=SimpleNamespace(hosted=True,public_url='https://product.test',vault_key='test'),accounts=Accounts())
    try:
        for identifier,change in zip(identifiers,(1,9)):
            with database.workspace_scope(identifier),database.current_session() as db:
                pairs(db,[20],[change]);db.commit()
        with TestClient(app,base_url='https://product.test') as client:
            assert client.get('/dashboard/training-response').status_code==401
            for cookie,expected in [('a',1),('b',9)]:
                client.cookies.set(COOKIE,cookie)
                result=client.get('/dashboard/training-response',params={'lag':'1'})
                assert result.status_code==200
                assert result.headers['cache-control']=='no-store'
                assert series(result.json())['rows'][0]['change']==expected
        assert database.WORKSPACE.get() is None
    finally:
        for path in list(database._WORKSPACE_ENGINES):
            if str(tmp_path) in path:database._WORKSPACE_ENGINES.pop(path).dispose()
