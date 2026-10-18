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
    {'message':'Niente mattina dal 2026-10-20 al 2026-11-01'},
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
