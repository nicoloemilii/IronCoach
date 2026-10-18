"""Evidence-led weekly review, descriptive personal trends and race checklists."""
import hashlib
import json
import math
from datetime import date, timedelta
from statistics import mean, median

from sqlalchemy import or_

from app.db.experience_models import AthleteCheckin, SessionFeedback
from app.db.garmin_models import GarminDelivery
from app.db.iron_models import RaceGoal
from app.db.models import Activity, Workout, WorkoutEvaluation
from app.db.progress_models import GoalPreparationItem, WorkoutResolution
from app.services.athlete_clock import activity_day, as_utc_naive, athlete_today, utc_now_naive
from app.services.health import METRICS, series, reference_range
from app.services.race_gap import gap_analysis
from app.services.training_load import sport_key


SPORTS = {'swim': ('swimming', 'Nuoto'), 'bike': ('cycling', 'Bici'),
          'run': ('running', 'Corsa'), 'strength': ('strength', 'Forza')}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) else None


def _total(values):
    return round(sum(values), 1) if all(v is not None for v in values) else None


def active_resolution(db, workout_id):
    return db.query(WorkoutResolution).filter_by(workout_id=workout_id, undone_at=None).first()


def remote_resolution_blocked(db, workout_id):
    # A blocked local Garmin export is safe; an acknowledged publication or an
    # unresolved delivery must first be reconciled by its own connector.
    return db.query(GarminDelivery).filter(
        GarminDelivery.workout_id == workout_id,
        or_(GarminDelivery.remote_published.is_(True),
            GarminDelivery.status.in_(['queued', 'pending', 'sending', 'published', 'sent', 'unknown']))
    ).first() is not None


def resolution_block(db, workout):
    if date.fromisoformat(workout.date) >= athlete_today():
        return 'Puoi riconciliare soltanto sedute dei giorni precedenti.'
    if workout.status != 'planned':
        return 'La seduta non è più pianificata; non può essere riconciliata come non svolta.'
    if (db.query(SessionFeedback).filter_by(workout_id=workout.id).first()
            or db.query(WorkoutEvaluation).filter_by(workout_id=workout.id).first()):
        return 'La seduta ha già un feedback o una valutazione confermati.'
    if remote_resolution_blocked(db, workout.id):
        return 'Risolvi prima lo stato della pubblicazione Garmin di questa seduta.'
    return None


def resolution_dict(row):
    if not row:
        return None
    return {'id': row.id, 'status': row.kind, 'reason': row.reason,
            'created_at': row.created_at.isoformat() + 'Z',
            'undone_at': row.undone_at.isoformat() + 'Z' if row.undone_at else None}


def _feedback_actual(db, feedback, today, now):
    content = json.loads(feedback.payload_json)
    if feedback.activity_id is not None:
        activity = db.get(Activity, feedback.activity_id)
        if (not activity or as_utc_naive(activity.start_time) > now
                or activity_day(activity.start_time) > today):
            return None
        duration = _number(activity.duration_seconds)
        return {'minutes': duration / 60 if duration is not None else None,
                'source': 'activity', 'activity_id': activity.id, 'content': content}
    actual = content.get('actual') or {}
    duration = _number(actual.get('duration_min'))
    return {'minutes': duration, 'source': 'self_reported', 'activity_id': None, 'content': content}


