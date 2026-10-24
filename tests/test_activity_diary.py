"""Actual bounded diary HTTP, compared with the complete rating reference."""
import base64
from datetime import datetime,timedelta
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,event,inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes_dashboard import router
from app.db.database import Base,get_db,WORKSPACE
from app.db.models import Activity
from app.db.iron_models import ActivityObservation
from app.services import activity_diary
from app.services.activity_rating import activity_ratings

NOW=datetime(2026,10,22,12)


@pytest.fixture
def api(monkeypatch):
    engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine);app=FastAPI();app.include_router(router)
    def database():
        with Session(engine) as db:yield db
    app.dependency_overrides[get_db]=database
    monkeypatch.setattr(activity_diary,'utc_now_naive',lambda:NOW)
    with TestClient(app) as client:yield client,engine
    engine.dispose()


def row(db,index,**changes):
    values=dict(source='manual',source_id=f'qa-{index}',sport='Ride',name=f'Uscita {index}',
                start_time=NOW-timedelta(days=index+1),duration_seconds=600,load=10)
    values.update(changes);value=Activity(**values);db.add(value);db.flush();return value


def test_pages_have_stable_date_id_ties_counts_and_identical_complete_ratings(api):
    client,engine=api
    with Session(engine) as db:
        for index in range(137):row(db,index,start_time=NOW-timedelta(hours=index//3+1))
        db.commit();reference=activity_ratings(db,now=NOW)
        expected=[item.id for item in db.query(Activity).order_by(Activity.start_time.desc(),Activity.id.desc())]
    identifiers=[];cursor=None;pages=0
    while True:
        params={'limit':50}
        if cursor:params['cursor']=cursor
        response=client.get('/activities/diary',params=params);assert response.status_code==200
        value=response.json();pages+=1;assert value['total']==value['archive_total']==137
        assert len(value['items'])<=50
        for item in value['items']:
            identifiers.append(item['id']);assert item['coach_rating']==json.loads(json.dumps(reference[item['id']]))
        cursor=value['next_cursor']
        if not value['has_more']:assert cursor is None;break
    assert pages==3 and identifiers==expected and len(set(identifiers))==137


def test_unicode_name_search_is_literal_and_sports_use_shared_category(api):
    client,engine=api
    with Session(engine) as db:
        row(db,1,name='CAFÉ %_ / prova',sport='Crossfit')
        row(db,2,name='café abcXX / prova',sport='Ride')
        row(db,3,name='STRASSE',sport='Workout')
        db.commit()
    value=client.get('/activities/diary',params={'q':'café %_ /','sport':'strength'}).json()
    assert value['total']==1 and value['archive_total']==3 and value['items'][0]['sport_category']=='strength'
    assert client.get('/activities/diary',params={'q':'straße','sport':'other'}).json()['total']==1
    assert client.get('/activities/diary',params={'q':"' OR 1=1 --"}).json()['total']==0


def test_new_imports_are_excluded_from_existing_page_boundary_until_refresh(api):
    client,engine=api
    with Session(engine) as db:
        for index in range(5):row(db,index)
        db.commit()
    first=client.get('/activities/diary',params={'limit':2}).json()
    with Session(engine) as db:row(db,99,start_time=NOW-timedelta(days=3,hours=1));db.commit()
    next_page=client.get('/activities/diary',params={'limit':10,'cursor':first['next_cursor']}).json()
    assert next_page['total']==5 and next_page['snapshot']==first['snapshot']
    assert all(item['name']!='Uscita 99' for item in next_page['items'])
    assert client.get('/activities/diary').json()['total']==6


@pytest.mark.parametrize('params',[{'limit':0},{'limit':101},{'sport':'arbitrary'},{'q':'x'*201},{'cursor':'!invalid'}, {'cursor':'x'*2049}])
def test_invalid_query_is_rejected_before_any_data_write(api,params):
    client,_=api;assert client.get('/activities/diary',params=params).status_code==422


def test_cursor_is_bound_to_filters_and_workspace_and_rejects_malformed_fields(api):
    client,engine=api
    with Session(engine) as db:
        for index in range(3):row(db,index)
        db.commit()
    first=client.get('/activities/diary',params={'limit':1}).json();cursor=first['next_cursor']
    assert client.get('/activities/diary',params={'cursor':cursor,'q':'different'}).status_code==422
    # Service-level context mirrors the hosted boundary's session binding.
    token=WORKSPACE.set('0123456789abcdef0123456789abcdef')
    try:
        with Session(engine) as db:
            with pytest.raises(HTTPException) as caught:activity_diary.diary_page(db,cursor=cursor,now=NOW)
            assert caught.value.status_code==422
    finally:WORKSPACE.reset(token)
    raw=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
    for key,value in [('max_id',True),('before',['bad',1]),('as_of','9999-01-01T00:00:00Z'),('v',True)]:
        changed={**raw,key:value};bad=activity_diary._encode(changed)
        assert client.get('/activities/diary',params={'cursor':bad}).status_code==422


def test_missing_and_conflicting_loads_are_not_replaced_by_np_or_another_alias(api):
    client,engine=api
    with Session(engine) as db:
        cleared=row(db,0,source='intervals',source_id='primary',load=80,normalized_power=300)
        db.add_all([ActivityObservation(activity_id=cleared.id,source_key='intervals:primary',payload_json='{"load":null}'),
                    ActivityObservation(activity_id=cleared.id,source_key='intervals:another',payload_json='{"load":999}')])
        conflict=row(db,1,load=90,normalized_power=300)
        db.add_all([ActivityObservation(activity_id=conflict.id,source_key='intervals:a',payload_json='{"load":20}'),
                    ActivityObservation(activity_id=conflict.id,source_key='intervals:b',payload_json='{"load":30}')]);db.commit()
    values=client.get('/activities/diary').json()['items']
    assert all(item['load'] is None for item in values)
    assert values[0]['load_evidence']['reason']=='canonical_identity'
    assert values[1]['load_evidence']['status']=='conflict'


def test_future_record_is_visible_but_never_receives_a_completed_rating(api):
    client,engine=api
    with Session(engine) as db:row(db,0,start_time=NOW+timedelta(days=1));db.commit()
    value=client.get('/activities/diary').json();assert value['includes_future']
    assert value['items'][0]['coach_rating']['score'] is None


def test_empty_and_malformed_numeric_records_stay_serializable(api):
    client,engine=api
    assert client.get('/activities/diary').json()['total']==0
    with Session(engine) as db:row(db,1,duration_seconds=float('inf'),distance_meters=-1);db.commit()
    value=client.get('/activities/diary').json()['items'][0]
    assert value['duration_seconds'] is value['distance_meters'] is None


def test_read_queries_are_bounded_and_do_not_create_indexes_or_modify_rows(api):
    client,engine=api
    with Session(engine) as db:
        for index in range(300):row(db,index)
        db.commit()
    statements=[]
    def capture(_connection,_cursor,statement,*_):statements.append(statement)
    event.listen(engine,'before_cursor_execute',capture)
    try:value=client.get('/activities/diary',params={'limit':10}).json()
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert len(value['items'])==10
    assert all(s.lstrip().upper().startswith('SELECT') for s in statements)
    assert any('LIMIT' in s and 'activities.start_time DESC' in s for s in statements)
    assert all('WHERE' in s or 'count(' in s.lower() or 'max(' in s.lower() for s in statements if 'FROM activities' in s)
    assert 'ix_activities_start_id' in {index['name'] for index in inspect(engine).get_indexes('activities')}


def test_hosted_diary_requires_login_and_rejects_another_workspaces_cursor(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.db import database
    from app.commercial.middleware import ProductBoundary
    from app.commercial.accounts import COOKIE
    identifiers=[uuid4().hex,uuid4().hex]
    monkeypatch.setattr(database,'DATA_DIR',tmp_path)
    monkeypatch.setattr(activity_diary,'utc_now_naive',lambda:NOW)
    class Accounts:
        def authenticate(self,cookie):
            return {'id':identifiers[0 if cookie=='a' else 1],'csrf':'test'} if cookie in ('a','b') else None
        def limit(self,*args):return None
    app=FastAPI();app.include_router(router)
    app.add_middleware(ProductBoundary,settings=SimpleNamespace(hosted=True,public_url='https://product.test',vault_key='test'),accounts=Accounts())
    try:
        for identifier,label in zip(identifiers,('Alice','Bruno')):
            with database.workspace_scope(identifier),database.current_session() as db:
                row(db,0,name=label+' one');row(db,1,name=label+' two');db.commit()
        with TestClient(app,base_url='https://product.test') as client:
            assert client.get('/activities/diary').status_code==401
            client.cookies.set(COOKIE,'a');first=client.get('/activities/diary?limit=1').json()
            assert first['items'][0]['name'].startswith('Alice')
            client.cookies.set(COOKIE,'b')
            assert client.get('/activities/diary',params={'cursor':first['next_cursor']}).status_code==422
            other=client.get('/activities/diary').json()
            assert all(item['name'].startswith('Bruno') for item in other['items'])
        assert database.WORKSPACE.get() is None
    finally:
        for key in list(database._WORKSPACE_ENGINES):
            if str(tmp_path) in key:database._WORKSPACE_ENGINES.pop(key).dispose()


def test_physical_history_index_is_added_once_with_backup_and_unchanged_records(tmp_path):
    from contextlib import closing
    import sqlite3
    from app.db.history_index import ensure_history_index
    filename=tmp_path/'legacy.db';engine=create_engine(f'sqlite:///{filename}')
    Base.metadata.create_all(engine)
    with engine.begin() as connection:connection.exec_driver_sql('DROP INDEX ix_activities_start_id')
    with Session(engine) as db:row(db,1,duration_seconds=0,load=None);db.commit()
    with closing(sqlite3.connect(filename)) as connection:
        before=connection.execute('SELECT * FROM activities').fetchall()
    ensure_history_index(engine)
    snapshots=list((tmp_path/'backups').glob('*-before-history-index-*.db'));assert len(snapshots)==1
    with closing(sqlite3.connect(snapshots[0])) as backup:
        assert backup.execute('SELECT * FROM activities').fetchall()==before
        assert backup.execute("SELECT name FROM sqlite_master WHERE name='ix_activities_start_id'").fetchall()==[]
    with closing(sqlite3.connect(filename)) as connection:
        assert connection.execute('SELECT * FROM activities').fetchall()==before
        plan=connection.execute('EXPLAIN QUERY PLAN SELECT * FROM activities WHERE id<=100 ORDER BY start_time DESC,id DESC LIMIT 50').fetchall()
        assert any('ix_activities_start_id' in item[3] for item in plan)
    ensure_history_index(engine);assert len(list((tmp_path/'backups').glob('*.db')))==1
    engine.dispose()


def test_page_counts_rows_and_ratings_share_one_read_snapshot_during_sync(tmp_path,monkeypatch):
    filename=tmp_path/'snapshot.db';engine=create_engine(f'sqlite:///{filename}')
    Base.metadata.create_all(engine)
    with engine.connect() as connection:connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    with Session(engine) as db:
        row(db,0);row(db,1);db.commit()
    imported=[]
    def sync_between_reads(_connection,_cursor,statement,*_):
        if not imported and statement.lstrip().startswith('SELECT count('):
            imported.append(True)
            with Session(engine) as writer:
                row(writer,2,start_time=NOW-timedelta(hours=1));writer.commit()
    event.listen(engine,'before_cursor_execute',sync_between_reads)
    try:
        with Session(engine) as db:value=activity_diary.diary_page(db,now=NOW)
    finally:event.remove(engine,'before_cursor_execute',sync_between_reads)
    assert imported and value['total']==value['archive_total']==len(value['items'])==2
    with Session(engine) as db:assert activity_diary.diary_page(db,now=NOW)['total']==3
    engine.dispose()


def test_sparse_search_rates_only_union_of_relevant_history_windows(api):
    client,engine=api
    with Session(engine) as db:
        for index in range(1000):row(db,index,name='Target' if index in (0,999) else 'Other')
        db.commit();reference=activity_ratings(db,now=NOW)
    loaded=[]
    def capture(instance,_):loaded.append(instance.id)
    event.listen(Activity,'load',capture)
    try:value=client.get('/activities/diary',params={'q':'Target'}).json()
    finally:event.remove(Activity,'load',capture)
    assert len(value['items'])==2
    assert len(set(loaded))<=86 # Two 42-day contexts, excluding intervening years.
    for item in value['items']:
        assert item['coach_rating']==json.loads(json.dumps(reference[item['id']]))
