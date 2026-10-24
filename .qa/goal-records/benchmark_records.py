"""Isolated synthetic GET cost; never imports main or opens a workspace DB."""
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from datetime import datetime,timedelta

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT))
os.environ['IRON_DATA_DIR']=str(Path(__file__).resolve().parent/'runtime-benchmark-isolated')
os.environ['IRON_AUTO_SYNC']='0'

from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from app.db.database import Base
from app.db.models import Activity
from app.db.iron_models import ActivityObservation
from app.db.effort_models import EffortAnalysis
from app.services import effort_records as service

NOW=datetime(2026,10,23,12)
OUTPUT=Path(__file__).resolve().parent/'records_benchmark.json'


def run(name,count,padding=0,aliases=0,derived=False):
    engine=create_engine('sqlite://'); Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        db.bulk_insert_mappings(Activity,[dict(id=i+1,source='intervals',source_id=str(i+1),
            sport='Ride',name='Synthetic ride',start_time=NOW-timedelta(days=i%1000,hours=2),
            duration_seconds=60.) for i in range(count)])
        payload=json.dumps({'elapsed_time':60,'streams':[{'type':'time','data':list(range(61))},
            {'type':'watts','data':[123]*61}],'padding':'x'*padding})
        if not derived: payload=json.dumps({'elapsed_time':60,'average_watts':123,'padding':'x'*padding})
        db.bulk_insert_mappings(ActivityObservation,[dict(id=i+1,activity_id=i+1,
            source_key='intervals:'+str(i+1),payload_json=payload) for i in range(count)])
        db.bulk_insert_mappings(ActivityObservation,[dict(activity_id=1,source_key='intervals:foreign-'+str(i),
            payload_json='not parseable foreign payload') for i in range(aliases)])
        db.commit()
        if derived:
            for activity in db.query(Activity):
                result=service.analyze_snapshot(service.source_snapshot(db,activity),now=NOW)
                values={key:value for key,value in result.items() if key not in ('stop_batch','provider_status')}
                values['payload_json']=json.dumps(values['payload_json'],allow_nan=False)
                db.add(EffortAnalysis(activity_id=activity.id,**values))
            db.commit()
        db.expunge_all()
        statements=[]
        def collect(c,cu,s,p,co,e): statements.append(s)
        event.listen(engine,'before_cursor_execute',collect)
        start=perf_counter(); result=service.records(db,now=NOW); elapsed=perf_counter()-start
        event.remove(engine,'before_cursor_execute',collect)
        encoded=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        answer=dict(case=name,activities=count,selected_payload_bytes=count*len(payload.encode()),
            foreign_headers=aliases,derived=derived,seconds=elapsed,selects=len(statements),
            coverage=result['coverage'],json_sha256=hashlib.sha256(encoded).hexdigest())
        assert result['coverage']['total']==count
        assert result['coverage']['indexed']==(count if derived else 0)
        assert all(s.lstrip().upper().startswith('SELECT') for s in statements)
    engine.dispose()
    return answer


if __name__=='__main__':
    service.intervals.get_activity_remote=lambda *a,**k:(_ for _ in ()).throw(AssertionError('provider tripwire'))
    values=[run('summary_10000_pending',10000),run('derived_10000_small',10000,derived=True),
        run('derived_100_selected_1MiB',100,padding=1024*1024,derived=True),
        run('one_canonical_plus_999_foreign_headers',1,aliases=999,derived=True)]
    report=dict(synthetic=True,personal_db_access=False,provider_calls=0,results=values,
        limitation='GET validation intentionally reads every selected source in the requested scope; cost depends on total selected payload bytes. No latency guarantee.')
    OUTPUT.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