def week_progress(db, requested_start=None):
    today = athlete_today(); now = utc_now_naive()
    requested_start = requested_start or today
    monday = requested_start - timedelta(days=requested_start.weekday())
    end = monday + timedelta(days=6)
    workouts = db.query(Workout).filter(Workout.date >= str(monday), Workout.date <= str(end),
                                      ~Workout.status.in_(['cancelled', 'superseded'])).order_by(Workout.date, Workout.id).all()
    activities = [a for a in db.query(Activity).all() if as_utc_naive(a.start_time) <= now
                  and monday <= activity_day(a.start_time) <= min(today, end)]
    sessions = []; adherence_values = []
    counts = dict(completed=0, modified=0, skipped=0, rest=0, unconfirmed=0, planned=0)
    for workout in workouts:
        feedback = db.query(SessionFeedback).filter_by(workout_id=workout.id).first()
        evidence = _feedback_actual(db, feedback, today, now) if feedback and workout.date <= str(today) else None
        resolution = active_resolution(db, workout.id)
        actual_minutes = evidence['minutes'] if evidence else None
        completion = None; source = None; state = 'planned'
        if evidence:
            source = evidence['source']
            completion = _number(evidence['content'].get('completion_percent'))
            if actual_minutes is not None and workout.duration_min > 0:
                completion = actual_minutes / workout.duration_min * 100
                adherence_values.append(max(0, 100 - abs(completion - 100)))
            modified = ((completion is not None and not math.isclose(completion, 100, abs_tol=0.01))
                        or evidence['content'].get('feeling') in ('harder', 'easier'))
            state = 'modified' if modified else 'completed'
        elif resolution and workout.date < str(today):
            state = resolution.kind; source = 'explicit_resolution'
            if state == 'skipped':
                adherence_values.append(0)
        elif workout.status == 'skipped':
            # Legacy planned skips are explicit too, but carry no retrospective
            # intent or completion evidence. Never fabricate a rest resolution.
            state = 'skipped'
            source = 'planned_skip'
            if workout.date < str(today):
                adherence_values.append(0)
        elif workout.date < str(today):
            state = 'unconfirmed'
        counts[state] += 1
        sessions.append({'id': workout.id, 'date': workout.date,
                         'sport': SPORTS.get(sport_key(workout.sport), (workout.sport, ''))[0],
                         'name': workout.name, 'status': state, 'planned_minutes': workout.duration_min,
                         'actual_minutes': round(actual_minutes, 1) if actual_minutes is not None else None,
                         'completion_percent': round(completion, 1) if completion is not None else None,
                         'evidence_source': source, 'activity_id': evidence['activity_id'] if evidence else None,
                         'resolution': resolution_dict(resolution),
                         'can_resolve': state == 'unconfirmed' and resolution_block(db, workout) is None})
    sports = []
    keys = list(SPORTS)
    keys.extend(sorted({sport_key(a.sport) for a in activities} - set(keys)))
    for key in keys:
        canonical, label = SPORTS.get(key, (key, key))
        selected = [row for row in sessions if sport_key(row['sport']) == key]
        recorded = [row for row in activities if sport_key(row.sport) == key]
        confirmed = [row for row in selected if row['evidence_source'] in ('activity', 'self_reported')]
        sports.append({'sport': canonical, 'label': label,
                       'planned_minutes': round(sum(row['planned_minutes'] for row in selected), 1),
                       'confirmed_actual_minutes': _total([row['actual_minutes'] for row in confirmed]),
                       'recorded_minutes': _total([row.duration_seconds / 60 if row.duration_seconds is not None else None for row in recorded]),
                       'recorded_activities': len(recorded)})
    confirmed = [row for row in sessions if row['evidence_source'] in ('activity', 'self_reported')]
    unconfirmed = [row for row in sessions if row['status'] == 'unconfirmed']
    suggestions = []
    if unconfirmed:
        suggestions.append('Conferma l’attività o il feedback delle sedute passate; indica esplicitamente quelle saltate o sostituite da riposo.')
    if counts['modified']:
        suggestions.append('Rivedi gli scostamenti confermati dal programma prima di modificare le prossime sedute.')
    if counts['skipped']:
        suggestions.append('Rivaluta il calendario dopo le sedute saltate, senza accumulare automaticamente il lavoro arretrato.')
    if not adherence_values:
        suggestions.append('L’aderenza sarà disponibile dopo un’esecuzione confermata o una seduta dichiarata saltata.')
    return {'start': str(monday), 'end': str(end), 'today': str(today), 'counts': counts,
            'totals': {'planned_minutes': round(sum(row['planned_minutes'] for row in sessions), 1),
                       'confirmed_actual_minutes': _total([row['actual_minutes'] for row in confirmed]),
                       'recorded_minutes': _total([a.duration_seconds / 60 if a.duration_seconds is not None else None for a in activities]),
                       'planned_sessions': len(sessions), 'recorded_activities': len(activities),
                       'confirmed_sessions': len(confirmed)},
            'adherence': {'percent': round(mean(adherence_values), 1) if adherence_values else None,
                          'evaluated_sessions': len(adherence_values), 'unknown_sessions': len(unconfirmed),
                          'description': 'Aderenza alla durata delle esecuzioni confermate e delle sedute dichiarate saltate. Riposo e sedute non confermate non producono un voto.'},
            'sessions': sessions, 'unconfirmed_past': unconfirmed, 'sports': sports,
            'review': f"{counts['completed']} sedute completate, {counts['modified']} modificate, {counts['skipped']} saltate e {counts['rest']} sostituite da riposo. {len(unconfirmed)} sedute passate da confermare.",
            'suggestions': suggestions}


