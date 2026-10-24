"""Independent reversible-review HTTP probes on synthetic SQLite with FK ON."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api import routes_iron, routes_duplicate_review
from app.db.database import Base, get_db
from app.db.models import Activity, Workout, WorkoutEvaluation
from app.db.experience_models import ActivityIdentity, SessionFeedback
from app.db.iron_models import ActivityObservation, DuplicateCandidate
from app.db.review_models import DuplicateReview
from app.db.repository import save_activity

NOW = datetime(2026, 10, 19, 10, 11, 12, 345678)
REPORTS = []
FIXTURES = {}


@contextmanager
def fixture():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    def foreign_keys(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
    event.listen(engine, 'connect', foreign_keys)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(routes_duplicate_review.router)
    app.include_router(routes_iron.router)
    def session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    try:
        with TestClient(app, client=('127.0.0.1', 19002)) as client:
            yield client, engine
    finally:
        engine.dispose()


def activity(db, key, **changes):
    values = dict(source='fit', source_id=key, name=key, sport='running', start_time=NOW,
                  created_at=NOW, duration_seconds=3600, distance_meters=10000, load=None)
    values.update(changes)
    row = Activity(**values)
    db.add(row)
    db.flush()
    return row


def seed(engine, rich=False):
    with Session(engine) as db:
        keep = activity(db, 'keep', avg_hr=120)
        remove = activity(db, 'remove', source='intervals', avg_hr=130, load=70)
        candidate = DuplicateCandidate(activity_id=remove.id, other_id=keep.id)
        db.add(candidate)
        db.flush()
        result = dict(candidate=candidate.id, keep=keep.id, remove=remove.id)
        if rich:
            third = activity(db, 'third', load=10)
            result['third'] = third.id
            db.add_all([ActivityIdentity(source_key='fit:keep', activity_id=keep.id),
                        ActivityIdentity(source_key='intervals:remove', activity_id=remove.id),
                        ActivityObservation(source_key='fit:keep', activity_id=keep.id,
                                            payload_json='{ "load" : null, "opaque": [1, 2] }'),
                        ActivityObservation(source_key='intervals:remove', activity_id=remove.id,
                                            payload_json='{ "load" : 70, "opaque": {"retained": true} }'),
                        DuplicateCandidate(activity_id=remove.id, other_id=third.id, status='kept'),
                        DuplicateCandidate(activity_id=keep.id, other_id=third.id, status='pending')])
            workout = Workout(date='2026-10-19', sport='running', name='Synthetic', duration_min=60,
                              payload_json='{}', status='completed')
            db.add(workout)
            db.flush()
            result['workout'] = workout.id
            db.add_all([SessionFeedback(workout_id=workout.id, activity_id=remove.id, updated_at=NOW,
                                        payload_json='{ "activity_id" : ' + str(remove.id) + ', "nested": [{"matched_activity_id": ' + str(remove.id) + '}] }'),
                        WorkoutEvaluation(workout_id=workout.id, activity_id=remove.id, created_at=NOW, rpe=4,
                                          payload_json='{ "activity_id": ' + str(remove.id) + ', "unknown": ["preserve", 0, null] }')])
        db.commit()
        return result


def snapshot(engine):
    with Session(engine) as db:
        assert db.connection().exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
        return {table.name: [dict(row) for row in db.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
                for table in Base.metadata.sorted_tables if table.name != 'duplicate_reviews'}


def journal(engine):
    with Session(engine) as db:
        return [(row.id, row.action, row.status, row.before_fingerprint, row.after_fingerprint)
                for row in db.query(DuplicateReview).order_by(DuplicateReview.id)]


def merge(client, candidate):
    preview = client.get(f'/activities/duplicates/{candidate}/preview')
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body['merge_allowed'], body
    result = client.post(f'/activities/duplicates/{candidate}/merge', json={'state_token': body['state_token']})
    assert result.status_code == 200, result.text
    return result.json(), body


def undo(client, event_id, expected=200):
    result = client.post(f'/activities/duplicates/reviews/{event_id}/undo')
    assert result.status_code == expected, result.text
    return result


def exact_roundtrip():
    with fixture() as (client, engine):
        ids = seed(engine, rich=True)
        before = snapshot(engine)
        FIXTURES['history_initial'] = client.get('/activities/duplicates/review-history').json()
        preview_sql = []
        listener = lambda connection, cursor, statement, parameters, context, executemany: preview_sql.append(statement)
        event.listen(engine, 'before_cursor_execute', listener)
        try:
            result = client.get(f"/activities/duplicates/{ids['candidate']}/preview")
        finally:
            event.remove(engine, 'before_cursor_execute', listener)
        assert result.status_code == 200 and result.json()['merge_allowed']
        FIXTURES['preview'] = result.json()
        FIXTURES['pending_before'] = client.get('/activities/duplicates/pending').json()
        assert all(statement.lstrip().upper().startswith(('SELECT', 'BEGIN')) for statement in preview_sql)
        assert snapshot(engine) == before and journal(engine) == []
        merged, body = merge(client, ids['candidate'])
        FIXTURES['merge_response'] = merged
        assert body['result']['avg_hr'] == 120 and body['result']['load'] == 70
        after = snapshot(engine)
        assert len(after['activities']) == len(before['activities']) - 1
        history = client.get('/activities/duplicates/review-history').json()
        FIXTURES['history_applied'] = history
        FIXTURES['pending_after_merge'] = client.get('/activities/duplicates/pending').json()
        assert history['reviews'][0]['undo_available']
        assert not {'before_json', 'after_json', 'payload_json'} & set(history['reviews'][0])
        FIXTURES['undo_response'] = undo(client, merged['review_id']).json()
        FIXTURES['history_undone'] = client.get('/activities/duplicates/review-history').json()
        FIXTURES['pending_after_undo'] = client.get('/activities/duplicates/pending').json()
        assert snapshot(engine) == before
        assert journal(engine)[0][2] == 'undone'
        undo(client, merged['review_id'], expected=409)
        assert snapshot(engine) == before
        REPORTS.append({'case': 'rich-FK-enabled-roundtrip', 'result': 'all business tables and raw JSON restored exactly',
                        'preview_sql': len(preview_sql), 'preview_dml': 0, 'journal_private': True})


def legacy_reimports():
    with fixture() as (client, engine):
        ids = seed(engine)
        before = snapshot(engine)
        merged, _ = merge(client, ids['candidate'])
        with Session(engine) as db:
            assert db.query(ActivityIdentity).filter_by(source_key='intervals:remove').one().activity_id == ids['keep']
        undo(client, merged['review_id'])
        assert snapshot(engine) == before
        with Session(engine) as db:
            for source, source_id, expected in [('fit', 'keep', ids['keep']), ('intervals', 'remove', ids['remove'])]:
                values = dict(source=source, source_id=source_id, sport='running', name=source_id,
                              start_time=NOW, duration_seconds=3600, distance_meters=10000)
                assert save_activity(db, values).id == expected
            assert db.query(Activity).count() == 2
        REPORTS.append({'case': 'legacy-alias-and-reimports', 'result': 'created alias removed by undo; both providers route to original IDs'})


def keep_roundtrip():
    with fixture() as (client, engine):
        ids = seed(engine, rich=True)
        before = snapshot(engine)
        preview = client.get(f"/activities/duplicates/{ids['candidate']}/preview").json()
        result = client.post(f"/activities/duplicates/{ids['candidate']}/keep", json={'state_token': preview['state_token']})
        assert result.status_code == 200
        FIXTURES['keep_response'] = result.json()
        FIXTURES['history_kept'] = client.get('/activities/duplicates/review-history').json()
        entry = FIXTURES['history_kept']['reviews'][0]
        assert entry['action'] == 'keep' and entry['undo_available']
        FIXTURES['keep_undo_response'] = undo(client, entry['id']).json()
        FIXTURES['history_keep_undone'] = client.get('/activities/duplicates/review-history').json()
        assert snapshot(engine) == before
        REPORTS.append({'case': 'keep-roundtrip', 'result': 'actual keep response needs no review_id; history supports exact undo'})


def conflicts():
    for kind in ('observation', 'reused_candidate_id', 'source_id'):
        with fixture() as (client, engine):
            ids = seed(engine)
            merged, _ = merge(client, ids['candidate'])
            with Session(engine) as db:
                if kind == 'observation':
                    db.add(ActivityObservation(source_key='intervals:later-sync', activity_id=ids['keep'], payload_json='{"load":99}'))
                elif kind == 'reused_candidate_id':
                    left = activity(db, 'unrelated-left', id=100)
                    right = activity(db, 'unrelated-right', id=101)
                    reused = DuplicateCandidate(activity_id=left.id, other_id=right.id)
                    db.add(reused)
                    db.flush()
                    assert reused.id == ids['candidate']
                else:
                    activity(db, 'remove', id=100, source='strava')
                db.commit()
            changed, events = snapshot(engine), journal(engine)
            undo(client, merged['review_id'], expected=409)
            assert snapshot(engine) == changed and journal(engine) == events
            REPORTS.append({'case': 'undo-conflict-' + kind, 'http_status': 409, 'all_data_unchanged': True})


def unrelated_change_and_chain():
    with fixture() as (client, engine):
        ids = seed(engine, rich=True)
        before = snapshot(engine)
        merged, _ = merge(client, ids['candidate'])
        with Session(engine) as db:
            db.get(Activity, ids['third']).name = 'Later unrelated rename'
            db.commit()
        for row in before['activities']:
            if row['id'] == ids['third']:
                row['name'] = 'Later unrelated rename'
        undo(client, merged['review_id'])
        assert snapshot(engine) == before
        REPORTS.append({'case': 'unrelated-third-party-update', 'result': 'undo permitted; later third-party value preserved'})
    with fixture() as (client, engine):
        ids = seed(engine)
        with Session(engine) as db:
            third = activity(db, 'chain-third', source='strava', id=3)
            second = DuplicateCandidate(activity_id=third.id, other_id=ids['keep'])
            db.add(second)
            db.commit()
            second_id = second.id
        before = snapshot(engine)
        first, _ = merge(client, ids['candidate'])
        second, _ = merge(client, second_id)
        changed = snapshot(engine)
        undo(client, first['review_id'], expected=409)
        assert snapshot(engine) == changed
        undo(client, second['review_id'])
        undo(client, first['review_id'])
        assert snapshot(engine) == before
        REPORTS.append({'case': 'chained-merges', 'result': 'wrong undo order rejected; newest then oldest restores all exact rows'})


def failures():
    with fixture() as (client, engine):
        ids = seed(engine, rich=True)
        merged, _ = merge(client, ids['candidate'])
        changed, events = snapshot(engine), journal(engine)
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('INSERT INTO ACTIVITY_OBSERVATIONS'):
                raise OperationalError(statement, parameters, RuntimeError('Synthetic restore interruption'))
        event.listen(engine, 'before_cursor_execute', fail)
        try:
            undo(client, merged['review_id'], expected=409)
        finally:
            event.remove(engine, 'before_cursor_execute', fail)
        assert snapshot(engine) == changed and journal(engine) == events
        undo(client, merged['review_id'])
        REPORTS.append({'case': 'mid-restore-database-failure', 'result': 'all row deletions/inserts and journal state rolled back; retry succeeds'})
    for raw in ('[1]', '{broken', '{"value":NaN}', '[' * 2000 + '1' + ']' * 2000):
        with fixture() as (client, engine):
            ids = seed(engine)
            with Session(engine) as db:
                db.add(ActivityObservation(source_key='intervals:invalid', activity_id=ids['remove'], payload_json=raw))
                db.commit()
            before = snapshot(engine)
            preview = client.get(f"/activities/duplicates/{ids['candidate']}/preview")
            assert preview.status_code == 200 and not preview.json()['merge_allowed']
            result = client.post(f"/activities/duplicates/{ids['candidate']}/merge", json={'state_token': preview.json()['state_token']})
            assert result.status_code == 409
            assert snapshot(engine) == before and journal(engine) == []
    REPORTS.append({'case': 'four-malformed-payload-shapes', 'result': 'blocked previews/merges with no mutations or journal success'})
    with fixture() as (client, engine):
        with Session(engine) as db:
            row = activity(db, 'self')
            candidate = DuplicateCandidate(activity_id=row.id, other_id=row.id)
            db.add(candidate)
            db.commit()
            identifier = candidate.id
        before = snapshot(engine)
        preview = client.get(f'/activities/duplicates/{identifier}/preview').json()
        assert not preview['merge_allowed']
        result = client.post(f'/activities/duplicates/{identifier}/merge', json={'state_token': preview['state_token']})
        assert result.status_code == 409 and snapshot(engine) == before and journal(engine) == []
        REPORTS.append({'case': 'self-pair', 'result': '409; only Activity preserved; no success journal'})
        FIXTURES['self_preview'] = preview
    with fixture() as (client, engine):
        ids = seed(engine)
        initial = client.get(f"/activities/duplicates/{ids['candidate']}/preview").json()
        with Session(engine) as db:
            db.get(Activity, ids['keep']).load = 55
            db.commit()
        before = snapshot(engine)
        result = client.post(f"/activities/duplicates/{ids['candidate']}/merge", json={'state_token': initial['state_token']})
        assert result.status_code == 409 and snapshot(engine) == before and journal(engine) == []
        FIXTURES['stale_merge_error'] = result.json()
        REPORTS.append({'case': 'stale-preview-token', 'result': '409; current source values preserved; no journal success'})
    with fixture() as (client, engine):
        ids = seed(engine)
        with Session(engine) as db:
            db.get(Activity, ids['remove']).load = float('inf')
            db.commit()
        before = snapshot(engine)
        result = client.get(f"/activities/duplicates/{ids['candidate']}/preview")
        assert result.status_code == 200 and not result.json()['merge_allowed']
        assert snapshot(engine) == before
        FIXTURES['invalid_numeric_preview'] = result.json()
        REPORTS.append({'case': 'nonfinite-canonical-preview', 'result': 'safe JSON, visible block, no mutation'})


def main():
    paths = ['app/services/duplicate_review.py', 'app/db/review_models.py', 'app/api/routes_duplicate_review.py', 'app/api/routes_iron.py']
    hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    exact_roundtrip()
    legacy_reimports()
    keep_roundtrip()
    conflicts()
    unrelated_change_and_chain()
    failures()
    assert hashes == {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}, 'Sources changed during probe; rerun when stable'
    output = {'result': 'passed', 'scope': 'Actual FastAPI HTTP in memory; all scenarios SQLite foreign_keys=ON',
              'personal_database_opened': False, 'provider_calls': 0, 'source_sha256': hashes,
              'reports': REPORTS, 'fixtures': FIXTURES}
    Path(__file__).with_name('review_http.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in output.items() if key != 'fixtures'} | {'fixture_keys': list(FIXTURES)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
