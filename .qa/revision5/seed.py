import sys,json
from pathlib import Path
from datetime import timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from app.main import app
from app.db.database import SessionLocal
from app.db.models import Workout
from app.services.athlete_clock import athlete_today
today=athlete_today()
with SessionLocal() as db:
    for sport,name,day,status in [('swimming','QA Nuoto',today+timedelta(days=1),'planned'),('cycling','QA Bici',today+timedelta(days=1),'planned'),('running','QA Corsa',today+timedelta(days=1),'planned'),('swimming','QA Nuoto passato',today-timedelta(days=1),'completed')]:
        db.add(Workout(date=str(day),sport=sport,name=name,duration_min=20,status=status,payload_json=json.dumps({'sport':sport,'source':'manual','purpose':'Fixture isolata','expected_rpe':3,'steps':[{'type':'manual','name':'Facile','duration_min':20,'target_rpe':3}]})))
    db.commit()
