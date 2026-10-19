import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.api.routes_planning import router as planning_router
from app.db.experience_models import AthleteCheckin, SessionFeedback
from app.db.garmin_models import GarminDelivery
from app.db.models import Activity, Workout
from app.db.planning_models import PlanRevision
from app.services import planning


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(planning, "load_profile", lambda: {})
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def monday():
    return planning.week_start(planning._today()) + timedelta(days=7)


def sessions(week):
    return [row for day in week["days"] for row in day["sessions"] if row["status"] != "skipped"]


def save(db, **changes):
    profile = planning.PlanningProfile(**changes)
    return planning.save_settings(db, profile)["profile"]


def test_profile_validates_provenance_ranges_and_availability(db):
    assert planning.get_settings(db)["revision"] == 0
    for data in ({"availability": [60] * 6}, {"availability": [601] * 7}, {"thresholds": {"cycling_ftp_watts": {"value": 250}}}, {"thresholds": {"cycling_ftp_watts": {"value": 900, "source": "estimated"}}}, {"thresholds": {"cycling_ftp_watts": {"value": 250, "source": "measured"}}}):
        with pytest.raises(ValidationError):
            planning.PlanningProfile(**data)
    saved = save(db, weekly_minutes=480, thresholds={"cycling_ftp_watts": {"value": 250, "source": "measured", "measured_on": str(planning._today())}})
    assert saved["thresholds"]["cycling_ftp_watts"]["source"] == "measured"
    assert planning.get_settings(db)["revision"] == 1


def test_week_is_real_persisted_idempotent_and_conservative(db, monday):
    save(db, weekly_minutes=900)
    first = planning.generate_week(db, monday)
    second = planning.generate_week(db, monday)
    assert first == second
    assert first["version"] == 1
    assert first["total_minutes"] <= 180
    assert first["confidence"] <= 40
    assert db.query(Workout).count() == len(sessions(first))
    assert db.query(PlanRevision).count() == 1
    for day in first["days"]:
        assert day["planned_minutes"] <= day["available_minutes"]
    for row in sessions(first):
        assert sum(step["duration_min"] for step in row["payload"]["steps"]) == row["duration_min"]
        assert row["payload"]["priority"] in ("key", "support")
        assert all("target_rpe" in step and "target_power_w" not in step for step in row["payload"]["steps"])
        if row["sport"] == "running":
            assert row["duration_min"] <= 30


def test_cycling_targets_use_athlete_threshold_without_invented_defaults(db, monday):
    save(db, thresholds={"cycling_ftp_watts": {"value": 300, "source": "estimated"}})
    week = planning.generate_week(db, monday)
    bike = next(row for row in sessions(week) if row["sport"] == "cycling")
    main = next(s for s in bike["payload"]["steps"] if s["type"] == "aerobic")
    assert main["target_power_w"] == 195
    assert main["target_power_pct_ftp"] == 65


def test_pool_and_excluded_sports_are_hard_constraints(db, monday):
    save(db, pool_access=False, excluded_sports=["running"], availability=[0, 30, 0, 60, 0, 120, 90])
    week = planning.generate_week(db, monday)
    assert all(row["sport"] not in ("swimming", "running") for row in sessions(week))
    assert all(day["planned_minutes"] <= day["available_minutes"] for day in week["days"])
    assert week["days"][0]["sessions"] == []


def test_all_exclusions_produce_empty_explained_week(db, monday):
    save(db, excluded_sports=list(planning.SPORTS))
    week = planning.generate_week(db, monday)
    assert week["total_minutes"] == 0
    assert any("Nessuna nuova seduta" in reason for reason in week["rationale"])


def test_cancelled_workouts_do_not_count_as_planned_or_consume_generation_budget(db, monday):
    save(db)
    cancelled = Workout(date=str(monday + timedelta(days=1)), sport='cycling',
                        name='Cancelled manual session', duration_min=500, status='cancelled',
                        payload_json=json.dumps({'source': 'manual', 'sport': 'cycling', 'steps': []}))
    db.add(cancelled)
    db.commit()
    week = planning.get_week(db, monday)
    assert week['total_minutes'] == 0
    assert week['days'][1]['planned_minutes'] == 0
    assert week['days'][1]['required_minutes'] == 0
    assert not planning._occupancy(db, monday, [cancelled])
    generated = planning.generate_week(db, monday)
    assert generated['total_minutes'] > 0
    assert generated['total_minutes'] <= generated['target_minutes']
    assert db.get(Workout, cancelled.id).status == 'cancelled'


