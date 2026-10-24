from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from garmin_fit_sdk import Encoder, Profile
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_experience
from app.db.database import Base, get_db
from app.db.experience_models import ActivityIdentity
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.db.repository import save_activity
from app.services.activity_effort import parse_streams
from app.services import fit_observations as observations

STAMP = datetime(2026, 10, 10, 6, tzinfo=timezone.utc)


@pytest.fixture
def api():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(routes_experience.router)

    def db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = db
    yield TestClient(app), engine
    engine.dispose()


def session(**changes):
    return dict(sport='swimming', sub_sport='lap_swimming', start_time=STAMP,
                total_elapsed_time=300, total_timer_time=300, pool_length=25,
                pool_length_unit='metric', **changes)


def length(index, start, duration, *, kind='active', stroke='freestyle'):
    return dict(message_index=index, start_time=STAMP + timedelta(seconds=start),
                timestamp=STAMP + timedelta(seconds=int(start + duration)),
                total_elapsed_time=duration, total_timer_time=duration,
                length_type=kind, swim_stroke=stroke)


def detailed_file(*, two_sessions=False):
    encoder = Encoder()
    encoder.on_mesg(Profile['mesg_num']['FILE_ID'], dict(type='activity', manufacturer='development',
                                                        product=1, time_created=STAMP))
    for index, data in enumerate([length(0, 0, 30.5), length(1, 31, 15, kind='idle'), length(2, 46, 35)]):
        encoder.on_mesg(Profile['mesg_num']['LENGTH'], data)
    for offset, distance, power in [(0, 0, 0), (1, 2, 70), (2, 4, None)]:
        record = dict(timestamp=STAMP + timedelta(seconds=offset), distance=distance,
                      heart_rate=100 + offset, position_lat=0, position_long=2**29)
        if power is not None:
            record['power'] = power
        encoder.on_mesg(Profile['mesg_num']['RECORD'], record)
    encoder.on_mesg(Profile['mesg_num']['SESSION'], session())
    if two_sessions:
        encoder.on_mesg(Profile['mesg_num']['SESSION'], dict(sport='running',
            start_time=STAMP + timedelta(seconds=600), total_elapsed_time=300, total_timer_time=300))
    return bytes(encoder.close())


def test_official_fit_preserves_samples_lengths_and_precision(api):
    client, engine = api
    response = client.post('/experience/import-fit', content=detailed_file())
    assert response.status_code == 200, response.text
    with Session(engine) as db:
        observation = db.query(ActivityObservation).one()
        payload = json.loads(observation.payload_json)
        assert observation.source_key == f"fit:{response.json()['file_sha256']}:0"
        streams = {item['type']: item['data'] for item in payload['streams']}
        assert streams['time'] == [0, 1, 2]
        assert streams['watts'] == [0, 70, None]
        assert streams['distance'] == [0, 2, 4]
        assert streams['latlng'][0] == [0, 45]
        pool = payload['pool_lengths']
        assert pool['status'] == 'available'
        assert pool['pool_length_m'] == 25 and pool['pool_length_unit'] == 'metric'
        assert [row['type'] for row in pool['rows']] == ['active', 'idle', 'active']
        assert pool['rows'][0]['end_s'] == 30.5
        assert pool['rows'][0]['timestamp_s'] == 30
        assert pool['rows'][1]['start_s'] == 31  # No invented continuity across quantization.


def test_reimport_enriches_older_summary_and_keeps_identity_and_user_name(api):
    client, engine = api
    content = detailed_file()
    key = f'{sha256(content).hexdigest()}:0'
    with Session(engine) as db:
        row = save_activity(db, dict(source='fit', source_id=key, sport='swimming', name='My swim',
                                    start_time=STAMP.replace(tzinfo=None), duration_seconds=300))
        identifier = row.id
        assert 'streams' not in json.loads(db.query(ActivityObservation).one().payload_json)
    for _ in range(2):
        response = client.post('/experience/import-fit', content=content)
        assert response.status_code == 200 and response.json()['matched'] == 1
    with Session(engine) as db:
        assert db.query(Activity).count() == db.query(ActivityIdentity).count() == db.query(ActivityObservation).count() == 1
        assert db.get(Activity, identifier).name == 'My swim'
        assert json.loads(db.query(ActivityObservation).one().payload_json)['pool_lengths']['rows']


def test_multisession_import_rolls_back_summaries_and_observation_updates(api, monkeypatch):
    client, engine = api
    content = detailed_file(two_sessions=True)
    key = f'{sha256(content).hexdigest()}:0'
    with Session(engine) as db:
        save_activity(db, dict(source='fit', source_id=key, sport='swimming',
                              start_time=STAMP.replace(tzinfo=None), duration_seconds=300))
        original = db.query(ActivityObservation).one().payload_json
    real_save = routes_experience.save_activity
    calls = []

    def fail_second(db, data, **kwargs):
        calls.append(data['source_id'])
        if len(calls) == 2:
            raise IntegrityError('synthetic', {}, Exception('synthetic'))
        return real_save(db, data, **kwargs)

    monkeypatch.setattr(routes_experience, 'save_activity', fail_second)
    assert client.post('/experience/import-fit', content=content).status_code == 503
    with Session(engine) as db:
        assert db.query(Activity).count() == db.query(ActivityIdentity).count() == db.query(ActivityObservation).count() == 1
        assert db.query(ActivityObservation).one().payload_json == original


