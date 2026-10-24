"""Production app on port 8004, read-only synthetic fixture and network tripwire."""
from collections import Counter
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = (HERE / 'browser-isolated').resolve()
assert HERE in DATA.parents
if (DATA / 'coach.db').exists():
    raise RuntimeError('QA fixture database already exists; refusing to reseed or overwrite it.')
os.environ['IRON_DATA_DIR'] = str(DATA)
os.environ['IRON_AUTO_SYNC'] = '0'
os.environ['IRON_MODE'] = 'local'
for name in ('INTERVALS_API_KEY', 'INTERVALS_ATHLETE_ID', 'STRAVA_CLIENT_ID', 'STRAVA_CLIENT_SECRET',
             'STRAVA_ACCESS_TOKEN', 'STRAVA_REFRESH_TOKEN', 'GARMIN_CLIENT_ID', 'GARMIN_CLIENT_SECRET', 'GARMIN_ACCESS_TOKEN'):
    os.environ[name] = ''
sys.path.insert(0, str(ROOT))

provider_calls = []; mutation_calls = []


def blocked_request(*args, **kwargs):
    provider_calls.append('outgoing_requests_tripwire')
    raise RuntimeError('QA provider tripwire: outgoing provider requests are disabled.')


import requests
requests.sessions.Session.request = blocked_request

from app.main import app
from app.db.database import engine
from app.db.models import Activity
from app.db.iron_models import ActivityObservation, HealthMetric
from app.integrations import intervals, intervals_credentials, strava
from app.services.athlete_clock import athlete_today, day_start_utc
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

intervals.get_activity_remote = blocked_request
intervals_credentials.credentials = lambda: {'api_key': '', 'athlete_id': '0', 'source': None}
strava.read_tokens = lambda: {}
DAY = athlete_today()
FIXTURE = 'goal-performance-diary-v1'
SPORTS = ('Ride', 'TrailRun', 'swimming', 'Crossfit', 'StrengthTraining', 'Weightlifting', 'palestra',
          'weight_lifting', 'Workout', 'Walk', 'Yoga', 'VirtualRide')
CATEGORIES = {'Ride': 'cycling', 'VirtualRide': 'cycling', 'TrailRun': 'running', 'swimming': 'swimming',
              'Crossfit': 'strength', 'StrengthTraining': 'strength', 'Weightlifting': 'strength',
              'palestra': 'strength', 'weight_lifting': 'strength'}
SPECIAL = {
    147: ('Café soglia · canonico nullo', 'Ride'),
    146: ('STRASSE · intervalli sintetici', 'Ride'),
    145: ('100% recupero _ letterale', 'Walk'),
    144: ('Caffè speciale · nuoto sintetico', 'swimming'),
    143: ('Straße · tecnica sintetica', 'TrailRun'),
    142: ('Workout ambiguo · sintetico', 'Workout'),
    141: ('Trail sintetico · carico in conflitto', 'TrailRun'),
}
fixture_rows = []

