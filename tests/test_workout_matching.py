"""Guided matching uses isolated HTTP and canonical recorded data."""
from datetime import date, datetime, timedelta
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_experience
from app.db.database import Base, get_db
from app.db.experience_models import SessionFeedback
from app.db.iron_models import DuplicateCandidate
from app.db.models import Activity, Workout, WorkoutEvaluation, AthleteAdaptation
from app.services import workout_matching, matching

NOW = datetime(2026,10,20,10)
DAY = date(2026,10,20)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(workout_matching,'utc_now_naive',lambda:NOW)
    monkeypatch.setattr(routes_experience,'athlete_today',lambda:DAY)
    monkeypatch.setattr(matching,'utc_now_naive',lambda:NOW)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Workout(id=key,date='2026-10-19',sport='cycling',name=f'Piano {key}',duration_min=60,
            payload_json=json.dumps({'sport':'cycling','duration_min':60,'expected_rpe':4,'steps':[]})) for key in (1,2)])
        db.commit()
    app = FastAPI(); app.include_router(routes_experience.router)
    def isolated():
        with Session(engine) as db: yield db
    app.dependency_overrides[get_db] = isolated
    with TestClient(app,client=('127.0.0.1',12345)) as client: yield client,engine
    engine.dispose()


def activity(engine, identifier=1, **overrides):
    values = dict(id=identifier,source='fit',source_id=f'fit-{identifier}',sport='cycling',name=f'Attività {identifier}',
        start_time=datetime(2026,10,19,8),duration_seconds=3600,distance_meters=30000)
    values.update(overrides)
    with Session(engine) as db:
        db.add(Activity(**values)); db.commit()


def review(client):
    response = client.get('/experience/workouts/1/review')
    assert response.status_code == 200,response.text
    return response.json()


def save(client, identifier=1, **changes):
    return client.put('/experience/workouts/1/feedback',json={'rpe':4,'completion_percent':100,'activity_id':identifier,**changes})


def ledger(engine):
    models = (Activity,Workout,SessionFeedback,WorkoutEvaluation,AthleteAdaptation,DuplicateCandidate)
    with engine.connect() as connection:
        return {model.__tablename__:[dict(row) for row in connection.execute(select(model.__table__).order_by(model.__table__.c.id)).mappings()] for model in models}


def test_review_is_readonly_contains_workout_and_explained_candidates(api):
    client,engine = api; activity(engine)
    before = ledger(engine); statements=[]
    def record(conn,cursor,sql,parameters,context,many): statements.append(sql)
    event.listen(engine,'before_cursor_execute',record)
    try: body=review(client)
    finally: event.remove(engine,'before_cursor_execute',record)
    candidate=body['activity_candidates']['items'][0]
    assert candidate['day_delta']==0 and candidate['duration_delta_min']==0
    assert candidate['activity_date']=='2026-10-19' and candidate['available']
    assert len(candidate['match_token'])==64 and 'confidence' not in candidate
    assert body['workout']['payload']['duration_min']==60 and body['auto_completed'] is False
    assert statements[0]=='BEGIN' and all(sql=='BEGIN' or sql.lstrip().upper().startswith('SELECT') for sql in statements)
    assert ledger(engine)==before


@pytest.mark.parametrize('stamp',[datetime(2026,10,21,8),datetime(2026,10,20,11)])
def test_future_activity_never_appears_or_completes_a_past_plan(api,stamp):
    client,engine=api; activity(engine,start_time=stamp); before=ledger(engine)
    assert review(client)['activity_candidates']['items']==[]
    assert save(client).status_code==422 and ledger(engine)==before


@pytest.mark.parametrize('sport',['VirtualRide','virtual_ride','MountainBikeRide'])
def test_canonical_sport_alias_is_a_candidate_and_can_be_confirmed(api,sport):
    client,engine=api; activity(engine,sport=sport)
    candidate=review(client)['activity_candidates']['items'][0]
    assert candidate['sport']==sport
    result=save(client,activity_match_token=candidate['match_token'])
    assert result.status_code==200,result.text
    assert result.json()['actual']['duration_min']==60


@pytest.mark.parametrize('field,value',[('duration_seconds',1800),('name','Nuova importazione'),('source_id','changed-source')])
def test_changed_record_invalidates_preview_and_preserves_all_tables(api,field,value):
    client,engine=api; activity(engine)
    token=review(client)['activity_candidates']['items'][0]['match_token']
    with Session(engine) as db: setattr(db.get(Activity,1),field,value); db.commit()
    before=ledger(engine)
    result=save(client,activity_match_token=token)
    assert result.status_code==409 and ledger(engine)==before
    assert save(client,activity_match_token=review(client)['activity_candidates']['items'][0]['match_token']).status_code==200


