import json
from datetime import timedelta
from collections import defaultdict
from app.db.models import Activity
from app.services.athlete_clock import athlete_today, activity_day, as_utc_naive, utc_now_naive
from app.services.training_load import sport_key


def gap_analysis(db, goal):
    today = athlete_today(); start = today - timedelta(days=89)
    now = utc_now_naive()
    activities = [a for a in db.query(Activity).all()
                  if as_utc_naive(a.start_time) <= now and start <= activity_day(a.start_time) <= today]
    output = []; all_scores = []
    for leg in json.loads(goal.legs_json):
        rows = [a for a in activities if sport_key(a.sport) == sport_key(leg['sport'])]
        # Mean over the last four complete weeks, including weeks with zero activity.
        monday = today - timedelta(days=today.weekday())
        weekly = defaultdict(float)
        for a in rows:
            day = activity_day(a.start_time)
            if monday - timedelta(days=28) <= day < monday:
                weekly[(day - timedelta(days=day.weekday())).isoformat()] += (a.duration_seconds or 0) / 3600
        recent_rows = [a for a in rows if monday - timedelta(days=28) <= activity_day(a.start_time) < monday]
        observed = {
            'weekly_hours': sum(weekly.values()) / 4 if recent_rows and all(a.duration_seconds is not None for a in recent_rows) else None,
            'longest_distance_km': max((a.distance_meters / 1000 for a in rows if a.distance_meters is not None), default=None),
            'longest_elevation_m': max((a.elevation_gain_meters for a in rows if a.elevation_gain_meters is not None), default=None),
        }
        requirements = []
        for key, value in observed.items():
            target = leg['requirements'].get(key)
            score = round(min(value / target, 1) * 100, 1) if target is not None and value is not None else None
            requirements.append({'kind': key, 'reference': target, 'observed': value, 'score': score,
                                 'gap': max(0, target - value) if target is not None and value is not None else None})
        # Technical specificity and altitude are explicitly confirmed, never inferred from climbing.
        for key, applicable in [('terrain_confirmed', leg['terrain'] in ('trail', 'mixed')), ('altitude_confirmed', leg.get('altitude_max_m') is not None and leg['altitude_max_m'] > 0)]:
            if applicable:
                confirmed = leg['requirements'].get(key)
                requirements.append({'kind': key, 'reference': True, 'observed': confirmed, 'score': None if confirmed is None else 100 if confirmed else 0, 'gap': None})
        # Elevation requirement is optional only for a flat event leg.
        applicable = [r for r in requirements if r['kind'] != 'longest_elevation_m' or (leg.get('elevation_gain_m') or 0) > 0 or r['reference'] is not None]
        all_scores.extend(r['score'] for r in applicable)
        output.append({'sport': leg['sport'], 'terrain': leg['terrain'], 'requirements': applicable})
    complete = bool(all_scores) and all(s is not None for s in all_scores)
    return {'goal_id': goal.id, 'window': {'from': str(start), 'to': str(today)}, 'legs': output,
            'score': round(sum(all_scores) / len(all_scores), 1) if complete else None,
            'status': 'available' if complete else 'insufficient_data', 'method_version': 'coverage-1',
            'explanation': 'Indice euristico di copertura dei riferimenti configurati. Non è una probabilità di completamento né una misura del recupero giornaliero.',
            'limits': ['Attività mancanti o non registrate limitano il confronto.', 'I riferimenti sono scelti dall’utente; non equivalgono automaticamente alle distanze di gara.', 'La quota richiede una conferma distinta dal dislivello.']}