with Session(engine) as db:
    if db.query(Activity).count():
        raise RuntimeError('QA data already populated; reseeding is forbidden.')
    for identifier in range(1, 148):
        sport = SPORTS[(identifier - 1) % len(SPORTS)]
        name = f'Seduta sintetica {identifier:03d}'
        if identifier in SPECIAL:
            name, sport = SPECIAL[identifier]
        group = (147 - identifier) // 7
        days_ago = 1 + 2 * group
        if identifier <= 21:
            days_ago = 365 * (1 + identifier // 7)
        if identifier == 1:
            days_ago = -3; name = 'Seduta futura sintetica'
        stamp = day_start_utc(DAY - timedelta(days=days_ago)) + timedelta(hours=8)
        row = Activity(id=identifier, source='intervals' if identifier == 147 else 'fit',
            source_id='qa-canonical' if identifier == 147 else f'qa-performance-{identifier}', sport=sport,
            name=name, start_time=stamp, duration_seconds=3600 if identifier == 147 else 1200 + identifier % 9 * 300,
            distance_meters=0 if CATEGORIES.get(sport) == 'strength' else 5000 + identifier * 100,
            elevation_gain_meters=identifier * 3, avg_hr=130 + identifier % 12,
            avg_power=150 if CATEGORIES.get(sport) == 'cycling' else None,
            normalized_power=250 if identifier == 147 else 170 if CATEGORIES.get(sport) == 'cycling' else None,
            load=88 if identifier == 147 else 55 if identifier == 141 else 20 + identifier % 31)
        db.add(row)
        fixture_rows.append(dict(id=identifier, name=name, sport=sport, category=CATEGORIES.get(sport, 'other'),
                                 start_time=stamp.isoformat(), future=identifier == 1))
    db.flush()
    times = list(range(0, 3601, 5))
    db.add_all([
        ActivityObservation(id=1, activity_id=147, source_key='intervals:qa-canonical', payload_json=json.dumps({
            'load': None, 'icu_training_load': 777, 'moving_time': 3600, 'elapsed_time': 3600, 'avg_power': 150,
            'streams': [{'type': 'time', 'data': times}, {'type': 'watts', 'data': [150 if t < 1800 else 160 for t in times]},
                        {'type': 'heartrate', 'data': [130 if t < 1800 else 140 for t in times]}]})),
        ActivityObservation(id=2, activity_id=147, source_key='intervals:qa-newer-alias', payload_json=json.dumps({
            'load': 999, 'avg_power': 999, 'streams': [{'type': 'time', 'data': [0, 3600]}, {'type': 'watts', 'data': [999, 999]}]})),
        ActivityObservation(id=3, activity_id=141, source_key='intervals:qa-conflict-a', payload_json=json.dumps({'load': 20, 'avg_power': 10})),
        ActivityObservation(id=4, activity_id=141, source_key='intervals:qa-conflict-b', payload_json=json.dumps({'load': 80, 'avg_power': 999})),
    ])
    for days_ago in range(1, 76):
        stamp = str(DAY - timedelta(days=days_ago))
        for kind, value in [('tsb', -5), ('sleep_hours', 8), ('hrv_ms', 50), ('resting_hr_bpm', 55)]:
            db.add(HealthMetric(date=stamp, kind=kind, value=value, source='intervals', unit='synthetic', observed_at=stamp, synced_at=stamp))
    db.commit()

CONTRACT = dict(synthetic=True, fixture=FIXTURE, day=str(DAY), rows=fixture_rows, total=len(fixture_rows),
    categories=dict(Counter(row['category'] for row in fixture_rows)),
    expected_ids=[row['id'] for row in sorted(fixture_rows, key=lambda row: (row['start_time'], row['id']), reverse=True)],
    canonical_null_id=147, conflicting_id=141, future_id=1, special_names={str(key): value[0] for key, value in SPECIAL.items()},
    ordering='start_time DESC, id DESC', source='synthetic fixture; no personal data or external provider')
(HERE / 'browser_fixture.json').write_text(json.dumps(CONTRACT, indent=2, ensure_ascii=False), encoding='utf-8')


@app.middleware('http')
async def qa_readonly(request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        mutation_calls.append(request.method + ' ' + request.url.path)
        return JSONResponse({'detail': 'Synthetic QA server is read-only.'}, status_code=405)
    return await call_next(request)


@app.get('/_qa/fixture')
def qa_fixture():
    return CONTRACT


@app.get('/_qa/state')
@app.get('/_qa/statehash')
def qa_state():
    hashes = {}
    with engine.connect() as connection:
        names = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")).scalars().all()
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(map(repr, connection.exec_driver_sql('SELECT * FROM ' + quoted).fetchall()))
            hashes[name] = dict(rows=len(rows), sha256=hashlib.sha256('\n'.join(rows).encode()).hexdigest())
    return dict(synthetic=True, fixture=FIXTURE, day=str(DAY), tables=hashes, table_count=len(hashes),
        provider_configured=False, provider_tripwire_calls=list(provider_calls), mutation_tripwire_calls=list(mutation_calls))


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8004, access_log=False)
