"""Explainable assessments; no external language model or implicit workout matching."""
from datetime import timedelta
from statistics import mean
from sqlalchemy import or_
from app.db.models import Activity, Workout
from app.db.experience_models import SessionFeedback
from app.db.progress_models import WorkoutResolution
from app.services.athlete_clock import athlete_today, activity_day, as_utc_naive, utc_now_naive, day_start_utc
from app.services.evaluation import evaluate_workout
from app.services.execution_evidence import finite_number, safe_object, usable_feedback

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
    if finite_number(score) is None: return 'Non valutabile'
    return 'Eccellente' if score >= 90 else 'Buono' if score >= 75 else 'Adeguato' if score >= 60 else 'Insufficiente' if score >= 40 else 'Scarso'


def _assess_activity(activity, workout, feedback, *, today, now):
    evidence = []; score = None; evaluation = None; issue = None
    if workout and feedback:
        checked = usable_feedback(workout, feedback, activity, today=today, now=now)
        if checked['usable']:
            content = checked['content']
            actual = {'duration_min': checked['minutes'], 'rpe': content.get('rpe')}
            planned = {**checked['planned'], 'duration_min': workout.duration_min}
            evaluation = evaluate_workout(planned, actual)
            duration_available = checked['minutes'] is not None
            if duration_available and (evaluation['intensity_control'] is not None or evaluation['rpe_score'] is not None):
                ratio = finite_number(checked['minutes'] / workout.duration_min)
                if ratio is not None:
                    adherence = max(0, 100 - abs(ratio - 1) * 100)
                    components = [adherence] + [evaluation[k] for k in ('intensity_control', 'rpe_score') if evaluation[k] is not None]
                    score = round(mean(components), 1)
            if duration_available:
                evidence.append(f"Durata eseguita {checked['minutes']:.0f} min; prevista {workout.duration_min:.0f} min.")
            else:
                evidence.append(f"Durata eseguita non disponibile; prevista {workout.duration_min:.0f} min.")
            if content.get('rpe') is not None:
                evidence.append(f"Sforzo dichiarato: RPE {content['rpe']}/10.")
        else:
            issue = checked['issue']
    elif feedback:
        issue = 'missing_workout'
    limits = ['Una valutazione di esecuzione non misura direttamente gli adattamenti fisiologici.']
    if score is None:
        limits.append('Occorrono un abbinamento confermato, durata eseguita e prevista valida, e dati di intensità o feedback RPE.')
    elif evaluation['intensity_control'] is None:
        limits.append('Controllo dell’intensità non verificato: mancano dati per fase.')
    if issue:
        limits.append('Il collegamento o il feedback salvato non fornisce una conferma utilizzabile: rivedi la seduta.')
    return {'activity_id': activity.id, 'score': score, 'judgment': grade(score), 'evidence': evidence,
            'evidence_issue': issue,
            'limits': limits, 'actions': ['Rivedi con precisione durata e intensità del programma.'] if score is not None and score < 60 else [],
            'benefits': expected_benefit(activity.sport) + ' Benefici attesi, non adattamenti misurati.',
            'rules_version': VERSION}


def assess(db, activity):
    feedback = db.query(SessionFeedback).filter_by(activity_id=activity.id).first()
    workout = db.get(Workout, feedback.workout_id) if feedback else None
    return _assess_activity(activity, workout, feedback, today=athlete_today(), now=utc_now_naive())


def overview(db, day=None):
    today = day or athlete_today(); start = today - timedelta(days=27)
    now = utc_now_naive()
    activities = db.query(Activity).filter(Activity.start_time >= day_start_utc(start),
        Activity.start_time < day_start_utc(today + timedelta(days=1)), Activity.start_time <= now).order_by(Activity.start_time, Activity.id).all()
    past = db.query(Workout).filter(Workout.date >= str(start), Workout.date < str(today),
        ~Workout.status.in_(['cancelled', 'superseded'])).all()
    upcoming = db.query(Workout).filter(Workout.date >= str(today), Workout.status == 'planned').order_by(Workout.date, Workout.id).limit(7).all()
    ids = [row.id for row in activities]; past_ids = [row.id for row in past]
    feedback_rows = db.query(SessionFeedback).filter(or_(SessionFeedback.activity_id.in_(ids), SessionFeedback.workout_id.in_(past_ids))).all()
    feedback_by_activity = {row.activity_id: row for row in feedback_rows if row.activity_id is not None}
    feedback_by_workout = {row.workout_id: row for row in feedback_rows}
    workout_ids = [row.workout_id for row in feedback_rows]
    workouts = {row.id: row for row in past + upcoming}
    missing_workout_ids = set(workout_ids) - set(workouts)
    if missing_workout_ids:
        workouts.update((row.id, row) for row in db.query(Workout).filter(Workout.id.in_(missing_workout_ids)))
    activity_map = {row.id: row for row in activities}
    missing_activity_ids = {row.activity_id for row in feedback_rows if row.activity_id is not None} - set(activity_map)
    if missing_activity_ids:
        activity_map.update((row.id, row) for row in db.query(Activity).filter(Activity.id.in_(missing_activity_ids)))
    resolutions = {row.workout_id: row for row in db.query(WorkoutResolution).filter(
        WorkoutResolution.workout_id.in_(past_ids), WorkoutResolution.undone_at.is_(None))}
    assessed = [_assess_activity(a, workouts.get(feedback_by_activity[a.id].workout_id) if a.id in feedback_by_activity else None,
        feedback_by_activity.get(a.id), today=today, now=now) for a in activities]
    scores = [row['score'] for row in assessed if row['score'] is not None]
    execution_score = round(mean(scores), 1) if len(scores) >= 3 else None
    unresolved = []
    for row in past:
        feedback = feedback_by_workout.get(row.id)
        checked = usable_feedback(row, feedback, activity_map.get(feedback.activity_id) if feedback else None, today=today, now=now)
        resolution = resolutions.get(row.id)
        resolved = resolution is not None and resolution.kind in ('skipped', 'rest')
        if not checked['usable'] and not resolved and row.status != 'skipped':
            unresolved.append(row)
    # Unreviewed sessions and unusable feedback are evidence gaps, never
    # automatically missed sessions, even if a stale status says completed.
    score = None if unresolved else execution_score
    judgment = 'Non valutabile' if score is None else 'Corretta' if score >= 75 else 'Mediamente Corretta' if score >= 60 else 'Scorretta'
    explanations = []
    for w in upcoming:
        payload = safe_object(w.payload_json) or {}
        reason = payload.get('reason')
        explanations.append({'id': w.id, 'date': w.date, 'name': w.name,
                             'reason': reason if isinstance(reason, str) and reason.strip() else 'Seduta prevista dal piano personale.',
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
