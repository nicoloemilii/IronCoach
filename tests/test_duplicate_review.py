"""Preview, mutation and undo operate on exact guarded closures atomically."""
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_iron, routes_duplicate_review
from app.db import database
from app.db.database import Base, get_db
from app.db.models import Activity, Workout, WorkoutEvaluation
from app.db.experience_models import ActivityIdentity, SessionFeedback
from app.db.iron_models import ActivityObservation, DuplicateCandidate
from app.db.review_models import DuplicateReview
from app.services import duplicate_review as review


NOW = datetime(2026,10,19,10,11,12,345678)


@pytest.fixture
def api():
    engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    app=FastAPI();app.include_router(routes_duplicate_review.router);app.include_router(routes_iron.router)
    def session():
        with Session(engine) as db:yield db
    app.dependency_overrides[get_db]=session
    with TestClient(app,client=('127.0.0.1',50000)) as client:yield client,engine
    engine.dispose()


def activity(db,key,**changes):
    values=dict(source='fit',source_id=key,sport='cycling',name=key,start_time=NOW,
                created_at=NOW,duration_seconds=3600,distance_meters=20000,load=None)
    values.update(changes);row=Activity(**values);db.add(row);db.flush();return row


def seed(engine,rich=False):
    with Session(engine) as db:
        keep=activity(db,'keep',avg_hr=120)
        remove=activity(db,'remove',source='intervals',load=70,avg_hr=130)
        candidate=DuplicateCandidate(activity_id=remove.id,other_id=keep.id,status='pending')
        db.add(candidate);db.flush()
        if rich:
            db.add_all([ActivityIdentity(source_key='fit:keep',activity_id=keep.id),
                        ActivityIdentity(source_key='intervals:remove',activity_id=remove.id),
                        ActivityObservation(activity_id=remove.id,source_key='intervals:remove',payload_json='{ "load" : 70, "unknown": [1, 2] }'),
                        ActivityObservation(activity_id=keep.id,source_key='fit:keep',payload_json='{"load":null}')])
            workout=Workout(date='2026-10-19',sport='cycling',name='Seduta',duration_min=60,payload_json='{}',status='planned')
            db.add(workout);db.flush()
            db.add(SessionFeedback(workout_id=workout.id,activity_id=remove.id,updated_at=NOW,
                payload_json='{ "activity_id": '+str(remove.id)+', "nested": [{"matched_activity_id": '+str(remove.id)+'}] }'))
            db.add(WorkoutEvaluation(workout_id=workout.id,activity_id=remove.id,created_at=NOW,rpe=4,
                                     payload_json='{ "matched_activity_id": '+str(remove.id)+' }'))
            third=activity(db,'third',load=30)
            db.add_all([DuplicateCandidate(activity_id=remove.id,other_id=third.id,status='kept'),
                        DuplicateCandidate(activity_id=keep.id,other_id=third.id,status='pending')])
        db.commit();return candidate.id,remove.id,keep.id


def snapshot(engine):
    with Session(engine) as db:
        return {model.__tablename__:[review._row(row) for row in db.query(model).order_by(model.id)] for model in review.MODELS}


def merge(client,identifier,token=None):
    if token is None:
        response=client.get(f'/activities/duplicates/{identifier}/preview')
        assert response.status_code==200,response.text
        token=response.json()['state_token']
    return client.post(f'/activities/duplicates/{identifier}/merge',json={'state_token':token})


def test_preview_is_select_only_and_describes_field_precedence_and_links(api):
    client,engine=api;identifier,remove,keep=seed(engine,rich=True);before=snapshot(engine)
    statements=[]
    def capture(conn,cursor,statement,*args):statements.append(statement)
    event.listen(engine,'before_cursor_execute',capture)
    try:result=client.get(f'/activities/duplicates/{identifier}/preview')
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert result.status_code==200
    body=result.json();assert body['merge_allowed'] and len(body['state_token'])==64
    assert body['activity']['id']==remove and body['other']['id']==keep
    assert body['result']['avg_hr']==120 and body['result']['load']==70
    assert body['result']['source']=='fit' and body['result']['source_id']=='keep'
    assert next(row for row in body['fields'] if row['field']=='avg_hr')['source']=='existing'
    assert next(row for row in body['fields'] if row['field']=='load')['source']=='imported'
    assert body['links']['feedback']['total']==1 and body['links']['candidates']['total']==3
    assert statements[0]=='BEGIN'
    assert all(statement=='BEGIN' or statement.lstrip().upper().startswith('SELECT') for statement in statements)
    assert snapshot(engine)==before
    with Session(engine) as db:assert db.query(DuplicateReview).count()==0


