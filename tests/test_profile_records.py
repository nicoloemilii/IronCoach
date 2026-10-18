"""Observed weights and whole-activity records never become invented biometrics or segments."""
from datetime import date, datetime, timedelta
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_health_reports
from app.db.database import Base, get_db
from app.db.iron_models import ActivityObservation, HealthMetric
from app.db.models import Activity, DailyMetrics
from app.services import athlete_snapshot, health, health_reports, planning

TODAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 18, 12)


@pytest.fixture
def profile_api(monkeypatch):
    for module in (routes_health_reports, athlete_snapshot, health_reports):
        monkeypatch.setattr(module, 'athlete_today', lambda: TODAY)
    for module in (athlete_snapshot, health_reports):
        monkeypatch.setattr(module, 'utc_now_naive', lambda: NOW)
    monkeypatch.setattr(planning, 'load_profile', lambda: {'weight_kg':55, 'ftp_watts':170})
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI(); app.include_router(routes_health_reports.router)
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def add_activity(db, identifier, ago, **changes):
    value = dict(source='intervals', source_id=identifier, sport='cycling', name=identifier,
                 start_time=datetime.combine(TODAY-timedelta(days=ago),datetime.min.time())+timedelta(hours=8),
                 duration_seconds=3600, distance_meters=40000, elevation_gain_meters=400, avg_power=200,
                 normalized_power=220, avg_speed=10, calories=600, load=40)
    value.update(changes)
    row = Activity(**value);db.add(row);db.flush();return row


def test_wellness_weight_is_imported_in_kg_and_temporary_profile_copies_are_not_observations(profile_api):
    _, engine = profile_api
    with Session(engine) as db:
        count, errors = health.import_wellness(db, [
            {'id':str(TODAY-timedelta(days=2)), 'weight':72.5, 'tempWeight':False},
            {'id':str(TODAY-timedelta(days=1)), 'weight':72.5, 'tempWeight':True},
            {'id':str(TODAY), 'weight':0},
        ])
        assert count == 3 and not errors
        metric = next(item for item in health.health_response(db,TODAY-timedelta(days=27),TODAY)['metrics'] if item['kind']=='weight_kg')
        assert metric['unit']=='kg' and metric['latest']['value']==72.5
        assert len(metric['points'])==1 and metric['latest']['date']==str(TODAY-timedelta(days=2))
        assert db.query(HealthMetric).filter_by(date=str(TODAY),kind='weight_kg').one().value is None


@pytest.mark.parametrize('value',[-1,True,float('nan'),float('inf'),'invalid'])
def test_invalid_weight_does_not_poison_the_import_session(profile_api,value):
    _,engine=profile_api
    with Session(engine) as db:
        count,errors=health.import_wellness(db,[{'id':str(TODAY),'weight':value},{'id':str(TODAY-timedelta(days=1)),'weight':70}])
        assert count==1 and len(errors)==1
        assert db.query(HealthMetric).filter_by(kind='weight_kg').one().value==70


def test_cleared_weight_does_not_reappear_and_a_separately_observed_manual_weight_is_preserved(profile_api):
    _,engine=profile_api
    with Session(engine) as db:
        assert health.import_wellness(db,[{'id':str(TODAY),'weight':71}])==(1,[])
        assert health.import_wellness(db,[{'id':str(TODAY),'weight':None}])==(1,[])
        assert not [point for point in health.series(db,TODAY,TODAY) if point['kind']=='weight_kg']
        db.add(DailyMetrics(date=str(TODAY),sleep_hours=8))
        db.add(HealthMetric(date=str(TODAY),kind='weight_kg',value=70.5,unit='kg',source='manual',observed_at=str(TODAY),synced_at=NOW.isoformat()))
        db.commit()
        point=next(point for point in health.series(db,TODAY,TODAY) if point['kind']=='weight_kg')
        assert point['value']==70.5 and point['source']=='manual'
        assert health.import_wellness(db,[{'id':str(TODAY),'weight':70.2}])==(1,[])
        point=next(point for point in health.series(db,TODAY,TODAY) if point['kind']=='weight_kg')
        assert point['value']==70.2 and point['source']=='intervals'


def test_derived_profile_keeps_default_weight_and_default_ftp_unknown(profile_api):
    client,_=profile_api
    body=client.get('/athlete/derived-profile').json()
    assert body['health']['weight']['latest'] is None
    assert body['health']['weight']['change_28d_kg'] is None
    assert 'weight_kg' in body['unknown_fields']
    assert body['thresholds']=={}
    assert body['health']['metrics']==[]


