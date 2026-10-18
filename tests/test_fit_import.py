from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from garmin_fit_sdk import Encoder, Profile
import pytest
from app.db.database import Base, get_db
from app.api.routes_experience import router
from app.db.models import Activity


@pytest.fixture
def setup():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI(); app.include_router(router)
    def db():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_db] = db
    yield TestClient(app), engine
    engine.dispose()


def activity_file(sports=('cycling',), kind='activity'):
    encoder = Encoder()
    timestamp = datetime(2026, 10, 10, 6, tzinfo=timezone.utc)
    encoder.on_mesg(Profile['mesg_num']['FILE_ID'], {'type': kind, 'manufacturer': 'development', 'product': 1, 'time_created': timestamp})
    for sport in sports:
        encoder.on_mesg(Profile['mesg_num']['SESSION'], {'sport': sport, 'start_time': timestamp,
            'total_elapsed_time': 3700, 'total_timer_time': 3600, 'total_distance': 25000, 'avg_power': 150})
    return bytes(encoder.close())


def test_official_sdk_import_is_idempotent_and_normalizes_timestamp(setup):
    client, engine = setup
    content = activity_file()
    one = client.post('/experience/import-fit', content=content)
    assert one.status_code == 200, one.text
    assert one.json()['created'] == 1
    two = client.post('/experience/import-fit', content=content)
    assert two.json()['created'] == 0 and two.json()['matched'] == 1
    with Session(engine) as db:
        row = db.query(Activity).one()
        assert row.duration_seconds == 3600 and row.avg_power == 150
        assert row.start_time == datetime(2026, 10, 10, 6)


def test_multisport_sessions_are_preserved(setup):
    client, engine = setup
    result = client.post('/experience/import-fit', content=activity_file(('cycling', 'running', 'swimming')))
    assert result.status_code == 200, result.text
    assert result.json()['created'] == 3


@pytest.mark.parametrize('payload', [b'not fit', b'\x00'*100])
def test_invalid_header_is_rejected(setup, payload):
    client, _ = setup
    assert client.post('/experience/import-fit', content=payload).status_code == 422


def test_bad_crc_and_non_activity_file_rejected(setup):
    client, _ = setup
    corrupt = bytearray(activity_file()); corrupt[-1] ^= 0x55
    assert client.post('/experience/import-fit', content=bytes(corrupt)).status_code == 422
    assert client.post('/experience/import-fit', content=activity_file(kind='workout')).status_code == 422


def test_unsupported_sport_is_not_silently_imported(setup):
    client, _ = setup
    assert client.post('/experience/import-fit', content=activity_file(('rowing',))).status_code == 422