def test_direct_merge_requires_preview_token_and_stale_state_never_mutates(api):
    client,engine=api;identifier,remove,keep=seed(engine)
    before=snapshot(engine)
    assert client.post(f'/activities/duplicates/{identifier}/merge').status_code==428
    assert snapshot(engine)==before
    token=client.get(f'/activities/duplicates/{identifier}/preview').json()['state_token']
    with Session(engine) as db:db.get(Activity,remove).load=80;db.commit()
    changed=snapshot(engine)
    assert merge(client,identifier,token).status_code==409
    assert snapshot(engine)==changed
    with Session(engine) as db:assert db.query(DuplicateReview).count()==0


def test_merge_and_undo_restore_exact_raw_strings_ids_timestamps_and_third_candidates(api):
    client,engine=api;identifier,remove,keep=seed(engine,rich=True);before=snapshot(engine)
    result=merge(client,identifier);assert result.status_code==200,result.text
    event_id=result.json()['review_id']
    with Session(engine) as db:
        assert db.get(Activity,remove) is None
        assert db.get(Activity,keep).load==70 and db.get(Activity,keep).avg_hr==120
        assert {row.activity_id for row in db.query(ActivityIdentity)}=={keep}
        assert db.query(DuplicateCandidate).one().status=='kept'
        content=json.loads(db.query(SessionFeedback).one().payload_json)
        assert content['nested'][0]['matched_activity_id']==keep
        saved=db.get(DuplicateReview,event_id)
        closure_before={**before,'activities':[row for row in before['activities'] if row['id'] in (remove,keep)]}
        assert json.loads(saved.before_json)==closure_before and saved.status=='applied'
    history=client.get('/activities/duplicates/review-history').json()
    assert history['reviews'][0]['undo_available']
    assert 'before_json' not in json.dumps(history) and 'payload_json' not in json.dumps(history)
    undone=client.post(f'/activities/duplicates/reviews/{event_id}/undo')
    assert undone.status_code==200,undone.text
    assert snapshot(engine)==before
    with Session(engine) as db:
        saved=db.get(DuplicateReview,event_id)
        assert saved.status=='undone' and saved.undone_at is not None
    assert client.post(f'/activities/duplicates/reviews/{event_id}/undo').status_code==409


def test_undo_removes_merge_created_legacy_alias(api):
    client,engine=api;identifier,remove,keep=seed(engine);before=snapshot(engine)
    result=merge(client,identifier);event_id=result.json()['review_id']
    with Session(engine) as db:assert db.query(ActivityIdentity).filter_by(source_key='intervals:remove').one().activity_id==keep
    assert client.post(f'/activities/duplicates/reviews/{event_id}/undo').status_code==200
    assert snapshot(engine)==before
    with Session(engine) as db:assert db.query(ActivityIdentity).count()==0


@pytest.mark.parametrize('change',['canonical','identity','observation','feedback','evaluation','candidate','retarget'])
def test_new_sync_or_link_changes_block_undo_without_overwriting_them(api,change):
    client,engine=api;identifier,remove,keep=seed(engine,rich=True)
    result=merge(client,identifier);event_id=result.json()['review_id']
    with Session(engine) as db:
        if change=='canonical':db.get(Activity,keep).name='Nuovo sync'
        elif change=='identity':db.add(ActivityIdentity(source_key='new:alias',activity_id=keep))
        elif change=='observation':db.query(ActivityObservation).first().payload_json='{"load":99}'
        elif change=='feedback':db.query(SessionFeedback).one().payload_json='{"new":true}'
        elif change=='evaluation':db.query(WorkoutEvaluation).one().rpe=7
        elif change=='candidate':
            third=activity(db,'later');db.add(DuplicateCandidate(activity_id=keep,other_id=third.id))
        elif change=='retarget':
            third=db.query(Activity).filter(Activity.id!=keep).one()
            db.query(ActivityObservation).first().activity_id=third.id
        db.commit()
    changed=snapshot(engine)
    result=client.post(f'/activities/duplicates/reviews/{event_id}/undo')
    assert result.status_code==409,result.text
    assert snapshot(engine)==changed
    with Session(engine) as db:assert db.get(DuplicateReview,event_id).status=='applied'


