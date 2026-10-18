from __future__ import annotations

from app.services.training_load import sport_key


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def _data_confidence(readiness, historical, adaptation):
    readiness_c = float(readiness.get('confidence') or 0)
    hist_c = float(historical.get('confidence') or 0)
    adapt_c = float(adaptation.get('confidence') or 0)
    # Recovery data are the most important for daily decisions; history is secondary.
    if readiness_c <= 0:
        return round(_clamp(hist_c * 0.65 + adapt_c * 0.15), 1)
    return round(_clamp(readiness_c * 0.60 + hist_c * 0.25 + adapt_c * 0.15), 1)


def _choose_sport(state):
    acute = state['acute']
    loads = acute['7d']['sports']
    last = state.get('last_session') or {}
    candidates = ['cycling', 'running', 'swimming']
    # Prefer sports with less recent load, while avoiding immediate repetition after a hard session.
    sport_load = {s: float(loads.get({'cycling':'bike','running':'run','swimming':'swim'}[s], 0)) for s in candidates}
    ranked = sorted(candidates, key=lambda s: sport_load[s])
    if last.get('sport') in ranked and last.get('load', 0) >= 100 and len(ranked) > 1:
        ranked.remove(last['sport']); ranked.append(last['sport'])
    return ranked[0]


def decide_training(state, phase='base', requested_sport=None):
    readiness = state['readiness']
    r = readiness.get('readiness')
    tsb = float(state['fitness'].get('tsb') or 0)
    stress = float(state.get('training_stress_index') or 0)
    acute3 = state['acute']['3d']
    acute7 = state['acute']['7d']
    last = state.get('last_session') or {}
    adaptation = state.get('adaptation') or {}
    hist = state.get('historical_capacity') or {}
    sport = requested_sport or _choose_sport(state)
    sport_key_name = {'cycling':'cycling','running':'running','swimming':'swimming'}[sport]
    hist_sport = hist.get('sports', {}).get(sport_key_name, {})
    hist_conf = float(hist_sport.get('confidence') or 0)

    hard_stop = []
    caution = []
    if r is not None and r < 35: hard_stop.append('readiness_very_low')
    if tsb <= -30: hard_stop.append('high_fatigue')
    if acute3['sessions'] >= 3: hard_stop.append('three_sessions_in_three_days')
    if last.get('load', 0) >= 180 and (last.get('hours_since') or 999) < 36: hard_stop.append('recent_high_load')

    if hard_stop:
        recommendation = 'recovery'
        intensity = 'low'
        reason = 'Recupero prioritario: ' + ', '.join(hard_stop).replace('_', ' ') + '.'
    elif r is not None and r < 50:
        recommendation = 'easy'
        intensity = 'low'
        caution.append('readiness_below_50')
        reason = 'Recupero incompleto: mantenere il lavoro aerobico facile e limitare l intensità.'
    elif stress >= 65 or (acute7['sessions'] >= 5 and tsb < 0):
        recommendation = 'endurance'
        intensity = 'moderate'
        caution.append('acute_load_high')
        reason = 'Il carico recente è significativo: stimolo aerobico controllato senza ulteriore lavoro intenso.'
    elif adaptation.get('action') == 'reduce_next_similar' and float(adaptation.get('confidence') or 0) >= 60:
        recommendation = 'easy'
        intensity = 'low'
        caution.append('negative_training_response')
        reason = 'La risposta recente agli allenamenti suggerisce di ridurre temporaneamente lo stimolo simile.'
    elif adaptation.get('action') == 'consider_progression' and float(adaptation.get('confidence') or 0) >= 60 and tsb > -20:
        recommendation = 'progression'
        intensity = 'moderate'
        reason = 'Lo storico delle risposte supporta una progressione, compatibile con carico e recupero attuali.'
    elif tsb >= 10 and r is not None and r >= 65:
        recommendation = 'quality'
        intensity = 'high'
        reason = 'Carico recente e recupero consentono uno stimolo qualitativo, con intensità controllata.'
    else:
        recommendation = 'endurance'
        intensity = 'moderate'
        reason = 'Stato complessivo compatibile con un lavoro aerobico controllato.'

    # Missing recovery data must reduce confidence, never create a false green light.
    if r is None:
        caution.append('recovery_data_missing')
        if recommendation in ('quality', 'progression'):
            recommendation = 'easy'
            intensity = 'low'
        reason += ' Dati di recupero insufficienti: evitare di interpretare l assenza di dati come recupero completo.'
    elif recommendation in ('quality', 'progression') and (float(readiness.get('confidence') or 0) < 40 or hist_conf < 50):
        recommendation = 'easy'
        intensity = 'low'
        caution.append('insufficient_evidence_for_intensity')
        reason = 'Recupero o storico poco documentati: mantieni uno stimolo facile prima di aumentare l’intensità.'

    journal = state.get('subjective') or {}
    if journal.get('pain') or (journal.get('fatigue') or 0) >= 4 or (journal.get('soreness') or 0) >= 4:
        recommendation = 'recovery'
        intensity = 'low'
        caution.append('subjective_recovery_warning')
        reason = 'Il check-in segnala dolore o fatica elevata: rivedi la seduta prima di iniziare e privilegia il recupero.'
    elif (journal.get('sleep_quality') or 5) <= 2 and recommendation in ('quality', 'progression'):
        recommendation = 'easy'
        intensity = 'low'
        caution.append('poor_sleep_quality')
        reason = 'La qualità del sonno segnalata suggerisce di ridurre oggi lo stimolo.'

    context = state.get('coach_context') or {}
    policy = context.get('session_policy') or {}
    if policy.get('mode') in ('rest', 'easy'):
        recommendation = 'recovery' if hard_stop or recommendation == 'recovery' or policy['mode'] == 'rest' else 'easy'
        intensity = 'low'
        reason = context['judgment'] + '. ' + context['evidence'][0] + ' ' + context['actions'][0]
        caution.append('contextual_load_reduction')
    elif context:
        reason += ' ' + context['explanation']

    confidence = _data_confidence(readiness, hist, adaptation)
    if r is None:
        confidence = min(confidence, 60.0)

    return {
        'recommendation': recommendation,
        'sport': sport,
        'intensity': intensity,
        'confidence': round(confidence, 1),
        'phase': phase,
        'reasons': [reason],
        'hard_stops': hard_stop,
        'cautions': caution,
        'inputs': {
            'readiness': r,
            'readiness_confidence': float(readiness.get('confidence') or 0),
            'tsb': round(tsb, 1),
            'training_stress_index': stress,
            'load_24h': state['acute']['24h']['load'],
            'load_3d': acute3['load'],
            'load_7d': acute7['load'],
            'sessions_3d': acute3['sessions'],
            'sessions_7d': acute7['sessions'],
            'historical_sport_confidence': hist_conf,
            'adaptation': adaptation.get('action', 'none'),
            'adaptation_confidence': float(adaptation.get('confidence') or 0),
        },
        'capacity': hist_sport,
        'session_policy': policy,
    }
