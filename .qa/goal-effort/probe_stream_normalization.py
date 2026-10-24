"""Read-only synthetic probes for the existing stream normalizer."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['IRON_DATA_DIR'] = str(Path(__file__).parent / 'isolated-data')
os.environ['IRON_AUTO_SYNC'] = '0'

from app.services import activity_detail as detail

result = {}


def probe(key, callback):
    try:
        result[key] = {'result': callback()}
    except Exception as error:
        result[key] = {'error': type(error).__name__}


def points(raw, **options):
    charts, gps, count = detail.normalize_streams(raw, 'cycling', **options)
    return {'charts': charts, 'gps': gps, 'count': count}


probe('conflicting_duplicate_timestamps', lambda: points([
    {'type': 'time', 'data': [0, 10, 10, 20]},
    {'type': 'watts', 'data': [100, 100, 999, 100]},
    {'type': 'heartrate', 'data': [100, 100, 200, 100]}]))
probe('duplicate_channel_type_last_wins', lambda: points([
    {'type': 'time', 'data': [0, 10]},
    {'type': 'watts', 'data': [100, 100]},
    {'type': 'watts', 'data': [999, 999]}]))
probe('unhashable_channel_type', lambda: points([
    {'type': [], 'data': [0]}, {'type': 'time', 'data': [0]}]))
probe('oversized_integer', lambda: points([
    {'type': 'time', 'data': [0, 1]}, {'type': 'watts', 'data': [100, 10 ** 1000]}]))
probe('deep_observation_json', lambda: detail._load_json('{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}'))
probe('out_of_order_has_no_gap_marker', lambda: points([
    {'type': 'time', 'data': [0, 10, 5, 15]}, {'type': 'watts', 'data': [100, 200, 999, 300]}]))
probe('gps_beyond_time_axis_after_cutoff', lambda: points([
    {'type': 'time', 'data': [0, 10]}, {'type': 'watts', 'data': [100, 200]},
    {'type': 'latlng', 'data': [[42, 13], [42.1, 13.1], [42.2, 13.2]]}], max_time_s=10))
probe('silent_raw_truncation', lambda: {
    'input_count': detail.MAX_RAW_POINTS + 1,
    'output_metadata': [{key: value for key, value in chart.items() if key != 'points'} for chart in detail.normalize_streams([
        {'type': 'time', 'data': list(range(detail.MAX_RAW_POINTS + 1))},
        {'type': 'watts', 'data': [100] * (detail.MAX_RAW_POINTS + 1)}], 'cycling')[0]]})
target = Path(__file__).with_suffix('.json')
target.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
print(json.dumps({'report': str(target), 'probes': {key: value.get('error', 'observed') for key, value in result.items()}}))
