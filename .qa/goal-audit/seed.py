"""Only synthetic data, inside the unique IRON_DATA_DIR supplied by run.py."""
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

folder = Path(__file__).resolve().parent
sys.path.insert(0, str(folder.parents[1]))
from app.main import app
from app.db.database import DATA_DIR, SessionLocal
from app.db.models import Activity, Workout
from app.db.iron_models import ActivityObservation, HealthMetric
from app.services.athlete_clock import athlete_today, local_stamp_to_utc_naive
from app.services.planning import PlanningProfile, save_settings

assert DATA_DIR == Path(os.environ['IRON_DATA_DIR']).resolve()
assert DATA_DIR.parent == folder and DATA_DIR.name.startswith('data-')
today = athlete_today()
with SessionLocal() as db:
    save_settings(db, PlanningProfile(name='Fixture QA isolata', height_cm=175, birth_date=date(1985, 6, 1), sex='male',
        experience='intermediate', weekly_minutes=480, recent_weekly_minutes=240, availability=[90, 90, 90, 90, 90, 150, 120],
        pool_access=True, event_name='Gara QA', event_date=today + timedelta(days=180)))
    rows = []
    for index, (ago, sport, duration, load) in enumerate([
        (1, 'cycling', 3600, 40), (3, 'running', 1800, 0), (5, 'swimming', 2700, None),
        (8, 'strength', 2100, 20), (12, 'other', None, None),
        (32, 'cycling', 3000, 35), (39, 'running', 1500, 15), (45, 'swimming', 2700, 30),
        (-1, 'cycling', 5940, 99),
    ]):
        row = Activity(source='manual', source_id=f'goal-audit-synthetic-{index}', sport=sport,
            name=f'QA {sport} {index}', start_time=local_stamp_to_utc_naive(datetime.combine(today-timedelta(days=ago), time(9))),
            duration_seconds=duration, distance_meters=10000 if sport == 'cycling' else 4000 if sport == 'running' else None,
            elevation_gain_meters=100 if sport == 'cycling' else 0 if sport == 'running' else None,
            avg_hr=130 if duration else None, max_hr=155 if duration else None, load=load)
        db.add(row); db.flush(); rows.append(row)
    # An authoritative missing load must remain missing instead of using old scalar data.
    rows[2].load = 45
    db.add(ActivityObservation(activity_id=rows[2].id, source_key='intervals:goal-audit-missing', payload_json=json.dumps({'load': None})))
    duplicate = {column.name:getattr(rows[0],column.name) for column in Activity.__table__.columns
        if column.name not in ('id','source_id','name','created_at')}
    db.add(Activity(**duplicate,source_id='goal-audit-synthetic-duplicate',name='QA seconda registrazione da rivedere'))
    for ago in range(14):
        for kind, value, unit in [('weight_kg', 70+ago*.1, 'kg'), ('resting_hr_bpm', 52+ago%4, 'bpm'), ('sleep_hours', 7.2, 'ore'), ('hrv_ms', 58+ago%3, 'ms')]:
            day = str(today-timedelta(days=ago))
            db.add(HealthMetric(date=day, kind=kind, value=value, unit=unit, source='manual', observed_at=day+'T07:00:00Z', synced_at=day+'T07:00:00Z'))
    workout_ids = {}
    for sport, name, day, status in [('swimming','QA Nuoto futuro',today+timedelta(days=1),'planned'),
            ('cycling','QA Bici futura',today+timedelta(days=1),'planned'),
            ('running','QA Corsa futura',today+timedelta(days=1),'planned'),
            ('swimming','QA Nuoto completato',today-timedelta(days=1),'completed')]:
        payload = {'sport':sport, 'source':'manual', 'purpose':'Fixture isolata', 'expected_rpe':3,
            'steps':[{'type':'manual','name':'Facile','duration_min':20,'target_rpe':3}]}
        row = Workout(date=str(day), sport=sport, name=name, duration_min=20, status=status, payload_json=json.dumps(payload))
        db.add(row); db.flush(); workout_ids[name] = row.id
    db.commit()
manifest = {'today':str(today), 'activity_count':10, 'current_insights_activities':6, 'previous_insights_activities':3,
    'known_current_loads':4, 'current_load':100, 'workout_ids':workout_ids, 'data_dir':str(DATA_DIR)}
(folder/'seed-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps({'seeded_synthetic_activities':10,'seeded_synthetic_workouts':4,'today':str(today)}))