def test_move_validates_capacity_and_shortening_has_reversible_structure(db, monday):
    save(db)
    first = planning.generate_week(db, monday)
    row = max(sessions(first), key=lambda item: item["duration_min"])
    with pytest.raises(HTTPException) as err:
        planning.modify_workout(db, row["id"], "move", new_date=monday)
    assert err.value.status_code == 409
    with pytest.raises(HTTPException):
        planning.modify_workout(db, row["id"], "move", new_date=monday + timedelta(days=8))
    changed = planning.modify_workout(db, row["id"], "shorten", duration_min=15)
    assert changed["workout"]["duration_min"] == 15
    assert sum(s["duration_min"] for s in changed["workout"]["payload"]["steps"]) == 15
    restored = planning.undo_week(db, monday)
    assert next(w for w in sessions(restored) if w["id"] == row["id"])["payload"] == row["payload"]
    assert restored["version"] == 3


def test_skip_does_not_backfill_and_completed_workouts_are_immutable(db, monday):
    save(db)
    first = planning.generate_week(db, monday)
    row = sessions(first)[0]
    skipped = planning.modify_workout(db, row["id"], "skip")
    assert skipped["week"]["total_minutes"] == first["total_minutes"] - row["duration_min"]
    assert any("nessun debito" in reason for reason in skipped["week"]["rationale"])
    planning.undo_week(db, monday)
    workout = db.get(Workout, row["id"])
    workout.status = "completed"
    db.commit()
    with pytest.raises(HTTPException) as err:
        planning.modify_workout(db, row["id"], "shorten", duration_min=15)
    assert err.value.status_code == 409


def test_regeneration_preserves_manual_completed_and_delivery_identity(db, monday):
    save(db)
    first = planning.generate_week(db, monday)
    old = sessions(first)
    completed = db.get(Workout, old[0]["id"])
    completed.status = "completed"
    manual = Workout(date=(monday + timedelta(days=1)).isoformat(), sport="running", name="Manuale", duration_min=15, payload_json="{}", status="planned")
    db.add(manual)
    db.commit()
    second = planning.generate_week(db, monday, regenerate=True)
    now = sessions(second)
    assert any(row["id"] == completed.id and row["status"] == "completed" for row in now)
    assert any(row["id"] == manual.id for row in now)
    superseded = {row.id for row in db.query(Workout).filter(Workout.status == "superseded").all()}
    assert superseded == {row["id"] for row in old[1:]}
    assert superseded.isdisjoint({row["id"] for row in now})
    assert second["version"] == 2
    restored = planning.undo_week(db, monday)
    assert {row["id"] for row in sessions(restored)} == {row["id"] for row in old} | {manual.id}
    assert db.get(Workout, completed.id).status == "completed"


def test_undo_never_removes_newly_completed_session(db, monday):
    save(db)
    planning.generate_week(db, monday)
    current = planning.generate_week(db, monday, regenerate=True)
    done = sessions(current)[0]
    db.get(Workout, done["id"]).status = "completed"
    db.commit()
    restored = planning.undo_week(db, monday)
    matching = [row for row in sessions(restored) if row["date"] == done["date"] and row["sport"] == done["sport"]]
    assert len(matching) == 1
    assert matching[0]["id"] == done["id"]
    assert matching[0]["status"] == "completed"


def test_recent_journal_reduces_volume_and_never_prescribes_today_with_pain(db, monday):
    save(db, availability=[60] * 7)
    baseline = planning.generate_week(db, monday)
    db.add(AthleteCheckin(date=planning._today().isoformat(), payload_json=json.dumps({"fatigue": 5, "soreness": 4, "pain": True})))
    db.commit()
    reduced = planning.generate_week(db, monday, regenerate=True)
    assert reduced["target_minutes"] < baseline["target_minutes"]
    current = planning.generate_week(db, planning.week_start())
    today_rows = next(day["sessions"] for day in current["days"] if day["date"] == planning._today().isoformat())
    assert not today_rows
    assert any("Dolore segnalato" in reason for reason in current["rationale"])


def test_historical_capacity_uses_full_four_week_denominator_and_sport_caps(db, monday):
    save(db, weekly_minutes=900, experience="advanced")
    for index in range(8):
        db.add(Activity(source="test", source_id=str(index), sport="cycling", start_time=planning.day_start_utc(planning._today() - timedelta(days=index + 1)), duration_seconds=3600))
    db.commit()
    week = planning.generate_week(db, monday)
    assert week["target_minutes"] <= 126  # 8 h over four weeks, then at most 5%.
    assert week["total_minutes"] <= week["target_minutes"]
    assert all(row["duration_min"] <= 30 for row in sessions(week) if row["sport"] == "running")


