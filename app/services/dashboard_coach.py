"""Descriptive coaching of recorded work; distinct from plan execution grading."""
from statistics import median


def judgment(label, tone, explanation, action):
    return {'scope':'recorded_training', 'score':None, 'judgment':label, 'tone':tone,
            'explanation':explanation, 'actions':[action], 'evidence':[explanation],
            'limits':['Confronto descrittivo con lo storico personale; non certifica prestazione o recupero.'],
            'rules_version':'recorded-load-1'}


def weekly_assessments(current, history, condition):
    latest = {m['kind']:m['latest']['value'] for m in condition if m.get('latest') and not m.get('stale') and m['latest'].get('source') != 'local_estimate'}
    tsb = latest.get('tsb')
    result = {}
    for key, name, unit in [('hours','Volume','ore'),('activities','Frequenza','attività'),('elevation_m','Dislivello','m'),('load','Carico','punti')]:
        value = current.get(key)
        complete = True
        baseline = [row[key] for row in history if row.get(key) is not None and row.get('activities',0)>0]
        if value is None:
            result[key] = judgment('Dato parziale' if key == 'elevation_m' and current.get('elevation_known_activities',0) else 'Non misurabile', 'unknown',
                f'{name}: mancano misure per un totale completo. Un dato assente non equivale a zero.',
                'Aggiorna i dati; valuta soltanto il totale effettivamente registrato.')
            if key == 'elevation_m' and current.get('elevation_known_activities',0):
                result[key]['explanation'] = f"Almeno {current['elevation_known_m']:g} m registrati; dislivello assente per {current['elevation_missing_activities']} attività. Il totale è parziale."
            continue
        if len(baseline) < 2 or median(baseline) <= 0 or not complete:
            result[key] = judgment('Storico ancora breve', 'neutral', f'{name}: {value:g} {unit} registrati. Servono almeno due periodi precedenti con dati completi e attività per un confronto personale.',
                'Costruisci continuità; non aumentare il lavoro soltanto per raggiungere un numero.')
            continue
        reference = median(baseline); change = (value / reference - 1) * 100
        note = f'{name}: {value:g} {unit}, {change:+.0f}% rispetto alla mediana dei periodi precedenti sugli stessi giorni della settimana.'
        if value > reference * 1.3:
            result[key] = judgment(f'{name} elevato rispetto al solito', 'critical' if key == 'load' else 'caution', note,
                'Verifica se l’aumento era programmato; evita di aggiungere lavoro per compensare sedute perse.')
        elif value < reference * .7:
            result[key] = judgment(f'{name} ridotto', 'neutral', note,
                'Può essere coerente con scarico o disponibilità ridotta. Non recuperare automaticamente il volume mancante.')
        else:
            result[key] = judgment(f'{name} coerente con lo storico', 'good', note,
                'Se recupero e sensazioni lo consentono, mantieni il programma. Non aggiungere lavoro soltanto perché il carico è stabile.')
        result[key]['reference'] = reference; result[key]['change_percent'] = round(change,1)
    if tsb is not None and tsb <= -25:
        result['load'] = judgment('Carico da alleggerire' if tsb <= -30 else 'Carico da controllare', 'critical' if tsb <= -30 else 'caution',
            f'Il bilancio del carico è {tsb:.1f}: il lavoro recente supera il riferimento cronico. La regola del Coach segnala fatica relativa elevata.',
            'Rivaluta la prossima seduta e privilegia un lavoro facile o il recupero previsto dal piano.')
    return result


def recorded_overview(current, assessments):
    load = assessments['load']
    if load['tone'] in ('critical','caution'):
        return {**load, 'explanation':f"{current['activities']} attività registrate. {load['explanation']}"}
    if current['activities'] == 0:
        return judgment('Nessuna attività registrata questa settimana', 'neutral',
            'Non risultano registrazioni nel periodo selezionato; non posso dedurre che tu non ti sia allenato.',
            'Sincronizza le attività prima di modificare il piano.')
    if current.get('load') is not None and load['judgment'] != 'Storico ancora breve':
        return {**load, 'explanation':f"{current['activities']} attività registrate. {load['explanation']}"}
    return judgment('Lavoro registrato, progressione da verificare', 'neutral',
        f"Sono registrate {current['activities']} attività e {current['hours']:g} ore." if current.get('hours') is not None else f"Sono registrate {current['activities']} attività, con durata incompleta.",
        'Mantieni una distribuzione sostenibile delle sedute; lo storico non basta ancora a definire un carico abituale.')


