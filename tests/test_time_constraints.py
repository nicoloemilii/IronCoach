import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes_planning import router
from app.db.database import Base, get_db
from app.db.models import Workout
from app.db.planning_models import AthleteSettings
from app.services import planning, time_constraints as times

TODAY = date(2026, 10, 18)
REQUEST = {'message': 'Non voglio allenarmi la mattina nei giorni feriali', 'start': '2026-10-19', 'end': '2026-11-01'}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(times, 'athlete_today', lambda: TODAY)
    monkeypatch.setattr(planning, '_today', lambda: TODAY)
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI(); app.include_router(router)
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    with Session(engine) as db:
        planning.save_settings(db, planning.PlanningProfile())
        for day in ['2026-10-20', '2026-10-24']:
            db.add(Workout(date=day, sport='cycling', name='Fondo', duration_min=30,
                           status='planned', payload_json=json.dumps({'source': 'manual', 'steps': [{'type': 'manual', 'duration_min':30}]})))
        db.commit()
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_preview_is_readonly_and_limits_changes_to_selected_days(api):
    client, engine = api
    result = client.post('/planning/coach/preview', json=REQUEST)
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['rule']['blocked'] == ['morning']
    assert value['rule']['weekdays'] == list(range(5))
    assert len(value['changes']) == 1
    assert value['changes'][0]['window']['allowed'] == ['afternoon', 'evening']
    with Session(engine) as db:
        assert planning.get_settings(db)['revision'] == 1
        assert planning.load_settings(db)['time_constraints'] == []
        assert all(row.status == 'planned' for row in db.query(Workout))


def test_apply_updates_existing_calendar_windows_and_preserves_session_work(api):
    client, engine = api
    signature = client.post('/planning/coach/preview', json=REQUEST).json()['preview_signature']
    response = client.post('/planning/coach/apply', json={**REQUEST, 'preview_signature':signature})
    assert response.status_code == 200, response.text
    week = client.get('/planning/week?start=2026-10-19').json()
    tuesday = week['days'][1]['sessions'][0]
    assert tuesday['payload']['time_window']['allowed'] == ['afternoon','evening']
    assert tuesday['duration_min'] == 30 and tuesday['status'] == 'planned'
    assert 'time_window' not in week['days'][5]['sessions'][0]['payload']
    with Session(engine) as db:
        assert db.query(Workout).count() == 2
        assert 'time_window' not in json.loads(db.query(Workout).first().payload_json)


def test_old_preview_cannot_apply_after_profile_or_plan_changed(api):
    client, engine = api
    signature = client.post('/planning/coach/preview', json=REQUEST).json()['preview_signature']
    with Session(engine) as db:
        row = db.query(Workout).filter_by(date='2026-10-20').one();row.duration_min=40;db.commit()
    assert client.post('/planning/coach/apply',json={**REQUEST,'preview_signature':signature}).status_code == 409
    with Session(engine) as db:
        assert not planning.load_settings(db)['time_constraints']


def test_pause_blocks_new_generation_but_exposes_existing_conflicts(api):
    client, engine = api
    request = {**REQUEST,'message':'Pausa dagli allenamenti'}
    preview = client.post('/planning/coach/preview',json=request).json()
    assert preview['conflicts'] == 2
    assert client.post('/planning/coach/apply',json={**request,'preview_signature':preview['preview_signature']}).status_code == 200
    week=client.post('/planning/week',json={'start':'2026-10-19','regenerate':True}).json()
    assert all(day['available_minutes'] == 0 for day in week['days'])
    assert sum(len(day['sessions']) for day in week['days']) == 2
    assert all(session['payload']['time_window']['conflict'] for day in week['days'] for session in day['sessions'])
    with Session(engine) as db:
        assert all(row.status=='planned' for row in db.query(Workout))