@pytest.mark.parametrize('collision',['activity_id','candidate_id','source_id','identity_id','source_key'])
def test_global_reused_ids_and_external_identity_collisions_block_undo(api,collision):
    client,engine=api;identifier,remove,keep=seed(engine)
    result=merge(client,identifier);event_id=result.json()['review_id']
    with Session(engine) as db:
        third=activity(db,'unrelated',id=100)
        fourth=activity(db,'unrelated2',id=101)
        if collision=='activity_id':activity(db,'reused',id=remove)
        elif collision=='candidate_id':db.add(DuplicateCandidate(id=identifier,activity_id=third.id,other_id=fourth.id))
        elif collision=='source_id':activity(db,'remove',id=102)
        elif collision=='identity_id':
            # Synthetic alias is expected in the current closure. Move it away:
            # membership fingerprint must detect this even before ID checks.
            db.query(ActivityIdentity).one().activity_id=third.id
        else:
            db.query(ActivityIdentity).one().source_key='changed:key'
            db.add(ActivityIdentity(source_key='intervals:remove',activity_id=third.id))
        db.commit()
    changed=snapshot(engine)
    assert client.post(f'/activities/duplicates/reviews/{event_id}/undo').status_code==409
    assert snapshot(engine)==changed


def test_chained_reviews_can_only_be_undone_in_reverse_order(api):
    client,engine=api;identifier,remove,keep=seed(engine);before=snapshot(engine)
    first=merge(client,identifier).json()['review_id']
    with Session(engine) as db:
        third=activity(db,'third')
        candidate=DuplicateCandidate(activity_id=keep,other_id=third.id)
        db.add(candidate);db.commit();second_candidate=candidate.id
    before_second=snapshot(engine)
    second=merge(client,second_candidate).json()['review_id']
    assert client.post(f'/activities/duplicates/reviews/{first}/undo').status_code==409
    assert client.post(f'/activities/duplicates/reviews/{second}/undo').status_code==200
    assert snapshot(engine)==before_second
    # The new third/candidate remains a later change to first's closure: remove
    # that explicit review lead before the older undo can match its exact post.
    with Session(engine) as db:
        db.delete(db.get(DuplicateCandidate,second_candidate));db.flush()
        db.delete(db.query(Activity).filter_by(source_id='third').one());db.commit()
    assert client.post(f'/activities/duplicates/reviews/{first}/undo').status_code==200
    assert snapshot(engine)==before


def test_preexisting_chained_candidates_restore_in_reverse_without_manual_cleanup(api):
    client,engine=api;identifier,remove,keep=seed(engine)
    with Session(engine) as db:
        third=activity(db,'third')
        candidate=DuplicateCandidate(activity_id=keep,other_id=third.id)
        db.add(candidate);db.commit();second_candidate=candidate.id
    before=snapshot(engine)
    first=merge(client,identifier).json()['review_id']
    second=merge(client,second_candidate).json()['review_id']
    assert client.post(f'/activities/duplicates/reviews/{first}/undo').status_code==409
    assert client.post(f'/activities/duplicates/reviews/{second}/undo').status_code==200
    assert client.post(f'/activities/duplicates/reviews/{first}/undo').status_code==200
    assert snapshot(engine)==before


def test_reverse_kept_decision_is_disclosed_and_blocks_silent_override(api):
    client,engine=api;identifier,remove,keep=seed(engine)
    with Session(engine) as db:
        db.add(DuplicateCandidate(activity_id=keep,other_id=remove,status='kept'));db.commit()
    before=snapshot(engine)
    body=client.get(f'/activities/duplicates/{identifier}/preview').json()
    assert not body['merge_allowed'] and any('distinte' in reason for reason in body['blocking_reasons'])
    assert merge(client,identifier,body['state_token']).status_code==409
    assert snapshot(engine)==before


def test_keep_is_journaled_and_can_be_undone_without_a_preview_body(api):
    client,engine=api;identifier,remove,keep=seed(engine);before=snapshot(engine)
    assert client.post(f'/activities/duplicates/{identifier}/keep').json()=={'status':'kept'}
    item=client.get('/activities/duplicates/review-history').json()['reviews'][0]
    assert item['action']=='keep' and item['undo_available']
    assert client.post(f"/activities/duplicates/reviews/{item['id']}/undo").status_code==200
    assert snapshot(engine)==before


