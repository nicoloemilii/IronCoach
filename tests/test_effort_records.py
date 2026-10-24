"""Historical curves keep source identity, time scope and missing data explicit."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.effort_models import EffortAnalysis
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.services import effort_records as service

NOW = datetime(2026, 10, 23, 12)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(service.intervals, 'get_activity_remote',
                        lambda *a, **k: pytest.fail('Unexpected provider request'))
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def activity(db, key='a', **changes):
    data = dict(source='intervals', source_id=key, sport='Ride', name='Synthetic ride',
                start_time=NOW-timedelta(hours=2), duration_seconds=60, avg_power=900)
    data.update(changes)
    row = Activity(**data); db.add(row); db.flush()
    return row


def raw(power=100, duration=60, *, elapsed=None):
    times = list(range(duration+1))
    return {'elapsed_time': duration if elapsed is None else elapsed,
            'streams': [{'type':'time','data':times}, {'type':'watts','data':[power]*len(times)}]}


def observation(db, row, key=None, payload=None, *, encoded=False):
    value = ActivityObservation(activity_id=row.id, source_key=key or 'intervals:'+row.source_id,
        payload_json=payload if encoded else json.dumps(raw() if payload is None else payload))
    db.add(value); db.flush()
    return value


def indexed(db, row, *, now=NOW, **options):
    snapshot = service.source_snapshot(db, row)
    result = service.analyze_snapshot(snapshot, now=now, **options)
    values = {key:value for key,value in result.items() if key not in ('stop_batch','provider_status')}
    values['payload_json'] = json.dumps(values['payload_json'], allow_nan=False)
    cached = EffortAnalysis(activity_id=row.id, **values); db.add(cached); db.flush()
    assert service.cache_valid(cached, snapshot, now)
    return cached


def test_canonical_older_source_wins_and_foreign_payload_is_not_parsed(db, monkeypatch):
    row = activity(db, 'intervals:canonical')
    canonical = observation(db,row,'intervals:canonical',raw(123))
    observation(db,row,'intervals:foreign', '{broken secret foreign payload',encoded=True)
    decoded=[]; original=service._decode_source
    monkeypatch.setattr(service, '_decode_source', lambda value:(decoded.append(value),original(value))[1])
    snapshot=service.source_snapshot(db,row)
    assert snapshot['resolution']=='canonical_identity' and snapshot['remote_id']=='canonical'
    assert snapshot['source_hash']==service._text_hash(canonical.payload_json)
    assert decoded==[canonical.payload_json]
    result=service.analyze_snapshot(snapshot,now=NOW)
    assert result['payload_json']['curve']['durations'][0]['mean_power_w']==123
    assert result['source_identity']=='intervals:canonical'


def test_equal_loads_do_not_make_foreign_recordings_interchangeable(db):
    row=activity(db,'canonical')
    observation(db,row,'intervals:alias-one',dict(raw(100),load=30))
    observation(db,row,'intervals:alias-two',dict(raw(500),load=30))
    snapshot=service.source_snapshot(db,row)
    assert snapshot['resolution']=='ambiguous_identity' and snapshot['remote_id'] is None
    assert snapshot['payload']=={} and snapshot['source_hash'] is None
    result=service.analyze_snapshot(snapshot,now=NOW,mode='intervals',refresh=True)
    assert result['status']=='unavailable' and result['error_code']=='ambiguous_identity'
    assert result['payload_json']['curve'] is None


def test_single_provider_identity_and_latest_nonprovider_have_explicit_resolution(db):
    row=activity(db,'canonical',source='garmin')
    observation(db,row,'garmin:first',raw(10)); observation(db,row,'garmin:second',raw(20))
    assert service.source_snapshot(db,row)['payload']['streams'][1]['data'][0]==20
    observation(db,row,'intervals:safe-remote',raw(30))
    snapshot=service.source_snapshot(db,row)
    assert snapshot['resolution']=='single_intervals' and snapshot['remote_id']=='safe-remote'


@pytest.mark.parametrize('key',['../other','https://host/path','x?token=y','x/y','x'*81,''])
def test_unsafe_provider_identity_never_fetches(db,key):
    row=activity(db,'canonical')
    observation(db,row,'intervals:'+key,{})
    snapshot=service.source_snapshot(db,row)
    assert snapshot['remote_id'] is None
    assert service.analyze_snapshot(snapshot,now=NOW,mode='intervals')['error_code']=='missing_provider_identity'


def test_selected_payload_mutation_changes_fingerprint_even_same_row_and_source_key(db):
    row=activity(db); stored=observation(db,row,payload=raw(100))
    before=service.source_snapshot(db,row)
    stored.payload_json=json.dumps(raw(200)); db.flush()
    after=service.source_snapshot(db,row)
    assert before['source_hash']!=after['source_hash'] and before['fingerprint']!=after['fingerprint']
    row.sport='Run'; db.flush()
    assert after['fingerprint']!=service.source_snapshot(db,row)['fingerprint']


def test_batch_source_reads_are_bounded_and_do_not_issue_per_activity_queries(db):
    rows=[activity(db,str(index)) for index in range(1101)]
    for row in rows: observation(db,row,payload={})
    statements=[]
    def collect(connection,cursor,statement,parameters,context,executemany):
        statements.append(statement)
    event.listen(db.bind,'before_cursor_execute',collect)
    try: snapshots=service.source_snapshots(db,rows)
    finally: event.remove(db.bind,'before_cursor_execute',collect)
    assert len(snapshots)==1101
    assert len(statements)==6  # metadata + selected payload, per <=500 activity IDs
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)


def test_observation_count_limit_fails_closed_without_parsing_foreign_payloads(db,monkeypatch):
    row=activity(db)
    for index in range(1001): observation(db,row,'intervals:'+str(index),'{bad',encoded=True)
    monkeypatch.setattr(service,'_decode_source',lambda value:pytest.fail('Over-limit source parsed'))
    snapshot=service.source_snapshot(db,row)
    assert snapshot['resolution']=='observation_limit' and snapshot['observation_count']==1001
    assert service.analyze_snapshot(snapshot,now=NOW)['status']=='unavailable'


@pytest.mark.parametrize('payload,code', [('{bad','invalid_source_payload'),('[]','invalid_source_payload'),
                                        ('['*1200+'0'+']'*1200,'invalid_source_payload')])
def test_malformed_original_payload_is_unavailable_without_crash(db,payload,code):
    row=activity(db); observation(db,row,payload=payload,encoded=True)
    result=service.analyze_snapshot(service.source_snapshot(db,row),now=NOW)
    assert result['status']=='unavailable' and result['error_code']==code
    assert 'streams' not in result['payload_json']


def test_source_payload_limit_applies_to_utf8_bytes(db,monkeypatch):
    monkeypatch.setattr(service,'MAX_SOURCE_CHARACTERS',64)
    row=activity(db); observation(db,row,payload=json.dumps({'name':'é'*30},ensure_ascii=False),encoded=True)
    result=service.analyze_snapshot(service.source_snapshot(db,row),now=NOW)
    assert result['error_code']=='source_payload_limit'


@pytest.mark.parametrize('power',[0,100,1e-100,1e100])
def test_true_raw_curve_persists_only_derived_values_including_zero(db,power):
    row=activity(db); observation(db,row,payload=raw(power))
    result=service.analyze_snapshot(service.source_snapshot(db,row),now=NOW)
    assert result['status']=='available' and result['payload_json']['has_power']
    assert result['payload_json']['curve']['durations'][0]['mean_power_w']==power
    serialized=json.dumps(result['payload_json'],allow_nan=False)
    assert '"data"' not in serialized and '"streams"' not in serialized
    assert result['source_fetched_at'] is None


@pytest.mark.parametrize('payload,status', [({},'missing_streams'),
    ({'elapsed_time':60,'streams':[{'type':'time','data':list(range(61))}]},'no_power'),
    (raw(100,3),'insufficient_data'),
    ({'streams':[{'type':'time','data':[0,1,1,2,6]},{'type':'watts','data':[100]*5}]},'unavailable')])
def test_unavailable_and_missing_results_are_valid_index_entries(db,payload,status):
    row=activity(db); observation(db,row,payload=payload)
    cached=indexed(db,row)
    assert cached.status==status
    value=service.records(db,now=NOW)
    assert value['coverage']['indexed']==1 and value['coverage']['stale']==0
    assert all(entry['mean_power_w'] is None for entry in value['records'])


def test_remote_missing_watts_fetches_only_time_power_and_actual_elapsed(db,monkeypatch):
    row=activity(db,duration_seconds=6)
    observation(db,row,payload={'streams':[{'type':'heartrate','data':[150]*61}]})
    calls=[]
    def fetch(identifier,resource='',params=None):
        calls.append((identifier,resource,params))
        return {'elapsed_time':120,'moving_time':6} if not resource else raw(123)['streams']
    monkeypatch.setattr(service.intervals,'get_activity_remote',fetch)
    result=service.analyze_snapshot(service.source_snapshot(db,row),now=NOW,mode='intervals')
    assert calls==[('a','',None),('a','/streams.json',{'types':'time,watts'})]
    assert result['source_fetched_at']==NOW and result['status']=='available'
    payload=result['payload_json']
    assert payload['window']['end_s']==120 and payload['input_partial']
    assert payload['curve']['coverage']['coverage_pct']==50
    assert payload['curve']['source']=='intervals_remote_streams'
    assert len(result['source_hash'])==64 and result['source_hash']!=service.source_snapshot(db,row)['source_hash']
    assert '"data"' not in json.dumps(payload)


def test_stored_curve_reused_without_remote_but_refresh_explicitly_fetches(db,monkeypatch):
    row=activity(db); observation(db,row,payload=raw(50))
    snapshot=service.source_snapshot(db,row)
    assert service.analyze_snapshot(snapshot,now=NOW,mode='intervals')['source_fetched_at'] is None
    calls=[]
    monkeypatch.setattr(service.intervals,'get_activity_remote',lambda *a: calls.append(a) or
        ({'elapsed_time':60} if len(a)==1 else raw(75)['streams']))
    result=service.analyze_snapshot(snapshot,now=NOW,mode='intervals',refresh=True)
    assert len(calls)==2 and result['payload_json']['curve']['durations'][0]['mean_power_w']==75


def test_remote_payload_size_limit_rejects_before_computation_without_raw_or_error_leak(db,monkeypatch):
    row=activity(db); snapshot=service.source_snapshot(db,row)
    monkeypatch.setattr(service,'MAX_SOURCE_CHARACTERS',128)
    monkeypatch.setattr(service.intervals,'get_activity_remote',lambda *a:
        {'elapsed_time':60} if len(a)==1 else raw(100)['streams'])
    monkeypatch.setattr(service,'best_power_efforts',lambda *a:pytest.fail('Oversized payload analyzed'))
    result=service.analyze_snapshot(snapshot,now=NOW,mode='intervals')
    assert result['status']=='unavailable' and result['error_code']=='provider_payload_limit'
    assert result['payload_json']['curve'] is None and not result['stop_batch']
    assert '"data"' not in json.dumps(result['payload_json'])


def test_streaming_remote_hash_matches_independent_canonical_json_sha(db,monkeypatch):
    row=activity(db); snapshot=service.source_snapshot(db,row)
    streams=raw(100)['streams']; summary={'elapsed_time':60,'moving_time':20,'unrelated':'excluded'}
    monkeypatch.setattr(service.intervals,'get_activity_remote',lambda *a:summary if len(a)==1 else streams)
    result=service.analyze_snapshot(snapshot,now=NOW,mode='intervals')
    canonical=json.dumps({'elapsed_time':60,'streams':streams},sort_keys=True,separators=(',',':'),ensure_ascii=True)
    assert result['status']=='available'
    assert result['source_hash']==hashlib.sha256(canonical.encode('ascii')).hexdigest()


@pytest.mark.parametrize('status,stop,code', [(401,True,'provider_auth'),(403,True,'provider_auth'),
    (429,True,'provider_rate_limited'),(409,True,'provider_connection'),(404,False,'provider_unavailable'),
    (502,False,'provider_unavailable')])
def test_provider_failures_are_sanitized_and_auth_rate_limits_stop_batch(db,monkeypatch,status,stop,code):
    row=activity(db)
    def fail(*a,**k): raise service.intervals.IntervalsError('secret raw response token=hidden',status)
    monkeypatch.setattr(service.intervals,'get_activity_remote',fail)
    result=service.analyze_snapshot(service.source_snapshot(db,row),now=NOW,mode='intervals')
    assert result['stop_batch']==stop and result['error_code']==code and result['provider_status']==status
    assert 'hidden' not in repr(result) and 'token=' not in repr(result)


def test_future_or_clipped_samples_cannot_supply_future_records(db):
    future=activity(db,'future',start_time=NOW+timedelta(seconds=1)); observation(db,future)
    assert service.analyze_snapshot(service.source_snapshot(db,future),now=NOW)['error_code']=='future_or_invalid_activity'
    current=activity(db,'current',start_time=NOW-timedelta(seconds=30)); observation(db,current,payload=raw(100))
    result=service.analyze_snapshot(service.source_snapshot(db,current),now=NOW)
    assert result['payload_json']['window']['end_s']==30
    assert result['payload_json']['input_partial']
    assert result['payload_json']['curve']['durations'][3]['mean_power_w'] is None


def test_records_dates_use_rome_activity_start_inclusive_28_and_90_days(db):
    # Oct23 Rome -> 28-day interval begins Sep26 00:00 CEST (Sep25 22:00 UTC).
    before=activity(db,'before',start_time=datetime(2026,9,25,21,59,59)); observation(db,before,payload=raw(900)); indexed(db,before)
    edge=activity(db,'edge',start_time=datetime(2026,9,25,22)); observation(db,edge,payload=raw(200)); indexed(db,edge)
    recent=activity(db,'recent'); observation(db,recent,payload=raw(100)); indexed(db,recent)
    future=activity(db,'future',start_time=NOW+timedelta(seconds=1)); observation(db,future,payload=raw(1000))
    last28=service.records(db,period='last_28_days',now=NOW)
    assert last28['period']=={'key':'last_28_days','start':'2026-09-26','end':'2026-10-23','timezone':'Europe/Rome'}
    assert last28['coverage']['total']==2 and last28['records'][0]['activity']['id']==edge.id
    last90=service.records(db,period='last_90_days',now=NOW)
    assert last90['period']['start']=='2026-07-26' and last90['coverage']['total']==3
    assert last90['records'][0]['activity']['id']==before.id


def test_sports_never_mix_and_supported_aliases_use_same_category(db):
    for index,sport in enumerate(['Ride','VirtualRide','Run','TrailRun','Swimming','weight_lifting','palestra','Workout','Walk']):
        row=activity(db,str(index),sport=sport); observation(db,row,payload=raw(100+index)); indexed(db,row)
    expected={'cycling':2,'running':2,'swimming':1,'strength':2,'other':2}
    for sport,count in expected.items():
        value=service.records(db,sport=sport,now=NOW)
        assert value['coverage']['total']==count
        assert value['records'][0]['candidate_coverage']=={'available':count,'total':count}


def test_equal_records_choose_earliest_activity_then_interval_then_id(db):
    later=activity(db,'later'); observation(db,later,payload=raw(100)); indexed(db,later)
    earlier=activity(db,'earlier',start_time=NOW-timedelta(days=2)); observation(db,earlier,payload=raw(100)); indexed(db,earlier)
    tied=activity(db,'tied',start_time=earlier.start_time); observation(db,tied,payload=raw(100)); indexed(db,tied)
    result=service.records(db,now=NOW)
    assert result['records'][0]['activity']['id']==earlier.id
    assert result['records'][0]['start_s']==0


def test_pending_stale_and_valid_zero_are_counted_without_inventing_records(db):
    valid=activity(db,'zero'); observation(db,valid,payload=raw(0)); indexed(db,valid)
    pending=activity(db,'pending'); observation(db,pending)
    stale=activity(db,'stale'); stored=observation(db,stale); indexed(db,stale)
    stored.payload_json=json.dumps(raw(900)); db.flush()
    result=service.records(db,now=NOW)
    assert result['coverage']==dict(total=3,indexed=1,with_power=1,missing_streams=0,no_power=0,
        unavailable=0,stale=1,pending=1,complete_index=False,input_partial=True)
    assert result['records'][0]['mean_power_w']==0
    assert result['records'][0]['candidate_coverage']=={'available':1,'total':3}


@pytest.mark.parametrize('field,value', [('mean_power_w',-1),('mean_power_w',float('inf')),
    ('mean_power_w',True),('start_s',-1),('start_s',float('nan')),('end_s',500),
    ('segment_start_s',-1),('segment_end_s',4),('coverage_pct',99),('covered_seconds',4)])
def test_invalid_cached_windows_are_stale_never_record_candidates(db,field,value):
    row=activity(db); observation(db,row); cache=indexed(db,row)
    payload=json.loads(cache.payload_json); payload['curve']['durations'][0][field]=value
    cache.payload_json=json.dumps(payload); db.flush()
    result=service.records(db,now=NOW)
    assert result['coverage']['stale']==1 and result['coverage']['indexed']==0
    assert result['records'][0]['mean_power_w'] is None


@pytest.mark.parametrize('field,value', [('algorithm_version','old'),('fingerprint','forged'),
    ('source_hash','wrong'),('source_identity','intervals:foreign'),('status','no_power'),
    ('cutoff_utc',NOW+timedelta(seconds=1)),('analyzed_at',NOW+timedelta(seconds=1)),
    ('source_fetched_at',NOW+timedelta(seconds=1)),('payload_json','[]'),('payload_json','{"a":NaN}')])
def test_invalid_cache_metadata_is_stale(db,field,value):
    row=activity(db); observation(db,row); cache=indexed(db,row)
    setattr(cache,field,value); db.flush()
    assert not service.cache_valid(cache,service.source_snapshot(db,row),NOW)
    assert service.records(db,now=NOW)['coverage']['stale']==1


def test_cache_cannot_claim_future_window_even_when_activity_start_is_in_the_past(db):
    row=activity(db,start_time=NOW-timedelta(seconds=30)); observation(db,row,payload=raw(100,10)); cache=indexed(db,row)
    payload=json.loads(cache.payload_json); payload['window']['end_s']=3600
    payload['curve']['coverage']['window_seconds']=3600
    cache.payload_json=json.dumps(payload); db.flush()
    assert service.records(db,now=NOW)['coverage']['stale']==1


def test_records_is_select_only_with_no_analysis_network_or_raw_output(db,monkeypatch):
    row=activity(db); observation(db,row); indexed(db,row); db.commit()
    before={table.name:list(db.execute(table.select())) for table in Base.metadata.sorted_tables}
    db.rollback()
    statements=[]
    event.listen(db.bind,'before_cursor_execute',lambda c,cu,s,p,co,e:statements.append(s))
    monkeypatch.setattr(service,'best_power_efforts',lambda *a:pytest.fail('GET analyzed samples'))
    result=service.records(db,now=NOW)
    after={table.name:list(db.execute(table.select())) for table in Base.metadata.sorted_tables}
    assert before==after
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
    assert '"data"' not in json.dumps(result,allow_nan=False)
    assert result['coverage']['complete_index'] and result['records'][0]['analysis']['source_hash']


def test_empty_history_returns_all_durations_and_no_claimed_record(db):
    result=service.records(db,now=NOW)
    assert result['coverage']['total']==0 and result['coverage']['complete_index']
    assert result['period']['start'] is None
    assert [row['duration_seconds'] for row in result['records']]==list(service.DURATIONS)
    assert all(row['status']=='insufficient_data' and row['mean_power_w'] is None for row in result['records'])


def test_connection_probe_returns_only_boolean_and_handles_secret_read_failure(monkeypatch):
    monkeypatch.setattr(service.intervals.intervals_credentials,'credentials',lambda:{'api_key':'private'})
    assert service.provider_available() is True
    def fail(): raise RuntimeError('protected secret unavailable')
    monkeypatch.setattr(service.intervals.intervals_credentials,'credentials',fail)
    assert service.provider_available() is False
