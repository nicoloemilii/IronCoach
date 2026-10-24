"""Independent HTTP records QA, isolated synthetic SQLite and provider tripwire."""
from datetime import datetime,timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from threading import Event
import time
from unittest.mock import patch
from uuid import uuid4

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
os.environ['IRON_DATA_DIR'] = str(HERE / 'review-api-unused')
os.environ['IRON_AUTO_SYNC'] = '0'; os.environ['IRON_MODE'] = 'local'
sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,event,text
from sqlalchemy.orm import Session
from app.db.database import Base,get_db
from app.db.effort_models import EffortAnalysis,EffortScan
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.db.planning_models import AthleteSettings
from app.services import effort_jobs as jobs,effort_records as records
from app.services.athlete_clock import athlete_today,day_start_utc
from app.api.routes_effort_records import router

NOW = datetime(2026,10,23,0,30)
SOURCES = ['app/services/effort_jobs.py','app/services/effort_records.py',
    'app/db/effort_models.py','app/db/effort_schema.py','app/db/effort_lock.py','app/api/routes_effort_records.py']


def hashes():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}


def digest(engine,only=None):
    with engine.connect() as conn:
        tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")).scalars().all()
        return {name:hashlib.sha256(repr(conn.exec_driver_sql('SELECT * FROM "'+name+'" ORDER BY rowid').fetchall()).encode()).hexdigest()
            for name in tables if only is None or name in only}


def payload(power):
    return dict(elapsed_time=10,streams=[{'type':'time','data':[0,4,10]},
        {'type':'watts','data':power if isinstance(power,list) else [power]*3}])