@pytest.mark.parametrize('message,blocked,days',[
    ('Niente mattina',['morning'],list(range(7))),
    ('No sera',['evening'],list(range(7))),
    ('Solo pomeriggio',['morning','evening'],list(range(7))),
    ('Non allenarmi lunedì e mercoledì sera',['evening'],[0,2]),
    ('Niente mattina nel weekend',['morning'],[5,6]),
    ('Riposo',['morning','afternoon','evening'],list(range(7))),
])
def test_bounded_local_interpreter_handles_availability(message,blocked,days,monkeypatch):
    monkeypatch.setattr(times,'athlete_today',lambda:TODAY)
    rule=times.interpret(times.CoachRequest(**{**REQUEST,'message':message}))
    assert rule.blocked==blocked and rule.weekdays==days


@pytest.mark.parametrize('changes',[
    {'message':'Allenami più forte'}, {'message':'Sposta la seduta di mattina'},
    {'message':'Non allenarmi solo la mattina'}, {'message':'Niente mattina tranne lunedì'},
    {'message':'Ciao Coach'}, {'start':'2026-10-17'}, {'end':'2026-10-18'},
    {'message':'Niente mattina dal 2026-10-20 al 2026-11-01', 'interpret_dates':False},
])
def test_ambiguous_unsupported_or_invalid_requests_do_not_write(api,changes):
    client,engine=api
    assert client.post('/planning/coach/preview',json={**REQUEST,**changes}).status_code==422
    with Session(engine) as db:
        assert db.get(AthleteSettings,1).revision==1


def test_overlapping_rules_expiry_and_capacity_are_date_sensitive():
    profile={'availability':[600]*7,'time_constraints':[
        {'start':'2026-10-19','end':'2026-10-20','weekdays':list(range(7)),'blocked':['morning']},
        {'start':'2026-10-20','end':'2026-10-20','weekdays':list(range(7)),'blocked':['afternoon']}]}
    assert times.available_minutes(profile,'2026-10-20')==240
    assert times.windows(profile,'2026-10-20')['allowed']==['evening']
    assert times.available_minutes(profile,'2026-10-21')==600
    assert not times.windows(profile,'2026-10-21')['restricted']


@pytest.mark.parametrize('message,start,end',[
    ('Non voglio fare nuoto fino a gennaio','2026-10-18','2026-12-31'),
    ('Niente nuoto fino a gennaio 2027','2026-10-18','2026-12-31'),
    ('Non nuotare fino a fine gennaio','2026-10-18','2027-01-31'),
    ('Niente nuoto fino alla fine di gennaio','2026-10-18','2027-01-31'),
    ('Niente nuoto fino al 15 gennaio','2026-10-18','2027-01-15'),
    ('Niente nuoto per due settimane','2026-10-18','2026-10-31'),
    ('Niente nuoto da domani per 10 giorni','2026-10-19','2026-10-28'),
    ('Niente nuoto per tre mesi','2026-10-18','2027-01-17'),
    ('Niente nuoto dal 1 novembre al 31 dicembre','2026-11-01','2026-12-31'),
    ('Niente nuoto da novembre a gennaio','2026-11-01','2027-01-31'),
    ('Niente nuoto dal 2026-11-01 al 2027-01-15','2026-11-01','2027-01-15'),
    ('Niente nuoto fino a fine mese','2026-10-18','2026-10-31'),
    ('Niente nuoto fino a fine anno','2026-10-18','2026-12-31'),
    ('Niente nuoto la prossima settimana','2026-10-19','2026-10-25'),
    ('Niente nuoto a gennaio','2027-01-01','2027-01-31'),
    ('Niente nuoto per tutto gennaio','2027-01-01','2027-01-31'),
    ('Niente nuoto fino al 29 febbraio 2028','2026-10-18','2028-02-29'),
    ('Niente nuoto fino al 29 febbraio','2026-10-18','2028-02-29'),
])
def test_natural_dates_and_year_rollover_are_explicit(monkeypatch,message,start,end):
    monkeypatch.setattr(times,'athlete_today',lambda:TODAY)
    rule=times.interpret(times.CoachRequest(**{**REQUEST,'message':message}))
    assert str(rule.start)==start and str(rule.end)==end
    assert rule.sports==['swimming'] and rule.blocked==[]