def recovery_progress(db, days=28):
    today = athlete_today(); start = today - timedelta(days=days - 1)
    points = series(db, start - timedelta(days=28), today)
    output = []
    for kind in ('hrv_ms', 'resting_hr_bpm', 'sleep_hours'):
        spec = METRICS[kind]
        available = [point for point in points if point['kind'] == kind and start <= date.fromisoformat(point['date']) <= today]
        latest = available[-1] if available else None
        source = latest['source'] if latest else None
        same_source = [point for point in points if point['kind'] == kind and point['source'] == source
                       and isinstance(point['value'], (int, float)) and not isinstance(point['value'], bool)
                       and math.isfinite(point['value'])
                       and (kind == 'sleep_hours' or point['value'] > 0)]
        recent_start = today - timedelta(days=6)
        recent = [point for point in same_source if recent_start <= date.fromisoformat(point['date']) <= today][-3:]
        # The reference precedes the recent period, so no observation votes in
        # both the current value and its baseline.
        baseline_points = [point for point in same_source
                           if recent_start - timedelta(days=28) <= date.fromisoformat(point['date']) < recent_start]
        baseline = median([point['value'] for point in baseline_points]) if len(baseline_points) >= 7 else None
        current = median([point['value'] for point in recent]) if len(recent) >= 3 else None
        stale = bool(latest and (today - date.fromisoformat(latest['date'])).days > 1)
        delta = current - baseline if current is not None and baseline is not None else None
        status = ('stale' if stale else 'insufficient_data' if delta is None
                  else 'above_baseline' if delta > 0 else 'below_baseline' if delta < 0 else 'unchanged')
        if delta is None:
            explanation = 'Servono almeno tre letture recenti e sette letture precedenti della stessa fonte per un confronto personale.'
        else:
            explanation = f"Mediana delle tre letture recenti {current:.2f} {spec['unit']}; riferimento personale {baseline:.2f} {spec['unit']}, stessa fonte."
        if stale:
            explanation += ' L’ultima misura non è aggiornata: il confronto non descrive necessariamente la condizione di oggi.'
        output.append({'kind': kind, 'label': spec['label'], 'unit': spec['unit'], 'source': source,
                       'baseline': round(baseline, 2) if baseline is not None else None,
                       'current': round(current, 2) if current is not None else None,
                       'recent_delta': round(delta, 2) if delta is not None else None,
                       'status': status, 'stale': stale,
                       'counts': {'baseline': len(baseline_points), 'recent': len(recent)},
                       'reference_range': reference_range(baseline_points, positive_only=kind in ('hrv_ms', 'resting_hr_bpm')),
                       'points': available, 'explanation': explanation})
    subjective = []
    for row in db.query(AthleteCheckin).filter(AthleteCheckin.date >= str(start), AthleteCheckin.date <= str(today)).order_by(AthleteCheckin.date):
        content = json.loads(row.payload_json)
        subjective.append({**content, 'date': row.date, 'source': 'self_reported'})
    comparisons = sum(row['recent_delta'] is not None and not row['stale'] for row in output)
    return {'from': str(start), 'to': str(today), 'metrics': output, 'subjective': subjective,
            'summary': f'{comparisons} confronti personali disponibili su 3. {len(subjective)} check-in soggettivi nel periodo.',
            'limits': ['Trend descrittivi: non costituiscono diagnosi o una misura complessiva del recupero.',
                       'Si confrontano soltanto letture della stessa fonte: il metodo di misura deve rimanere comparabile.',
                       'Il riferimento usa i 28 giorni prima della settimana recente; la mediana recente richiede tre letture negli ultimi sette giorni.']}


