"""FIT protocol/meaning checks, not a claim of hardware compatibility."""

import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from garmin_fit_sdk import Decoder, Stream
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes_garmin import router
from app.db.database import Base, get_db
from app.db.garmin_models import GarminDelivery
from app.db.models import Workout
from app.services.fit_export import FitExportError, encode_fit_workout, fit_export_metadata
from app.services.garmin_delivery import normalize_workout


STAMP = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def validation(steps=None, **overrides):
    return normalize_workout({'name': 'Bici · qualità', 'sport': 'cycling', 'date': '2026-10-18',
                              'steps': steps or [{'type': 'active', 'duration_min': 10, 'target_power_w': 170}],
                              **overrides})


def decode(data):
    decoder = Decoder(Stream.from_byte_array(bytearray(data)))
    assert decoder.check_integrity() is True
    # Integrity checking consumes this SDK stream; decoding starts from byte 0.
    messages, errors = Decoder(Stream.from_byte_array(bytearray(data))).read()
    assert errors == []
    return messages


def test_real_workout_file_header_crc_sequence_and_identity():
    data, metadata = encode_fit_workout(validation(), created_at=STAMP)
    assert data[8:12] == b'.FIT'
    assert len(data) == data[0] + int.from_bytes(data[4:8], 'little') + 2
    messages = decode(data)
    identity = messages['file_id_mesgs'][0]
    assert identity['type'] == 'workout'
    assert identity['manufacturer'] == 'development'
    assert identity['time_created'] == STAMP
    assert identity['serial_number'] > 0
    assert messages['workout_mesgs'][0]['sport'] == 'cycling'
    assert messages['workout_mesgs'][0]['num_valid_steps'] == 1
    assert 'activity_mesgs' not in messages and 'session_mesgs' not in messages
    sequence = []
    Decoder(Stream.from_byte_array(bytearray(data))).read(mesg_listener=lambda number, message: sequence.append(number))
    assert sequence == [0, 26, 27]
    corrupted = bytearray(data)
    corrupted[-3] ^= 1
    assert Decoder(Stream.from_byte_array(corrupted)).check_integrity() is False
    assert metadata['device_compatibility'] == 'untested'
    assert metadata['cloud_delivery'] is metadata['calendar_scheduled'] is False


def test_absolute_power_is_offset_and_time_is_milliseconds():
    data, _ = encode_fit_workout(validation(), created_at=STAMP)
    step = decode(data)['workout_step_mesgs'][0]
    assert step['duration_type'] == 'time'
    assert step['duration_value'] == 600000
    assert step['duration_time'] == 600
    assert step['target_type'] == 'power' and step['target_power_zone'] == 0
    assert step['custom_target_value_low'] == step['custom_target_value_high'] == 1170


@pytest.mark.parametrize('target,fit_type,low,high', [
    ({'type': 'power', 'min': 175, 'max': 195, 'unit': 'W'}, 'power', 1175, 1195),
    ({'type': 'power', 'min': 65, 'max': 75, 'unit': 'percent_ftp'}, 'power', 65, 75),
    ({'type': 'heart_rate', 'min': 135, 'max': 155, 'unit': 'bpm'}, 'heart_rate', 235, 255),
    ({'type': 'heart_rate', 'value': 85, 'unit': 'percent_max_hr'}, 'heart_rate', 85, 85),
    ({'type': 'cadence', 'min': 80, 'max': 95, 'unit': 'rpm'}, 'cadence', 80, 95),
])
def test_custom_target_units_survive_round_trip(target, fit_type, low, high):
    plan = validation([{'type': 'warmup', 'duration_min': 10, 'target': target}])
    data, _ = encode_fit_workout(plan)
    step = decode(data)['workout_step_mesgs'][0]
    assert step['target_type'] == fit_type
    assert step['target_value'] == 0
    assert step['custom_target_value_low'] == low
    assert step['custom_target_value_high'] == high


def test_running_distance_and_pace_convert_to_speed_with_inverted_range():
    plan = validation([{'type': 'interval', 'distance_meters': 800,
                        'target': {'type': 'pace', 'min': 300, 'max': 330, 'unit': 's/km'}}], sport='running')
    data, _ = encode_fit_workout(plan)
    messages = decode(data)
    assert messages['workout_mesgs'][0]['sport'] == 'running'
    step = messages['workout_step_mesgs'][0]
    assert step['duration_value'] == 80000 and step['duration_distance'] == 800
    assert step['target_type'] == 'speed'
    assert step['custom_target_speed_low'] == pytest.approx(1000 / 330, abs=.001)
    assert step['custom_target_speed_high'] == pytest.approx(1000 / 300, abs=.001)


def test_rpe_is_open_and_never_fabricated_as_heart_rate():
    plan = validation([{'type': 'warmup', 'duration_min': 5, 'target_rpe': 3}])
    data, metadata = encode_fit_workout(plan)
    step = decode(data)['workout_step_mesgs'][0]
    assert step['target_type'] == 'open'
    assert 'custom_target_heart_rate_low' not in step
    assert 'RPE 3/10' in step['notes']
    assert 'target libero' in step['notes']
    assert any(item['code'] == 'rpe_as_open_target' for item in metadata['warnings'])