@pytest.mark.parametrize('raw',['{broken','[]','null','{"value":NaN}', '{"x":'+ '['*40+'1'+']'*40+'}', '['*2000+'1'+']'*2000])
def test_malformed_linked_json_disables_merge_in_preview_without_a_server_error(api,raw):
    client,engine=api;identifier,remove,keep=seed(engine)
    with Session(engine) as db:
        db.add(ActivityObservation(activity_id=remove,source_key='intervals:bad',payload_json=raw));db.commit()
    before=snapshot(engine)
    reply=client.get(f'/activities/duplicates/{identifier}/preview')
    assert reply.status_code==200
    body=reply.json();assert not body['merge_allowed'] and body['blocking_reasons']
    assert merge(client,identifier,body['state_token']).status_code==409
    assert snapshot(engine)==before


def test_self_pair_never_deletes_the_activity_or_creates_a_journal_event(api):
    client,engine=api
    with Session(engine) as db:
        row=activity(db,'self');candidate=DuplicateCandidate(activity_id=row.id,other_id=row.id)
        db.add(candidate);db.commit();identifier=candidate.id
    before=snapshot(engine)
    body=client.get(f'/activities/duplicates/{identifier}/preview').json()
    assert not body['merge_allowed']
    assert merge(client,identifier,body['state_token']).status_code==409
    assert snapshot(engine)==before
    with Session(engine) as db:assert db.query(DuplicateReview).count()==0


@pytest.mark.parametrize('bound',['MAX_ROWS','MAX_BYTES','MAX_DEPTH'])
def test_preview_limits_make_merge_unavailable_instead_of_truncating_a_journal(api,monkeypatch,bound):
    client,engine=api;identifier,remove,keep=seed(engine,rich=True)
    monkeypatch.setattr(review,bound,1)
    result=client.get(f'/activities/duplicates/{identifier}/preview')
    assert result.status_code==200 and not result.json()['merge_allowed']


def test_failure_after_mutation_rolls_back_activity_changes_and_journal(api,monkeypatch):
    client,engine=api;identifier,remove,keep=seed(engine,rich=True);before=snapshot(engine)
    token=client.get(f'/activities/duplicates/{identifier}/preview').json()['state_token']
    original=review._snapshot;calls=0
    def failing(db,ids):
        nonlocal calls
        calls+=1
        if calls==2:raise review.Unavailable('Synthetic post-snapshot failure')
        return original(db,ids)
    monkeypatch.setattr(review,'_snapshot',failing)
    assert merge(client,identifier,token).status_code==409
    assert snapshot(engine)==before
    with Session(engine) as db:assert db.query(DuplicateReview).count()==0


def test_local_mutations_reject_cross_origin_and_remote_clients(api):
    client,engine=api;identifier,remove,keep=seed(engine)
    token=client.get(f'/activities/duplicates/{identifier}/preview').json()['state_token']
    assert client.post(f'/activities/duplicates/{identifier}/merge',json={'state_token':token},headers={'Origin':'https://other.invalid'}).status_code==403
    remote=TestClient(client.app,client=('192.168.1.1',1234))
    assert remote.post(f'/activities/duplicates/{identifier}/keep').status_code==403
    assert remote.post('/activities/duplicates/reviews/1/undo').status_code==403


def test_deleted_external_workout_or_third_activity_blocks_undo(api):
    client,engine=api;identifier,remove,keep=seed(engine,rich=True)
    event_id=merge(client,identifier).json()['review_id']
    with Session(engine) as db:
        db.delete(db.query(Workout).one());db.commit()
    changed=snapshot(engine)
    assert client.post(f'/activities/duplicates/reviews/{event_id}/undo').status_code==409
    assert snapshot(engine)==changed


def test_sqlite_writer_lock_precedes_snapshot_and_all_mutations(api):
    client,engine=api;identifier,remove,keep=seed(engine)
    token=client.get(f'/activities/duplicates/{identifier}/preview').json()['state_token']
    statements=[]
    def capture(conn,cursor,statement,*args):statements.append(statement)
    event.listen(engine,'before_cursor_execute',capture)
    try:result=merge(client,identifier,token)
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert result.status_code==200
    assert statements[0]=='BEGIN IMMEDIATE'
    assert next(i for i,item in enumerate(statements) if item.lstrip().upper().startswith(('INSERT','UPDATE','DELETE'))) > 0


