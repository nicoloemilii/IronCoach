import json
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes_activity_detail import router
from app.db.database import Base, get_db
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.integrations import intervals
from app.services import activity_detail as detail


@pytest.fixture
def api(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI(); app.include_router(router)
    def database():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_db] = database
    detail._CACHE.clear()
    monkeypatch.setattr(detail.intervals_credentials, 'credentials', lambda: {})
    yield TestClient(app, client=('127.0.0.1', 55001)), engine
    detail._CACHE.clear(); engine.dispose()


def activity(db, *, payload=None, **values):
    content = dict(source='intervals', source_id='i12345', name='Bici registrata', sport='cycling',
                   start_time=datetime(2026, 10, 17, 8), duration_seconds=3600,
                   distance_meters=30000, load=50, avg_power=150, normalized_power=155)
    content.update(values)
    row = Activity(**content); db.add(row); db.flush()
    if payload is not None:
        db.add(ActivityObservation(activity_id=row.id, source_key='intervals:i12345', payload_json=json.dumps(payload)))
    db.commit(); return row.id


def test_recorded_summary_works_offline_without_inventing_gps_or_measurements(api):
    client, engine = api
    with Session(engine) as db:
        identifier = activity(db, elevation_gain_meters=None, avg_hr=None)
    response = client.get(f'/activities/{identifier}/detail')
    assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'
    value = response.json()
    assert value['summary']['distance_meters'] == 30000
    assert value['summary']['elevation_gain_meters'] is None and value['summary']['avg_hr'] is None
    assert value['summary']['avg_speed'] == pytest.approx(30000/3600)
    assert 'avg_speed' in value['derived_fields']
    assert value['route'] == {'available':False, 'points':[], 'sample_count':0, 'downsampled':False}
    assert value['streams'] == [] and value['coach_rating']['score'] is not None
    assert value['availability']['status'] == 'summary_only'


@pytest.mark.parametrize('ftp', [None, 200])
def test_power_band_requires_personal_ftp_and_never_invents_a_heart_rate_zone(api, ftp):
    client, engine = api
    from app.services.planning import PlanningProfile, save_settings
    with Session(engine) as db:
        profile = PlanningProfile()
        if ftp:
            profile.thresholds.cycling_ftp_watts.value = ftp
            profile.thresholds.cycling_ftp_watts.source = 'estimated'
        save_settings(db, profile)
        identifier = activity(db, payload={'streams': [{'type':'time','data':[0,60]},
                                                       {'type':'watts','data':[100,160]},
                                                       {'type':'heartrate','data':[120,150]}]})
    result = client.get(f'/activities/{identifier}/detail').json()['chart_references']
    assert 'heart_rate' not in result
    if ftp:
        assert result['power']['lower'] == 112 and result['power']['upper'] == 150
        assert 'attuale stimata' in result['power']['note']
    else:
        assert result == {}


def test_real_observation_null_and_zero_take_precedence_over_canonical_values(api):
    client, engine = api
    with Session(engine) as db:
        identifier = activity(db, payload={'load':None, 'elevation_gain_meters':0, 'avg_power':None}, elevation_gain_meters=50)
    value = client.get(f'/activities/{identifier}/detail').json()
    assert value['summary']['load'] is None and value['summary']['avg_power'] is None
    assert value['summary']['elevation_gain_meters'] == 0


def test_local_streams_keep_real_time_axis_null_gaps_and_negative_altitude(api):
    client, engine = api
    streams = [{'type':'time','data':[0, 2, 5]}, {'type':'watts','data':[0,None,300]},
               {'type':'altitude','data':[-15,-10,-5]},
               {'type':'latlng','data':[42.1,42.2,42.3], 'data2':[13.1,13.2,13.3]}]
    with Session(engine) as db:
        identifier = activity(db, payload={'streams':streams})
    value = client.get(f'/activities/{identifier}/detail').json()
    power = next(item for item in value['streams'] if item['kind']=='power')
    assert power['unit'] == 'W' and power['points'] == [{'time_s':0,'value':0},{'time_s':2,'value':None},{'time_s':5,'value':300}]
    assert next(item for item in value['streams'] if item['kind']=='altitude')['points'][0]['value'] == -15
    assert value['route']['available'] and value['route']['points'][1] == {'lat':42.2,'lon':13.2,'time_s':2}


