"""Date-bounded availability, interpreted locally and applied only after preview."""
import hashlib
import json
import re
import unicodedata
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.services.athlete_clock import athlete_today
from app.services.coach_dates import resolve as resolve_dates
from app.db.training_models import GymProgress

PERIODS = {'morning': ('Mattina', '06:00–12:00', 360),
           'afternoon': ('Pomeriggio', '12:00–18:00', 360),
           'evening': ('Sera', '18:00–22:00', 240)}
SPORTS = {'swimming': 'Nuoto', 'cycling': 'Bici', 'running': 'Corsa', 'strength': 'Palestra'}
SPORT_WORDS = {'swimming': r'\b(nuoto|nuotare|nuotarmi|piscina|swim|swimming)\b',
               'cycling': r'\b(bici|bicicletta|ciclismo|rulli|bike|cycling)\b',
               'running': r'\b(corsa|correre|running|run)\b',
               'strength': r'\b(palestra|pesi|forza|strength)\b'}


def canonical_sport(sport):
    from app.services.training_load import sport_key
    key = sport_key(sport)
    return {'bike':'cycling','run':'running','swim':'swimming'}.get(key,key)


class TimeConstraint(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: date
    end: date
    blocked: list[Literal['morning', 'afternoon', 'evening']] = Field(default_factory=list, max_length=3)
    sports: list[Literal['swimming', 'cycling', 'running', 'strength']] = Field(default_factory=list, max_length=4)
    weekdays: list[Annotated[int, Field(strict=True, ge=0, le=6)]] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)

    @model_validator(mode='after')
    def valid_range(self):
        if self.end < self.start or (self.end-self.start).days > 730:
            raise ValueError('Indica un periodo ordinato di massimo due anni.')
        if (not self.blocked and not self.sports) or len(set(self.sports)) != len(self.sports):
            raise ValueError('Indica fasce o discipline valide da sospendere, senza duplicati.')
        if len(set(self.blocked)) != len(self.blocked) or len(set(self.weekdays)) != len(self.weekdays) or any(isinstance(d, bool) or d not in range(7) for d in self.weekdays):
            raise ValueError('Giorni e fasce devono essere validi e senza duplicati.')
        return self


def windows(profile, day, sport=None):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    blocked = set()
    for rule in profile.get('time_constraints', []):
        if rule.get('sports') and (sport is None or canonical_sport(sport) not in rule['sports']):
            continue
        if rule['start'] <= str(day) <= rule['end'] and day.weekday() in rule['weekdays']:
            blocked.update(rule['blocked'] or PERIODS)
    allowed = [key for key in PERIODS if key not in blocked]
    return {'allowed': allowed, 'restricted': bool(blocked), 'conflict': not allowed,
            'label': 'Giorno non disponibile' if not allowed else ' / '.join(f'{PERIODS[key][0]} {PERIODS[key][1]}' for key in allowed)}


def available_minutes(profile, day, sport=None):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return min(profile['availability'][day.weekday()], sum(PERIODS[key][2] for key in windows(profile, day, sport)['allowed']))


def manual_pause_reservations(db, start, profile):
    """Reserve omitted manual time only during generation, never as actual work."""
    from collections import Counter
    from app.db.models import Workout
    result=Counter()
    rows=db.query(Workout).filter(Workout.date>=str(start),Workout.date<str(start+timedelta(days=7)),Workout.status=='superseded').all()
    for row in rows:
        payload=json.loads(row.payload_json)
        if row.date>=str(athlete_today()) and payload.get('source')=='manual' and payload.get('coach_pause') and windows(profile,row.date,row.sport)['conflict']:
            result[row.date]+=row.duration_min
    return result


class CoachRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=3, max_length=1200)
    start: date
    end: date
    interpret_dates: bool = True


class CoachApply(CoachRequest):
    preview_signature: str = Field(min_length=64, max_length=64)