def test_changed_plan_or_duplicate_decision_invalidates_preview(api):
    client,engine=api; activity(engine); activity(engine,2)
    token=review(client)['activity_candidates']['items'][0]['match_token']
    with Session(engine) as db:
        db.add(DuplicateCandidate(activity_id=1,other_id=2,status='pending')); db.commit()
    body=review(client)
    assert body['activity_candidates']['items'][0]['duplicate_warning']
    before=ledger(engine)
    assert save(client,activity_match_token=token).status_code==409 and ledger(engine)==before
    token=body['activity_candidates']['items'][0]['match_token']
    with Session(engine) as db: db.get(Workout,1).name='Piano cambiato'; db.commit()
    before=ledger(engine)
    assert save(client,activity_match_token=token).status_code==409 and ledger(engine)==before


def test_used_activity_disclosed_and_cannot_be_associated_twice(api):
    client,engine=api; activity(engine)
    result=client.put('/experience/workouts/2/feedback',json={'rpe':4,'completion_percent':100,'activity_id':1})
    assert result.status_code==200,result.text
    row=review(client)['activity_candidates']['items'][0]
    assert row['available'] is False and row['match_token'] is None and 'altra seduta' in row['unavailable_reason']
    before=ledger(engine)
    assert save(client).status_code==409 and ledger(engine)==before


@pytest.mark.parametrize('seconds,expected',[(None,None),(0,0)])
def test_missing_duration_and_explicit_zero_remain_distinct(api,seconds,expected):
    client,engine=api; activity(engine,duration_seconds=seconds)
    candidate=review(client)['activity_candidates']['items'][0]
    assert candidate['duration_min']==expected and candidate['available']
    result=save(client,activity_match_token=candidate['match_token'])
    assert result.status_code==200,result.text
    body=result.json()
    assert body['actual']['duration_min']==expected and body['evaluation']['completion']==expected
    if seconds is None: assert body['evaluation']['adaptation']=='hold' and body['evaluation']['overall'] is None


def test_current_binding_can_be_reviewed_and_edited_with_new_token(api):
    client,engine=api; activity(engine)
    candidate=review(client)['activity_candidates']['items'][0]
    assert save(client,activity_match_token=candidate['match_token']).status_code==200
    current=review(client)['activity_candidates']['items'][0]
    assert current['current'] and current['match_token']!=candidate['match_token']
    assert save(client,rpe=5,activity_match_token=current['match_token']).status_code==200
    with Session(engine) as db: assert db.query(SessionFeedback).count()==db.query(WorkoutEvaluation).count()==1


def test_matching_uses_rome_calendar_day_not_utc_midnight(api):
    client,engine=api
    activity(engine,start_time=datetime(2026,10,16,22,30)) # Rome17Oct, exactly D-2.
    row=review(client)['activity_candidates']['items'][0]
    assert row['activity_date']=='2026-10-17' and row['day_delta']==-2
    assert save(client,activity_match_token=row['match_token']).status_code==200


def test_candidates_do_not_depend_on_first_fifty_diary_records(api):
    client,engine=api
    with Session(engine) as db:
        db.add_all([Activity(id=i,source='fit',source_id=f'old-{i}',sport='cycling',start_time=datetime(2020,1,1),duration_seconds=60) for i in range(1,61)])
        db.commit()
    activity(engine,99)
    assert review(client)['activity_candidates']['items'][0]['id']==99


def test_immediate_write_lock_precedes_feedback_reads(api):
    client,engine=api; activity(engine); statements=[]
    def record(conn,cursor,sql,parameters,context,many):statements.append(sql)
    event.listen(engine,'before_cursor_execute',record)
    try: assert save(client).status_code==200
    finally:event.remove(engine,'before_cursor_execute',record)
    assert statements[0]=='BEGIN IMMEDIATE'


def test_existing_invalid_link_is_explained_and_can_be_explicitly_replaced(api):
    client,engine=api; activity(engine,start_time=datetime(2026,10,21,8))
    with Session(engine) as db:
        db.get(Workout,1).status='completed'
        db.add(SessionFeedback(workout_id=1,activity_id=1,payload_json='{"rpe":4,"completion_percent":100,"actual":{"duration_min":60}}'))
        db.commit()
    before=ledger(engine); body=review(client)
    assert 'future' in body['feedback_issue'] and body['feedback_available'] is True
    assert body['feedback']['activity_id']==1 and body['workout']['status']=='completed'
    assert body['activity_candidates']['items']==[] and ledger(engine)==before
    result=save(client,identifier=None)
    assert result.status_code==200 and result.json()['source']=='self_reported'
