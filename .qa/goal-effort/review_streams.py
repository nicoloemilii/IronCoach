"""Read-only reproduction of stream hazards using synthetic values only."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent
os.environ['IRON_DATA_DIR'] = str(QA / 'review-isolated-unused')
os.environ['IRON_AUTO_SYNC'] = '0'
sys.path.insert(0, str(ROOT))
from app.services.activity_detail import normalize_streams


def run_case(raw):
    try:
        charts, route, count = normalize_streams(raw, 'cycling')
        return {'time_samples': count, 'streams': charts, 'route_points': len(route)}
    except Exception as error:
        return {'error': type(error).__name__, 'message': str(error)[:160]}


def short_case(times, watts, hr=None):
    raw = [{'type': 'time', 'data': times}, {'type': 'watts', 'data': watts}]
    if hr is not None:
        raw.append({'type': 'heartrate', 'data': hr})
    return run_case(raw)


if __name__ == '__main__':
    report = {'production_data_accessed': False, 'database_opened': False,
              'source_sha256': {str(path.relative_to(ROOT)).replace('\\', '/'):
                  hashlib.sha256(path.read_bytes()).hexdigest() for path in
                  [ROOT / 'app/services/activity_detail.py', ROOT / 'web/charts.js']}, 'cases': {}}
    cases = report['cases']
    cases['duplicate_times'] = short_case([0, 1, 1, 2], [100, 110, 300, 120])
    cases['backward_time_removed_without_explicit_gap'] = short_case([0, 2, 1, 3], [100, 200, 1000, 300])
    cases['unmarked_899_second_dropout'] = short_case([0, 1, 900, 901], [100, 100, 200, 200])
    cases['short_hr_channel'] = short_case([0, 1, 2, 3, 4], [100] * 5, [120] * 3)
    cases['trailing_missing_values'] = short_case([0, 1, 2, 100], [100, 100, 100, None])
    cases['huge_integer'] = short_case([0, 1], [100, 10**400])
    cases['unhashable_type'] = run_case([{'type': [], 'data': [1, 2]}])
    cases['duplicate_stream_type_last_wins'] = run_case([
        {'type': 'time', 'data': [0, 1]}, {'type': 'time', 'data': [100, 101]},
        {'type': 'watts', 'data': [100, 100]}])
    count = 10000
    watts = [100] * count
    heart_rate = [120] * count
    for index in range(0, count, 34):
        watts[index] = 1000
    for index in range(16, count, 34):
        heart_rate[index] = 180
    result = short_case(list(range(count)), watts, heart_rate)
    sampled_power, sampled_hr = result['streams']
    power_times = {point['time_s'] for point in sampled_power['points']}
    hr_times = {point['time_s'] for point in sampled_hr['points']}
    cases['extrema_sampling_not_a_statistical_sample'] = {
        'raw_samples': count, 'display_power_samples': len(sampled_power['points']),
        'raw_mean_power': sum(watts) / count,
        'display_mean_power': sum(point['value'] for point in sampled_power['points']) / len(sampled_power['points']),
        'power_only_times': len(power_times - hr_times), 'hr_only_times': len(hr_times - power_times),
        'chart_downsampled': sampled_power['downsampled']}
    count = 250002
    truncated = short_case(list(range(count)), [100] * count)
    cases['raw_cap_hides_last_two_points'] = {
        'raw_samples': count, 'reported_time_samples': truncated['time_samples'],
        'last_display_time': truncated['streams'][0]['points'][-1]['time_s'],
        'chart_downsampled': truncated['streams'][0]['downsampled'],
        'explicit_raw_truncation_flag': 'raw_truncated' in truncated}
    (QA / 'review_streams.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'cases': list(cases), 'sampling_bias': cases['extrema_sampling_not_a_statistical_sample'],
                      'errors': {key: value['error'] for key, value in cases.items() if 'error' in value}}, ensure_ascii=False))
