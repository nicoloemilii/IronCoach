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

PERIODS = {'morning': ('Mattina', '06:00–12:00', 360),
           'afternoon': ('Pomeriggio', '12:00–18:00', 360),
           'evening': ('Sera', '18:00–22:00', 240)}


class TimeConstraint(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: date
    end: date
    blocked: list[Literal['morning', 'afternoon', 'evening']] = Field(min_length=1, max_length=3)
    weekdays: list[Annotated[int, Field(strict=True, ge=0, le=6)]] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)

    @model_validator(mode='after')
    def valid_range(self):
        if self.end < self.start or (self.end-self.start).days > 730:
            raise ValueError('Indica un periodo ordinato di massimo due anni.')
        if len(set(self.blocked)) != len(self.blocked) or len(set(self.weekdays)) != len(self.weekdays) or any(isinstance(d, bool) or d not in range(7) for d in self.weekdays):
            raise ValueError('Giorni e fasce devono essere validi e senza duplicati.')
        return self


def windows(profile, day):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    blocked = set()
    for rule in profile.get('time_constraints', []):
        if rule['start'] <= str(day) <= rule['end'] and day.weekday() in rule['weekdays']:
            blocked.update(rule['blocked'])
    allowed = [key for key in PERIODS if key not in blocked]
    return {'allowed': allowed, 'restricted': bool(blocked), 'conflict': not allowed,
            'label': 'Giorno non disponibile' if not allowed else ' / '.join(f'{PERIODS[key][0]} {PERIODS[key][1]}' for key in allowed)}


def available_minutes(profile, day):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return min(profile['availability'][day.weekday()], sum(PERIODS[key][2] for key in windows(profile, day)['allowed']))


class CoachRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=3, max_length=1200)
    start: date
    end: date


class CoachApply(CoachRequest):
    preview_signature: str = Field(min_length=64, max_length=64)


def interpret(request):
    # A bounded local interpreter never claims to be a general language model.
    text = ''.join(c for c in unicodedata.normalize('NFD', request.message.lower()) if unicodedata.category(c) != 'Mn')
    if not athlete_today() <= request.start <= request.end <= athlete_today()+timedelta(days=730):
        raise HTTPException(422, 'Scegli un periodo da oggi ai prossimi due anni.')
    embedded = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text)
    if embedded and embedded != [str(request.start), str(request.end)]:
        raise HTTPException(422, 'Le date del messaggio non coincidono con Dal/Al. Allineale prima di continuare.')
    if re.search(r'\b(piu|meno|riduci|aumenta|sposta|intensita|minuti|ore)\b', text):
        raise HTTPException(422, 'Qui posso cambiare giorni e fasce disponibili. Per durata o spostamenti usa «Sposta / accorcia / salta» sulla seduta.')
    negative = bool(re.search(r'\b(no|mai|non|niente|evita|senza|indisponibile|riposo|pausa|libero)\b', text))
    only = bool(re.search(r'\b(solo|soltanto|esclusivamente)\b', text))
    periods = [key for key,word in [('morning','mattin'),('afternoon','pomeriggi'),('evening','sera')] if word in text]
    if only and negative:
        raise HTTPException(422, 'La richiesta è ambigua. Scrivi ad esempio «Niente mattina» oppure «Solo pomeriggio».')
    if not periods and negative and re.search(r'allen|riposo|pausa', text):
        blocked = list(PERIODS)
    elif periods and (negative or only):
        blocked = [key for key in PERIODS if key not in periods] if only else periods
    else:
        raise HTTPException(422, 'Dimmi quali fasce evitare: «Niente mattina», «Solo pomeriggio» o «Pausa dagli allenamenti». Imposta il periodo nei campi Dal/Al.')
    day_words = ['lunedi','martedi','mercoledi','giovedi','venerdi','sabato','domenica']
    days = [i for i,word in enumerate(day_words) if word in text]
    if 'weekend' in text or 'fine settimana' in text:
        days = [5,6]
    elif 'feriali' in text:
        days = list(range(5))
    if re.search(r'\b(tranne|eccetto|escluso)\b', text):
        raise HTTPException(422, 'Indica direttamente i giorni interessati, senza eccezioni: ad esempio «Niente mattina lunedì e mercoledì».')
    return TimeConstraint(start=request.start, end=request.end, blocked=blocked, weekdays=days or list(range(7)))


def preview(db, request):
    from app.services.planning import get_settings
    from app.db.models import Workout
    current = get_settings(db)
    rule = interpret(request).model_dump(mode='json')
    profile = {**current['profile'], 'time_constraints': current['profile'].get('time_constraints', []) + [rule]}
    rows = db.query(Workout).filter(Workout.date >= str(request.start), Workout.date <= str(request.end), Workout.status == 'planned').order_by(Workout.date, Workout.id).all()
    changes = [{'id': row.id, 'date': row.date, 'name': row.name, 'duration_min': row.duration_min,
                'window': windows(profile, row.date)} for row in rows if date.fromisoformat(row.date).weekday() in rule['weekdays']]
    from app.services.planning import _published
    if any(_published(db, row.id) for row in rows if date.fromisoformat(row.date).weekday() in rule['weekdays']):
        raise HTTPException(409, 'Una seduta del periodo è già pubblicata su Garmin: riconciliala prima di cambiare la disponibilità.')
    material = {'rule': rule, 'revision': current['revision'], 'changes': changes}
    signature = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**material, 'preview_signature': signature,
            'message': 'Eviterò: ' + ', '.join(PERIODS[key][0].lower() for key in rule['blocked']) + f" · {rule['start']} – {rule['end']}.",
            'conflicts': sum(change['window']['conflict'] for change in changes),
            'note': 'La fascia indica disponibilità, non un orario prenotato. Durata e intensità restano uguali. Le sedute nei giorni interamente bloccati restano visibili come conflitti da spostare o saltare; nuove generazioni li evitano.'}


def apply(db, request):
    from app.services.planning import get_settings, save_settings
    candidate = preview(db, request)
    if candidate['preview_signature'] != request.preview_signature:
        raise HTTPException(409, 'Piano o preferenze cambiati. Chiedi di nuovo l’anteprima prima di confermare.')
    current = get_settings(db)
    rules = current['profile'].get('time_constraints', [])
    rules = [rule for rule in rules if rule['end'] >= str(athlete_today())]
    if candidate['rule'] not in rules:
        rules.append(candidate['rule'])
    if len(rules) > 100:
        raise HTTPException(422, 'Hai raggiunto il limite delle preferenze temporanee. Rimuovi quelle non più utili.')
    return save_settings(db, {**current['profile'], 'time_constraints': rules}, expected_revision=candidate['revision'])
