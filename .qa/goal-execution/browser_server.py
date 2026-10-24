"""Serve production UI with synthetic plan/evidence, never personal data."""
from datetime import datetime,timedelta
import json,os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]; HERE=Path(__file__).resolve().parent
os.environ['IRON_DATA_DIR']=str(HERE/'browser-isolated')
os.environ['IRON_AUTO_SYNC']='0'
sys.path.insert(0,str(ROOT))
from app.main import app
from app.db.database import engine
from app.db.models import Activity,Workout
from app.db.experience_models import SessionFeedback
from app.services.athlete_clock import athlete_today,day_start_utc,utc_now_naive
from sqlalchemy.orm import Session

today=athlete_today(); yesterday=today-timedelta(days=1)
def plan(identifier,sport,minutes):
    return Workout(id=identifier,date=str(yesterday),sport=sport,name={1:'Bici facile · da confermare',2:'Palestra · durata sconosciuta',3:'Corsa · evidenza da verificare'}[identifier],duration_min=minutes,
        status='completed' if identifier==3 else 'planned',payload_json=json.dumps({'sport':sport,'duration_min':minutes,
        'expected_rpe':4,'purpose':'Prova sintetica del confronto tra piano e attività.','steps':[{'type':'easy','name':'Lavoro facile','duration_min':minutes,'target_rpe':3}]}))
if os.getenv('IRON_EXECUTION_QA_REUSE')=='1':
    with Session(engine) as db:
        assert db.query(Workout).count()==3 and db.query(Activity).count()==3
        assert [(row.workout_id,row.activity_id) for row in db.query(SessionFeedback)]==[(3,3)]
        assert db.get(Workout,1).status==db.get(Workout,2).status=='planned'
else:
    with Session(engine) as db:
        if db.query(Workout).count():raise RuntimeError('QA database already populated; inspect before reseeding.')
        db.add_all([plan(1,'cycling',60),plan(2,'strength',45),plan(3,'running',30)])
        db.add_all([
            Activity(id=1,source='intervals',source_id='qa-ride',sport='VirtualRide',name='Rulli · attività sintetica',start_time=day_start_utc(yesterday)+timedelta(hours=9),duration_seconds=4500,distance_meters=30000,load=45),
            Activity(id=2,source='fit',source_id='qa-gym',sport='WeightTraining',name='Palestra · durata non registrata',start_time=day_start_utc(yesterday)+timedelta(hours=17),duration_seconds=None),
            Activity(id=3,source='fit',source_id='qa-future',sport='running',name='Registrazione futura · prova di controllo',start_time=utc_now_naive()+timedelta(days=1),duration_seconds=1800),
        ]);db.flush()
        db.add(SessionFeedback(id=1,workout_id=3,activity_id=3,payload_json=json.dumps({'rpe':4,'completion_percent':100,'actual':{'duration_min':30},'evaluation':{'completion':100}})))
        db.commit()

@app.get('/_qa/state')
def qa_state():
    from app.services.progress import week_progress
    from app.services.iron_coach import overview
    with Session(engine) as db:
        return {'synthetic':True,'week':week_progress(db),'execution_coach':overview(db),
            'feedbacks':[{'workout_id':row.workout_id,'activity_id':row.activity_id,'content':json.loads(row.payload_json)} for row in db.query(SessionFeedback)]}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8001,access_log=False)