def test_unplanned_actual_training_consumes_current_week_capacity(db):
    save(db, availability=[180] * 7)
    db.add(Activity(source="test", source_id="unplanned", sport="cycling", start_time=planning.day_start_utc(planning._today()), duration_seconds=120 * 60))
    db.commit()
    week = planning.generate_week(db)
    assert week["total_minutes"] <= max(0, week["target_minutes"] - 120)
    today = next(day for day in week["days"] if day["date"] == planning._today().isoformat())
    assert today["recorded_minutes"] == 120


def test_taper_and_post_race_reduce_volume_and_race_day_stays_free(db, monday):
    save(db, event_date=monday + timedelta(days=70))
    base = planning.generate_week(db, monday)
    save(db, event_date=monday + timedelta(days=14))
    taper = planning.generate_week(db, monday, regenerate=True)
    assert taper["phase"] == "taper"
    assert taper["target_minutes"] < base["target_minutes"]
    assert len(taper["block_preview"]) == 4
    assert all(item["provisional"] for item in taper["block_preview"])
    save(db, event_date=monday + timedelta(days=5))
    race = planning.generate_week(db, monday, regenerate=True)
    assert not race["days"][5]["sessions"]
    assert race["phase"] == "race_week"
    save(db, event_date=monday - timedelta(days=2))
    recovery = planning.generate_week(db, monday, regenerate=True)
    assert recovery["phase"] == "post_race"
    assert recovery["target_minutes"] < taper["target_minutes"]


def test_published_garmin_workout_cannot_be_silently_replaced(db, monday):
    save(db)
    first = planning.generate_week(db, monday)
    row = sessions(first)[0]
    db.add(GarminDelivery(workout_id=row["id"], content_hash="a" * 64, status="published", reason_code="published", remote_published=True))
    db.commit()
    with pytest.raises(HTTPException) as err:
        planning.generate_week(db, monday, regenerate=True)
    assert err.value.status_code == 409
    assert planning.get_week(db, monday)["version"] == 1
    assert db.get(Workout, row["id"]).status == "planned"
    with pytest.raises(HTTPException):
        planning.modify_workout(db, row["id"], "skip")


def test_past_generation_and_empty_undo_fail_clearly(db, monday):
    with pytest.raises(HTTPException) as err:
        planning.generate_week(db, planning._today() - timedelta(days=14))
    assert err.value.status_code == 422
    with pytest.raises(HTTPException) as err:
        planning.undo_week(db, monday)
    assert err.value.status_code == 409


def add_feedback(db, sport="running", days_ago=2, **content):
    workout = Workout(date=(planning._today() - timedelta(days=days_ago)).isoformat(), sport=sport, name="Seduta conclusa", duration_min=30, payload_json="{}", status="completed")
    db.add(workout)
    db.flush()
    feedback = SessionFeedback(workout_id=workout.id, payload_json=json.dumps({"rpe": 9, "completion_percent": 100, "feeling": "harder", **content}))
    db.add(feedback)
    db.commit()
    return feedback


def test_feedback_reduces_same_sport_without_repeated_save_or_regen_inflation(db, monday):
    save(db, weekly_minutes=900)
    baseline = planning.generate_week(db, monday)
    feedback = add_feedback(db)
    assert planning.get_week(db, monday)["needs_review"]
    adapted = planning.generate_week(db, monday, regenerate=True)
    assert adapted["target_minutes"] < baseline["target_minutes"]
    assert adapted["summary"]["running"]["minutes"] < baseline["summary"]["running"]["minutes"]
    signal = adapted["feedback_adaptation"]["sports"]["running"]
    assert signal["samples"] == signal["concerning"] == 1
    assert signal["factor"] == .8
    feedback.updated_at = datetime.utcnow()
    db.commit()
    again = planning.generate_week(db, monday, regenerate=True)
    assert again["feedback_adaptation"] == adapted["feedback_adaptation"]
    assert again["target_minutes"] == adapted["target_minutes"]
    assert not again["needs_review"]


def test_low_actual_completion_reduces_and_old_edited_feedback_is_ignored(db, monday):
    save(db)
    add_feedback(db, days_ago=40)
    old_only = planning.generate_week(db, monday)
    assert old_only["feedback_adaptation"]["sports"]["running"]["samples"] == 0
    add_feedback(db, rpe=4, feeling="as_expected", evaluation={"completion": 50})
    adapted = planning.generate_week(db, monday, regenerate=True)
    assert adapted["feedback_adaptation"]["sports"]["running"]["factor"] == .8
    assert adapted["target_minutes"] < old_only["target_minutes"]
    add_feedback(db, days_ago=3)
    repeated = planning.generate_week(db, monday, regenerate=True)
    assert repeated["feedback_adaptation"]["sports"]["running"]["factor"] == .65