def test_weight_history_and_known_metrics_refresh_from_observed_values(profile_api):
    client,engine=profile_api
    with Session(engine) as db:
        health.import_wellness(db,[{'id':str(TODAY-timedelta(days=7)),'weight':71.5,'restingHR':50},
                                  {'id':str(TODAY),'weight':70.8,'hrv':60}])
    body=client.get('/athlete/derived-profile').json()
    assert body['health']['weight']['latest']['value']==70.8
    assert body['health']['weight']['change_28d_kg']==-.7
    assert len(body['health']['weight']['points'])==2
    assert {item['kind'] for item in body['health']['metrics']}=={'weight_kg','resting_hr_bpm','hrv_ms'}
    assert 'weight_kg' not in body['unknown_fields']
    assert 'height_cm' in body['unknown_fields']
    historical=client.get(f'/athlete/derived-profile?day={TODAY-timedelta(days=1)}').json()
    assert historical['health']['weight']['latest']['value']==71.5
    assert historical['health']['weight']['change_28d_kg'] is None


def test_all_time_and_recent_records_are_distinct_whole_activity_observations(profile_api):
    client,engine=profile_api
    with Session(engine) as db:
        older=add_activity(db,'Older long ride',120,distance_meters=200000,duration_seconds=8*3600,elevation_gain_meters=3000,avg_power=280,normalized_power=290,avg_speed=12)
        recent=add_activity(db,'Recent ride',5,distance_meters=80000,duration_seconds=3*3600,elevation_gain_meters=800,avg_power=220,normalized_power=230,avg_speed=10)
        add_activity(db,'Run',2,sport='running',distance_meters=10000,duration_seconds=3600,avg_power=None,normalized_power=None,avg_speed=3)
        add_activity(db,'Future ride',-1,distance_meters=999000,duration_seconds=999999,avg_power=999)
        db.commit();old_id=older.id;recent_id=recent.id
    body=client.get('/athlete/derived-profile').json()
    bike=next(item for item in body['sports'] if item['sport']=='bike')
    assert body['totals']['all_time']['activities']==3
    assert body['totals']['days_90']['activities']==2
    assert bike['records']['all_time']['longest_distance_km']['value']==200
    assert bike['records']['days_90']['longest_distance_km']['value']==80
    assert bike['records']['all_time']['longest_duration_min']['value']==480
    assert bike['records']['days_90']['most_elevation_m']['value']==800
    assert bike['records']['all_time']['highest_avg_speed_kmh']['value']==43.2
    assert bike['records']['days_90']['highest_avg_speed_kmh']['value']==36
    assert bike['records']['all_time']['highest_avg_power_w']['activity_id']==old_id
    assert bike['records']['days_90']['highest_avg_power_w']['activity_id']==recent_id
    assert all(record['scope']=='whole_activity' for record in bike['records']['all_time'].values() if record)
    assert 'segmenti' in body['records_note'] and 'FTP' in body['records_note']


def test_missing_records_remain_null_without_computing_fake_speed_or_power(profile_api):
    client,engine=profile_api
    with Session(engine) as db:
        add_activity(db,'Distance without sensors',1,distance_meters=10000,duration_seconds=3600,avg_speed=None,avg_power=None,normalized_power=None,elevation_gain_meters=0)
        db.commit()
    body=client.get('/athlete/derived-profile').json()
    bike=next(item for item in body['sports'] if item['sport']=='bike')
    records=bike['records']['all_time']
    assert records['highest_avg_speed_kmh'] is None and records['highest_avg_power_w'] is None
    assert records['highest_normalized_power_w'] is None
    assert records['most_elevation_m']['value']==0
    assert next(item for item in body['sports'] if item['sport']=='run')['records']['all_time']['longest_distance_km'] is None


def test_totals_sum_only_documented_fields_and_do_not_resurrect_cleared_remote_load(profile_api):
    client,engine=profile_api
    with Session(engine) as db:
        one=add_activity(db,'Known',1,distance_meters=20000,elevation_gain_meters=200,load=30)
        two=add_activity(db,'Unknown',3,distance_meters=None,elevation_gain_meters=None,load=50)
        db.add(ActivityObservation(activity_id=two.id,source_key='intervals:Unknown',payload_json=json.dumps({'load':None})))
        db.commit()
    totals=client.get('/athlete/derived-profile').json()['totals']['all_time']
    assert totals['distance_km']==20 and totals['elevation_m']==200 and totals['load']==30
    assert totals['coverage']['distance_meters']==1 and totals['activities']==2


def test_measured_thresholds_are_retained_without_being_replaced_by_power_records(profile_api):
    client,engine=profile_api
    with Session(engine) as db:
        settings=planning.load_settings(db)
        settings['thresholds']['cycling_ftp_watts']={'value':235,'source':'measured','measured_on':'2026-10-01'}
        planning.save_settings(db,settings)
        add_activity(db,'Short hard ride',1,avg_power=350)
        db.commit()
    body=client.get('/athlete/derived-profile').json()
    assert body['thresholds']['cycling_ftp_watts']['value']==235
    assert next(item for item in body['sports'] if item['sport']=='bike')['records']['all_time']['highest_avg_power_w']['value']==350
    with Session(engine) as db:
        assert planning.load_settings(db)['thresholds']['cycling_ftp_watts']['value']==235
