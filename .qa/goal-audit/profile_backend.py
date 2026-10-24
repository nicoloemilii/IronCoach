"""Representative synthetic history, direct service timings and SQL counts."""
import os
from pathlib import Path
import sys
import cProfile
import io
import json
import hashlib
import pstats
from time import perf_counter
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent
os.chdir(PROJECT)
sys.path.insert(0, str(PROJECT))
os.environ['IRON_DATA_DIR'] = str(QA / 'performance-runtime')
os.environ['IRON_AUTO_SYNC'] = '0'

from datetime import datetime, timedelta
from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api import routes_coach, routes_iron, routes_progress, routes_training
from app.api.routes_dashboard import get_activities
from app.db import sync_models
from app.db.database import Base
from app.db.models import Activity
from app.db.iron_models import ActivityObservation, HealthMetric
from app.integrations import strava
from app.services.athlete_snapshot import derived_profile
from app.services.athlete_clock import athlete_today, utc_now_naive


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else 'profile'
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    now = utc_now_naive()
    with Session(engine) as db:
        for identifier in range(1, 1201):
            stamp = now - timedelta(hours=12 * (1201 - identifier))
            sport = ('cycling', 'running', 'swimming', 'strength')[identifier % 4]
            row = Activity(id=identifier, source='intervals', source_id=f'synthetic-{identifier}',
                           sport=sport, start_time=stamp, name=f'Synthetic session {identifier}',
                           duration_seconds=3600, distance_meters=15000, load=65,
                           normalized_power=150 if sport == 'cycling' else None,
                           elevation_gain_meters=100, avg_hr=140)
            db.add(row)
            db.add(ActivityObservation(activity_id=identifier, source_key=f'intervals:synthetic-{identifier}',
                                      payload_json=json.dumps({'load': None if identifier % 10 == 0 else 65})))
        for offset in range(90):
            day = str(athlete_today() - timedelta(days=offset))
            for kind, value in [('sleep_hours', 7.5), ('hrv_ms', 55), ('resting_hr_bpm', 52),
                                ('ctl', 45), ('atl', 50), ('tsb', -5), ('weight_kg', 70)]:
                db.add(HealthMetric(date=day, kind=kind, value=value, unit='synthetic', source='intervals',
                                    observed_at=day + 'T00:00:00Z', synced_at=now.isoformat() + 'Z'))
        db.commit()
    strava.read_tokens = lambda: {}
    metrics = []
    queries = []

    def counted(connection, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(engine, 'before_cursor_execute', counted)
    for name, function, kwargs in [('coach_today', routes_coach.get_coach_today, {}),
                                   ('dashboard_home', routes_iron.dashboard, {}),
                                   ('derived_profile', derived_profile, {}),
                                   ('activities', get_activities, {'ftp_watts': 170})]:
        queries.clear()
        profiler = cProfile.Profile()
        with Session(engine) as db:
            started = perf_counter()
            result = profiler.runcall(function, db=db, **kwargs)
            elapsed = perf_counter() - started
            size = len(json.dumps(jsonable_encoder(result), ensure_ascii=False).encode())
        output = io.StringIO()
        pstats.Stats(profiler, stream=output).strip_dirs().sort_stats('cumulative').print_stats(45)
        (QA / f'{label}-{name}.profile.txt').write_text(output.getvalue(), encoding='utf-8')
        metrics.append({'route': name, 'activities': 1200, 'sql_queries': len(queries),
                        'observation_queries': sum('activity_observations' in query for query in queries),
                        'elapsed_ms_profiled': round(elapsed * 1000), 'json_bytes': size})
    engine.dispose()
    (QA / f'{label}.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    paths = ['app/services/training_load.py', 'app/services/activity_rating.py',
             'app/services/historical_learning.py', 'app/api/routes_coach.py',
             'app/api/routes_iron.py', 'app/api/routes_dashboard.py',
             'app/api/errors.py', 'app/commercial/setup.py', 'app/main.py']
    manifest = {path: hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() for path in paths}
    (QA / f'{label}-source-sha256.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    main()
