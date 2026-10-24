"""Production app with raw synthetic recordings; isolated from personal data."""
from datetime import timedelta
import hashlib, json, os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]; HERE=Path(__file__).resolve().parent
os.environ['IRON_DATA_DIR']=str(HERE/'browser-isolated')
os.environ['IRON_AUTO_SYNC']='0'
for name in ('INTERVALS_API_KEY','INTERVALS_ATHLETE_ID','STRAVA_ACCESS_TOKEN','STRAVA_REFRESH_TOKEN'):
    os.environ[name]=''
sys.path.insert(0,str(ROOT))
from app.main import app
from app.db.database import engine
from app.db.models import Activity
from app.db.iron_models import ActivityObservation
from app.services.athlete_clock import utc_now_naive
from sqlalchemy import text
from sqlalchemy.orm import Session

with Session(engine) as db:
    if db.query(Activity).count():
        raise RuntimeError('Existing QA data: inspect before any reseeding.')
    times=list(range(0,1201,2))
    for identifier in (1,2):
        power=[100+t/6 for t in times]
        heart=[130+t/60 if identifier==1 or t<590 else None for t in times]
        row=Activity(id=identifier,source='manual',source_id=f'qa-effort-{identifier}',sport='VirtualRide',
                     name='Rulli sintetici · '+('completi' if identifier==1 else 'FC parziale'),
                     start_time=utc_now_naive()-timedelta(days=1,hours=identifier),duration_seconds=1000,
                     distance_meters=10000,avg_power=200,avg_hr=140)
        db.add(row);db.flush()
        db.add(ActivityObservation(activity_id=identifier,source_key=f'synthetic:effort-{identifier}',
            payload_json=json.dumps({'elapsed_time':1200,'moving_time':1000,'streams':[
                {'type':'time','data':times},{'type':'watts','data':power},{'type':'heartrate','data':heart}]})))
    db.commit()

@app.get('/_qa/state')
def state():
    with engine.connect() as connection:
        tables=connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")).scalars().all()
        hashes={}
        for name in tables:
            quoted='"'+name.replace('"','""')+'"'
            rows=sorted(map(repr,connection.exec_driver_sql('SELECT * FROM '+quoted).fetchall()))
            hashes[name]={'rows':len(rows),'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
    return {'synthetic':True,'tables':hashes}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8002,access_log=False)
