"""Explainable assessments; no external language model or implicit workout matching."""
import json
import math
from datetime import timedelta
from statistics import mean
from app.db.models import Activity, Workout
from app.db.experience_models import SessionFeedback
from app.services.athlete_clock import athlete_today, activity_day, as_utc_naive, utc_now_naive
from app.services.evaluation import evaluate_workout
from app.services.matching import activity_to_actual

VERSION = 'rules-1'


def expected_benefit(sport):
    from app.services.training_load import sport_key
    return {
        'swim': 'Coordinazione del gesto e continuità respiratoria; il lavoro aerobico sostiene la capacità di mantenere lo sforzo in acqua.',
        'bike': 'Stimolo alla resistenza specifica e all’economia di pedalata; intensità e recuperi determinano il contributo aerobico della seduta.',
        'run': 'Stimolo alla resistenza specifica e all’economia di corsa; la progressione richiede tolleranza muscolare e recupero adeguati.',
        'strength': 'Pratica tecnica e stimolo neuromuscolare; gli adattamenti di forza dipendono da esercizi, carichi e continuità.',
        'recovery': 'Riduzione dello stimolo allenante per consentire il recupero; nessun adattamento è garantito dalla singola seduta.',
    }.get(sport_key(sport), 'Pratica della disciplina secondo lo scopo della seduta; lo stimolo dipende da intensità, durata e recuperi.')


def grade(score):
    if score is None: return 'Non valutabile'
    return 'Eccellente' if score >= 90 else 'Buono' if score >= 75 else 'Adeguato' if score >= 60 else 'Insufficiente' if score >= 40 else 'Scarso'


def assess(db, activity):
    feedback = db.query(SessionFeedback).filter_by(activity_id=activity.id).first()
    workout = db.get(Workout, feedback.workout_id) if feedback else None
    evidence = []; score = None; evaluation = None
    if workout:
        content = json.loads(feedback.payload_json)
        actual = {**activity_to_actual(activity), 'rpe': content.get('rpe')}
        planned = json.loads(workout.payload_json)
        evaluation = evaluate_workout(planned, actual)
        # The legacy evaluator assumes RPE 4 if the plan has no target. That
        # fallback is not evidence for an objective execution assessment.
        if planned.get('expected_rpe') is None:
            evaluation['rpe_score'] = None
        # Duration alone cannot produce a comprehensive judgment.
        duration_available = (activity.duration_seconds is not None and math.isfinite(activity.duration_seconds)
                              and activity.duration_seconds >= 0 and math.isfinite(workout.duration_min)
                              and workout.duration_min > 0)
        if duration_available and (evaluation['intensity_control'] is not None or evaluation['rpe_score'] is not None):
            # Penalize both overshooting and undershooting the planned duration.
            adherence = max(0, 100 - abs((activity.duration_seconds or 0) / 60 / max(workout.duration_min, 1) - 1) * 100)
            components = [adherence] + [evaluation[k] for k in ('intensity_control', 'rpe_score') if evaluation[k] is not None]
            score = round(mean(components), 1)
        if activity.duration_seconds is not None:
            evidence.append(f"Durata eseguita {activity.duration_seconds / 60:.0f} min; prevista {workout.duration_min:.0f} min.")
        else:
            evidence.append(f"Durata eseguita non disponibile; prevista {workout.duration_min:.0f} min.")
        if content.get('rpe') is not None: evidence.append(f"Sforzo dichiarato: RPE {content['rpe']}/10.")
    limits = ['Una valutazione di esecuzione non misura direttamente gli adattamenti fisiologici.']
    if score is None: limits.append('Occorrono un abbinamento confermato, durata eseguita e prevista valida, e dati di intensità o feedback RPE.')
    elif evaluation['intensity_control'] is None: limits.append('Controllo dell’intensità non verificato: mancano dati per fase.')
    return {'activity_id': activity.id, 'score': score, 'judgment': grade(score), 'evidence': evidence,
            'limits': limits, 'actions': ['Rivedi con precisione durata e intensità del programma.'] if score is not None and score < 60 else [],
            'benefits': expected_benefit(activity.sport) + ' Benefici attesi, non adattamenti misurati.',
            'rules_version': VERSION}


def overview(db, day=None):
    today = day or athlete_today(); start = today - timedelta(days=27)
    now = utc_now_naive()
    activities = [a for a in db.query(Activity).all()
                  if as_utc_naive(a.start_time) <= now and start <= activity_day(a.start_time) <= today]
    assessed = [assess(db, a) for a in activities]
    scores = [a['score'] for a in assessed if a['score'] is not None]
    execution_score = round(mean(scores), 1) if len(scores) >= 3 else None
    confirmed = {row.workout_id for row in db.query(SessionFeedback).all()}
    unresolved = db.query(Workout).filter(Workout.date >= str(start), Workout.date < str(today),
                                         Workout.status == 'planned').all()
    unresolved = [row for row in unresolved if row.id not in confirmed]
    # Unreviewed past sessions are an evidence gap, not proof they were missed.
    score = None if unresolved else execution_score
    judgment = 'Non valutabile' if score is None else 'Corretta' if score >= 75 else 'Mediamente Corretta' if score >= 60 else 'Scorretta'
    upcoming = db.query(Workout).filter(Workout.date >= str(today), Workout.status == 'planned').order_by(Workout.date).limit(7).all()
    explanations = []
    for w in upcoming:
        payload = json.loads(w.payload_json)
        explanations.append({'id': w.id, 'date': w.date, 'name': w.name,
                             'reason': payload.get('reason') or 'Seduta prevista dal piano personale.',
                             'expected_benefit': expected_benefit(w.sport),
                             'limits': 'Adattamento atteso, non misurato.'})
    evidence = [f'{len(scores)} attività valutabili su {len(activities)} negli ultimi 28 giorni.']
    limits = ['Servono almeno tre sedute valutabili. La valutazione riguarda l’esecuzione del piano, non la sua validazione fisiologica.']
    if unresolved:
        evidence.append(f'{len(unresolved)} sedute programmate nei giorni passati senza conferma di esecuzione.')
        limits.append('Le sedute senza conferma non sono considerate automaticamente saltate; impediscono un giudizio globale affidabile.')
    actions = (['Rivedi le sedute programmate nei giorni passati: conferma esecuzione, riposo o mancato svolgimento.'] if unresolved else
               ['Riduci gli scostamenti dal programma e rivaluta la distribuzione del carico.'] if score is not None and score < 75 else [])
    missing = []
    if len(scores) < 3:
        missing.append(f'{len(scores)} attività valutabili su {len(activities)} registrate: ne servono almeno 3 abbinate a una seduta, con durata e dati di intensità o feedback RPE.')
    if unresolved:
        missing.append(f'{len(unresolved)} sedute passate sono ancora da confermare: non vengono considerate automaticamente saltate.')
    return {'score': score, 'execution_score': execution_score, 'judgment': judgment, 'rules_version': VERSION,
            'unavailable_reason': ' '.join(missing) if score is None else None,
            'evaluated_activities': len(scores), 'recorded_activities': len(activities),
            'evidence': evidence, 'limits': limits, 'unconfirmed_past_sessions': len(unresolved),
            'actions': actions,
            'upcoming': explanations}