def contextual_assessment(current, assessments, condition, recovery, goal):
    """One evidence-based verdict shared by the dashboard and session decision."""
    result = recorded_overview(current, assessments)
    score = recovery.get('readiness')
    signals = {m['kind']: m['latest']['value'] for m in condition
               if m.get('latest') and not m.get('stale') and m['latest'].get('source') != 'local_estimate'}
    tsb = signals.get('tsb')
    excessive = assessments['load']['tone'] in ('critical', 'caution')
    journal = current.get('subjective') or {}
    subjective_warning = bool(journal.get('pain') or (journal.get('fatigue') or 0) >= 4 or (journal.get('soreness') or 0) >= 4)
    restriction = current.get('restrictions') or {}
    rest = restriction.get('training_paused', False) or subjective_warning or (score is not None and score < 35) or (tsb is not None and tsb <= -30)
    warnings = []
    for metric in condition:
        band = metric.get('reference_range')
        value = signals.get(metric['kind'])
        if band and value is not None and (value < band['lower'] or value > band['upper']):
            warnings.append(metric.get('label', metric['kind']))
    dense = current.get('sessions_3d', 0) >= 3 or current.get('recent_heavy_session', False)
    reduce = restriction.get('mode') == 'easy' or excessive or dense or len(warnings) >= 2 or (score is not None and score < 50)
    policy = {'mode': 'rest' if rest else 'easy' if reduce else 'normal',
              'max_duration_min': 0 if rest else 30 if reduce else None,
              'max_rpe': 0 if rest else 3 if reduce else None}
    if restriction.get('mode') == 'easy' and not rest:
        policy['max_duration_min'] = min(policy['max_duration_min'] or 20, 20)
        policy['max_rpe'] = min(policy['max_rpe'] or 2, 2)
    evidence = [result['explanation'], *restriction.get('reasons', [])]
    if tsb is not None:
        evidence.append(f'Bilancio del carico {tsb:.1f}: questo segnale non annulla un aumento marcato della settimana.')
    if score is not None:
        evidence.append(f'Recupero stimato {score:.0f}/100: ' + ('non sostiene una nuova seduta impegnativa.' if score < 65 else 'favorevole, ma non giustifica da solo altro volume.'))
    else:
        evidence.append('Recupero non confermato: non considero i dati mancanti un via libera.')
    if dense:
        evidence.append('Tre sedute negli ultimi tre giorni o una seduta recente molto impegnativa: prima di aggiungere lavoro serve spazio per assimilare lo stimolo.')
    if warnings:
        evidence.append('Fuori dal tuo intervallo abituale: ' + ', '.join(warnings) + '. Non ignoro questi scostamenti perché il bilancio del carico sembra regolare.')
    if recovery.get('confidence') is not None:
        evidence.append(f"Copertura dei segnali di recupero {recovery['confidence']:g}%: il giudizio usa i dati presenti, senza inventare quelli mancanti.")
    goal_name = (goal or {}).get('name') or (goal or {}).get('event_name')
    if subjective_warning:
        evidence.append('Il check-in segnala dolore o fatica marcata: oggi ha priorità sul programma e sulla gara.')
    if goal_name:
        evidence.append(f'Obiettivo {goal_name}: conta arrivare con continuità e capacità specifiche, non accumulare carico oggi a ogni costo.')
    if goal and goal.get('legs'):
        from datetime import date
        days = (date.fromisoformat(goal['date']) - date.fromisoformat(current['date'])).days if goal.get('date') and current.get('date') else None
        if days is not None:
            evidence.append(f'Mancano {days} giorni alla gara.' if days >= 0 else 'La gara configurata è passata: aggiorna il prossimo obiettivo prima di impostare una progressione specifica.')
        names = {'cycling':('bike','bici'), 'running':('run','corsa'), 'swimming':('swim','nuoto')}
        for leg in goal['legs']:
            key, name = names[leg['sport']]
            count = current.get('sports',{}).get(key)
            if count is not None:
                evidence.append(f"Gara: {leg['distance_km']:g} km di {name}; questa settimana {count} attività registrate." + (' La specificità manca nel periodo: va ricostruita nel piano, senza compensarla con una seduta extra oggi.' if count == 0 else ' Il solo numero di sedute non dimostra ancora la capacità di coprire la distanza.'))
            if leg.get('elevation_gain_m'):
                evidence.append(f"La frazione richiede {leg['elevation_gain_m']:g} m di salita: il dislivello totale multisport non prova la preparazione specifica a questa frazione.")
    action = ('Oggi riposo: nessuna nuova seduta. Rivaluta recupero e fatica prima di riprendere.' if rest else
              f"Oggi al massimo {policy['max_duration_min']} minuti facili, RPE massimo {policy['max_rpe']}/10, senza ripetute né dislivello da inseguire. Non aggiungere questa proposta a una seduta già svolta." if reduce else
              'Mantieni la seduta prevista nelle discipline della gara; aumenta lo stimolo solo se recupero, disponibilità e risposta recente lo consentono.')
    if rest or reduce:
        result = {**result, 'judgment': 'Recupero prioritario' if rest else 'Carico da alleggerire oggi', 'tone': 'critical' if excessive or rest else 'caution'}
    result.update(explanation=' '.join(evidence), evidence=evidence, actions=[action], session_policy=policy)
    metric_assessments = {}
    for m in condition:
        latest = m.get('latest')
        if not latest or m.get('stale') or latest.get('source') == 'local_estimate':
            continue
        kind = m['kind']
        reason = f"{m.get('label', kind)}: {latest['value']:.1f}. "
        labels = {'ctl':'Base allenante da consolidare', 'atl':'Fatica da leggere nel carico complessivo',
                  'tsb':'Bilancio del carico, non via libera', 'sleep_hours':'Sonno da valutare insieme al recupero',
                  'hrv_ms':'Risposta autonoma nel contesto', 'resting_hr_bpm':'Risposta cardiaca nel contesto'}
        if kind in ('sleep_hours','hrv_ms','resting_hr_bpm'):
            band = m.get('reference_range')
            if band:
                outside = latest['value'] < band['lower'] or latest['value'] > band['upper']
                reason += f"Il tuo riferimento abituale è {band['lower']:.1f}–{band['upper']:.1f}: " + ('oggi sei fuori intervallo. ' if outside else 'oggi rientri nell’intervallo. ')
            else:
                reason += 'Lo storico non permette ancora un confronto personale affidabile. '
            reason += 'Un singolo segnale favorevole non compensa una settimana eccessiva. '
        elif kind == 'ctl':
            reason += 'Non scambio il carico cronico per una misura della prestazione: occorre continuità nelle discipline e nelle distanze della gara. '
        elif kind == 'tsb':
            reason += 'Un bilancio vicino a zero può convivere con una settimana in aumento: non lo uso per autorizzare automaticamente altro volume. '
        elif kind == 'atl':
            reason += 'Il lavoro recente va confrontato con la base cronica e la progressione settimanale, insieme alla risposta del recupero. '
        metric_assessments[kind] = {**result, 'judgment': labels.get(kind, result['judgment']), 'explanation': reason, 'evidence': [reason, *result['evidence'][:3]], 'contextual_tone': result['tone']}
    metric_assessments['readiness'] = {**result, 'explanation': f'Recupero stimato {score:.0f}/100.' if score is not None else 'Recupero non ancora stimabile.', 'evidence': result['evidence'][:3]}
    return result, metric_assessments
