"""Full production app, named synthetic history and provider-free isolated DB."""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = (HERE / 'browser-isolated').resolve()
assert HERE.resolve() in DATA.parents
os.environ['IRON_DATA_DIR'] = str(DATA)
os.environ['IRON_AUTO_SYNC'] = '0'
os.environ['IRON_MODE'] = 'local'
for name in ('INTERVALS_API_KEY', 'INTERVALS_ATHLETE_ID', 'STRAVA_CLIENT_ID', 'STRAVA_CLIENT_SECRET',
             'STRAVA_ACCESS_TOKEN', 'STRAVA_REFRESH_TOKEN', 'GARMIN_CLIENT_ID', 'GARMIN_CLIENT_SECRET',
             'GARMIN_ACCESS_TOKEN'):
    os.environ[name] = ''
sys.path.insert(0, str(ROOT))

from app.main import app
from app.db.database import engine
from app.db.models import Activity
from app.db.iron_models import ActivityObservation
from app.integrations import intervals, intervals_credentials
from app.services.athlete_clock import athlete_today, day_start_utc
from sqlalchemy import text
from sqlalchemy.orm import Session

DAY = athlete_today()
FIXTURE = 'goal-history-v1'
provider_calls = []


def blocked_provider(*args, **kwargs):
    provider_calls.append('intervals.get_activity_remote')
    raise RuntimeError('QA external-provider tripwire: no provider call is allowed.')


# No provider credential exists in this isolated directory or environment. The
# tripwire also makes an accidental external call fail before any network use.
intervals.get_activity_remote = blocked_provider


def activity(identifier, days_ago, sport, minutes, km, *, source='fit', source_id=None, load=None, np=None):
    return Activity(id=identifier, source=source, source_id=source_id or f'qa-history-{identifier}',
                    sport=sport, name={1:'Rulli sintetici · identità canonica',2:'Crossfit sintetico · durata mancante',
                        3:'Trail sintetico · carico in conflitto',4:'Ride sintetico',5:'StrengthTraining sintetico',
                        6:'Weightlifting sintetico',7:'Bici sintetica · periodo precedente',8:'Bici sintetica · record storico'}[identifier],
                    start_time=day_start_utc(DAY-timedelta(days=days_ago))+timedelta(hours=8),
                    duration_seconds=None if minutes is None else minutes*60,
                    distance_meters=None if km is None else km*1000,
                    avg_power=150 if sport in ('VirtualRide','Ride') else None, normalized_power=np, load=load)


with Session(engine) as db:
    if db.query(Activity).count():
        raise RuntimeError('QA data already populated. Inspect this isolated database before reusing it.')
    db.add_all([
        activity(1,1,'VirtualRide',60,30,source='intervals',source_id='qa-canonical',load=88,np=250),
        activity(2,2,'Crossfit',None,None),activity(3,3,'TrailRun',30,5,load=55,np=180),
        activity(4,4,'Ride',10,5,load=10),activity(5,5,'StrengthTraining',15,None,load=5),
        activity(6,6,'Weightlifting',20,0,load=8),activity(7,40,'Ride',120,100,load=90),
        activity(8,120,'Ride',180,200,load=120),
    ])
    db.flush()
    times=list(range(0,3601,5))
    db.add_all([
        ActivityObservation(id=1,activity_id=1,source_key='intervals:qa-canonical',payload_json=json.dumps({
            'load':None,'icu_training_load':777,'moving_time':3600,'elapsed_time':3600,'avg_power':150,
            'streams':[{'type':'time','data':times},{'type':'watts','data':[150]*len(times)},
                       {'type':'heartrate','data':[130]*len(times)}]})),
        ActivityObservation(id=2,activity_id=1,source_key='intervals:qa-newer-alias',payload_json=json.dumps({
            'load':999,'avg_power':999,'streams':[{'type':'time','data':[0,3600]},
                                               {'type':'watts','data':[999,999]}]})),
        ActivityObservation(id=3,activity_id=3,source_key='intervals:qa-conflict-a',payload_json=json.dumps({'load':20,'avg_power':10})),
        ActivityObservation(id=4,activity_id=3,source_key='intervals:qa-conflict-b',payload_json=json.dumps({'load':80,'avg_power':999})),
    ])
    db.commit()


@app.get('/_qa/state')
def qa_state():
    hashes={}
    with engine.connect() as connection:
        names=connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")).scalars().all()
        for name in names:
            quoted='"'+name.replace('"','""')+'"'
            rows=sorted(map(repr,connection.exec_driver_sql('SELECT * FROM '+quoted).fetchall()))
            hashes[name]={'rows':len(rows),'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
    return {'synthetic':True,'fixture':FIXTURE,'day':str(DAY),'tables':hashes,
            'provider_configured':bool(intervals_credentials.credentials().get('api_key')),
            'provider_tripwire_calls':list(provider_calls)}


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8003,access_log=False)
