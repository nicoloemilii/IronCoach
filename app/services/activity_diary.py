"""Bounded keyset diary reads, with recorded values and exact rating context."""
import base64
import hashlib
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import and_, or_, func

from app.db.database import WORKSPACE
from app.db.models import Activity
from app.services.activity_rating import activity_ratings
from app.services.athlete_clock import as_utc_naive, utc_now_naive
from app.services.observed_load import resolve_observed_loads, finite_load
from app.services.training_load import sport_key

CATEGORIES = {'bike':'cycling', 'run':'running', 'swim':'swimming', 'strength':'strength'}
NUMERIC_FIELDS = ('duration_seconds', 'distance_meters', 'elevation_gain_meters', 'avg_hr', 'max_hr',
                  'avg_power', 'normalized_power', 'avg_speed', 'avg_cadence', 'calories')


def category(value):
    return CATEGORIES.get(sport_key(value), 'other')


def serialize_activity(activity, rating=None, load=None, load_evidence=None):
    result = {key:getattr(activity,key) for key in ('id','source','sport','name','start_time')}
    result.update({key:finite_load(getattr(activity,key)) for key in NUMERIC_FIELDS})
    result.update(sport_category=category(activity.sport), load=finite_load(load), coach_rating=rating,
                  load_evidence={key:load_evidence.get(key) for key in
                     ('source','status','reason','value_reason','conflict','observation_count')} if load_evidence else None)
    return result


def _scope(db):
    identity = WORKSPACE.get() or str(db.get_bind().url)
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def _filter_key(sport, search):
    return hashlib.sha256(json.dumps([sport,search],ensure_ascii=True).encode('ascii')).hexdigest()


def _encode(value):
    return base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode('ascii')).decode('ascii').rstrip('=')


def _decode(raw, scope, filter_key, now):
    try:
        if not isinstance(raw,str) or not 1 <= len(raw) <= 2048:
            raise ValueError()
        payload=json.loads(base64.b64decode(raw+'='*(-len(raw)%4),altchars=b'-_',validate=True))
        if not isinstance(payload,dict) or set(payload)!={'v','scope','filter','as_of','max_id','before'}:
            raise ValueError()
        if type(payload['v']) is not int or payload['v'] != 1 or payload['scope'] != scope or payload['filter'] != filter_key:
            raise ValueError()
        maximum=payload['max_id']; before=payload['before']
        if type(maximum) is not int or not 0 <= maximum <= 2**63-1 or not isinstance(before,list) or len(before)!=2:
            raise ValueError()
        if type(before[1]) is not int or not 1 <= before[1] <= maximum:
            raise ValueError()
        if not isinstance(payload['as_of'],str) or not isinstance(before[0],str):
            raise ValueError()
        stamp=as_utc_naive(datetime.fromisoformat(payload['as_of'].removesuffix('Z')))
        boundary=as_utc_naive(datetime.fromisoformat(before[0].removesuffix('Z')))
        if stamp > now or not 1970 <= stamp.year <= 9999:
            raise ValueError()
        return payload,stamp,boundary
    except (ValueError,TypeError,OverflowError,RecursionError,KeyError):
        raise HTTPException(422,'La posizione del diario non è valida per questi filtri. Ricarica il diario.') from None


def _prepare_sqlite(db):
    # Python casefold preserves the Unicode search behavior of the web client;
    # SQLite's built-in LOWER/NOCASE alone only covers ASCII. These deterministic
    # functions are connection-local and perform no schema or data writes.
    connection=db.connection().connection.driver_connection
    connection.create_function('iron_diary_casefold',1,lambda value:value.casefold() if isinstance(value,str) else '',deterministic=True)
    connection.create_function('iron_diary_category',1,category,deterministic=True)
    # sqlite3's legacy transaction mode does not start a transaction for SELECT.
    # Keep counts, rows and rating context in the same read snapshot during sync.
    if not connection.in_transaction:
        connection.execute('BEGIN')


def diary_page(db, *, limit=50, sport=None, q='', cursor=None, now=None):
    now=as_utc_naive(now or utc_now_naive())
    if type(limit) is not int or not 1 <= limit <= 100 or sport not in (None,'','cycling','running','swimming','strength','other'):
        raise HTTPException(422,'Filtri del diario non validi.')
    if not isinstance(q,str) or len(q)>200:
        raise HTTPException(422,'La ricerca deve contenere al massimo 200 caratteri.')
    sport=sport or None; search=q.casefold(); scope=_scope(db); filter_key=_filter_key(sport,search)
    _prepare_sqlite(db)
    if cursor:
        token,as_of,boundary=_decode(cursor,scope,filter_key,now)
        maximum=token['max_id']
    else:
        maximum=db.query(func.max(Activity.id)).scalar() or 0
        as_of=now; boundary=None
    archived=db.query(Activity).filter(Activity.id<=maximum)
    archive_total=archived.count()
    query=archived
    if sport:
        query=query.filter(func.iron_diary_category(Activity.sport)==sport)
    if search:
        query=query.filter(func.iron_diary_casefold(Activity.name).contains(search,autoescape=True))
    total=query.count()
    if cursor:
        query=query.filter(or_(Activity.start_time<boundary,
                              and_(Activity.start_time==boundary,Activity.id<token['before'][1])))
    selected=query.order_by(Activity.start_time.desc(),Activity.id.desc()).limit(limit+1).all()
    has_more=len(selected)>limit; rows=selected[:limit]
    evidence=resolve_observed_loads(db,rows)
    ratings=activity_ratings(db,target_ids={row.id for row in rows},now=as_of) if rows else {}
    items=[serialize_activity(row,ratings.get(row.id),evidence[row.id]['value'],evidence[row.id]) for row in rows]
    next_cursor=None
    if has_more:
        last=rows[-1]
        next_cursor=_encode(dict(v=1,scope=scope,filter=filter_key,as_of=as_of.isoformat()+'Z',max_id=maximum,
                                 before=[as_utc_naive(last.start_time).isoformat()+'Z',last.id]))
    return dict(items=items,total=total,archive_total=archive_total,limit=limit,has_more=has_more,
                next_cursor=next_cursor,snapshot=dict(as_of_utc=as_of.isoformat()+'Z',max_id=maximum),
                consistency='keyset_live_view',includes_future=True)