def test_preview_keeps_one_sqlite_read_snapshot_when_a_sync_commits_between_reads(tmp_path,monkeypatch):
    engine=create_engine(f'sqlite:///{tmp_path / "concurrent-review.db"}')
    with engine.begin() as connection:connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    Base.metadata.create_all(engine)
    identifier,remove,keep=seed(engine)
    original=review._snapshot;changed=False
    def concurrent_sync(db,identifiers):
        nonlocal changed
        if not changed:
            changed=True
            with Session(engine) as writer:
                writer.add(ActivityObservation(activity_id=remove,source_key='intervals:during-preview',payload_json='{"load":99}'))
                writer.commit()
        return original(db,identifiers)
    monkeypatch.setattr(review,'_snapshot',concurrent_sync)
    try:
        with Session(engine) as db:
            body=review.preview(db,identifier)
            assert body['links']['observations']['total']==0
            assert body['result']['load']==70
        with Session(engine) as db:
            with pytest.raises(Exception) as failure:review.resolve(db,identifier,'merge',body['state_token'])
            assert failure.value.status_code==409
            assert db.query(Activity).count()==2
            assert db.query(ActivityObservation).count()==1
            assert db.query(DuplicateReview).count()==0
    finally:engine.dispose()


def test_undo_failure_rolls_back_restoration_and_preserves_the_applied_journal(api,monkeypatch):
    client,engine=api;identifier,remove,keep=seed(engine,rich=True)
    event_id=merge(client,identifier).json()['review_id'];after=snapshot(engine)
    original=review._restore
    def fail_after_restore(db,before,post):
        original(db,before,post)
        raise review.Unavailable('Synthetic restore failure')
    monkeypatch.setattr(review,'_restore',fail_after_restore)
    assert client.post(f'/activities/duplicates/reviews/{event_id}/undo').status_code==409
    assert snapshot(engine)==after
    with Session(engine) as db:assert db.get(DuplicateReview,event_id).status=='applied'


def test_hosted_review_history_and_mutations_are_workspace_scoped_and_csrf_protected(tmp_path,monkeypatch):
    from app.commercial.middleware import ProductBoundary
    from app.commercial.accounts import COOKIE
    monkeypatch.setattr(database,'DATA_DIR',tmp_path)
    identifiers=[uuid4().hex,uuid4().hex]
    class Accounts:
        def authenticate(self,cookie):
            if cookie not in ('a','b'):return None
            return {'id':identifiers[0 if cookie=='a' else 1],'csrf':'test-csrf'}
        def limit(self,*args):return None
    app=FastAPI();app.include_router(routes_duplicate_review.router);app.include_router(routes_iron.router)
    app.add_middleware(ProductBoundary,settings=SimpleNamespace(hosted=True,public_url='https://product.test',vault_key='test'),accounts=Accounts())
    try:
        for identifier in identifiers:
            with database.workspace_scope(identifier):seed(database.workspace_engine())
        with TestClient(app,base_url='https://product.test') as client:
            assert client.get('/activities/duplicates/1/preview').status_code==401
            client.cookies.set(COOKIE,'a')
            preview=client.get('/activities/duplicates/1/preview')
            assert preview.status_code==200 and preview.headers['cache-control']=='no-store'
            token=preview.json()['state_token']
            assert client.post('/activities/duplicates/1/merge',json={'state_token':token}).status_code==403
            result=client.post('/activities/duplicates/1/merge',json={'state_token':token},headers={'x-csrf-token':'test-csrf'})
            assert result.status_code==200,result.text
            event_id=result.json()['review_id']
            assert len(client.get('/activities/duplicates/review-history').json()['reviews'])==1
            client.cookies.set(COOKIE,'b')
            assert client.get('/activities/duplicates/review-history').json()['reviews']==[]
            assert client.post(f'/activities/duplicates/reviews/{event_id}/undo',headers={'x-csrf-token':'test-csrf'}).status_code==404
            assert client.get('/activities/duplicates/1/preview').json()['merge_allowed']
        assert database.WORKSPACE.get() is None
    finally:
        for path in list(database._WORKSPACE_ENGINES):
            if str(tmp_path) in path:database._WORKSPACE_ENGINES.pop(path).dispose()
