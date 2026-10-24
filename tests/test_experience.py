"""Athlete-facing API regressions, isolated from the real DB and profile file."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_coach, routes_experience
from app.db import experience_models, garmin_models, planning_models, health_reports, sync_models  # register metadata
from app.db.database import Base, get_db
from app.db.experience_models import ActivityIdentity, AthleteCheckin, SessionFeedback
from app.db.iron_models import DuplicateCandidate
from app.db.models import Activity, AthleteAdaptation, DailyMetrics, Workout, WorkoutEvaluation
from app.services import planning
from app.services.adaptation import adaptation_context
from app.services.athlete_profile import DEFAULT_PROFILE


@pytest.fixture
def experience_api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    profile = deepcopy(DEFAULT_PROFILE)
    profile.update(name="Atleta test")
    profile["goals"] = {"event": "Gara test", "event_date": None, "target_time": None}
    monkeypatch.setattr(routes_coach, "load_profile", lambda: deepcopy(profile))
    monkeypatch.setattr(planning, "load_profile", lambda: deepcopy(profile))
    app = FastAPI()
    app.include_router(routes_experience.router)
    app.include_router(routes_coach.router)

    def isolated_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = isolated_db
    today = datetime.now(timezone.utc).date()
    with factory() as session:
        for workout_id, sport, day, status in [
            (1, "cycling", today, "planned"),
            (2, "cycling", today, "planned"),
            (3, "running", today, "planned"),
            (4, "cycling", today + timedelta(days=2), "planned"),
            (5, "cycling", today, "cancelled"),
        ]:
            payload = {"sport": sport, "name": f"Seduta {workout_id}", "duration_min": 60,
                       "expected_rpe": 4, "steps": [{"type": "easy", "duration_min": 60, "target_rpe": 3}]}
            session.add(Workout(id=workout_id, sport=sport, date=str(day), status=status,
                                name=payload["name"], duration_min=60, payload_json=json.dumps(payload)))
        session.commit()
    with TestClient(app) as client:
        yield client, factory, today
    engine.dispose()


def _feedback(**overrides):
    return {"rpe": 3, "completion_percent": 100, "feeling": "as_expected", **overrides}


def _activity(factory, today, **overrides):
    data = {"source": "garmin", "source_id": "actual-1", "sport": "cycling", "name": "Registrata",
            "start_time": min(datetime.combine(today, datetime.min.time()) + timedelta(hours=8),
                              datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)),
            "duration_seconds": 1800, "distance_meters": 15000, "avg_power": 140}
    data.update(overrides)
    with factory() as session:
        row = Activity(**data)
        session.add(row)
        session.commit()
        return row.id


def _import_item(today, **overrides):
    return {"source_id": "id-1", "sport": "cycling", "name": "Importata",
            "start_time": f"{today}T08:00:00+02:00", "duration_seconds": 3600,
            "distance_meters": 30000, **overrides}


def test_checkin_upserts_one_day_and_subjective_fatigue_changes_decision(experience_api):
    client, factory, today = experience_api
    assert client.get("/experience/checkin").json()["checkin"] is None
    response = client.put("/experience/checkin", json={"date": str(today), "sleep_hours": 8,
                                                      "stress_score": 15, "fatigue": 5, "motivation": 2})
    assert response.status_code == 200
    state = client.get("/coach/today").json()
    assert state["decision"]["recommendation"] == "recovery"
    assert "subjective_recovery_warning" in state["decision"]["cautions"]
    assert state["athlete_state"]["subjective"]["fatigue"] == 5
    assert state["readiness"]["raw"]["sleep_hours"] == 8
    assert client.put("/experience/checkin", json={"date": str(today), "fatigue": 2}).status_code == 200
    with factory() as session:
        assert session.query(AthleteCheckin).count() == 1
        assert session.query(DailyMetrics).count() == 1


@pytest.mark.parametrize("payload", [
    {"fatigue": 0}, {"fatigue": 6}, {"soreness": -1}, {"motivation": 9},
    {"sleep_hours": 25}, {"stress_score": 101}, {"sleep_quality": 0},
    {"sleep_hours": "NaN"}, {"unknown": "field"}, {"date": "invalid"},
])
def test_checkin_rejects_invalid_input(experience_api, payload):
    client, factory, _ = experience_api
    assert client.put("/experience/checkin", json=payload).status_code == 422
    with factory() as session:
        assert session.query(AthleteCheckin).count() == 0


def test_future_checkin_is_rejected(experience_api):
    client, _, today = experience_api
    response = client.put("/experience/checkin", json={"date": str(today + timedelta(days=1)), "fatigue": 3})
    assert response.status_code == 422


def test_explicit_null_checkin_clears_metric_used_for_readiness(experience_api):
    client, factory, _ = experience_api
    assert client.put("/experience/checkin", json={"sleep_hours": 4, "stress_score": 90}).status_code == 200
    assert client.put("/experience/checkin", json={"sleep_hours": None, "stress_score": None}).status_code == 200
    with factory() as session:
        row = session.query(DailyMetrics).one()
        assert row.sleep_hours is None
        assert row.stress_score is None


def test_coach_does_not_present_old_training_capacity_as_recent(experience_api):
    client, factory, today = experience_api
    _activity(factory, today, start_time=datetime.combine(today - timedelta(days=500), datetime.min.time()) + timedelta(hours=8))
    response = client.get('/coach/today')
    assert response.status_code == 200
    day = response.json()
    assert day['historical']['reference_date'] == day['date']
    assert day['historical']['all_time']['activities'] == 1
    assert day['historical']['recent_90d']['activities'] == 0
    assert day['historical']['recent_12m']['activities'] == 0
    assert day['historical_capacity']['sports']['cycling']['recent_90d']['activities'] == 0


def test_feedback_edits_do_not_inflate_adaptation_trend(experience_api):
    client, factory, _ = experience_api
    for rpe in (4, 3, 2):
        response = client.put("/experience/workouts/1/feedback", json=_feedback(rpe=rpe))
        assert response.status_code == 200
    with factory() as session:
        assert session.query(SessionFeedback).count() == 1
        assert session.query(WorkoutEvaluation).count() == 1
        rows = session.query(AthleteAdaptation).all()
        assert len(rows) == 1
        context = adaptation_context(rows)
        assert context["sample_size"] == 1
        assert context["trend"] == "single_signal"
        assert session.get(Workout, 1).status == "completed"


def test_self_reported_completion_is_distinct_from_recorded_activity(experience_api):
    client, factory, today = experience_api
    manual = client.put("/experience/workouts/1/feedback", json=_feedback(completion_percent=75)).json()
    assert manual["source"] == "self_reported"
    assert manual["actual"]["duration_min"] == 45
    assert manual["evaluation"]["completion"] == 75
    actual_id = _activity(factory, today)
    recorded = client.put("/experience/workouts/1/feedback", json=_feedback(activity_id=actual_id)).json()
    assert recorded["source"] == "activity"
    assert recorded["actual"]["duration_min"] == 30
    assert recorded["evaluation"]["completion"] == 50
    assert recorded["intensity_comparison"] == "unavailable_without_step_data"
    assert recorded["evaluation"]["intensity_control"] is None


def test_one_activity_cannot_complete_two_workouts(experience_api):
    client, factory, today = experience_api
    actual_id = _activity(factory, today)
    assert client.put("/experience/workouts/1/feedback", json=_feedback(activity_id=actual_id)).status_code == 200
    assert client.put("/experience/workouts/2/feedback", json=_feedback(activity_id=actual_id)).status_code == 409
    with factory() as session:
        assert session.query(SessionFeedback).count() == 1
        assert session.get(Workout, 2).status == "planned"


def test_activity_match_requires_matching_sport_and_near_date(experience_api):
    client, factory, today = experience_api
    actual_id = _activity(factory, today)
    assert client.put("/experience/workouts/3/feedback", json=_feedback(activity_id=actual_id)).status_code == 422
    far_id = _activity(factory, today, source_id="far", start_time=datetime.combine(today - timedelta(days=5), datetime.min.time()))
    assert client.put("/experience/workouts/1/feedback", json=_feedback(activity_id=far_id)).status_code == 422
    assert client.put("/experience/workouts/1/feedback", json=_feedback(activity_id=999)).status_code == 404


def test_future_and_removed_workouts_reject_feedback(experience_api):
    client, _, _ = experience_api
    assert client.put("/experience/workouts/4/feedback", json=_feedback()).status_code == 409
    assert client.put("/experience/workouts/5/feedback", json=_feedback()).status_code == 409
    assert client.put("/experience/workouts/999/feedback", json=_feedback()).status_code == 404


@pytest.mark.parametrize("override", [{"rpe": 0}, {"rpe": 11}, {"completion_percent": -1}, {"completion_percent": 101}, {"feeling": "magic"}])
def test_feedback_input_bounds(experience_api, override):
    client, _, _ = experience_api
    assert client.put("/experience/workouts/1/feedback", json=_feedback(**override)).status_code == 422


def test_review_suggests_confirmable_match_without_completing(experience_api):
    client, factory, today = experience_api
    actual_id = _activity(factory, today, duration_seconds=3600)
    review = client.get("/experience/workouts/1/review").json()
    assert review["suggested_match"]["activity_id"] == actual_id
    assert review["suggested_match"]["requires_confirmation"] is True
    assert review["auto_completed"] is False
    with factory() as session:
        assert session.get(Workout, 1).status == "planned"
        assert session.query(SessionFeedback).count() == 0


def test_import_repeated_id_is_idempotent_and_preserves_richer_data(experience_api):
    client, factory, today = experience_api
    item = _import_item(today, avg_hr=130)
    first = client.post("/experience/import-activities", json={"source": "garmin", "activities": [item]}).json()
    item.update(avg_hr=140, avg_power=160)
    second = client.post("/experience/import-activities", json={"source": "garmin", "activities": [item]}).json()
    assert first["created"] == 1
    assert second["created"] == 0 and second["matched"] == 1
    with factory() as session:
        assert session.query(Activity).count() == 1
        assert session.query(ActivityIdentity).count() == 1
        row = session.query(Activity).one()
        assert row.avg_hr == 130  # existing observation retained
        assert row.avg_power == 160  # new source can fill a missing field
        assert row.start_time.hour == 6  # +02:00 was converted to UTC


def test_cross_source_same_session_requires_merge_confirmation(experience_api):
    client, factory, today = experience_api
    a = _import_item(today, source_id="garmin-1")
    b = _import_item(today, source_id="strava-77", avg_hr=132)
    assert client.post("/experience/import-activities", json={"source": "garmin", "activities": [a]}).json()["created"] == 1
    result = client.post("/experience/import-activities", json={"source": "strava", "activities": [b]}).json()
    assert result["created"] == 1 and result["matched"] == 0
    with factory() as session:
        assert session.query(Activity).count() == 2
        assert session.query(ActivityIdentity).count() == 2
        assert session.query(DuplicateCandidate).filter_by(status='pending').count() == 1
        assert session.query(Activity).filter_by(source='strava').one().avg_hr == 132
        assert session.query(Activity).filter_by(source='garmin').one().avg_hr is None


def test_reused_raw_id_across_sources_keeps_distinct_sessions(experience_api):
    client, factory, today = experience_api
    a = _import_item(today)
    b = _import_item(today, start_time=f"{today}T16:00:00+02:00")
    assert client.post("/experience/import-activities", json={"source": "garmin", "activities": [a]}).json()["created"] == 1
    assert client.post("/experience/import-activities", json={"source": "strava", "activities": [b]}).json()["created"] == 1
    assert client.post("/experience/import-activities", json={"source": "strava", "activities": [b]}).json()["matched"] == 1
    with factory() as session:
        assert session.query(Activity).count() == 2
        assert session.query(ActivityIdentity).count() == 2


@pytest.mark.parametrize("changed", [
    {"duration_seconds": 1200}, {"distance_meters": 10000},
])
def test_near_timestamp_distinct_sessions_are_not_merged(experience_api, changed):
    client, factory, today = experience_api
    a = _import_item(today)
    b = _import_item(today, source_id="second", start_time=f"{today}T08:01:00+02:00", **changed)
    client.post("/experience/import-activities", json={"source": "garmin", "activities": [a]})
    result = client.post("/experience/import-activities", json={"source": "strava", "activities": [b]}).json()
    assert result["created"] == 1
    with factory() as session:
        assert session.query(Activity).count() == 2


def test_cross_source_legacy_sport_alias_still_suggests_duplicate(experience_api):
    client, factory, today = experience_api
    _activity(factory, today, source="fit", sport="bike", start_time=datetime.combine(today, datetime.min.time()) + timedelta(hours=6),
              duration_seconds=3600, distance_meters=30000)
    result = client.post("/experience/import-activities", json={"source": "garmin", "activities": [_import_item(today)]}).json()
    assert result["created"] == 1 and result["matched"] == 0
    with factory() as session:
        assert session.query(Activity).count() == 2
        assert session.query(DuplicateCandidate).filter_by(status='pending').count() == 1


def test_namespace_fallback_cannot_collide_with_existing_raw_id(experience_api):
    client, factory, today = experience_api
    for offset, source, source_id in [(0, "fit", "strava:id-1"), (1, "fit", "id-1")]:
        _activity(factory, today, source=source, source_id=source_id,
                  start_time=datetime.combine(today - timedelta(days=3 + offset), datetime.min.time()))
    response = client.post("/experience/import-activities", json={"source": "strava", "activities": [_import_item(today)]})
    assert response.status_code == 200
    assert response.json()["created"] == 1
    with factory() as session:
        assert session.query(Activity).count() == 3


def test_import_requires_timezone_before_writing_any_item(experience_api):
    client, factory, today = experience_api
    response = client.post("/experience/import-activities", json={"source": "garmin", "activities": [
        _import_item(today), _import_item(today, source_id="naive", start_time=f"{today}T09:00:00")
    ]})
    assert response.status_code == 422
    with factory() as session:
        assert session.query(Activity).count() == 0


def test_race_preparation_roundtrip(experience_api):
    client, _, _ = experience_api
    assert client.get("/experience/race-preparation").json()["nutrition"] is False
    payload = {"nutrition": True, "hydration": True, "notes": "Provare la strategia durante il lungo."}
    assert client.put("/experience/race-preparation", json=payload).status_code == 200
    saved = client.get("/experience/race-preparation").json()
    assert saved["nutrition"] is True and saved["hydration"] is True
    assert saved["notes"] == payload["notes"]