def test_missing_time_never_becomes_an_invented_second_by_second_series():
    streams, route, count = detail.normalize_streams([{'type':'watts','data':[100,150]},
                {'type':'latlng','data':[[42.1,13.1],[42.2,13.2]]}], 'cycling')
    assert streams == [] and count == 0
    assert len(route) == 2 and all(point['time_s'] is None for point in route)


def test_running_pace_comes_only_from_observed_positive_speed():
    streams, _, _ = detail.normalize_streams([{'type':'time','data':[0,1,2]},
                  {'type':'velocity_smooth','data':[4,0,None]}], 'running')
    pace = next(item for item in streams if item['kind']=='pace')
    assert pace['unit'] == 's/km' and [point['value'] for point in pace['points']] == [250,None,None]


def test_future_dated_samples_never_enter_charts_or_gps_segments():
    streams, route, count = detail.normalize_streams([
        {'type':'time','data':[0,10,20]}, {'type':'watts','data':[100,200,300]},
        {'type':'latlng','data':[[42,13],[42.1,13.1],[42.2,13.2]]}
    ], 'cycling', max_time_s=15)
    assert [point['time_s'] for point in streams[0]['points']] == [0,10]
    assert [point['time_s'] for point in route] == [0,10] and count == 2


def test_downsample_keeps_peak_gaps_endpoints_and_honest_count():
    data = [100]*10000; data[5011] = 999; data[7001] = None
    streams, _, _ = detail.normalize_streams([{'type':'time','data':list(range(10000))},{'type':'watts','data':data}], 'cycling')
    power = streams[0]
    assert power['downsampled'] and power['sample_count'] == 10000
    assert len(power['points']) <= 900
    assert max(point['value'] or 0 for point in power['points']) == 999
    assert any(point['value'] is None for point in power['points'])
    assert power['points'][0]['time_s'] == 0 and power['points'][-1]['time_s'] == 9999


def test_automatic_protected_fetch_and_cache_do_not_return_credentials(api, monkeypatch):
    client, engine = api; calls=[]
    monkeypatch.setattr(detail.intervals_credentials,'credentials',lambda:{'api_key':'private-test-key','athlete_id':'123'})
    def remote(identifier, resource='', params=None):
        calls.append((identifier,resource,params))
        if resource == '':
            return {'average_heartrate':140, 'max_watts':600, 'private_token':'must-not-leak'}
        return [{'type':'time','data':[0,1]}, {'type':'heartrate','data':[130,140]},
                {'type':'latlng','data':[[42,13],[42.1,13.1]]}]
    monkeypatch.setattr(intervals,'get_activity_remote',remote)
    with Session(engine) as db:
        identifier=activity(db)
    first=client.get(f'/activities/{identifier}/detail').json()
    second=client.get(f'/activities/{identifier}/detail').json()
    assert first == second and len(calls) == 2
    assert first['summary']['avg_hr'] == 140 and first['summary']['max_power'] == 600
    assert first['availability']['remote_fetched'] and first['route']['available']
    assert 'private-test-key' not in json.dumps(first) and 'must-not-leak' not in json.dumps(first)
    assert {entry[1] for entry in calls} == {'','/streams.json'}


def test_map_fallback_uses_observed_coordinates_without_fake_times(api,monkeypatch):
    client,engine=api
    monkeypatch.setattr(detail.intervals_credentials,'credentials',lambda:{'api_key':'private-test-key','athlete_id':'123'})
    def remote(identifier,resource='',params=None):
        if resource=='/map': return {'latlngs':[[42,13],[42.1,13.1]]}
        if resource=='/streams.json': return [{'type':'time','data':[0,10]}, {'type':'watts','data':[100,200]}]
        return {}
    monkeypatch.setattr(intervals,'get_activity_remote',remote)
    with Session(engine) as db: identifier=activity(db)
    value=client.get(f'/activities/{identifier}/detail').json()
    assert value['route']['available'] and all(point['time_s'] is None for point in value['route']['points'])