@pytest.mark.parametrize('message,sports,blocked',[
    ('Non voglio fare bici',['cycling'],[]),('Niente corsa',['running'],[]),
    ('Pausa dalla palestra',['strength'],[]),('Niente nuoto e corsa',['swimming','running'],[]),
    ('Niente nuoto la mattina',['swimming'],['morning']),
    ('Solo bici',['swimming','running','strength'],[]),
])
def test_discipline_scope_never_becomes_an_unrelated_global_pause(monkeypatch,message,sports,blocked):
    monkeypatch.setattr(times,'athlete_today',lambda:TODAY)
    rule=times.interpret(times.CoachRequest(**{**REQUEST,'message':message}))
    assert rule.sports==sports and rule.blocked==blocked


def add_swim(db,day='2026-10-20',**values):
    row=Workout(date=day,sport='swimming',name='Nuoto previsto',duration_min=30,status='planned',payload_json=json.dumps({'source':'manual','steps':[{'type':'manual','duration_min':30}]}))
    for key,value in values.items():setattr(row,key,value)
    db.add(row);db.flush();return row


def test_swim_pause_previews_removes_only_target_future_work_and_expires(api):
    client,engine=api
    with Session(engine) as db:
        swim=add_swim(db);identifier=swim.id
        completed=add_swim(db,status='completed').id
        outside=add_swim(db,day='2027-01-02').id;db.commit()
    request={**REQUEST,'message':'non voglio fare nuoto fino a gennaio'}
    preview=client.post('/planning/coach/preview',json=request).json()
    assert preview['removed_count']==1 and preview['rule']['end']=='2026-12-31'
    assert preview['rule']['start']=='2026-10-18' and '2027-01-01' in preview['assumptions'][0]
    with Session(engine) as db:assert db.get(Workout,identifier).status=='planned'
    response=client.post('/planning/coach/apply',json={**request,'preview_signature':preview['preview_signature']})
    assert response.status_code==200,response.text
    with Session(engine) as db:
        assert db.get(Workout,identifier).status=='superseded'
        assert db.get(Workout,completed).status=='completed' and db.get(Workout,outside).status=='planned'
        assert all(row.status=='planned' for row in db.query(Workout).filter_by(sport='cycling'))
        settings=planning.load_settings(db)
        assert not times.windows(settings,'2026-12-31','swim')['allowed']
        assert times.windows(settings,'2027-01-01','swim')['allowed']
        assert times.available_minutes(settings,'2026-10-20','cycling')==60


def test_scoped_morning_pause_keeps_swim_and_other_sports_available_later(api):
    client,engine=api
    with Session(engine) as db:add_swim(db);db.commit()
    request={**REQUEST,'message':'Niente nuoto la mattina'}
    preview=client.post('/planning/coach/preview',json=request).json()
    assert preview['removed_count']==0 and len(preview['changes'])==1
    assert preview['changes'][0]['window']['allowed']==['afternoon','evening']
    assert client.post('/planning/coach/apply',json={**request,'preview_signature':preview['preview_signature']}).status_code==200
    week=client.get('/planning/week?start=2026-10-19').json()
    assert week['days'][1]['available_minutes']==60
    swims=[row for row in week['days'][1]['sessions'] if row['sport']=='swimming']
    assert swims[0]['payload']['time_window']['allowed']==['afternoon','evening']


def test_generation_omits_paused_sport_without_increasing_other_sessions(api):
    client,engine=api
    baseline=client.post('/planning/week',json={'start':'2026-10-19','regenerate':True}).json()
    original={(row['date'],row['sport']):row['duration_min'] for day in baseline['days'] for row in day['sessions'] if row['payload'].get('planning',{}).get('managed')}
    request={**REQUEST,'message':'Niente nuoto fino a gennaio'}
    preview=client.post('/planning/coach/preview',json=request).json()
    assert client.post('/planning/coach/apply',json={**request,'preview_signature':preview['preview_signature']}).status_code==200
    updated=client.post('/planning/week',json={'start':'2026-10-19','regenerate':True}).json()
    for day in updated['days']:
        for row in day['sessions']:
            assert row['sport']!='swimming'
            if row['payload'].get('planning',{}).get('managed'):assert row['duration_min']<=original[(row['date'],row['sport'])]
    resumed=client.post('/planning/week',json={'start':'2027-01-04','regenerate':True}).json()
    assert any(row['sport']=='swimming' for day in resumed['days'] for row in day['sessions'])


