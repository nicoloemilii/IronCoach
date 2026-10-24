"""Production app and synthetic power fixtures, with no external credentials."""
from datetime import timedelta
import hashlib,json,os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
DATA=(HERE/'browser-isolated').resolve();assert HERE.resolve() in DATA.parents
os.environ['IRON_DATA_DIR']=str(DATA);os.environ['IRON_AUTO_SYNC']='0';os.environ['IRON_MODE']='local'
for name in ('INTERVALS_API_KEY','INTERVALS_ATHLETE_ID','STRAVA_CLIENT_ID','STRAVA_CLIENT_SECRET',
             'STRAVA_ACCESS_TOKEN','STRAVA_REFRESH_TOKEN','GARMIN_CLIENT_ID','GARMIN_CLIENT_SECRET','GARMIN_ACCESS_TOKEN'):
    os.environ[name]=''
sys.path.insert(0,str(ROOT))
from app.main import app
from app.db.database import engine
from app.db.models import Activity
from app.db.iron_models import ActivityObservation
from app.integrations import intervals,intervals_credentials
from app.services.athlete_clock import athlete_today,day_start_utc
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

DAY=athlete_today();FIXTURE='goal-power-v1';provider_calls=[];mutation_attempts=[]
def blocked(*args,**kwargs):
    provider_calls.append('outgoing_provider');raise RuntimeError('QA provider tripwire')
intervals.get_activity_remote=blocked
@app.middleware('http')
async def read_only(request,call_next):
    if request.method!='GET':
        mutation_attempts.append(request.method+' '+request.url.path)
        return JSONResponse({'detail':'QA GET-only guard'},status_code=405)
    return await call_next(request)

names={1:'Triangolo registrato',2:'Potenza zero registrata',3:'Due segmenti con vuoto',4:'Potenza ambigua',
       5:'Alias equivalenti nel carico',6:'Solo riepilogo',7:'Serie lunga originale',8:'Attività futura'}
def add(db,identifier,times=None,power=None,*,elapsed=None,extra=(),source='fit'):
    duration=elapsed if elapsed is not None else times[-1] if times else 600
    item=Activity(id=identifier,source=source,source_id=f'qa-power-{identifier}',sport='Ride',
        name=names[identifier],start_time=day_start_utc(DAY-timedelta(days=identifier))+timedelta(hours=8),
        duration_seconds=duration,avg_power=999,avg_hr=140,load=42)
    if identifier==8:item.start_time=day_start_utc(DAY+timedelta(days=1))
    db.add(item);db.flush()
    if times is not None:
        streams=[{'type':'time','data':times},{'type':'watts','data':power},
                 {'type':'heartrate','data':[140]*len(times)},*extra]
        content={'moving_time':duration,'elapsed_time':duration,'load':42,'avg_power':999,'streams':streams}
        key=f'intervals:qa-power-{identifier}' if source=='intervals' else f'fit:qa-power-{identifier}'
        db.add(ActivityObservation(activity_id=identifier,source_key=key,payload_json=json.dumps(content)))
    return item
with Session(engine) as db:
    if db.query(Activity).count():raise RuntimeError('QA database already seeded; inspect before restart')
    add(db,1,[0,4,10],[0,120,0],source='intervals')
    db.add(ActivityObservation(activity_id=1,source_key='intervals:qa-foreign-newer',payload_json=json.dumps({'load':42,'streams':[{'type':'time','data':[0,10]},{'type':'watts','data':[9999,9999]}]})))
    add(db,2,list(range(121)),[0]*121)
    add(db,3,list(range(91))+list(range(1000,1101)),[100]*91+[200]*101,elapsed=1100)
    add(db,4,list(range(121)),[200]*121,extra=[{'type':'watts','data':[999]*121}])
    add(db,5)
    db.add_all([ActivityObservation(activity_id=5,source_key='intervals:alias-a',payload_json=json.dumps({'load':42,'streams':[{'type':'time','data':[0,10]},{'type':'watts','data':[50,50]}]})),
                ActivityObservation(activity_id=5,source_key='intervals:alias-b',payload_json=json.dumps({'load':42,'streams':[{'type':'time','data':[0,10]},{'type':'watts','data':[500,500]}]}))])
    add(db,6)
    times=[index/2 for index in range(14401)]
    powers=[0 if stamp<=3600 or stamp>=3610 else (stamp-3600)*30 if stamp<=3604 else (3610-stamp)*20 for stamp in times]
    add(db,7,times,powers)
    add(db,8,[0,1,2,3,4,5,6],[100]*7)
    db.commit()
FIXTURE_DATA=dict(synthetic=True,fixture=FIXTURE,day=str(DAY),names=names,rows=8,
    expected={1:dict(mean_power_w=90,start_s=2,end_s=7),2:dict(mean_power_w=0,start_s=0,end_s=5),
              3:dict(mean_power_w=200,start_s=1000,end_s=1060,duration_seconds=60),
              7:dict(mean_power_w=90,start_s=3602,end_s=3607)},
    no_personal_data=True,no_external_credentials=True)
(HERE/'browser_fixture.json').write_text(json.dumps(FIXTURE_DATA,indent=2),encoding='utf-8')
@app.get('/_qa/fixture')
def qa_fixture():return FIXTURE_DATA
@app.get('/_qa/state')
def qa_state():
    hashes={}
    with engine.connect() as connection:
        for name in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")).scalars():
            quoted='"'+name.replace('"','""')+'"'
            rows=sorted(map(repr,connection.exec_driver_sql('SELECT * FROM '+quoted).fetchall()))
            hashes[name]=dict(rows=len(rows),sha256=hashlib.sha256('\n'.join(rows).encode()).hexdigest())
    return dict(synthetic=True,fixture=FIXTURE,tables=hashes,provider_configured=bool(intervals_credentials.credentials().get('api_key')),
                provider_tripwire_calls=list(provider_calls),mutation_attempts=list(mutation_attempts))
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8005,access_log=False)