def _context_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def preparation_items(db, goal):
    gap = gap_analysis(db, goal)
    legs = json.loads(goal.legs_json)
    specifications = [{key: value for key, value in leg.items() if key != 'requirements'} for leg in legs]
    saved = {row.key: row for row in db.query(GoalPreparationItem).filter_by(goal_id=goal.id)}
    items = []

    def add(key, label, description, category, evidence=None, context=None):
        evidence = evidence or {'source': 'self_reported', 'status': 'unconfirmed', 'observed': None, 'reference': None}
        verified = evidence['source'] == 'activity_data' and evidence['status'] == 'met'
        signature = _context_hash(context if context is not None else specifications)
        previous = saved.get(key)
        fresh = bool(previous and previous.context_hash == signature)
        confirmed = bool(previous and fresh and previous.done)
        items.append({'key': key, 'label': label, 'description': description, 'category': category,
                      'done': verified or confirmed, 'notes': previous.notes if previous else '',
                      'self_confirmed': confirmed, 'verified': verified, 'evidence': evidence,
                      'confirmation_stale': bool(previous and not fresh), '_context_hash': signature})
        return fresh

    labels = {'weekly_hours': 'Continuità settimanale', 'longest_distance_km': 'Distanza della seduta lunga',
              'longest_elevation_m': 'Dislivello della seduta lunga', 'terrain_confirmed': 'Specificità del terreno',
              'altitude_confirmed': 'Esperienza e preparazione alla quota'}
    for index, (leg, observed_leg) in enumerate(zip(legs, gap['legs'])):
        canonical, sport_label = SPORTS.get(sport_key(leg['sport']), (leg['sport'], leg['sport']))
        for requirement in observed_leg['requirements']:
            kind = requirement['kind']; qualitative = kind.endswith('_confirmed')
            score = requirement['score']; missing = score is None
            status = 'insufficient_data' if missing else 'met' if score >= 100 else 'gap'
            if qualitative:
                description = ('Verifica esperienze e condizioni di quota separatamente dal dislivello; annota ciò che hai effettivamente provato.'
                               if kind == 'altitude_confirmed' else 'Verifica esperienza sul terreno previsto e annota le prove effettuate.')
            elif missing:
                description = 'Configura il riferimento mancante o registra dati della disciplina per rendere verificabile questo requisito.'
            else:
                description = f"Copertura {score:.1f}% del riferimento configurato. Rivedi il divario e la progressione con il tuo piano."
            evidence = {'source': 'goal_self_confirmation' if qualitative else 'activity_data',
                        'status': status, 'observed': requirement['observed'], 'reference': requirement['reference']}
            fresh_override = add(f'leg_{index}_{kind}', f'{sport_label} · {labels[kind]}', description,
                                 'specificity' if qualitative else 'capacity', evidence,
                                 [specifications[index], kind, requirement['reference']])
            if qualitative and requirement['observed'] is True and not fresh_override:
                # A goal checkbox remains self-confirmation, never verification
                # from an activity or an assertion about acclimatization.
                items[-1]['self_confirmed'] = True
                items[-1]['done'] = True
        add(f'leg_{index}_technique', f'{sport_label} · Tecnica',
            'Annota una verifica della tecnica nelle condizioni previste dalla frazione e gli aspetti da curare.',
            'technique', context=specifications[index])
    if len({sport_key(leg['sport']) for leg in legs}) > 1:
        add('bricks', 'Transizioni e sedute combinate',
            'Verifica l’organizzazione delle transizioni e la pratica delle discipline consecutive; annota le prove già svolte.', 'technique')
    for key, label, text in [
        ('equipment', 'Attrezzatura', 'Controlla e prova attrezzatura, abbigliamento e materiale obbligatorio nelle condizioni previste.'),
        ('nutrition', 'Alimentazione in gara', 'Annota la strategia alimentare provata, la tollerabilità e gli aspetti da verificare con il professionista che ti segue.'),
        ('hydration', 'Idratazione', 'Annota disponibilità dei ristori, condizioni previste e strategia già provata, senza dedurre quantità dai soli dati di allenamento.'),
        ('pacing', 'Gestione del ritmo', 'Definisci e prova una gestione del ritmo per le frazioni e il terreno previsti, usando riferimenti personali verificati.'),
    ]:
        add(key, label, text, 'logistics')
    return gap, items


def preparation_signature(goal):
    content = {'id':goal.id,'name':goal.name,'date':goal.date,'legs':json.loads(goal.legs_json)}
    return hashlib.sha256(json.dumps(content,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def goal_preparation(db, goal):
    gap, items = preparation_items(db, goal)
    for item in items:
        item.pop('_context_hash', None)
    verified = sum(item['verified'] for item in items)
    self_confirmed = sum(item['self_confirmed'] and not item['verified'] for item in items)
    return {'goal_id': goal.id, 'context_signature': preparation_signature(goal), 'gap_score': gap['score'], 'gap_status': gap['status'], 'items': items,
            'summary': f'{verified} requisiti supportati dai dati; {self_confirmed} voci confermate da te. Le conferme non cambiano l’indice gara.',
            'limits': ['Una spunta è una dichiarazione personale, non una certificazione tecnica o fisiologica.',
                       'Le voci operative non prescrivono dosi, quantità o prestazioni.',
                       'Una modifica alle specifiche dell’obiettivo richiede una nuova conferma delle voci interessate.']}