def test_map_fallback_also_handles_an_empty_or_invalid_latlng_stream(api,monkeypatch):
    client,engine=api
    monkeypatch.setattr(detail.intervals_credentials,'credentials',lambda:{'api_key':'private-test-key','athlete_id':'123'})
    def remote(identifier,resource='',params=None):
        if resource=='/map': return {'latlngs':[[42,13],[42.1,13.1]]}
        if resource=='/streams.json': return [{'type':'time','data':[0,10]}, {'type':'latlng','data':[None,None]}, {'type':'watts','data':[100,200]}]
        return {}
    monkeypatch.setattr(intervals,'get_activity_remote',remote)
    with Session(engine) as db: identifier=activity(db)
    value=client.get(f'/activities/{identifier}/detail').json()
    assert value['route']['available'] and len(value['route']['points']) == 2


def test_provider_failure_preserves_recorded_summary_and_rating(api,monkeypatch):
    client,engine=api
    monkeypatch.setattr(detail.intervals_credentials,'credentials',lambda:{'api_key':'private-test-key','athlete_id':'123'})
    def remote(*args,**kwargs): raise intervals.IntervalsError('Intervals.icu non è raggiungibile.',502)
    monkeypatch.setattr(intervals,'get_activity_remote',remote)
    with Session(engine) as db: identifier=activity(db)
    response=client.get(f'/activities/{identifier}/detail'); value=response.json()
    assert response.status_code == 200 and value['summary']['duration_seconds'] == 3600
    assert value['coach_rating']['score'] is not None
    assert value['availability']['status'] == 'partial'
    assert 'non è raggiungibile' in value['availability']['message']


def test_fresh_remote_values_and_rating_agree_without_changing_the_saved_activity(api,monkeypatch):
    client,engine=api
    monkeypatch.setattr(detail.intervals_credentials,'credentials',lambda:{'api_key':'private-test-key','athlete_id':'123'})
    def remote(identifier,resource='',params=None):
        return {'moving_time':14400,'icu_training_load':240} if resource=='' else []
    monkeypatch.setattr(intervals,'get_activity_remote',remote)
    with Session(engine) as db:
        identifier=activity(db)
        original=db.get(Activity,identifier)
        for index in range(1,15):
            db.add(Activity(source='manual',source_id=f'history-{index}',sport='cycling',
                            start_time=original.start_time-timedelta(days=index*3),duration_seconds=3600,load=50))
        db.commit()
        before=detail.activity_ratings(db)[identifier]['score']
    result=client.get(f'/activities/{identifier}/detail').json()
    assert result['summary']['duration_seconds']==14400 and result['summary']['load']==240
    assert result['coach_rating']['score']<before
    assert any('240 punti di carico' in item['reason'] for item in result['coach_rating']['components'])
    with Session(engine) as db:
        saved=db.get(Activity,identifier)
        assert saved.duration_seconds==3600 and saved.load==50
        assert db.query(ActivityObservation).count()==0


def test_detail_is_local_only_and_unknown_activity_is_404(api):
    client,_=api
    assert client.get('/activities/9999/detail').status_code == 404
    assert client.get('/activities/9999/detail',headers={'origin':'https://example.com'}).status_code == 403


def test_protected_client_rejects_path_injection_without_network(monkeypatch):
    calls=[]
    monkeypatch.setattr(intervals.requests,'get',lambda *args,**kwargs:calls.append(args))
    for identifier,resource in [('../123',''),('https://example.com',''),('i123','/anything'),('i123?key=secret','')]:
        with pytest.raises(intervals.IntervalsError) as error: intervals.get_activity_remote(identifier,resource)
        assert error.value.status == 422
    assert not calls


def test_protected_client_uses_fixed_origin_auth_no_redirect_and_short_timeout(monkeypatch):
    calls=[]
    monkeypatch.setattr(intervals.intervals_credentials,'credentials',lambda:{'api_key':'private-test-key','athlete_id':'123'})
    class Response:
        status_code=200
        def json(self): return []
    def get(url,**kwargs): calls.append((url,kwargs)); return Response()
    monkeypatch.setattr(intervals.requests,'get',get)
    assert intervals.get_activity_remote('i123','/streams.json') == []
    assert calls[0][0] == 'https://intervals.icu/api/v1/activity/i123/streams.json'
    assert calls[0][1]['auth'] == ('API_KEY','private-test-key')
    assert calls[0][1]['allow_redirects'] is False and calls[0][1]['timeout'] == 12
