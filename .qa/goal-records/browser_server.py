"""Production routes/jobs, synthetic isolated DB, and a bounded QA-only worker delay."""
from datetime import timedelta
import hashlib,json,os,re,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
DATA=(HERE/'browser-isolated-2').resolve();assert HERE.resolve() in DATA.parents
os.environ.update(IRON_DATA_DIR=str(DATA),IRON_AUTO_SYNC='0',IRON_MODE='local')
for key in ('INTERVALS_API_KEY','INTERVALS_ATHLETE_ID','STRAVA_CLIENT_ID','STRAVA_CLIENT_SECRET','STRAVA_ACCESS_TOKEN','STRAVA_REFRESH_TOKEN','GARMIN_CLIENT_ID','GARMIN_CLIENT_SECRET','GARMIN_ACCESS_TOKEN'):
    os.environ[key]=''
sys.path.insert(0,str(ROOT))
from app.main import app
from app.db.database import engine
from app.db.models import Activity
from app.db.iron_models import ActivityObservation
from app.integrations import intervals,intervals_credentials
from app.services import effort_records
from app.services.athlete_clock import athlete_today,day_start_utc
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse
DAY=athlete_today();FIXTURE='goal-records-v1';provider_calls=[];mutations=[]
def blocked(*args,**kwargs):
    provider_calls.append('provider_tripwire');raise RuntimeError('QA provider tripwire')
intervals.get_activity_remote=blocked
original_analyze=effort_records.analyze_snapshot
def delayed_analyze(*args,**kwargs):
    time.sleep(.15)  # Makes cooperative cancellation observable; does not alter results.
    return original_analyze(*args,**kwargs)
effort_records.analyze_snapshot=delayed_analyze
@app.middleware('http')
async def restricted(request,call_next):
    if request.method!='GET':
        allowed=request.method=='POST' and (request.url.path=='/athlete/effort-records/scans' or re.fullmatch(r'/athlete/effort-records/scans/[a-f0-9]{32}/cancel',request.url.path))
        mutations.append(dict(method=request.method,path=request.url.path,allowed=bool(allowed)))
        if not allowed:return JSONResponse({'detail':'QA mutation blocked'},status_code=405)
    return await call_next(request)
def add(db,identifier,days,sport='Ride',power=None,times=None,name=None):
    stamp=day_start_utc(DAY-timedelta(days=days))+timedelta(hours=8)
    row=Activity(id=identifier,source='fit',source_id=f'qa-record-{identifier}',sport=sport,name=name or f'Registrazione sintetica {identifier}',start_time=stamp,duration_seconds=times[-1] if times else 600,avg_power=999,avg_hr=140,load=42)
    db.add(row);db.flush()
    if times is not None:db.add(ActivityObservation(activity_id=identifier,source_key=f'fit:qa-record-{identifier}',payload_json=json.dumps({'elapsed_time':times[-1],'load':42,'streams':[{'type':'time','data':times},{'type':'watts','data':power},{'type':'heartrate','data':[140]*len(times)}]})))
with Session(engine) as db:
    if db.query(Activity).count():raise RuntimeError('QA data already seeded; inspect before restart')
    add(db,1,3,power=[0,120,0],times=[0,4,10],name='Triangolo recente')
    add(db,2,10,power=[0]*121,times=list(range(121)),name='Zero misurato')
    add(db,3,5,sport='TrailRun',power=[300]*601,times=list(range(601)),name='Corsa separata')
    add(db,4,100,power=[450]*301,times=list(range(301)),name='Bici oltre novanta giorni')
    add(db,5,40,power=[150]*301,times=list(range(301)),name='Bici tra ventotto e novanta giorni')
    add(db,6,7,name='Riepilogo senza serie')
    add(db,7,-1,power=[9999]*11,times=list(range(11)),name='Attività futura esclusa')
    add(db,8,8,name='Fonte ambigua')
    for suffix,watts in [('a',50),('b',500)]:db.add(ActivityObservation(activity_id=8,source_key=f'intervals:alias-{suffix}',payload_json=json.dumps({'load':42,'streams':[{'type':'time','data':[0,10]},{'type':'watts','data':[watts,watts]}]})))
    db.commit()
FIXTURE_DATA=dict(synthetic=True,fixture=FIXTURE,day=str(DAY),activities=8,eligible_job_activities=7,cycling_activities=6,expected_power={'all_time':450,'last_90_days':150,'last_28_days':90,'running':300},qa_worker_delay_seconds=.15,no_personal_data=True,no_provider_credentials=True)
(HERE/'browser_fixture.json').write_text(json.dumps(FIXTURE_DATA,indent=2),encoding='utf-8')
@app.get('/_qa/fixture')
def fixture():return FIXTURE_DATA
@app.get('/_qa/state')
def state():
    tables={}
    with engine.connect() as connection:
        for name in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")).scalars():
            quoted='"'+name.replace('"','""')+'"';rows=sorted(map(repr,connection.exec_driver_sql('SELECT * FROM '+quoted).fetchall()))
            tables[name]=dict(rows=len(rows),sha256=hashlib.sha256('\n'.join(rows).encode()).hexdigest())
    return dict(synthetic=True,fixture=FIXTURE,tables=tables,provider_configured=bool(intervals_credentials.credentials().get('api_key')),provider_tripwire_calls=list(provider_calls),mutations=list(mutations))
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8006,access_log=False)