def test_nested_repeats_flatten_to_exact_execution_order():
    active = {'type': 'interval', 'duration_min': 1, 'target_power_w': 180}
    recovery = {'type': 'recovery', 'duration_min': 2, 'target_power_w': 100}
    plan = validation([{'type': 'repeat', 'repeat_count': 2, 'steps': [
        active, {'type': 'repeat', 'repeat_count': 2, 'steps': [recovery]}
    ]}])
    data, metadata = encode_fit_workout(plan)
    steps = decode(data)['workout_step_mesgs']
    assert [step['duration_time'] for step in steps] == [60, 120, 120, 60, 120, 120]
    assert [step['message_index'] for step in steps] == list(range(6))
    assert [step['intensity'] for step in steps] == ['interval', 'recovery', 'recovery'] * 2
    assert metadata['expanded_steps'] == 6
    assert any(item['code'] == 'repeats_flattened' for item in metadata['warnings'])


def test_step_limit_is_enforced_before_encoding():
    active = {'type': 'active', 'duration_min': 1, 'target_rpe': 3}
    fifty = validation([{'type': 'repeat', 'repeat_count': 50, 'steps': [active]}])
    data, _ = encode_fit_workout(fifty)
    assert decode(data)['workout_mesgs'][0]['num_valid_steps'] == 50
    too_many = validation([{'type': 'repeat', 'repeat_count': 51, 'steps': [active]}])
    with pytest.raises(FitExportError) as error:
        encode_fit_workout(too_many)
    assert any(item['code'] == 'too_many_fit_steps' for item in error.value.metadata['errors'])


@pytest.mark.parametrize('sport', ['swimming', 'strength'])
def test_unsupported_sport_is_not_silently_relabelled(sport):
    metadata = fit_export_metadata(validation(sport=sport))
    assert metadata['available'] is False
    assert any(item['code'] == 'sport_not_supported' for item in metadata['errors'])


def test_unsupported_target_and_subprecision_duration_are_rejected():
    swim_pace = validation([{'type': 'active', 'duration_min': 1,
                             'target': {'type': 'pace', 'value': 100, 'unit': 's/100m'}}])
    assert fit_export_metadata(swim_pace)['available'] is False
    too_short = validation([{'type': 'active', 'duration': {'type': 'time', 'value': .000001, 'unit': 's'}, 'target_rpe': 3}])
    assert fit_export_metadata(too_short)['available'] is False
    tiny_power = validation([{'type': 'active', 'duration_min': 1, 'target_power_w': .1}])
    assert fit_export_metadata(tiny_power)['available'] is False


def test_fractional_power_rounding_is_disclosed():
    plan = validation([{'type': 'active', 'duration_min': 1, 'target_power_w': 170.4}])
    data, metadata = encode_fit_workout(plan)
    assert decode(data)['workout_step_mesgs'][0]['custom_target_power_low'] == 1170
    assert any(item['code'] == 'integer_target_rounding' for item in metadata['warnings'])


def test_missing_targets_remain_errors_instead_of_becoming_open():
    plan = validation([{'type': 'active', 'duration_min': 10}])
    with pytest.raises(FitExportError):
        encode_fit_workout(plan)


def test_unicode_name_is_valid_and_controls_cannot_inject_fields():
    name = 'Bici · qualità\x00\n' + 'à' * 100
    data, _ = encode_fit_workout(validation(name=name))
    decoded = decode(data)['workout_mesgs'][0]['wkt_name']
    assert decoded.startswith('Bici · qualità')
    assert '\x00' not in decoded and '\n' not in decoded
    assert len(decoded.encode('utf-8')) <= 63


def test_same_revision_keeps_stable_file_identity():
    first, _ = encode_fit_workout(validation(), created_at=STAMP)
    second, _ = encode_fit_workout(validation(), created_at=STAMP)
    assert first == second
    changed = validation([{'type': 'active', 'duration_min': 10, 'target_power_w': 180}])
    third, _ = encode_fit_workout(changed, created_at=STAMP)
    assert decode(first)['file_id_mesgs'][0]['serial_number'] != decode(third)['file_id_mesgs'][0]['serial_number']


@pytest.fixture
def fit_api():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Workout.__table__, GarminDelivery.__table__])
    factory = sessionmaker(bind=engine)
    with factory() as session:
        for workout_id, sport in [(1, 'cycling'), (2, 'swimming')]:
            session.add(Workout(id=workout_id, date='2026-10-18', sport=sport, name='Test', duration_min=10,
                                payload_json=json.dumps({'sport': sport, 'name': 'Test', 'steps': [
                                    {'type': 'active', 'duration_min': 10, 'target_rpe': 3}]})))
        session.commit()
    app = FastAPI()
    app.include_router(router)

    def isolated_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = isolated_db
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def test_download_is_binary_has_warning_headers_and_does_not_send(fit_api):
    client, factory = fit_api
    validation_result = client.get('/workouts/1/garmin/validate').json()
    assert validation_result['fit_export']['available'] is True
    response = client.get('/workouts/1/export.fit')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/vnd.ant.fit'
    assert response.headers['content-disposition'] == 'attachment; filename="workout-1.fit"'
    assert response.headers['x-device-compatibility'] == 'untested'
    assert 'rpe_as_open_target' in response.headers['x-fit-warnings']
    assert response.headers['x-workout-content-hash'] == validation_result['content_hash']
    assert decode(response.content)['file_id_mesgs'][0]['type'] == 'workout'
    with factory() as session:
        assert session.query(GarminDelivery).count() == 0


def test_download_rejects_unsupported_sport_and_unknown_id(fit_api):
    client, _ = fit_api
    assert client.get('/workouts/2/garmin/validate').json()['fit_export']['available'] is False
    response = client.get('/workouts/2/export.fit')
    assert response.status_code == 422
    assert any(item['code'] == 'sport_not_supported' for item in response.json()['detail']['errors'])
    assert client.get('/workouts/999/export.fit').status_code == 404
