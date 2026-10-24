"""Actual HTTP GET-only checks against the isolated production QA server."""
import hashlib
import json
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
BASE = 'http://127.0.0.1:8004'
requests = []; checks = {}


def get(path, parameters=None, expected=200):
    target = path + ('?' + urlencode(parameters) if parameters else '')
    start = perf_counter()
    try:
        with urlopen(BASE + target, timeout=60) as response:
            status = response.status; body = response.read()
    except HTTPError as response:
        status = response.code; body = response.read()
    value = json.loads(body)
    requests.append(dict(method='GET', path=path, status=status,
        milliseconds=round((perf_counter() - start) * 1000, 3), parameters=list(parameters) if parameters else []))
    assert status == expected, (path, parameters, status, value)
    return value


def pages(parameters=None):
    parameters = dict(parameters or {})
    items = []; seen = set(); sizes = []; snapshots = []
    while True:
        value = get('/activities/diary', parameters)
        sizes.append(len(value['items'])); snapshots.append(value['snapshot'])
        assert len(value['items']) <= value['limit']
        assert value['archive_total'] == fixture['total']
        assert value['consistency'] == 'keyset_live_view' and value['includes_future'] is True
        for item in value['items']:
            assert item['id'] not in seen
            seen.add(item['id']); items.append(item)
        if not value['has_more']:
            assert value['next_cursor'] is None
            break
        assert value['next_cursor']
        parameters['cursor'] = value['next_cursor']
    assert len(items) == value['total']
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    return items, sizes


before = get('/_qa/state')
fixture = get('/_qa/fixture')
assert before['synthetic'] and fixture['synthetic'] and before['table_count'] == 27
assert fixture['total'] == 147
legacy = get('/activities')  # One full-list response; never rerun slow baseline.
legacy_by_id = {row['id']: row for row in legacy}
items, sizes = pages({'limit': 50})
assert sizes == [50, 50, 47]
assert [row['id'] for row in items] == fixture['expected_ids']
assert {row['id']: row for row in items} == legacy_by_id
checks.update(pagination_sizes=sizes, all_ids_exact=True, no_duplicate_or_missing_ids=True,
    tied_stamps_order='start_time DESC, id DESC', full_legacy_item_and_rating_parity=True,
    rating_json_sha256=hashlib.sha256(json.dumps(legacy_by_id, sort_keys=True, separators=(',', ':')).encode()).hexdigest())

results = []
for sport, query in [(category, '') for category in ('cycling', 'running', 'swimming', 'strength', 'other')] + [
    (None, 'CAFÉ'), (None, 'strasse'), (None, '%'), (None, '_'), (None, 'nessuna attività con questo nome'),
    ('other', '%'), ('cycling', 'CAFÉ'), ('strength', 'strasse')]:
    parameters = {'limit': 17, 'q': query}
    if sport is not None:
        parameters['sport'] = sport
    expected = [row['id'] for row in sorted(fixture['rows'], key=lambda row: (row['start_time'], row['id']), reverse=True)
                if (sport is None or row['category'] == sport) and query.casefold() in row['name'].casefold()]
    filtered, page_sizes = pages(parameters)
    assert [row['id'] for row in filtered] == expected
    assert all(row == legacy_by_id[row['id']] for row in filtered)
    results.append(dict(sport=sport, query=query, expected_ids=expected, page_sizes=page_sizes, exact=True))
checks['filters_unicode_casefold_and_literal_wildcards'] = results

hundred, hundred_sizes = pages({'limit': 100})
assert hundred_sizes == [100, 47] and [row['id'] for row in hundred] == fixture['expected_ids']
first = get('/activities/diary', {'limit': 5})
get('/activities/diary', {'limit': 5, 'sport': 'strength', 'cursor': first['next_cursor']}, expected=422)
get('/activities/diary', {'limit': 5, 'q': 'CAFÉ', 'cursor': first['next_cursor']}, expected=422)
for invalid in ({'limit': 0}, {'limit': 101}, {'sport': 'not-a-sport'}, {'q': 'a' * 201}, {'cursor': 'not_a_cursor'}):
    get('/activities/diary', invalid, expected=422)
checks['invalid_limits_filters_and_cursor_rejected'] = True

assert legacy_by_id[147]['load'] is None and legacy_by_id[147]['load_evidence']['reason'] == 'canonical_identity'
assert legacy_by_id[141]['load'] is None and legacy_by_id[141]['load_evidence']['status'] == 'conflict'
assert legacy_by_id[1]['coach_rating']['score'] is None
assert legacy_by_id[142]['sport_category'] == 'other'
canonical = get('/activities/147/detail')
assert canonical['summary']['load'] is None and canonical['summary']['avg_power'] == 150
assert canonical['effort_comparison']['status'] == 'available'
assert next(row for row in canonical['streams'] if row['kind'] == 'power')['statistics']['max'] == 160
conflict = get('/activities/141/detail')
assert conflict['summary']['load'] is None and not conflict['streams']
checks.update(canonical_null_stays_unknown=True, conflicting_aliases_stay_unknown=True,
    unknown_workout_stays_other=True, future_rating_unavailable=True,
    raw_stream_detail_uses_canonical_identity=True, conflicting_detail_does_not_use_foreign_streams=True)

after = get('/_qa/state')
assert after['tables'] == before['tables'], 'GET reads changed one or more table contents.'
assert after['table_count'] == before['table_count'] == 27
assert not after['provider_configured'] and after['provider_tripwire_calls'] == []
assert after['mutation_tripwire_calls'] == []
report = dict(synthetic=True, fixture=fixture['fixture'], base=BASE, checks=checks, requests=requests,
    request_count=len(requests), table_count=27, all_table_hashes_unchanged=True,
    before=before, after=after, provider_calls=0, mutations=0)
target = HERE / 'diary_api_review.json'
target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(dict(report=str(target), requests=len(requests), rows=len(items), pages=sizes,
    table_count=27, all_table_hashes_unchanged=True, provider_calls=0, mutations=0)))