def interpret_with_dates(request):
    # A bounded local interpreter never claims to be a general language model.
    text = ''.join(c for c in unicodedata.normalize('NFD', request.message.lower()) if unicodedata.category(c) != 'Mn')
    start,end,assumptions = resolve_dates(text,request.start,request.end,athlete_today()) if request.interpret_dates else (request.start,request.end,[])
    if not athlete_today() <= start <= end <= athlete_today()+timedelta(days=730):
        raise HTTPException(422, 'Scegli un periodo da oggi ai prossimi due anni.')
    embedded = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text)
    if not request.interpret_dates and embedded and embedded != [str(start), str(end)]:
        raise HTTPException(422, 'Le date del messaggio non coincidono con Dal/Al. Allineale prima di continuare.')
    if re.search(r'\b(piu|meno|riduci|aumenta|sposta|intensita|minuti|ore)\b', text):
        raise HTTPException(422, 'Qui posso cambiare giorni e fasce disponibili. Per durata o spostamenti usa «Sposta / accorcia / salta» sulla seduta.')
    negative = bool(re.search(r'\b(no|mai|non|niente|evita|senza|indisponibile|riposo|pausa|libero)\b', text))
    only = bool(re.search(r'\b(solo|soltanto|esclusivamente)\b', text))
    periods = [key for key,word in [('morning','mattin'),('afternoon','pomeriggi'),('evening','sera')] if word in text]
    sports = [key for key,pattern in SPORT_WORDS.items() if re.search(pattern,text)]
    if re.search(r'\bnon\s+(?:(?:voglio|intendo)\s+)?(?:saltare|evitare|escludere|sospendere|smettere|rinunciare)\b',text):
        raise HTTPException(422,'La richiesta non indica chiaramente una pausa. Per riattivare una disciplina rimuovi il relativo vincolo dall’elenco.')
    if only and negative:
        raise HTTPException(422, 'La richiesta è ambigua. Scrivi ad esempio «Niente mattina» oppure «Solo pomeriggio».')
    if only and sports and periods:
        raise HTTPException(422,'Indica una modifica per volta: prima la disciplina, poi eventualmente una fascia oraria.')
    if sports and not periods and (negative or only):
        if only:
            sports=[key for key in SPORTS if key not in sports]
            if not sports:
                raise HTTPException(422,'La richiesta include tutte le discipline: non risulta nessuna pausa da applicare.')
        blocked=[]
    elif sports and periods and negative:
        blocked=periods
    elif not periods and negative and re.search(r'allen|riposo|pausa', text):
        if re.search(r'allen(?:armi|amento|amenti)\s+(?:a|di|con)\s+(?!mattina|pomeriggio|sera)\w+',text):
            raise HTTPException(422,'Specifica una disciplina supportata: nuoto, bici, corsa o palestra.')
        blocked = list(PERIODS)
    elif periods and (negative or only):
        blocked = [key for key in PERIODS if key not in periods] if only else periods
    else:
        raise HTTPException(422, 'Indica una pausa o una disponibilità: «Niente nuoto fino a gennaio», «Niente mattina», «Solo pomeriggio» o «Pausa dagli allenamenti».')
    day_words = ['lunedi','martedi','mercoledi','giovedi','venerdi','sabato','domenica']
    days = [i for i,word in enumerate(day_words) if word in text]
    if 'weekend' in text or 'fine settimana' in text:
        days = [5,6]
    elif 'feriali' in text:
        days = list(range(5))
    if re.search(r'\b(tranne|eccetto|escluso)\b', text):
        raise HTTPException(422, 'Indica direttamente i giorni interessati, senza eccezioni: ad esempio «Niente mattina lunedì e mercoledì».')
    return TimeConstraint(start=start, end=end, blocked=blocked, sports=sports, weekdays=days or list(range(7))),assumptions


def interpret(request):
    return interpret_with_dates(request)[0]


def _started(db, row):
    from app.db.experience_models import SessionFeedback
    from app.db.progress_models import WorkoutResolution
    if db.query(SessionFeedback).filter_by(workout_id=row.id).first() or db.query(WorkoutResolution).filter(WorkoutResolution.workout_id==row.id,WorkoutResolution.undone_at.is_(None)).first():
        return True
    progress=db.query(GymProgress).filter_by(workout_id=row.id).first()
    if progress:
        value=json.loads(progress.payload_json)
        return bool(value.get('completed_sets') or value.get('timer',{}).get('running'))
    return False


