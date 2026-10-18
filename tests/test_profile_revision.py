"""Profile compare-and-swap behavior, including concurrent SQLite writers."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes_planning import router
from app.db.database import Base, get_db
from app.db.planning_models import AthleteSettings
from app.services import planning


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    application = FastAPI()
    application.include_router(router)

    def isolated_db():
        with Session(engine) as db:
            yield db

    application.dependency_overrides[get_db] = isolated_db
    yield TestClient(application), engine
    engine.dispose()


@pytest.fixture
def disk_engine(monkeypatch):
    monkeypatch.setattr(planning, 'load_profile', lambda: {})
    # Use an ordinary workspace file: pytest temporary-directory ACLs can be
    # inaccessible under the Windows sandbox used by the application checks.
    path = Path.cwd() / f'.profile-revision-{uuid4().hex}.db'
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': 5})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_optional_if_match_keeps_body_contract_and_rejects_stale_updates(api):
    client, _ = api
    initial = client.get('/planning/profile').json()
    assert initial['revision'] == 0
    profile = {**initial['profile'], 'name': 'First writer'}
    saved = client.put('/planning/profile', json=profile, headers={'If-Match': '"0"'})
    assert saved.status_code == 200 and saved.json() == {'profile': profile, 'revision': 1}
    stale = client.put('/planning/profile', json={**profile, 'name': 'Stale writer'}, headers={'If-Match': '0'})
    assert stale.status_code == 412
    assert stale.json()['detail']['current_revision'] == 1
    assert 'Ricarica' in stale.json()['detail']['message']
    assert client.get('/planning/profile').json() == saved.json()
    current = client.put('/planning/profile', json={**profile, 'name': 'Current writer'}, headers={'If-Match': '1'})
    assert current.status_code == 200 and current.json()['revision'] == 2
    legacy = client.put('/planning/profile', json=profile)
    assert legacy.status_code == 200 and legacy.json()['revision'] == 3


def test_nonzero_revision_on_unsaved_profile_is_conflict_without_insert(api):
    client, engine = api
    response = client.put('/planning/profile', json={'name': 'Outdated'}, headers={'If-Match': '1'})
    assert response.status_code == 412 and response.json()['detail']['current_revision'] == 0
    with Session(engine) as db:
        assert db.query(AthleteSettings).count() == 0


@pytest.mark.parametrize('header', ['*', 'W/"1"', '-1', '1.0', '"1', '1"', '"1", "2"', 'NaN', '', '9223372036854775808'])
def test_invalid_if_match_is_rejected_without_writing(api, header):
    client, _ = api
    response = client.put('/planning/profile', json={'name': 'Invalid header'}, headers={'If-Match': header})
    assert response.status_code == 422
    assert client.get('/planning/profile').json()['revision'] == 0


@pytest.mark.parametrize('initial_revision', [0, 1])
def test_concurrent_conditional_writers_have_exactly_one_winner(disk_engine, initial_revision):
    if initial_revision:
        with Session(disk_engine) as db:
            planning.save_settings(db, {'name': 'Initial'})
    barrier = Barrier(2)

    def write(name):
        with Session(disk_engine) as db:
            # Both writers observe the same snapshot before either writes.
            row = db.get(AthleteSettings, 1)
            assert (row.revision if row else 0) == initial_revision
            barrier.wait(timeout=5)
            try:
                result = planning.save_settings(db, {'name': name}, expected_revision=initial_revision)
                return 200, result['revision'], name
            except HTTPException as error:
                return error.status_code, error.detail['current_revision'], name

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(write, 'Writer A')
        second = executor.submit(write, 'Writer B')
        results = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(result[0] for result in results) == [200, 412]
    assert {result[1] for result in results} == {initial_revision + 1}
    winner = next(result[2] for result in results if result[0] == 200)
    with Session(disk_engine) as db:
        current = planning.get_settings(db)
        assert current['revision'] == initial_revision + 1
        assert current['profile']['name'] == winner
        assert db.query(AthleteSettings).count() == 1


def test_concurrent_legacy_writers_increment_revision_without_lost_updates(disk_engine):
    with Session(disk_engine) as db:
        planning.save_settings(db, {'name': 'Initial'})
    barrier = Barrier(2)

    def write(name):
        with Session(disk_engine) as db:
            assert db.get(AthleteSettings, 1).revision == 1
            barrier.wait(timeout=5)
            return planning.save_settings(db, {'name': name})

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(write, 'Legacy A')
        second = executor.submit(write, 'Legacy B')
        results = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(result['revision'] for result in results) == [2, 3]
    winner = next(result['profile']['name'] for result in results if result['revision'] == 3)
    with Session(disk_engine) as db:
        assert planning.get_settings(db)['profile']['name'] == winner
        assert planning.get_settings(db)['revision'] == 3
