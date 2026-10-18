"""Detailed race context and disclosed allocation proxies, never race predictions."""
import hashlib
import json
from copy import deepcopy

from app.db.iron_models import RaceGoal

SPORTS = ('cycling', 'running', 'swimming', 'strength')


def goal_context(db, profile):
    """Return a transient effective profile; never write copied goal metadata on reads."""
    effective = dict(profile)
    identifier = profile.get('race_goal_id')
    if identifier is None:
        return effective, None, []
    row = db.get(RaceGoal, identifier)
    warnings = []
    if row:
        try:
            legs = json.loads(row.legs_json)
            if not legs or any(leg['sport'] not in SPORTS[:3] or float(leg['distance_km']) <= 0 for leg in legs):
                raise ValueError()
            context = {'id': row.id, 'name': row.name, 'date': row.date, 'priority': row.priority,
                       'target_time': row.target_time, 'legs': legs}
            signature = hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        except (ValueError, KeyError, TypeError):
            warnings.append('Obiettivo collegato non leggibile: pianificazione generale prudente, senza usare le sue specifiche.')
        else:
            disciplines = list(dict.fromkeys(leg['sport'] for leg in legs))
            constraints = ['Distanze e dislivello orientano le quote del tempo disponibile; non aumentano capacità, durata massima o intensità.',
                           'Le quote non prescrivono esposizione in altitudine; nessun adattamento alla quota viene dato per acquisito.']
            if 'swimming' in disciplines and not profile['pool_access']:
                constraints.append('Nuoto richiesto dalla gara ma piscina non disponibile: nessuna seduta acquatica prescritta.')
            excluded = [sport for sport in disciplines if sport in profile['excluded_sports']]
            if excluded:
                constraints.append('Discipline di gara escluse dal profilo: ' + ', '.join(excluded) + '. Non vengono reintrodotte automaticamente.')
            if any(leg.get('terrain') in ('trail', 'mixed') for leg in legs):
                constraints.append('Tecnica su terreno facile e familiare; nessuna prescrizione automatica di passaggi tecnici o discese impegnative.')
            context.update(signature=signature, disciplines=disciplines, constraints=constraints)
            effective.update(event_name=row.name, event_date=row.date, goal_priority=row.priority,
                             target_time=row.target_time, _race_goal=context)
            return effective, context, warnings
    else:
        warnings.append('L’obiettivo collegato è stato eliminato: pianificazione generale prudente. Collega una nuova gara o scollega il riferimento.')
    # Copied dates from a deleted target must not silently trigger a stale taper.
    effective.update(event_name='', event_date=None, target_time=None, goal_priority='finish')
    return effective, None, warnings


def allocation_for_goal(context, priority):
    # Fixed time proxies only distribute an already bounded training budget.
    # They are neither predicted splits nor prescribed training speeds.
    minutes_per_km = {'cycling': 2.4, 'running': 6.0, 'swimming': 30.0}
    ascent_minutes_per_1000 = {'cycling': 20.0, 'running': 60.0, 'swimming': 0.0}
    weights = {sport: 0.0 for sport in SPORTS}
    for leg in context['legs']:
        sport = leg['sport']
        flat = leg['distance_km'] * minutes_per_km[sport]
        ascent = max(0, leg.get('elevation_gain_m') or 0) / 1000 * ascent_minutes_per_1000[sport]
        terrain = 1.15 if sport == 'running' and leg.get('terrain') in ('trail', 'mixed') else 1.0
        weights[sport] += (flat + min(ascent, flat * .5)) * terrain
    total = sum(weights.values())
    support_share = .08 if priority == 'finish' else .06
    shares = {sport: value / total * (1 - support_share) for sport, value in weights.items()}
    shares['strength'] = support_share
    return {'shares': shares, 'focus': f"Continuità nelle discipline di {context['name']}; tecnica e gestione dello sforzo per terreno e dislivello configurati.",
            'proxy': {'minutes_per_km': minutes_per_km, 'ascent_minutes_per_1000': ascent_minutes_per_1000,
                      'ascent_increment_cap': .5, 'trail_running_factor': 1.15,
                      'note': 'Pesi convenzionali di ripartizione, non tempi gara previsti né velocità da tenere.'}}


def focus_for_sport(context, sport):
    legs = [leg for leg in context['legs'] if leg['sport'] == sport]
    terrains = list(dict.fromkeys(leg.get('terrain', 'road') for leg in legs))
    terrain = terrains[0] if len(terrains) == 1 else 'mixed' if terrains else None
    gain = sum(leg.get('elevation_gain_m') or 0 for leg in legs)
    instructions = {
        'cycling': 'Continuità a sforzo facile e rapporti agili; usa soltanto percorsi familiari e gestibili.',
        'running': 'Corsa facile su fondo familiare, con controllo del gesto e margine nelle sensazioni.',
        'swimming': 'Cura assetto, respirazione e continuità del gesto nell’ambiente acquatico abituale e adeguato.',
        'strength': 'Controllo del movimento e mobilità come supporto, senza arrivare a cedimento.',
    }
    cue = instructions[sport]
    if sport == 'running' and (terrain in ('trail', 'mixed') or gain > 0):
        cue = 'Su fondo facile e già conosciuto cura appoggi e passo controllato; cammina sui tratti ripidi ed evita discese tecniche. Nessun dislivello minimo da raggiungere.'
    elif sport == 'cycling' and gain > 0:
        cue += ' Soltanto pendenze abituali gestibili allo stesso sforzo facile; nessun dislivello minimo da raggiungere.'
    return {'goal_id': context['id'], 'goal_name': context['name'], 'sport': sport,
            'terrain': terrain, 'distance_km': sum(leg['distance_km'] for leg in legs),
            'elevation_gain_m': gain, 'explanation': cue,
            'constraints': deepcopy(context['constraints'])}
