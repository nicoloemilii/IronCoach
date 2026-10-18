"""Deterministic Italian date expressions; all interpretations are previewed."""
import calendar
import re
from datetime import date, timedelta
from fastapi import HTTPException

MONTHS = {name: i for i,name in enumerate(['gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto','settembre','ottobre','novembre','dicembre'],1)}
MONTH = '(?:'+'|'.join(MONTHS)+')'
NUMBERS = {'un':1,'una':1,'uno':1,'due':2,'tre':3,'quattro':4,'cinque':5,'sei':6,'sette':7,'otto':8,'nove':9,'dieci':10,'dodici':12}
DATE = rf'(?:(?:\d{{1,2}})\s+)?{MONTH}(?:\s+\d{{4}})?|\d{{4}}-\d{{2}}-\d{{2}}'


def _fail():
    raise HTTPException(422, 'Non riesco a determinare il periodo. Scrivi ad esempio «fino a gennaio», «fino al 15 gennaio 2027» o «per 3 settimane», oppure disattiva le date dal messaggio e usa Dal/Al.')


def _shift_month(day, months):
    year, month = divmod(day.year*12+day.month-1+months,12)
    return date(year,month+1,min(day.day,calendar.monthrange(year,month+1)[1]))


def _date(token, reference, *, month_end=False):
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',token):
        return date.fromisoformat(token),False
    match=re.fullmatch(rf'(?:(\d{{1,2}})\s+)?({MONTH})(?:\s+(\d{{4}}))?',token)
    if not match:_fail()
    explicit_day,month_name,year=match.groups();month=MONTHS[month_name]
    for candidate_year in ([int(year)] if year else range(reference.year,reference.year+5)):
        day=int(explicit_day) if explicit_day else calendar.monthrange(candidate_year,month)[1] if month_end else 1
        try:result=date(candidate_year,month,day)
        except ValueError:
            if year:raise
            continue
        if year or result>=reference:return result,explicit_day is None
    _fail()


def resolve(text, start, end, today):
    """Return inclusive bounds and visible assumptions; fallback to form dates."""
    notes=[]
    try:
        pair=re.search(rf'\bda(?:l)?\s+({DATE})\s+(?:a|al|fino\s+a|fino\s+al)\s+({DATE})\b',text)
        if pair:
            start,_=_date(pair[1],today)
            end,_=_date(pair[2],start,month_end=True)
            return start,end,['Il periodo indicato nel messaggio sostituisce i campi Dal/Al.']
        if re.search(r'\bfino\b|\bper\s+(?:\d+|'+ '|'.join(NUMBERS) +r')\s+(?:giorn|settiman|mes)',text):
            start=today
        if re.search(r'\bda\s+dopodomani\b',text):start=today+timedelta(days=2)
        elif re.search(r'\bda\s+domani\b',text):start=today+timedelta(days=1)
        elif re.search(r'\bda\s+oggi\b',text):start=today
        until=re.search(rf'\bfino\s+(?:(?:a|al|alla)\s+)?(?:(fine)\s+(?:di\s+)?)?({DATE})\b',text)
        if until:
            end,month_only=_date(until[2],start,month_end=bool(until[1]))
            if month_only and not until[1]:
                resume=end;end-=timedelta(days=1)
                notes.append(f'«Fino a {until[2]}» interpretato come ripresa dal {resume.isoformat()}; pausa fino al {end.isoformat()} compreso.')
            else:notes.append(f'Pausa fino al {end.isoformat()} compreso.')
            return start,end,notes
        duration=re.search(r'\bper\s+(\d+|'+ '|'.join(NUMBERS) +r')\s+(giorn[oi]|settiman[ae]|mes[ei])\b',text)
        if duration:
            amount=int(duration[1]) if duration[1].isdigit() else NUMBERS[duration[1]]
            if not 1<=amount<=730:_fail()
            end=(_shift_month(start,amount) if duration[2].startswith('mes') else start+timedelta(days=amount*(7 if duration[2].startswith('settiman') else 1)))-timedelta(days=1)
            return start,end,[f'Durata interpretata dal {start.isoformat()} al {end.isoformat()}, estremi compresi.']
        if re.search(r'\bfino\s+a\s+fine\s+(?:mese|anno)\b',text):
            end=date(start.year,12,31) if 'fine anno' in text else date(start.year,start.month,calendar.monthrange(start.year,start.month)[1])
            return start,end,[f'Pausa fino al {end.isoformat()} compreso.']
        if 'prossima settimana' in text or 'questa settimana' in text:
            monday=today-timedelta(days=today.weekday())
            start=monday+timedelta(days=7) if 'prossima settimana' in text else today
            return start,(monday+timedelta(days=13) if 'prossima settimana' in text else monday+timedelta(days=6)),['Settimana da lunedì a domenica; nessuna modifica ai giorni già passati.']
        if re.search(r'\bfino\s+a\s+domani\b',text):return start,today+timedelta(days=1),['Domani è incluso nella pausa.']
        if re.search(r'\bsolo\s+oggi\b',text):return today,today,['Pausa limitata a oggi.']
        month_period=re.search(r'\b(?:a|in|durante|nel mese di|per tutto(?: il mese di)?)\s+('+MONTH+r')(?:\s+(\d{4}))?\b',text)
        if month_period:
            month=MONTHS[month_period[1]];explicit_year=month_period[2]
            year=int(explicit_year) if explicit_year else today.year+(month<today.month)
            start=date(year,month,1)
            if start.year==today.year and start.month==today.month:start=today
            end=date(year,month,calendar.monthrange(year,month)[1])
            return start,end,[f'Mese interpretato dal {start.isoformat()} al {end.isoformat()}.']
        if re.search(r'\bfino\b|\b'+MONTH+r'\b|\bdal?\s+\d|\bper\s+(?:\d+|'+ '|'.join(NUMBERS) +r')\s+(?:giorn|settiman|mes)|\b(?:finche|qualche|un po)\b',text):_fail()
        return start,end,[]
    except (ValueError,OverflowError):
        _fail()
