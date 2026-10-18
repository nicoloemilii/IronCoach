"""Symptom journals remain dated, actionable and explicitly non-causal."""
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_health_reports
from app.db.database import Base, get_db
from app.db.health_reports import HealthReport, ensure_health_reports_schema
from app.db.iron_models import ActivityObservation, HealthMetric
from app.db.models import Activity
from app.services import athlete_snapshot, health_reports, planning
from app.services.health_reports import active_restrictions

TODAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 18, 20)


@pytest.fixture
def journal_api(monkeypatch):
    for module in (routes_health_reports, athlete_snapshot, health_reports):
        monkeypatch.setattr(module, 'athlete_today', lambda: TODAY)
    for module in (athlete_snapshot, health_reports):
        monkeypatch.setattr(module, 'utc_now_naive', lambda: NOW)
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(routes_health_reports.router)
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def report(client, **changes):
    return client.post('/health/reports', json={'date': str(TODAY), 'kind': 'contracture', 'body_location': 'polpaccio sinistro', 'severity': 2, **changes})


def activity(db, identifier, day, sport='cycling', minutes=60, load=30):
    row = Activity(source='intervals', source_id=identifier, sport=sport, name=identifier,
                   start_time=datetime.combine(day, datetime.min.time()) + timedelta(hours=10),
                   duration_seconds=minutes * 60, distance_meters=10000, load=load)
    db.add(row)
    db.flush()
    return row


def metric(db, day, kind, value, source='intervals'):
    db.add(HealthMetric(date=str(day), kind=kind, value=value, unit={'sleep_hours':'ore', 'hrv_ms':'ms', 'resting_hr_bpm':'bpm'}[kind],
                        source=source, observed_at=str(day), synced_at=NOW.isoformat()))


def test_report_lifecycle_retains_hypothesis_and_restricts_until_resolved(journal_api):
    client, engine = journal_api
    response = report(client, suspected_cause='Salita intensa dopo i pesi')
    assert response.status_code == 201
    body = response.json()
    assert body['analysis']['athlete_hypothesis'] == 'Salita intensa dopo i pesi'
    assert body['analysis']['restriction']['mode'] == 'easy'
    assert body['analysis']['restriction']['max_duration_min'] == 20
    assert client.get('/health/reports').json()['restrictions']['max_rpe'] == 2
    response = client.patch(f"/health/reports/{body['id']}", json={'status': 'resolved'})
    assert response.status_code == 200 and response.json()['resolved_on'] == str(TODAY)
    assert client.get('/health/reports').json()['restrictions']['mode'] == 'normal'
    assert client.delete(f"/health/reports/{body['id']}").status_code == 200
    assert client.get('/health/reports').json()['reports'] == []


@pytest.mark.parametrize('changes', [{'date':'2026-10-19'}, {'kind':'diagnosis'}, {'kind':'injury','body_location':' '}, {'severity':0}, {'severity':True}, {'status':'active','resolved_on':str(TODAY)}, {'extra':'unexpected'}])
def test_invalid_reports_are_not_persisted(journal_api, changes):
    client, _ = journal_api
    assert report(client, **changes).status_code == 422
    assert client.get('/health/reports').json()['reports'] == []


def test_invalid_patch_preserves_original_and_404s_are_explicit(journal_api):
    client, _ = journal_api
    body = report(client).json()
    assert client.patch(f"/health/reports/{body['id']}", json={'kind':'injury','body_location':''}).status_code == 422
    assert client.get('/health/reports').json()['reports'][0]['body_location'] == 'polpaccio sinistro'
    assert client.delete('/health/reports/12345').status_code == 404
    assert client.get('/health/reports/12345/analysis').status_code == 404


@pytest.mark.parametrize('kind,severity', [('injury',1), ('contracture',3), ('illness',1), ('cold',1)])
def test_active_illness_or_injury_suspends_automatic_training(journal_api, kind, severity):
    client, _ = journal_api
    response = report(client, kind=kind, severity=severity)
    assert response.status_code == 201
    restriction = client.get('/health/reports').json()['restrictions']
    assert restriction['training_paused'] is True
    assert restriction['mode'] == 'rest' and restriction['max_duration_min'] == 0
    assert restriction['reasons']


def test_historical_restrictions_stop_at_resolution_and_ignore_future_onset(journal_api):
    client, engine = journal_api
    assert report(client, date='2026-10-10', kind='injury', status='resolved', resolved_on='2026-10-15').status_code == 201
    with Session(engine) as db:
        assert active_restrictions(db, date(2026,10,9))['mode'] == 'normal'
        assert active_restrictions(db, date(2026,10,14))['mode'] == 'rest'
        assert active_restrictions(db, date(2026,10,15))['mode'] == 'normal'
    assert client.get('/health/reports?end=2026-10-09').json()['reports'] == []
    assert client.get('/health/reports?end=2026-10-19').status_code == 422


def test_resolved_report_can_be_reopened_without_inventing_a_new_onset(journal_api):
    client, _ = journal_api
    body = report(client, date='2026-10-10', status='resolved', resolved_on='2026-10-15').json()
    response = client.patch(f"/health/reports/{body['id']}", json={'status':'active'})
    assert response.status_code == 200
    assert response.json()['date'] == '2026-10-10' and response.json()['resolved_on'] is None
    assert client.get('/health/reports').json()['restrictions']['mode'] == 'easy'
    assert client.patch(f"/health/reports/{body['id']}", json={'status':'active','resolved_on':str(TODAY)}).status_code == 422


