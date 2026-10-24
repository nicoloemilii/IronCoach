"""Run the production app with isolated synthetic data for visual review."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
os.environ['IRON_DATA_DIR'] = str(HERE / 'browser-isolated')
os.environ['IRON_AUTO_SYNC'] = '0'
sys.path.insert(0, str(ROOT))

from app.main import app
from app.db.database import engine, Base
from app.db.models import Activity, Workout, WorkoutEvaluation
from app.db.experience_models import ActivityIdentity, SessionFeedback
from app.db.iron_models import ActivityObservation, DuplicateCandidate
from app.db.review_models import DuplicateReview
from sqlalchemy import select
from sqlalchemy.orm import Session

RELATED = (Activity, ActivityIdentity, ActivityObservation, WorkoutEvaluation, SessionFeedback, DuplicateCandidate)

def snapshot():
    with Session(engine) as db:
        return {model.__tablename__: [{key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in row.items()} for row in db.execute(select(model.__table__).order_by(model.id)).mappings()]
            for model in RELATED}

with Session(engine) as db:
    if db.query(Activity).count():
        raise RuntimeError('Synthetic browser database already seeded; inspect it before starting again.')
    db.add_all([
        Activity(id=1, source='fit', source_id='qa-fit-1', sport='running', name='Corsa al parco · FIT',
                 start_time=datetime(2026, 10, 18, 7), duration_seconds=3600, distance_meters=10000,
                 avg_hr=0, elevation_gain_meters=0),
        Activity(id=2, source='intervals', source_id='qa-remote-1', sport='running', name='Corsa al parco · Intervals',
                 start_time=datetime(2026, 10, 18, 7), duration_seconds=3600, distance_meters=10003,
                 avg_hr=145, elevation_gain_meters=12, load=55),
        Activity(id=3, source='fit', source_id='qa-fit-3', sport='running', name='Altra registrazione di controllo',
                 start_time=datetime(2026, 10, 17, 7), duration_seconds=2400, distance_meters=6000),
        Workout(id=1, date='2026-10-18', name='Corsa facile · prova sintetica', sport='running', duration_min=60, payload_json='{}'),
    ]); db.flush()
    db.add_all([
        ActivityIdentity(id=1, activity_id=1, source_key='fit:qa-fit-1'),
        ActivityIdentity(id=2, activity_id=2, source_key='intervals:qa-remote-1'),
        ActivityObservation(id=1, activity_id=1, source_key='fit:qa-fit-1', payload_json='{"source":"fit","load":null}'),
        ActivityObservation(id=2, activity_id=2, source_key='intervals:qa-remote-1', payload_json='{"source":"intervals","load":55}'),
        SessionFeedback(id=1, workout_id=1, activity_id=2, payload_json='{"activity_id":2,"rpe":3}'),
        WorkoutEvaluation(id=1, workout_id=1, activity_id=2, payload_json='{"matched_activity_id":2}'),
        DuplicateCandidate(id=1, activity_id=2, other_id=1, status='pending'),
        DuplicateCandidate(id=2, activity_id=2, other_id=3, status='kept'),
        DuplicateCandidate(id=3, activity_id=1, other_id=3, status='pending'),
    ]); db.commit()

before = snapshot()
(HERE / 'browser-before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')

@app.get('/_qa/state')
def qa_state():
    current = snapshot()
    with Session(engine) as db:
        return {'synthetic': True, 'activities': len(current['activities']), 'exact_restoration': current == before,
                'reviews': [{'id': row.id, 'action': row.action, 'status': row.status} for row in db.query(DuplicateReview)],
                'fingerprint': hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8001, access_log=False)