def preview(db, request):
    from app.services.planning import get_settings, _published
    from app.db.models import Workout
    current=get_settings(db)
    interpreted,assumptions=interpret_with_dates(request)
    rule=interpreted.model_dump(mode='json')
    profile={**current['profile'],'time_constraints':current['profile'].get('time_constraints',[])+[rule]}
    rows=db.query(Workout).filter(Workout.date>=rule['start'],Workout.date<=rule['end'],Workout.status=='planned').order_by(Workout.date,Workout.id).all()
    changes=[]
    for row in rows:
        if date.fromisoformat(row.date).weekday() not in rule['weekdays'] or (rule['sports'] and canonical_sport(row.sport) not in rule['sports']):continue
        if _published(db,row.id):
            raise HTTPException(409,'Una seduta interessata è già pubblicata su Garmin: riconciliala prima di cambiare il piano.')
        window=windows(profile,row.date,row.sport)
        removing=bool(rule['sports'] and window['conflict'])
        if removing and json.loads(row.payload_json).get('brick'):
            raise HTTPException(409,'Una seduta interessata fa parte di un combinato: rivedi prima il gruppo dal calendario per non lasciare una frazione scollegata.')
        protected=removing and _started(db,row)
        state={'id':row.id,'date':row.date,'sport':row.sport,'name':row.name,'duration_min':row.duration_min,'status':row.status,'payload':row.payload_json}
        changes.append({'id':row.id,'date':row.date,'sport':row.sport,'name':row.name,'duration_min':row.duration_min,'window':window,
                        'action':'keep' if protected else 'remove' if removing else 'availability',
                        'reason':'Seduta già iniziata o confermata: conservata.' if protected else 'Rimuovi dal piano attivo per la pausa richiesta.' if removing else 'Aggiorna le fasce disponibili.',
                        'state_hash':hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()})
    material={'rule':rule,'revision':current['revision'],'changes':changes}
    signature=hashlib.sha256(json.dumps(material,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    scope=', '.join(SPORTS[key] for key in rule['sports']) if rule['sports'] else 'Tutte le discipline'
    description=('Pausa: '+scope) if rule['sports'] and not rule['blocked'] else 'Evita '+', '.join(PERIODS[key][0].lower() for key in rule['blocked'])+' · '+scope
    return {**material,'preview_signature':signature,'assumptions':assumptions,
            'message':description+f" · {rule['start']} – {rule['end']}.",
            'conflicts':sum(change['window']['conflict'] and change['action']!='remove' for change in changes),
            'removed_count':sum(change['action']=='remove' for change in changes),
            'note':'Le sedute indicate come rimosse vengono tolte dal piano attivo, senza creare attività, voti o recuperi di carico. Le altre discipline e le sedute già iniziate restano conservate. La preferenza scade automaticamente; puoi rimuoverla dall’elenco.' if rule['sports'] else 'La fascia indica disponibilità, non un orario prenotato. Durata e intensità restano uguali. Le sedute nei giorni interamente bloccati restano visibili come conflitti da spostare o saltare; nuove generazioni li evitano.'}


def apply(db, request):
    from app.services.planning import get_settings, save_settings, _audit, _rows, _snapshot, _latest, week_start
    from app.db.models import Workout
    candidate=preview(db,request)
    if candidate['preview_signature']!=request.preview_signature:
        raise HTTPException(409,'Piano o preferenze cambiati. Chiedi di nuovo l’anteprima prima di confermare.')
    current=get_settings(db)
    rules=[rule for rule in current['profile'].get('time_constraints',[]) if rule['end']>=str(athlete_today())]
    if candidate['rule'] not in rules:rules.append(candidate['rule'])
    if len(rules)>100:raise HTTPException(422,'Hai raggiunto il limite delle preferenze temporanee. Rimuovi quelle non più utili.')
    removals=[change for change in candidate['changes'] if change['action']=='remove']
    try:
        result=save_settings(db,{**current['profile'],'time_constraints':rules},expected_revision=candidate['revision'],commit=False)
        for start in sorted({week_start(change['date']) for change in removals}):
            latest=_latest(db,start);metadata=json.loads(latest.metadata_json) if latest else {}
            _audit(db,start,'coach_sport_pause',_snapshot(_rows(db,start)),{**metadata,'profile_revision':result['revision'],'coach_constraint':candidate['rule'],'rationale':list(metadata.get('rationale') or [])+[candidate['message']]})
        for change in removals:
            row=db.get(Workout,change['id']);payload=json.loads(row.payload_json);payload['coach_pause']=candidate['rule']
            row.payload_json=json.dumps(payload,ensure_ascii=False,allow_nan=False);row.status='superseded'
        db.commit()
        return {**result,'removed_workouts':[change['id'] for change in removals]}
    except Exception:
        db.rollback()
        raise