def test_omitted_manual_swim_budget_is_not_transferred_to_other_disciplines(api):
    client,engine=api
    with Session(engine) as db:add_swim(db);db.commit()
    original=client.post('/planning/week',json={'start':'2026-10-19','regenerate':True}).json()
    before={(row['date'],row['sport']):row['duration_min'] for day in original['days'] for row in day['sessions'] if row['payload'].get('planning',{}).get('managed')}
    request={**REQUEST,'message':'Niente nuoto fino a gennaio'}
    candidate=client.post('/planning/coach/preview',json=request).json()
    assert client.post('/planning/coach/apply',json={**request,'preview_signature':candidate['preview_signature']}).status_code==200
    updated=client.post('/planning/week',json={'start':'2026-10-19','regenerate':True}).json()
    for day in updated['days']:
        for row in day['sessions']:
            assert row['sport']!='swimming'
            if row['payload'].get('planning',{}).get('managed'):assert row['duration_min']<=before[(row['date'],row['sport'])]


@pytest.mark.parametrize('related',[True,False])
def test_published_work_is_blocked_only_if_the_request_would_change_it(api,related):
    from app.db.garmin_models import GarminDelivery
    client,engine=api
    with Session(engine) as db:
        swim=add_swim(db);identifier=swim.id if related else db.query(Workout).filter_by(sport='cycling').first().id
        db.add(GarminDelivery(workout_id=identifier,content_hash='a'*64,reason_code='published',remote_published=True));db.commit()
    result=client.post('/planning/coach/preview',json={**REQUEST,'message':'Niente nuoto fino a gennaio'})
    assert result.status_code==(409 if related else 200)
    with Session(engine) as db:assert planning.get_settings(db)['revision']==1


def test_started_gym_work_is_conserved_and_exposed_as_a_conflict(api):
    from app.db.training_models import GymProgress
    client,engine=api
    with Session(engine) as db:
        row=add_swim(db,sport='strength');identifier=row.id
        db.add(GymProgress(workout_id=identifier,payload_json=json.dumps({'completed_sets':[{'exercise_index':0,'set_index':0}]})));db.commit()
    request={**REQUEST,'message':'Niente palestra fino a gennaio'}
    preview=client.post('/planning/coach/preview',json=request).json()
    assert preview['removed_count']==0 and preview['conflicts']==1
    assert preview['changes'][0]['action']=='keep'
    assert client.post('/planning/coach/apply',json={**request,'preview_signature':preview['preview_signature']}).status_code==200
    with Session(engine) as db:assert db.get(Workout,identifier).status=='planned'


def test_failed_calendar_write_rolls_back_both_preference_and_session(api,monkeypatch):
    client,engine=api
    with Session(engine) as db:add_swim(db);db.commit()
    request={**REQUEST,'message':'Niente nuoto fino a gennaio'}
    candidate=client.post('/planning/coach/preview',json=request).json()
    def fail(*args,**kwargs):raise RuntimeError('Injected audit failure')
    monkeypatch.setattr(planning,'_audit',fail)
    with Session(engine) as db:
        with pytest.raises(RuntimeError):times.apply(db,times.CoachApply(**request,preview_signature=candidate['preview_signature']))
    with Session(engine) as db:
        assert planning.get_settings(db)['revision']==1
        assert planning.load_settings(db)['time_constraints']==[]
        assert db.query(Workout).filter_by(sport='swimming').one().status=='planned'


@pytest.mark.parametrize('message',[
    'Niente nuoto fino al 30 febbraio 2027','Niente nuoto fino a gennaio 2026',
    'Niente nuoto per 0 settimane','Niente nuoto per 50 mesi','Niente nuoto fino alle vacanze',
    'Non voglio smettere di nuotare','Solo bici al pomeriggio','Non allenarmi a tennis',
])
def test_ambiguous_or_impossible_natural_requests_never_write(api,message):
    client,engine=api
    assert client.post('/planning/coach/preview',json={**REQUEST,'message':message}).status_code==422
    with Session(engine) as db:assert planning.get_settings(db)['revision']==1