def test_yard_display_does_not_convert_pool_metres_again():
    fields = session()
    fields.update(pool_length=22.86, pool_length_unit='statute')
    result = observations.session_observations(dict(session_mesgs=[fields], length_mesgs=[length(0, 0, 30)]))[0]
    assert result['pool_lengths']['pool_length_m'] == 22.86
    assert result['pool_lengths']['pool_length_unit'] == 'statute'


def test_unsupported_session_still_owns_its_records():
    second = dict(sport='transition', start_time=STAMP + timedelta(seconds=400), total_elapsed_time=30)
    result = observations.session_observations(dict(session_mesgs=[session(), second],
        record_mesgs=[dict(timestamp=STAMP + timedelta(seconds=405), distance=999)]))
    assert result[0]['streams'] == []
    assert result[1]['streams'][0]['data'] == [5]


def test_overlapping_sessions_do_not_pick_an_arbitrary_stream_or_pool():
    result = observations.session_observations(dict(session_mesgs=[session(), session()],
        record_mesgs=[dict(timestamp=STAMP + timedelta(seconds=1), distance=99)],
        length_mesgs=[length(0, 0, 30)]))
    for payload in result.values():
        assert payload['streams'] == []
        assert payload['fit_recording']['timeline_ambiguous']
        assert payload['pool_lengths']['status'] == 'unavailable'
        assert payload['pool_lengths']['issues'] == ['ambiguous_length_session', 'missing_length_messages']


def test_null_timestamp_remains_in_place_and_invalid_values_stay_null():
    result = observations.session_observations(dict(session_mesgs=[session()], record_mesgs=[
        dict(timestamp=STAMP, distance=0), dict(distance=3),
        dict(timestamp=STAMP + timedelta(seconds=2), distance=float('nan'))]))[0]
    streams = {row['type']: row['data'] for row in result['streams']}
    assert streams['time'] == [0, None, 2]
    assert streams['distance'] == [0, 3, None]
    parsed = observations.apply_recording_quality(parse_streams(result['streams']), result)
    assert parsed.quality['timeline_ambiguous']


def test_limits_are_global_and_survive_later_parsing(monkeypatch):
    monkeypatch.setattr(observations, 'MAX_RECORDS', 2)
    monkeypatch.setattr(observations, 'MAX_LENGTHS', 1)
    result = observations.session_observations(dict(session_mesgs=[session()], record_mesgs=[
        dict(timestamp=STAMP + timedelta(seconds=i), distance=i) for i in range(3)],
        length_mesgs=[length(0, 0, 30), length(1, 30, 30)]))[0]
    assert result['fit_recording']['total_file_record_count'] == 3
    assert result['fit_recording']['source_count_complete'] is False
    assert result['fit_recording']['preserved_record_count'] == 2
    assert result['pool_lengths']['truncated'] and result['pool_lengths']['status'] == 'unavailable'
    parsed = observations.apply_recording_quality(parse_streams(result['streams']), result)
    assert parsed.quality['truncated'] and parsed.quality['fit_import_issues'] == ['record_limit']


def test_payload_size_limit_keeps_aligned_prefix_and_marks_loss(monkeypatch):
    monkeypatch.setattr(observations, 'MAX_PAYLOAD_BYTES', 1500)
    result = observations.session_observations(dict(session_mesgs=[session()], record_mesgs=[
        dict(timestamp=STAMP + timedelta(seconds=i), distance=2.3456 * i, power=70 + i) for i in range(200)]))[0]
    assert result['fit_recording']['truncated']
    assert 'payload_limit' in result['fit_recording']['issues']
    assert len({len(row['data']) for row in result['streams']}) == 1
    assert len(json.dumps(result, separators=(',', ':')).encode()) <= 1500


def test_missing_elapsed_cannot_turn_summary_duration_into_stream_bounds():
    fields = session()
    del fields['total_elapsed_time']
    result = observations.session_observations(dict(session_mesgs=[fields],
        record_mesgs=[dict(timestamp=STAMP, distance=0)], length_mesgs=[length(0, 0, 30)]))[0]
    assert result['streams'] == [] and result['elapsed_time'] is None
    assert result['fit_recording']['timeline_ambiguous']
    assert result['pool_lengths']['status'] == 'unavailable'


def test_non_pool_swimming_has_no_inferred_length_evidence():
    fields = session()
    fields['sub_sport'] = 'open_water'
    result = observations.session_observations(dict(session_mesgs=[fields], length_mesgs=[length(0, 0, 30)]))[0]
    assert result['pool_lengths']['status'] == 'not_applicable'
    assert result['pool_lengths']['rows'] == []
