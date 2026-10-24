"""Independent HTTP audit: synthetic in-memory DBs only, never personal data.

The runner deliberately imports route modules rather than app.main. IRON_DATA_DIR
points at an otherwise unused audit directory before any application import.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent
os.environ['IRON_DATA_DIR'] = str(QA / 'review-isolated-unused')
os.environ['IRON_AUTO_SYNC'] = '0'
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_experience, routes_iron, routes_planning, routes_progress
from app.db import experience_models, garmin_models, planning_models, progress_models
from app.db.database import Base, get_db
from app.db.experience_models import SessionFeedback
from app.db.models import Activity, Workout
from app.services import athlete_clock, iron_coach, matching, planning, progress, race_gap, workout_matching
from app.services.athlete_profile import DEFAULT_PROFILE

NOW = datetime(2026, 10, 20, 10)
TODAY = date(2026, 10, 20)
MONDAY = date(2026, 10, 19)


class FrozenDateTime(datetime):
    @classmethod
    def utcnow(cls):
        return NOW


def dump_database(engine):
    with engine.connect() as conn:
        return {table.name: [list(row) for row in conn.execute(text(f'SELECT * FROM "{table.name}" ORDER BY 1')).all()]
                for table in Base.metadata.sorted_tables}


@contextmanager
def fixture():
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    for router in (routes_experience.router, routes_progress.router, routes_iron.router, routes_planning.router):
        app.include_router(router)

    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    patches = []
    for module in (athlete_clock, routes_experience, routes_iron, iron_coach, matching, planning, progress, race_gap, workout_matching):
        for name, value in [('athlete_today', lambda *args: TODAY), ('utc_now_naive', lambda: NOW)]:
            if hasattr(module, name):
                item = patch.object(module, name, value)
                item.start(); patches.append(item)
    item = patch.object(routes_experience, 'datetime', FrozenDateTime)
    item.start(); patches.append(item)
    item = patch.object(planning, 'load_profile', lambda: deepcopy(DEFAULT_PROFILE))
    item.start(); patches.append(item)
    try:
        with TestClient(app, client=('127.0.0.1', 50000), raise_server_exceptions=False) as client:
            yield client, engine
    finally:
        for item in reversed(patches):
            item.stop()
        engine.dispose()


def add_workout(db, identifier=1, day=MONDAY, **changes):
    data = {'id': identifier, 'date': str(day), 'sport': 'cycling', 'name': 'Piano sintetico',
            'duration_min': 60, 'status': 'planned',
            'payload_json': json.dumps({'sport': 'cycling', 'duration_min': 60, 'expected_rpe': 4, 'steps': []})}
    data.update(changes)
    row = Workout(**data); db.add(row); db.flush()
    return row


def add_activity(db, identifier=1, stamp=None, **changes):
    data = {'id': identifier, 'source': 'fit', 'source_id': f'synthetic-{identifier}',
            'sport': 'cycling', 'name': 'Attività sintetica',
            'start_time': stamp or datetime(2026, 10, 19, 8), 'duration_seconds': 3600,
            'distance_meters': 25000, 'load': 50}
    data.update(changes)
    row = Activity(**data); db.add(row); db.flush()
    return row


def response(response):
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return {'status': response.status_code, 'body': body}


def get_readonly(client, engine, path):
    before = dump_database(engine)
    mutations = []
    def statement(conn, cursor, sql, parameters, context, executemany):
        if sql.lstrip().upper().split(' ', 1)[0] in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'DROP', 'ALTER'):
            mutations.append(sql.split(' ', 1)[0])
    event.listen(engine, 'before_cursor_execute', statement)
    try:
        result = response(client.get(path))
    finally:
        event.remove(engine, 'before_cursor_execute', statement)
    result['read_only'] = before == dump_database(engine) and not mutations
    result['mutation_statements'] = mutations
    return result


def save_link(client, workout_id, activity_id, *, token=None, **changes):
    content = {'rpe': 4, 'completion_percent': 100, 'activity_id': activity_id, **changes}
    if token is not None:
        content['activity_match_token'] = token
    return response(client.put(f'/experience/workouts/{workout_id}/feedback', json=content))


def token_from(result, identifier):
    return next(item['match_token'] for item in result['body']['activity_candidates']['items'] if item['id'] == identifier)


def audit():
    results = {}
    with fixture() as (client, engine):
        with Session(engine) as db:
            for identifier in range(1, 5):
                add_workout(db, identifier)
                add_activity(db, identifier, stamp=datetime(2026, 10, 21, 8) if identifier == 4 else None)
            db.commit()
        results['future_link'] = {'saves': [response(client.put(f'/experience/workouts/{identifier}/feedback',
            json={'rpe': 4, 'completion_percent': 100, 'activity_id': identifier})) for identifier in range(1, 5)]}
        for key, path in [('progress', '/progress/week?start=2026-10-19'), ('coach', '/coach/overview'),
                          ('planning', '/planning/week?start=2026-10-19')]:
            results['future_link'][key] = get_readonly(client, engine, path)
    for kind, content in [('malformed_feedback', '['), ('array_feedback', '[]'),
                          ('null_actual', '{"actual": null}'), ('null_evaluation', '{"evaluation": null, "rpe": 4}')]:
        with fixture() as (client, engine):
            with Session(engine) as db:
                add_workout(db, status='completed'); add_activity(db)
                db.add(SessionFeedback(workout_id=1, activity_id=None if kind == 'null_actual' else 1,
                                       payload_json=content))
                db.commit()
            results[kind] = {key: get_readonly(client, engine, path) for key, path in [
                ('review', '/experience/workouts/1/review'), ('progress', '/progress/week?start=2026-10-19'),
                ('coach', '/coach/overview'), ('planning', '/planning/week?start=2026-10-19')]}
    with fixture() as (client, engine):
        with Session(engine) as db:
            add_workout(db); add_activity(db, duration_seconds=None); db.commit()
        results['missing_duration'] = {'save': response(client.put('/experience/workouts/1/feedback',
            json={'rpe': 4, 'completion_percent': 100, 'activity_id': 1}))}
        for key, path in [('progress', '/progress/week?start=2026-10-19'), ('planning', '/planning/week?start=2026-10-19')]:
            results['missing_duration'][key] = get_readonly(client, engine, path)
    with fixture() as (client, engine):
        with Session(engine) as db:
            add_workout(db, day=date(2026, 10, 20), sport='bike',
                        payload_json=json.dumps({'sport': 'bike', 'duration_min': 60, 'expected_rpe': 4, 'steps': []}))
            # UTC 19th late evening belongs to the 20th in Europe/Rome.
            add_activity(db, stamp=datetime(2026, 10, 19, 23), sport='virtualride'); db.commit()
        results['rome_alias'] = {'review': get_readonly(client, engine, '/experience/workouts/1/review')}
        results['rome_alias']['save'] = response(client.put('/experience/workouts/1/feedback',
            json={'rpe': 4, 'completion_percent': 100, 'activity_id': 1}))
        results['rome_alias']['progress'] = get_readonly(client, engine, '/progress/week?start=2026-10-19')
    with fixture() as (client, engine):
        with Session(engine) as db:
            add_workout(db, status='completed')
            add_activity(db, stamp=datetime(2026, 10, 21, 8))
            db.add(SessionFeedback(workout_id=1, activity_id=1,
                                   payload_json=json.dumps({'rpe': 4, 'actual': {'duration_min': 60}})))
            db.commit()
        results['legacy_future_link'] = {key: get_readonly(client, engine, path) for key, path in [
            ('review', '/experience/workouts/1/review'), ('progress', '/progress/week?start=2026-10-19'),
            ('coach', '/coach/overview'), ('planning', '/planning/week?start=2026-10-19')]}
    with fixture() as (client, engine):
        with Session(engine) as db:
            add_workout(db); add_workout(db, 2); add_activity(db); db.commit()
        first = get_readonly(client, engine, '/experience/workouts/1/review')
        second = get_readonly(client, engine, '/experience/workouts/2/review')
        before = dump_database(engine)
        results['shared_activity'] = {'first_preview': first, 'second_preview': second,
            'first_save': save_link(client, 1, 1, token=token_from(first, 1))}
        after_first = dump_database(engine)
        results['shared_activity']['second_save'] = save_link(client, 2, 1, token=token_from(second, 1))
        results['shared_activity']['second_save_atomic'] = after_first == dump_database(engine)
        results['shared_activity']['first_persisted_once'] = len(after_first['session_feedback']) == 1
        results['shared_activity']['new_preview'] = get_readonly(client, engine, '/experience/workouts/2/review')
    with fixture() as (client, engine):
        with Session(engine) as db:
            add_workout(db); add_activity(db); db.commit()
        old = get_readonly(client, engine, '/experience/workouts/1/review')
        with Session(engine) as db:
            db.get(Activity, 1).duration_seconds = 2700; db.commit()
        before = dump_database(engine)
        result = save_link(client, 1, 1, token=token_from(old, 1))
        results['stale_activity'] = {'old_preview': old, 'save': result,
                                     'rejected_without_writes': before == dump_database(engine)}
        fresh = get_readonly(client, engine, '/experience/workouts/1/review')
        results['stale_activity']['fresh_preview'] = fresh
        results['stale_activity']['fresh_save'] = save_link(client, 1, 1, token=token_from(fresh, 1))
        results['stale_activity']['progress'] = get_readonly(client, engine, '/progress/week?start=2026-10-19')
    with fixture() as (client, engine):
        with Session(engine) as db:
            add_workout(db); add_activity(db); db.commit()
        old = get_readonly(client, engine, '/experience/workouts/1/review')
        with Session(engine) as db:
            db.get(Workout, 1).duration_min = 45; db.commit()
        before = dump_database(engine)
        results['stale_plan'] = {'save': save_link(client, 1, 1, token=token_from(old, 1)),
                                'rejected_without_writes': before == dump_database(engine)}
    with fixture() as (client, engine):
        with Session(engine) as db:
            add_workout(db); add_activity(db); db.commit()
        old = get_readonly(client, engine, '/experience/workouts/1/review')
        with Session(engine) as db:
            from app.db.iron_models import DuplicateCandidate
            add_activity(db, 2)
            db.add(DuplicateCandidate(activity_id=1, other_id=2, status='pending')); db.commit()
        before = dump_database(engine)
        results['stale_duplicate_decision'] = {'save': save_link(client, 1, 1, token=token_from(old, 1)),
                                              'rejected_without_writes': before == dump_database(engine),
                                              'fresh_preview': get_readonly(client, engine, '/experience/workouts/1/review')}
    return results


def verify(cases):
    future = cases['future_link']
    legacy = cases['legacy_future_link']
    missing = cases['missing_duration']
    used = cases['shared_activity']
    stale = cases['stale_activity']
    def first_session(item):
        return next(session for day in item['body']['days'] for session in day['sessions'])
    return {
        'future_activity_rejected': [item['status'] for item in future['saves']] == [200, 200, 200, 422],
        'future_rejection_retains_one_unconfirmed': future['progress']['body']['counts']['unconfirmed'] == 1,
        'coach_withholds_global_grade_for_unconfirmed': future['coach']['body']['score'] is None
            and future['coach']['body']['unconfirmed_past_sessions'] == 1,
        'calendar_counts_only_180_confirmed_minutes': future['planning']['body']['summary']['cycling']['completed_minutes'] == 180,
        'legacy_future_feedback_not_confirmation': legacy['progress']['body']['counts']['unconfirmed'] == 1
            and legacy['coach']['body']['unconfirmed_past_sessions'] == 1
            and first_session(legacy['planning'])['execution_status'] == 'unconfirmed',
        'legacy_ledger_status_preserved': first_session(legacy['planning'])['status'] == 'completed',
        'legacy_future_calendar_minutes_zero_confirmed': legacy['planning']['body']['summary']['cycling']['completed_minutes'] == 0,
        'malformed_json_routes_remain_available': all(cases[name][key]['status'] == 200
            for name in ('malformed_feedback', 'array_feedback', 'null_actual', 'null_evaluation')
            for key in ('review', 'progress', 'coach', 'planning')),
        'unknown_duration_does_not_become_zero': missing['save']['body']['actual']['duration_min'] is None
            and missing['save']['body']['evaluation']['completion'] is None
            and missing['planning']['body']['summary']['cycling']['completed_minutes'] is None,
        'unknown_duration_does_not_reduce_plan': missing['planning']['body']['feedback_adaptation']['sports']['cycling']['factor'] == 1,
        'rome_alias_roundtrip': cases['rome_alias']['save']['status'] == 200
            and cases['rome_alias']['review']['body']['activity_candidates']['items'][0]['activity_date'] == '2026-10-20',
        'competing_previews_one_canonical_link': used['first_save']['status'] == 200 and used['second_save']['status'] == 409
            and used['first_persisted_once'] and used['second_save_atomic']
            and not used['new_preview']['body']['activity_candidates']['items'][0]['available'],
        'changed_source_requires_new_preview': stale['save']['status'] == 409 and stale['rejected_without_writes']
            and stale['fresh_save']['status'] == 200
            and stale['progress']['body']['sessions'][0]['actual_minutes'] == 45,
        'changed_plan_requires_new_preview': cases['stale_plan']['save']['status'] == 409
            and cases['stale_plan']['rejected_without_writes'],
        'duplicate_context_requires_new_preview': cases['stale_duplicate_decision']['save']['status'] == 409
            and cases['stale_duplicate_decision']['rejected_without_writes']
            and cases['stale_duplicate_decision']['fresh_preview']['body']['activity_candidates']['items'][0]['duplicate_warning'],
        'all_get_requests_are_readonly': all(value['read_only'] for case in cases.values() for value in case.values()
            if isinstance(value, dict) and 'read_only' in value),
    }


if __name__ == '__main__':
    sources = [ROOT / name for name in ['app/api/routes_experience.py', 'app/services/progress.py',
        'app/services/iron_coach.py', 'app/services/matching.py', 'app/services/planning.py',
        'app/services/execution_evidence.py', 'app/services/workout_matching.py', 'app/services/evaluation.py']]
    hashes = {str(path.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    report = {'clock': str(NOW) + ' UTC', 'database': 'in-memory synthetic only', 'production_db_opened': False,
              'source_sha256': hashes, 'cases': audit()}
    report['checks'] = verify(report['cases'])
    report['checks']['sources_unchanged_during_audit'] = all(
        hashes[str(path.relative_to(ROOT)).replace('\\', '/')] == hashlib.sha256(path.read_bytes()).hexdigest() for path in sources)
    target = QA / 'review_api.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'report': str(target), 'checks': report['checks'], 'passed': all(report['checks'].values())}, ensure_ascii=False))
    if not all(report['checks'].values()):
        raise SystemExit(1)