def main():
    before_hashes = hashes(); provider_calls = []; sql = []
    folder = HERE/'review-api-runs'/uuid4().hex; folder.mkdir(parents=True)
    engine = create_engine('sqlite:///'+str(folder/'synthetic.db'),connect_args={'check_same_thread':False,'timeout':5})
    Base.metadata.create_all(engine)
    today = athlete_today(NOW); start28 = day_start_utc(today-timedelta(days=27))
    with Session(engine) as db:
        db.add(AthleteSettings(id=1,revision=0,payload_json='{}'))
        for identifier,name,at,source,content in [
            (1,'Canonical triangle',NOW-timedelta(days=1),'intervals',payload([0,120,0])),
            (2,'Summary only',NOW-timedelta(days=2),'fit',None),
            (3,'Ambiguous aliases',NOW-timedelta(days=3),'fit',None),
            (4,'Measured zero',NOW-timedelta(days=4),'fit',payload(0)),
            (5,'Future excluded',NOW+timedelta(seconds=1),'fit',payload(900)),
            (6,'Old29 days',NOW-timedelta(days=29),'fit',payload(200)),
            (7,'Exact Rome28 boundary',start28,'fit',payload(130)),
            (8,'Before Rome28 boundary',start28-timedelta(microseconds=1),'fit',payload(300)),
        ]:
            db.add(Activity(id=identifier,source=source,source_id='qa-record-'+str(identifier),name=name,sport='Ride',
                start_time=at,duration_seconds=10,avg_power=999,normalized_power=999))
            if content is not None:
                db.add(ActivityObservation(activity_id=identifier,source_key='intervals:qa-record-'+str(identifier),payload_json=json.dumps(content)))
        db.add_all([
            ActivityObservation(activity_id=1,source_key='intervals:newer-canonical-alias',payload_json=json.dumps(payload(999))),
            ActivityObservation(activity_id=3,source_key='intervals:ambiguous-one',payload_json=json.dumps(dict(payload(700),load=30))),
            ActivityObservation(activity_id=3,source_key='intervals:ambiguous-two',payload_json=json.dumps(dict(payload(800),load=30))),
        ]); db.commit()
    def cursor(conn,cursor,statement,parameters,context,executemany):
        sql.append(statement.lstrip().split(None,1)[0].upper())
    event.listen(engine,'before_cursor_execute',cursor)
    def tripwire(*args,**kwargs):
        provider_calls.append('forbidden_provider')
        raise AssertionError('No provider may be called by stored-only API QA')
    app = FastAPI(); app.include_router(router)
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    original_rows = digest(engine,{'activities','activity_observations'})
    checks = []; responses = {}
    def get(client,path):
        response = client.get(path)
        assert response.status_code == 200,(path,response.text)
        assert response.headers.get('cache-control') == 'private, no-store',(path,response.headers)
        return response.json()
    jobs.start()
    with patch.object(jobs.athlete_clock,'utc_now_naive',lambda:NOW), \
         patch.object(records,'utc_now_naive',lambda:NOW), \
         patch.object(records,'provider_available',lambda:False), \
         patch.object(records.intervals,'get_activity_remote',tripwire), \
         TestClient(app,client=('127.0.0.1',55111)) as client:
        # A GET cannot launch a job, compute a curve, fetch or write anything.
        state = digest(engine); sql.clear()
        with patch.object(records,'best_power_efforts',tripwire):
            initial = get(client,'/athlete/effort-records')
        assert initial['coverage']['total'] == initial['coverage']['pending'] == 7,initial
        assert initial['active_scan'] is None and initial['last_scan'] is None
        assert not any(kind in ('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP','REPLACE') for kind in sql),sql
        assert digest(engine) == state
        checks.append('GET before analysis: no CPU/provider/DML/schema work, future excluded')
        # Explicit synthetic user action is the only operation launching analysis.
        response = client.post('/athlete/effort-records/scans',json={'mode':'stored','refresh':False})
        assert response.status_code == 200,response.text
        identifier = response.json()['id']; deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            scan = get(client,'/athlete/effort-records/scans/'+identifier)
            if scan['status'] not in jobs.ACTIVE:
                break
            Event().wait(.01)
        assert scan['status'] == 'completed' and scan['processed'] == scan['total'] == 7,scan
        assert digest(engine,{'activities','activity_observations'}) == original_rows
        checks.append('Explicit stored-only scan completes fixed7 candidates without changing originals')
        state = digest(engine); sql.clear()
        with patch.object(records,'best_power_efforts',tripwire):
            for period in ('all_time','last_90_days','last_28_days'):
                responses[period] = get(client,'/athlete/effort-records?period='+period)
        assert digest(engine) == state
        assert not any(kind in ('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP','REPLACE') for kind in sql)
        assert responses['all_time']['records'][0]['activity']['id'] == 8
        assert responses['all_time']['records'][0]['mean_power_w'] == 300
        assert responses['last_90_days']['records'][0]['activity']['id'] == 8
        assert responses['last_28_days']['records'][0]['activity']['id'] == 7
        assert responses['last_28_days']['records'][0]['mean_power_w'] == 130
        assert responses['last_28_days']['period']['start'] == str(today-timedelta(days=27))
        coverage = responses['all_time']['coverage']
        assert coverage['total'] == coverage['indexed'] == 7 and coverage['complete_index'] is True,coverage
        assert coverage['missing_streams'] == 1 and coverage['unavailable'] == 1,coverage
        assert coverage['input_partial'] is True  # Indexed all is not complete power coverage.
        with Session(engine) as db:
            canonical = json.loads(db.get(EffortAnalysis,1).payload_json)
            zero = json.loads(db.get(EffortAnalysis,4).payload_json)
            assert canonical['curve']['durations'][0]['mean_power_w'] == 90
            assert canonical['curve']['durations'][0]['start_s'] == 2
            assert zero['curve']['durations'][0]['mean_power_w'] == 0
            for row in db.query(EffortAnalysis):
                assert 'streams' not in json.loads(row.payload_json)
                assert 'raw' not in json.loads(row.payload_json)
        checks.append('Canonical identity, measured zero, ambiguous aliases, source-only caches and Rome28 boundary')
        with Session(engine) as db:
            row = db.query(ActivityObservation).filter_by(activity_id=8).one()
            row.payload_json = json.dumps(payload(400)); db.commit()
        stale = get(client,'/athlete/effort-records')
        assert stale['coverage']['stale'] == 1 and stale['coverage']['complete_index'] is False,stale
        assert stale['records'][0]['activity']['id'] == 6 and stale['records'][0]['mean_power_w'] == 200
        with Session(engine) as db:
            db.query(ActivityObservation).filter_by(activity_id=6).delete()
            db.query(Activity).filter_by(id=6).delete(); db.commit()
        deleted = get(client,'/athlete/effort-records')
        assert deleted['coverage']['total'] == 6 and deleted['records'][0]['activity']['id'] == 7
        checks.append('Same-id payload update invalidates cache; deleted source cannot contribute an orphan record')
        responses.update(initial=initial,scan=scan,stale=stale,deleted=deleted)
    jobs.stop()
    if jobs._POOL:
        jobs._POOL.shutdown(wait=True)
    assert provider_calls == [],provider_calls
    after_hashes = hashes()
    report = dict(status='passed',fixed_utc=NOW.isoformat()+'Z',database='private synthetic SQLite',
        original_rows_unchanged_by_scan=True,provider_calls=provider_calls,checks=checks,
        source_sha256_before=before_hashes,source_sha256_after=after_hashes,
        sources_unchanged=before_hashes==after_hashes,responses=responses,
        boundary='Actual FastAPI HTTP over synthetic data; no personal archive, live provider, device or visual claim')
    (HERE/'review_api.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='responses'},ensure_ascii=False))
    engine.dispose()


if __name__ == '__main__':
    main()