def test_prior_days_comparisons_do_not_mix_sources_or_use_later_measurements(journal_api):
    client, engine = journal_api
    with Session(engine) as db:
        for ago in range(4, 15):
            metric(db, TODAY - timedelta(days=ago), 'sleep_hours', 8)
            metric(db, TODAY - timedelta(days=ago), 'hrv_ms', 60)
            metric(db, TODAY - timedelta(days=ago), 'resting_hr_bpm', 50)
        for ago in (1,2,3):
            metric(db, TODAY - timedelta(days=ago), 'sleep_hours', 5)
            metric(db, TODAY - timedelta(days=ago), 'hrv_ms', 45)
            metric(db, TODAY - timedelta(days=ago), 'resting_hr_bpm', 56)
        metric(db, TODAY, 'hrv_ms', 100)  # onset day never enters preceding comparison
        metric(db, TODAY - timedelta(days=10), 'hrv_ms', 500, source='manual')
        db.commit()
    body = report(client, suspected_cause='Troppo lavoro').json()
    analysis = body['analysis']
    assert {item['kind'] for item in analysis['associations']} == {'sleep_hours','hrv_ms','resting_hr_bpm'}
    hrv = next(item for item in analysis['comparisons'] if item['kind'] == 'hrv_ms')
    assert hrv['baseline_value'] == 60 and hrv['recent_value'] == 45
    assert analysis['athlete_hypothesis'] == 'Troppo lavoro'
    assert 'non dimostrano la causa' in analysis['summary']


def test_short_or_incompatible_history_does_not_invent_correlations(journal_api):
    client, engine = journal_api
    with Session(engine) as db:
        for ago in range(4,15):
            metric(db, TODAY - timedelta(days=ago), 'hrv_ms', 70, source='manual')
        for ago in (1,2,3):
            metric(db, TODAY - timedelta(days=ago), 'hrv_ms', 40)
        db.commit()
    analysis = report(client).json()['analysis']
    assert not analysis['comparisons'] and not analysis['associations']
    assert 'non basta' in analysis['summary']


def test_load_comparison_respects_remote_explicit_null_instead_of_reusing_stale_load(journal_api):
    client, engine = journal_api
    with Session(engine) as db:
        for ago in (5,10,15,20):
            activity(db, f'old{ago}', TODAY-timedelta(days=ago), load=20)
        row = activity(db, 'recent', TODAY-timedelta(days=1), load=100)
        db.add(ActivityObservation(activity_id=row.id, source_key='intervals:recent', payload_json=json.dumps({'load':None})))
        db.commit()
    analysis = report(client).json()['analysis']
    assert not any(item['kind'] == 'load' for item in analysis['comparisons'])


def test_derived_profile_refreshes_from_new_data_and_preserves_manual_preferences(journal_api):
    client, engine = journal_api
    with Session(engine) as db:
        value = planning.load_settings(db)
        value.update(name='Atleta', weekly_minutes=500, availability=[10,20,30,40,50,60,70])
        planning.save_settings(db, value)
        activity(db, 'one', TODAY-timedelta(days=1), minutes=80)
        db.commit()
    first = client.get('/athlete/derived-profile').json()
    assert first['window']['days'] == 28 and first['activities'] == 1
    assert first['weekly_minutes'] == 20 and first['name'] == 'Atleta'
    assert first['thresholds'] == {} and 'weight_kg' in first['unknown_fields']
    with Session(engine) as db:
        activity(db, 'two', TODAY, sport='running', minutes=40)
        db.commit()
    second = client.get('/athlete/derived-profile').json()
    assert second['activities'] == 2 and second['weekly_minutes'] == 30
    assert 'multisport' in second['headline']
    assert second['manual_preferences']['weekly_minutes'] == 500
    with Session(engine) as db:
        assert planning.load_settings(db)['availability'] == [10,20,30,40,50,60,70]


def test_derived_historical_profile_excludes_later_activities_and_reports(journal_api):
    client, engine = journal_api
    with Session(engine) as db:
        activity(db, 'past', TODAY-timedelta(days=1), minutes=60)
        activity(db, 'today', TODAY, minutes=60)
        db.commit()
    assert report(client, kind='illness').status_code == 201
    earlier = client.get('/athlete/derived-profile?day=2026-10-17').json()
    assert earlier['activities'] == 1 and earlier['restrictions']['mode'] == 'normal'
    assert client.get('/athlete/derived-profile').json()['restrictions']['mode'] == 'rest'


def test_additive_schema_upgrade_is_idempotent():
    engine = create_engine('sqlite://')
    ensure_health_reports_schema(engine)
    ensure_health_reports_schema(engine)
    with Session(engine) as db:
        assert db.query(HealthReport).all() == []
    engine.dispose()


def test_symptom_journal_is_included_in_backups_and_semantically_validated(journal_api):
    from app.services import backups
    client, engine = journal_api
    assert report(client, suspected_cause='Dopo una salita').status_code == 201
    with Session(engine) as db:
        exported = backups.export_backup(db)
        assert exported['data']['health_reports'][0]['suspected_cause'] == 'Dopo una salita'
        parsed = backups.validate_backup(db, json.dumps(exported).encode())
        assert parsed['data']['health_reports'][0]['severity'] == 2
        exported['data']['health_reports'][0]['severity'] = 9
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            backups.validate_backup(db, json.dumps(exported).encode())
        assert error.value.status_code == 422
