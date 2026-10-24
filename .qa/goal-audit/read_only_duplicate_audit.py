"""Local audit: aggregate counts only, read-only SQLite, no private text output."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[2]
path = PROJECT / 'data' / 'coach.db'


def sport(value):
    value = (value or '').strip().lower()
    for key, aliases in {
        'cycling': {'bike', 'cycling', 'biking', 'virtualride', 'virtual_ride', 'indoorcycling', 'indoor_cycling', 'mountainbikeride', 'mountain_bike', 'gravelride'},
        'running': {'run', 'running', 'trailrun', 'trailrunning', 'trail_run', 'virtualrun'},
        'swimming': {'swim', 'swimming', 'openwaterswim', 'open_water_swim'},
        'strength': {'strength', 'weighttraining', 'weight_training', 'gym', 'weightlifting', 'workout'},
    }.items():
        if value in aliases:
            return key
    return value


def number(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
    db.execute('PRAGMA query_only=ON')
    db.row_factory = sqlite3.Row
    fields = 'id,source,sport,start_time,duration_seconds,distance_meters,elevation_gain_meters,avg_hr,max_hr,avg_power,normalized_power,avg_speed,avg_cadence,calories,load'
    rows = [dict(row) for row in db.execute(f'SELECT {fields} FROM activities ORDER BY start_time,id')]
    candidates = Counter(row['status'] for row in db.execute('SELECT status FROM duplicate_candidates'))
    observations = db.execute('SELECT COUNT(*) FROM activity_observations').fetchone()[0]
    identities = db.execute('SELECT COUNT(*) FROM activity_identities').fetchone()[0]
    output = {'activities': len(rows), 'by_source': dict(Counter(row['source'] for row in rows)),
              'saved_duplicate_candidates': dict(candidates), 'observation_count': observations, 'identity_count': identities}
    groups = defaultdict(list)
    for row in rows:
        fingerprint = tuple(sport(row[key]) if key == 'sport' else row[key]
                            for key in fields.split(',') if key not in ('id', 'source'))
        groups[fingerprint].append(row)
    exact = [group for group in groups.values() if len(group) > 1]
    output['exact_observed_field_groups'] = len(exact)
    output['exact_observed_field_excess_rows'] = sum(len(group) - 1 for group in exact)
    output['exact_groups_same_provider_only'] = sum(len({row['source'] for row in group}) == 1 for group in exact)
    as_of = date(2026, 10, 19)
    def local_day(group):
        stamp = datetime.fromisoformat(group[0]['start_time']).replace(tzinfo=timezone.utc)
        return stamp.astimezone(ZoneInfo('Europe/Rome')).date()
    output['exact_excess_current_28_days'] = sum(len(group) - 1 for group in exact if as_of - timedelta(days=27) <= local_day(group) <= as_of)
    output['exact_excess_previous_28_days'] = sum(len(group) - 1 for group in exact if as_of - timedelta(days=55) <= local_day(group) <= as_of - timedelta(days=28))
    same_provider = across_provider = 0
    exact_core = 0
    for index, row in enumerate(rows):
        stamp = datetime.fromisoformat(row['start_time'])
        duration = row['duration_seconds']
        if not number(duration) or duration <= 0:
            continue
        for prior in reversed(rows[:index]):
            delta = (stamp - datetime.fromisoformat(prior['start_time'])).total_seconds()
            if delta > 120:
                break
            if sport(row['sport']) != sport(prior['sport']):
                continue
            old_duration = prior['duration_seconds']
            if not number(old_duration) or old_duration <= 0 or abs(duration - old_duration) > max(30, duration * .03):
                continue
            distance, old_distance = row['distance_meters'], prior['distance_meters']
            if distance is not None and old_distance is not None and abs(distance - old_distance) > max(100, distance * .03):
                continue
            if row['source'] == prior['source']:
                same_provider += 1
            else:
                across_provider += 1
            exact_core += int(delta == 0 and duration == old_duration and distance == old_distance)
    output['repository_heuristic_candidate_pairs_same_provider_ignored'] = same_provider
    output['repository_heuristic_candidate_pairs_across_providers'] = across_provider
    output['exact_start_sport_duration_distance_pairs'] = exact_core
    print(json.dumps(output, indent=2, sort_keys=True))