def test_distance_and_goal_change_allocation_and_real_sessions_without_more_capacity(db, monday):
    save(db, race_distance="sprint", goal_priority="finish")
    sprint = planning.generate_week(db, monday)
    save(db, race_distance="ironman", goal_priority="finish")
    long = planning.generate_week(db, monday, regenerate=True)
    assert long["summary"]["cycling"]["minutes"] > sprint["summary"]["cycling"]["minutes"]
    assert long["summary"]["swimming"]["minutes"] <= sprint["summary"]["swimming"]["minutes"]
    save(db, race_distance="sprint", goal_priority="time", target_time="01:10:00")
    timed = planning.generate_week(db, monday, regenerate=True)
    assert timed["allocation"]["shares"] != sprint["allocation"]["shares"]
    assert timed["allocation"]["focus"] != sprint["allocation"]["focus"]
    assert max(w["duration_min"] for w in sessions(timed) if w["sport"] == "cycling") >= max(w["duration_min"] for w in sessions(sprint) if w["sport"] == "cycling")
    assert timed["target_minutes"] == sprint["target_minutes"]
    assert any("non costituisce una previsione" in reason for reason in timed["rationale"])
    assert timed["total_minutes"] <= timed["target_minutes"]


def test_strength_is_concrete_and_shares_existing_volume_budget(db, monday):
    save(db)
    week = planning.generate_week(db, monday)
    strength = next(w for w in sessions(week) if w["sport"] == "strength")
    instructions = " ".join(step["instruction"] for step in strength["payload"]["steps"])
    assert "sedia" in instructions and "Ponte glutei" in instructions and "Bird-dog" in instructions
    assert sum(step["duration_min"] for step in strength["payload"]["steps"]) == strength["duration_min"]
    assert week["total_minutes"] <= week["target_minutes"]
    assert week["adaptation_mode"] == "conservative_aerobic"
    assert all(step["target_rpe"] <= 3 for w in sessions(week) for step in w["payload"]["steps"])


def established_athlete(db, good_checkin=True):
    save(db, experience="intermediate", weekly_minutes=600, availability=[0, 90, 90, 90, 90, 240, 120])
    for sport, count, minutes in (("cycling", 8, 90), ("running", 8, 45), ("swimming", 4, 30)):
        for index in range(count):
            db.add(Activity(source="test", source_id=f"{sport}-{index}", sport=sport, start_time=planning.day_start_utc(planning._today() - timedelta(days=index + 1)), duration_seconds=minutes * 60))
    if good_checkin:
        db.add(AthleteCheckin(date=planning._today().isoformat(), payload_json=json.dumps({"fatigue": 1, "soreness": 1, "motivation": 4, "sleep_quality": 4, "pain": False})))
    db.commit()


def test_brick_links_existing_easy_sessions_and_reserves_transition_without_extra_load(db, monday):
    established_athlete(db)
    week = planning.generate_week(db, monday)
    assert week["brick_status"]["scheduled"]
    linked = [w for w in sessions(week) if "brick" in w["payload"]]
    assert len(linked) == 2
    bike, run = sorted(linked, key=lambda w: w["payload"]["brick"]["sequence"])
    assert bike["sport"] == "cycling" and run["sport"] == "running"
    assert bike["date"] == run["date"]
    assert bike["payload"]["brick"]["partner_workout_id"] == run["id"]
    assert run["duration_min"] <= 20
    assert all(s["target_rpe"] == 2 for s in run["payload"]["steps"])
    assert all(day["required_minutes"] <= day["available_minutes"] for day in week["days"])
    assert week["total_minutes"] <= week["target_minutes"]
    profile = planning.load_settings(db)
    profile["availability"][0] = 300
    planning.save_settings(db, profile)
    changed = planning.modify_workout(db, run["id"], "move", new_date=monday)
    assert all(w["date"] == monday.isoformat() for w in sessions(changed["week"]) if w["id"] in (bike["id"], run["id"]))
    skipped = planning.modify_workout(db, bike["id"], "skip")
    assert not skipped["week"]["brick_status"]["scheduled"]
    assert "brick" not in json.loads(db.get(Workout, run["id"]).payload_json)
    undone = planning.undo_week(db, monday)
    assert undone["brick_status"]["scheduled"]
    assert "brick" in json.loads(db.get(Workout, run["id"]).payload_json)


