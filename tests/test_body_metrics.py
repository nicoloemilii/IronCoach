from datetime import date
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_iron, routes_planning, routes_health_reports
from app.db.database import Base, get_db
from app.db.iron_models import HealthMetric
from app.services import planning
from app.services.body_metrics import body_mass_index

TODAY = date(2026,10,18)


def profile(**values):
    return {'height_cm':175, 'birth_date':'1990-10-18', 'sex':'male', **values}


def weight(value=70, **changes):
    return {'latest':{'value':value, 'date':str(TODAY), 'source':'intervals'}, 'stale':False, **changes}


def test_formula_uses_observed_weight_and_declared_height_without_sex_coefficients():
    for sex in ['male','female','other',None]:
        body=body_mass_index(profile(sex=sex),weight(),TODAY)
        assert body['value']==22.86 and body['age_years']==36
        assert body['classification']['code']=='reference'
        assert body['sex']==sex and body['height_source']=='profile'


@pytest.mark.parametrize('bmi,code',[(18.49,'underweight'),(18.5,'reference'),(24.99,'reference'),(25,'overweight'),(29.99,'overweight'),(30,'obesity_1'),(35,'obesity_2'),(40,'obesity_3')])
def test_adult_screening_thresholds_are_exact(bmi,code):
    result=body_mass_index(profile(height_cm=200),weight(bmi*4),TODAY)
    assert result['classification']['code']==code


@pytest.mark.parametrize('birth,age,adult',[('2006-10-18',20,True),('2006-10-19',19,False),('2010-03-01',16,False),('1990-10-19',35,True)])
def test_age_at_measurement_controls_interpretation_and_birthday(birth,age,adult):
    body=body_mass_index(profile(birth_date=birth),weight(),TODAY)
    assert body['age_years']==age
    assert (body['classification'] is not None)==adult
    if not adult:
        assert 'età e sesso' in body['reason']


def test_historical_measurement_never_uses_age_from_a_later_birthday():
    body=body_mass_index(profile(birth_date='2006-10-18'),weight(latest={'value':70,'date':'2026-10-17','source':'intervals'}),TODAY)
    assert body['age_years']==19 and body['classification'] is None
    assert body['age_basis_date']=='2026-10-17'


def test_missing_height_weight_or_birthdate_remain_explicit():
    body=body_mass_index(profile(height_cm=None),weight(),TODAY)
    assert body['value'] is None and 'height_cm' in body['missing_fields']
    assert body_mass_index(profile(),{'latest':None},TODAY)['value'] is None
    body=body_mass_index(profile(birth_date=None),weight(),TODAY)
    assert body['value']==22.86 and body['classification'] is None
    assert 'birth_date' in body['missing_fields']


def test_stale_weight_and_infants_do_not_receive_an_adult_classification():
    body=body_mass_index(profile(),weight(stale=True),TODAY)
    assert body['value']==22.86 and body['classification'] is None
    body=body_mass_index(profile(height_cm=75,birth_date='2025-10-18'),weight(10),TODAY)
    assert body['value'] is None and body['classification'] is None


@pytest.mark.parametrize('height,kg',[(0,70),(True,70),(float('inf'),70),(175,0),(175,False),(175,float('nan'))])
def test_unusable_measurements_never_generate_a_numeric_index(height,kg):
    assert body_mass_index(profile(height_cm=height),weight(kg),TODAY)['value'] is None


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(planning,'_today',lambda:TODAY)
    monkeypatch.setattr(planning,'load_profile',lambda:{'height_cm':175,'weight_kg':55,'age':36})
    monkeypatch.setattr(routes_iron,'athlete_today',lambda:TODAY)
    monkeypatch.setattr(routes_health_reports,'athlete_today',lambda:TODAY)
    engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False});Base.metadata.create_all(engine)
    app=FastAPI()
    for router in [routes_iron.router,routes_planning.router,routes_health_reports.router]:app.include_router(router)
    def database():
        with Session(engine) as db:yield db
    app.dependency_overrides[get_db]=database
    with Session(engine) as db:
        db.add(HealthMetric(date=str(TODAY),kind='weight_kg',value=70,unit='kg',source='intervals',observed_at=str(TODAY),synced_at=str(TODAY)));db.commit()
    with TestClient(app) as client:yield client
    engine.dispose()


def test_profile_roundtrip_updates_home_and_derived_index_without_promoting_legacy_defaults(api):
    current=api.get('/planning/profile').json()
    assert current['profile']['height_cm'] is None
    assert api.get('/dashboard/home').json()['body_mass']['value'] is None
    current['profile'].update(profile())
    response=api.put('/planning/profile',json=current['profile'],headers={'If-Match':str(current['revision'])})
    assert response.status_code==200,response.text
    home=api.get('/dashboard/home').json()['body_mass']
    derived=api.get('/athlete/derived-profile').json()
    assert home['value']==22.86 and home['weight_source']=='intervals'
    assert derived['body_mass']['value']==home['value']
    assert derived['personal_details']['height_cm']==175
    assert not set(['age','height_cm','sex'])&set(derived['unknown_fields'])


@pytest.mark.parametrize('changes',[{'height_cm':True},{'height_cm':0},{'height_cm':251},{'birth_date':'2026-10-19'},{'birth_date':'1800-01-01'},{'sex':'invented'}])
def test_invalid_personal_fields_are_rejected_without_writes(api,changes):
    current=api.get('/planning/profile').json()
    assert api.put('/planning/profile',json={**current['profile'],**changes}).status_code==422
    assert api.get('/planning/profile').json()['revision']==0
