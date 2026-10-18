"""Isolated tests: no athlete database, no Garmin account, no remote requests."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes_garmin import router
from app.db.database import Base, get_db
from app.db.garmin_models import GarminDelivery
from app.db.models import Workout
from app.services.garmin_delivery import normalize_workout


@pytest.fixture
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # Only the two tables these routes need are created, in isolated memory.
    Base.metadata.create_all(engine, tables=[Workout.__table__, GarminDelivery.__table__])
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(router)

    def isolated_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = isolated_db
    payload = {
        "sport": "cycling", "name": "Intervalli controllati", "duration_min": 15,
        "steps": [{"type": "warmup", "duration_min": 5, "target_power_w": 100},
                  {"type": "repeat", "repeat_count": 2, "steps": [
                      {"type": "interval", "duration_min": 2, "target_power_w": 170},
                      {"type": "recovery", "duration_min": 3, "target_rpe": 2},
                  ]}],
    }
    with factory() as session:
        session.add(Workout(id=1, date="2026-10-18", sport="cycling", name=payload["name"], duration_min=15, payload_json=json.dumps(payload)))
        session.commit()
    with TestClient(app) as client:
        yield client, factory, payload
    engine.dispose()


def test_status_does_not_claim_a_connector(api):
    client, _, _ = api
    state = client.get("/integrations/garmin/status").json()
    assert state["status"] == "official_access_required"
    assert state["connected"] is state["can_send"] is state["connector_implemented"] is False
    assert state["last_sync_at"] is None
    assert state["export"]["garmin_importable"] is False


def test_normalizes_nested_repeat_and_explicit_units(api):
    client, _, _ = api
    result = client.get("/workouts/1/garmin/validate").json()
    assert result["valid"] is True
    assert result["compatibility"] == "unknown"
    assert result["totals"] == {"time_seconds": 900.0, "distance_meters": 0.0, "expanded_steps": 5}
    step = result["structured_workout"]["steps"][1]["steps"][0]
    assert step["duration"] == {"type": "time", "value": 120.0, "unit": "s"}
    assert step["target"] == {"type": "power", "value": 170.0, "unit": "W"}


def test_repeated_clicks_create_one_blocked_revision_and_no_remote_success(api):
    client, factory, _ = api
    initial = client.get("/workouts/1/garmin/status").json()
    assert initial["status"] == "not_sent"
    first = client.post("/workouts/1/garmin/send").json()
    second = client.post("/workouts/1/garmin/send").json()
    assert first["status"] == second["status"] == "blocked"
    assert first["remote_published"] is second["remote_published"] is False
    assert first["reason_code"] == "official_access_required"
    assert first["idempotent_replay"] is False and second["idempotent_replay"] is True
    assert first["delivery"]["id"] == second["delivery"]["id"]
    assert second["delivery"]["request_count"] == 2
    with factory() as session:
        assert session.query(GarminDelivery).count() == 1


def test_modified_workout_keeps_history_and_needs_new_revision(api):
    client, factory, payload = api
    first = client.post("/workouts/1/garmin/send").json()
    payload["steps"][0]["target_power_w"] = 110
    with factory() as session:
        session.get(Workout, 1).payload_json = json.dumps(payload)
        session.commit()
    current = client.get("/workouts/1/garmin/status").json()
    assert current["status"] == "not_sent" and current["content_changed"] is True
    second = client.post("/workouts/1/garmin/send").json()
    assert first["content_hash"] != second["content_hash"]
    assert len(second["history"]) == 2
    assert second["remote_published"] is False


def test_json_export_is_download_and_explicitly_not_a_garmin_file(api):
    client, _, _ = api
    response = client.get("/workouts/1/export.json")
    assert response.status_code == 200
    assert 'attachment; filename="workout-1.json"' == response.headers["content-disposition"]
    assert response.json()["garmin_importable"] is False
    assert response.json()["workout"]["schema_version"] == "ironman-coach.workout.v1"


def test_unknown_workout_is_404(api):
    client, _, _ = api
    for method, path in (("get", "validate"), ("get", "status"), ("post", "send")):
        assert getattr(client, method)(f"/workouts/999/garmin/{path}").status_code == 404
    assert client.get("/workouts/999/export.json").status_code == 404


@pytest.mark.parametrize("bad_value", [0, -2, float("inf"), float("nan"), True, "15", 10 ** 1000])
def test_invalid_duration_is_rejected_without_crash(bad_value):
    result = normalize_workout({"sport": "running", "name": "Test", "steps": [
        {"type": "easy", "duration_min": bad_value, "target_rpe": 3}
    ]})
    assert not result["valid"]
    assert any(error["code"] == "duration_invalid" for error in result["errors"])
    json.dumps(result, allow_nan=False)


def test_missing_target_is_not_guessed_and_blocks_send(api):
    client, factory, payload = api
    del payload["steps"][0]["target_power_w"]
    with factory() as session:
        session.get(Workout, 1).payload_json = json.dumps(payload)
        session.commit()
    result = client.post("/workouts/1/garmin/send").json()
    assert result["reason_code"] == "invalid_workout"
    assert any(error["code"] == "target_missing" for error in result["validation"]["errors"])
    assert result["remote_published"] is False


def test_distance_ranges_and_explicit_free_target():
    result = normalize_workout({"sport": "swim", "name": "Tecnica", "steps": [
        {"type": "easy", "duration": {"type": "distance", "value": 0.4, "unit": "km"}, "target": {"type": "open"}},
        {"type": "active", "distance_meters": 200, "target": {"type": "pace", "min": 110, "max": 120, "unit": "s/100m"}},
    ]})
    assert result["valid"] is True
    assert result["totals"]["distance_meters"] == 600
    assert result["structured_workout"]["sport"] == "swimming"
    assert any(warning["code"] == "open_target" for warning in result["warnings"])


@pytest.mark.parametrize("target", [
    {"type": "rpe", "value": 11},
    {"type": "rpe", "value": 0.5},
    {"type": "power", "min": 200, "max": 100, "unit": "W"},
    {"type": "heart_rate", "value": float("nan"), "unit": "bpm"},
    {"type": [], "value": 3, "unit": "score"},
    {"type": "power", "value": 3, "unit": []},
    {"type": "power", "value": 100, "min": 90, "max": 110, "unit": "W"},
])
def test_malformed_targets_rejected(target):
    result = normalize_workout({"sport": "cycling", "name": "Test", "steps": [
        {"type": "active", "duration_min": 10, "target": target}
    ]})
    assert not result["valid"]
    json.dumps(result, allow_nan=False)


def test_excessive_nested_repeat_is_rejected():
    step = {"type": "active", "duration_min": 1, "target_rpe": 3}
    for _ in range(8):
        step = {"type": "repeat", "repeat_count": 100, "steps": [step]}
    result = normalize_workout({"sport": "cycling", "name": "Test", "steps": [step]})
    assert not result["valid"]
    assert any(error["code"] == "structure_too_large" for error in result["errors"])


def test_expanded_step_limit_is_enforced():
    step = {"type": "active", "duration_min": 0.1, "target_rpe": 3}
    result = normalize_workout({"sport": "cycling", "name": "Test", "steps": [
        {"type": "repeat", "repeat_count": 100, "steps": [step] * 11}
    ]})
    assert not result["valid"]
    assert any(error["code"] == "expanded_steps_exceeded" for error in result["errors"])


@pytest.mark.parametrize("payload", [None, [], {"sport": [], "steps": []}, {"sport": "running", "name": 12}])
def test_corrupt_payload_never_breaks_validation(payload):
    result = normalize_workout(payload)
    assert result["valid"] is False
    json.dumps(result, allow_nan=False)