def test_brick_requires_fresh_good_recovery_and_is_omitted_after_concerning_feedback(db, monday):
    established_athlete(db, good_checkin=False)
    absent = planning.generate_week(db, monday)
    assert not absent["brick_status"]["scheduled"]
    assert "check-in" in absent["brick_status"]["reason"]
    db.add(AthleteCheckin(date=planning._today().isoformat(), payload_json=json.dumps({"fatigue": 1, "soreness": 1, "motivation": 4, "sleep_quality": 4, "pain": False})))
    db.commit()
    allowed = planning.generate_week(db, monday, regenerate=True)
    assert allowed["brick_status"]["scheduled"]
    add_feedback(db)
    blocked = planning.generate_week(db, monday, regenerate=True)
    assert not blocked["brick_status"]["scheduled"]
    assert "feedback" in blocked["brick_status"]["reason"]


def test_null_checkin_values_are_unknown_and_never_authorize_brick(db, monday):
    established_athlete(db, good_checkin=False)
    baseline = planning._capacity(db, planning.load_settings(db), monday)
    db.add(AthleteCheckin(date=planning._today().isoformat(), payload_json=json.dumps({"fatigue": None, "soreness": None, "sleep_quality": None, "motivation": None, "pain": False})))
    db.commit()
    unknown = planning._capacity(db, planning.load_settings(db), monday)
    assert unknown["good_recovery"] is False
    assert unknown["low_recovery"] is False
    assert unknown["limit"] == baseline["limit"]
    week = planning.generate_week(db, monday)
    assert not week["brick_status"]["scheduled"]
    assert "check-in" in week["brick_status"]["reason"]


def test_planning_activity_days_and_week_boundaries_follow_rome_not_utc(db, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return cls(2026, 10, 20, 12)

    monkeypatch.setattr(planning, "datetime", FixedDateTime)
    monkeypatch.setattr(planning, "athlete_today", lambda: date(2026, 10, 20))
    for identifier, stamp, minutes in (("local-monday", datetime(2026, 10, 11, 22, 30), 20), ("local-saturday", datetime(2026, 10, 16, 22, 30), 30), ("next-local-monday", datetime(2026, 10, 18, 22, 30), 40)):
        db.add(Activity(source="test", source_id=identifier, sport="cycling", start_time=stamp, duration_seconds=minutes * 60))
    db.commit()
    first = planning._recorded_minutes(db, date(2026, 10, 12))
    assert dict(first) == {"2026-10-12": 20, "2026-10-17": 30}
    assert dict(planning._recorded_minutes(db, date(2026, 10, 19))) == {"2026-10-19": 40}
    capacity = planning._capacity(db, planning.PlanningProfile().model_dump(mode="json"), date(2026, 10, 19))
    assert capacity["history_sessions"] == 2  # Monday's local session is outside previous history.


def test_planning_router_profile_week_edit_undo_and_openapi_roundtrip(db, monday):
    app = FastAPI()
    app.include_router(planning_router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        initial = client.get("/planning/profile")
        assert initial.status_code == 200
        profile = initial.json()["profile"]
        profile["weekly_minutes"] = 300
        saved = client.put("/planning/profile", json=profile)
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        generated = client.post("/planning/week", json={"start": monday.isoformat()})
        assert generated.status_code == 200
        week = generated.json()
        largest = max(sessions(week), key=lambda row: row["duration_min"])
        changed = client.patch(f"/planning/workouts/{largest['id']}", json={"action": "shorten", "duration_min": 15})
        assert changed.status_code == 200
        assert changed.json()["workout"]["duration_min"] == 15
        undone = client.post("/planning/undo", json={"start": monday.isoformat()})
        assert undone.status_code == 200
        assert next(w for w in sessions(undone.json()) if w["id"] == largest["id"])["duration_min"] == largest["duration_min"]
        persisted = client.get("/planning/week", params={"start": monday.isoformat()})
        assert persisted.status_code == 200
        assert persisted.json()["version"] == 3
        assert client.get("/planning/week", params={"start": "invalid"}).status_code == 422
        assert client.patch(f"/planning/workouts/{largest['id']}", json={"action": "move", "date": "invalid"}).status_code == 422
        schema = client.get("/openapi.json")
        assert schema.status_code == 200
        date_field = schema.json()["components"]["schemas"]["WorkoutChange"]["properties"]["date"]
        assert {"type": "string", "format": "date"} in date_field["anyOf"]
