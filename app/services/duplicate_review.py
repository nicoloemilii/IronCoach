"""Explicit previews and guarded, exactly reversible duplicate-review writes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
import json
import math

from fastapi import HTTPException
from sqlalchemy import DateTime, Float, Integer, String, or_
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.models import Activity, Workout, WorkoutEvaluation
from app.db.experience_models import ActivityIdentity, SessionFeedback
from app.db.iron_models import ActivityObservation, DuplicateCandidate
from app.db.review_models import DuplicateReview


MAX_ROWS = 5000
MAX_BYTES = 4_000_000
MAX_DEPTH = 30
MODELS = (Activity, ActivityIdentity, ActivityObservation, SessionFeedback, WorkoutEvaluation, DuplicateCandidate)
TABLES = {model.__tablename__: model for model in MODELS}
FIELDS = ('duration_seconds', 'distance_meters', 'elevation_gain_meters', 'avg_hr', 'max_hr',
          'avg_power', 'normalized_power', 'avg_speed', 'avg_cadence', 'calories', 'load')


class Unavailable(ValueError):
    pass


def _encode(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec='microseconds')
    if isinstance(value, float) and not math.isfinite(value):
        return {'invalid_number': repr(value)}
    return value


def _row(row):
    return {column.name: _encode(getattr(row, column.name)) for column in row.__table__.columns}


def _dump(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_BYTES:
        raise Unavailable('Il confronto contiene troppi dati per una revisione reversibile.')
    return encoded


def _fingerprint(snapshot):
    return hashlib.sha256(_dump(snapshot).encode('utf-8')).hexdigest()


def _json_object(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_BYTES:
        raise Unavailable('Un dato collegato supera i limiti della revisione.')
    try:
        value = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        raise Unavailable('Un dato collegato contiene JSON non valido: verifica la fonte prima di unire.') from None
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop(); nodes += 1
        if depth > MAX_DEPTH or nodes > 100000:
            raise Unavailable('Un dato collegato supera i limiti di struttura della revisione.')
        if isinstance(item, float) and not math.isfinite(item):
            raise Unavailable('Un dato collegato contiene numeri non validi.')
        if isinstance(item, dict):
            pending.extend((entry, depth + 1) for entry in item.values())
        elif isinstance(item, list):
            pending.extend((entry, depth + 1) for entry in item)
    if not isinstance(value, dict):
        raise Unavailable('Un dato collegato non ha la struttura JSON prevista.')
    return value


def _snapshot(db, identifiers):
    snapshot = {}; count = 0
    for model in MODELS:
        if model is Activity:
            condition = model.id.in_(identifiers)
        elif model is DuplicateCandidate:
            condition = or_(model.activity_id.in_(identifiers), model.other_id.in_(identifiers))
        else:
            condition = model.activity_id.in_(identifiers)
        rows = db.query(model).filter(condition).order_by(model.id.asc()).limit(MAX_ROWS + 1).all()
        count += len(rows)
        if count > MAX_ROWS:
            raise Unavailable('Troppi collegamenti: la revisione reversibile non è disponibile.')
        snapshot[model.__tablename__] = [_row(row) for row in rows]
    _dump(snapshot)
    return snapshot


def _candidate(db, identifier):
    row = db.get(DuplicateCandidate, identifier)
    if not row or row.status != 'pending':
        raise HTTPException(404, 'Confronto non disponibile.')
    return row


def _original_alias(db, remove, keep):
    observed_aliases = db.query(ActivityIdentity.source_key).join(
        ActivityObservation, ActivityObservation.source_key == ActivityIdentity.source_key,
    ).filter(ActivityIdentity.activity_id == remove.id, ActivityObservation.activity_id == remove.id).all()
    namespaced = any(source_key.startswith(f'{remove.source}:') and remove.source_id.startswith(source_key)
                     and (not (suffix := remove.source_id[len(source_key):])
                          or suffix.startswith(':') and suffix[1:].isascii() and suffix[1:].isdigit())
                     for (source_key,) in observed_aliases)
    if namespaced:
        return None
    key = f'{remove.source}:{remove.source_id}'
    alias = db.query(ActivityIdentity).filter_by(source_key=key).first()
    if alias and alias.activity_id not in (remove.id, keep.id):
        raise Unavailable('Identità esterna già collegata a un’altra attività.')
    observation = db.query(ActivityObservation).filter_by(source_key=key).first()
    if observation and observation.activity_id not in (remove.id, keep.id):
        raise Unavailable('Osservazione della fonte già collegata a un’altra attività.')
    return key if not alias else None


def _blocking(db, snapshot, remove, keep):
    reasons = []
    if not remove or not keep or remove.id == keep.id:
        return ['Attività non più disponibili o confronto non valido.']
    if any(row['status'] == 'kept' and {row['activity_id'],row['other_id']} == {remove.id,keep.id}
           for row in snapshot['duplicate_candidates']):
        reasons.append('Hai già scelto di mantenere distinte queste attività: annulla quella revisione prima di unirle.')
    feedback = snapshot['session_feedback']
    if any(row['activity_id'] == keep.id for row in feedback) and any(row['activity_id'] == remove.id for row in feedback):
        reasons.append('Entrambe le attività hanno feedback: risolvi gli abbinamenti prima della fusione.')
    try:
        _original_alias(db, remove, keep)
        for key in ('activity_observations', 'session_feedback', 'workout_evaluations'):
            for row in snapshot[key]:
                _json_object(row['payload_json'])
        if any(isinstance(value, dict) and 'invalid_number' in value
               for row in snapshot['activities'] for value in row.values()):
            raise Unavailable('Le attività contengono numeri non validi: verifica la fonte.')
    except Unavailable as exc:
        reasons.append(str(exc))
    return reasons


def _preview(db, identifier):
    candidate = _candidate(db, identifier)
    remove, keep = db.get(Activity, candidate.activity_id), db.get(Activity, candidate.other_id)
    activity, other = _row(remove) if remove else None, _row(keep) if keep else None
    result = dict(other) if other else None
    fields = []
    if activity and other:
        for field in FIELDS:
            result[field] = other[field] if other[field] is not None else activity[field]
            fields.append({'field': field, 'before_keep': other[field], 'before_remove': activity[field],
                           'result': result[field], 'source': 'existing' if other[field] is not None else 'imported' if activity[field] is not None else 'missing'})
    snapshot, token, links = None, None, {}
    try:
        snapshot = _snapshot(db, (candidate.activity_id, candidate.other_id))
        token = _fingerprint(snapshot)
        reasons = _blocking(db, snapshot, remove, keep)
        for label, key in [('identities','activity_identities'), ('observations','activity_observations'),
                           ('feedback','session_feedback'), ('evaluations','workout_evaluations'), ('candidates','duplicate_candidates')]:
            rows = snapshot[key]
            links[label] = {'activity': sum(row['activity_id'] == candidate.activity_id or (key == 'duplicate_candidates' and row['other_id'] == candidate.activity_id) for row in rows),
                            'other': sum(row['activity_id'] == candidate.other_id or (key == 'duplicate_candidates' and row['other_id'] == candidate.other_id) for row in rows), 'total': len(rows)}
    except Unavailable as exc:
        reasons = [str(exc)]
    return {'candidate_id': identifier, 'state_token': token, 'decision': 'merge',
            'activity': activity, 'other': other, 'result': result, 'fields': fields, 'links': links,
            'merge_allowed': not reasons, 'blocking_reasons': reasons,
            'limits': {'rows': MAX_ROWS, 'snapshot_bytes': MAX_BYTES, 'json_depth': MAX_DEPTH}}


def preview(db, identifier):
    # Python sqlite's legacy transaction mode does not BEGIN for SELECT. An
    # explicit read transaction makes displayed fields and token one snapshot.
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, 'Completa le modifiche in corso prima dell’anteprima.')
    db.rollback()
    with db.begin():
        if db.get_bind().dialect.name == 'sqlite':
            db.connection().exec_driver_sql('BEGIN')
        db.expire_all()
        return _preview(db, identifier)


@contextmanager
def _writer(db):
    if db.get_bind().dialect.name != 'sqlite':
        raise HTTPException(503, 'Revisione disponibile soltanto sul database SQLite.')
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, 'Completa le modifiche in corso prima della revisione.')
    db.rollback()
    try:
        with db.begin():
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            db.expire_all()
            yield
    except Unavailable as exc:
        raise HTTPException(409, str(exc)) from None
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(409, 'I dati sono cambiati o sono occupati: aggiorna la revisione.') from None


def _remap(value, remove_id, keep_id):
    if isinstance(value, dict):
        return {key: keep_id if key in ('activity_id', 'matched_activity_id') and item == remove_id
                else _remap(item, remove_id, keep_id) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, remove_id, keep_id) for item in value]
    return value


def _merge(db, remove, keep):
    alias = _original_alias(db, remove, keep)
    if alias:
        db.add(ActivityIdentity(source_key=alias, activity_id=keep.id))
    for model in (ActivityIdentity, ActivityObservation):
        for row in db.query(model).filter_by(activity_id=remove.id).all():
            row.activity_id = keep.id
    for row in db.query(WorkoutEvaluation).filter_by(activity_id=remove.id).all():
        row.activity_id = keep.id
        row.payload_json = json.dumps(_remap(_json_object(row.payload_json), remove.id, keep.id))
    feedback = db.query(SessionFeedback).filter_by(activity_id=remove.id).first()
    if feedback:
        feedback.activity_id = keep.id
        content = _remap(_json_object(feedback.payload_json), remove.id, keep.id)
        content['activity_id'] = keep.id
        feedback.payload_json = json.dumps(content)
    for field in FIELDS:
        if getattr(keep, field) is None:
            setattr(keep, field, getattr(remove, field))
    candidates = db.query(DuplicateCandidate).filter(or_(DuplicateCandidate.activity_id == remove.id,
                                                       DuplicateCandidate.other_id == remove.id)).order_by(DuplicateCandidate.id).all()
    for candidate in candidates:
        first = keep.id if candidate.activity_id == remove.id else candidate.activity_id
        second = keep.id if candidate.other_id == remove.id else candidate.other_id
        if first == second:
            db.delete(candidate); continue
        existing = db.query(DuplicateCandidate).filter(or_(
            (DuplicateCandidate.activity_id == first) & (DuplicateCandidate.other_id == second),
            (DuplicateCandidate.activity_id == second) & (DuplicateCandidate.other_id == first))).order_by(DuplicateCandidate.id).first()
        if existing and existing.id != candidate.id:
            if candidate.status == 'kept': existing.status = 'kept'
            db.delete(candidate)
        else:
            candidate.activity_id, candidate.other_id = first, second
        db.flush()
    db.flush()
    db.delete(remove); db.flush()


def resolve(db, identifier, decision, state_token=None):
    if decision == 'merge' and not state_token:
        raise HTTPException(428, 'Apri l’anteprima della fusione prima di confermare.')
    with _writer(db):
        candidate = _candidate(db, identifier)
        remove_id, keep_id = candidate.activity_id, candidate.other_id
        before = _snapshot(db, (remove_id, keep_id))
        before_fingerprint = _fingerprint(before)
        if state_token is not None and not hmac.compare_digest(state_token, before_fingerprint):
            raise HTTPException(409, 'I dati sono cambiati dopo l’anteprima: controlla il confronto aggiornato.')
        remove, keep = db.get(Activity, remove_id), db.get(Activity, keep_id)
        if decision == 'merge':
            reasons = _blocking(db, before, remove, keep)
            if reasons:
                raise Unavailable(' '.join(reasons))
            _merge(db, remove, keep)
        elif decision == 'keep':
            candidate.status = 'kept'; db.flush()
        else:
            raise HTTPException(422, 'Decisione non valida.')
        after = _snapshot(db, (remove_id, keep_id))
        event = DuplicateReview(candidate_id=identifier, action=decision, status='applied',
            activity_id=remove_id, other_id=keep_id, before_json=_dump(before), after_json=_dump(after),
            before_fingerprint=before_fingerprint, after_fingerprint=_fingerprint(after),
            summary_json=_dump({'activity_name': remove.name if remove else None, 'other_name': keep.name if keep else None,
                                'sources': sorted({row['source'] for row in before['activities']})}))
        db.add(event); db.flush(); event_id = event.id
    return {'status': 'merged', 'activity_id': keep_id, 'review_id': event_id, 'undo_available': True} if decision == 'merge' else {'status': 'kept'}


def _read_snapshot(raw):
    value = _json_object(raw)
    if set(value) != set(TABLES):
        raise Unavailable('Il diario di revisione non ha una struttura ripristinabile.')
    count = 0
    for key, rows in value.items():
        if not isinstance(rows, list):
            raise Unavailable('Il diario di revisione non ha una struttura ripristinabile.')
        count += len(rows)
        columns = {column.name for column in TABLES[key].__table__.columns}
        if count > MAX_ROWS or any(not isinstance(row, dict) or set(row) != columns for row in rows):
            raise Unavailable('Il diario di revisione supera i limiti o non è completo.')
        for row in rows:
            for column in TABLES[key].__table__.columns:
                item = row[column.name]
                if item is None:
                    continue
                if isinstance(column.type, DateTime):
                    try:
                        datetime.fromisoformat(item)
                    except (ValueError, TypeError):
                        raise Unavailable('Il diario contiene una data non ripristinabile.') from None
                elif isinstance(column.type, Integer) and (not isinstance(item, int) or isinstance(item, bool)):
                    raise Unavailable('Il diario contiene un identificativo non ripristinabile.')
                elif isinstance(column.type, Float) and not (isinstance(item, (int, float)) and not isinstance(item, bool)):
                    if not isinstance(item, dict) or set(item) != {'invalid_number'} or item['invalid_number'] not in ('inf','-inf','nan'):
                        raise Unavailable('Il diario contiene un numero non ripristinabile.')
                elif isinstance(column.type, String) and not isinstance(item, str):
                    raise Unavailable('Il diario contiene un campo non ripristinabile.')
    return value


def _undo_guard(db, event):
    if event.status != 'applied':
        raise Unavailable('Questa revisione è già stata annullata.')
    before, after = _read_snapshot(event.before_json), _read_snapshot(event.after_json)
    if _fingerprint(before) != event.before_fingerprint or _fingerprint(after) != event.after_fingerprint:
        raise Unavailable('Il diario di revisione non corrisponde alle impronte salvate.')
    current = _snapshot(db, (event.activity_id, event.other_id))
    if _fingerprint(current) != event.after_fingerprint:
        raise Unavailable('I dati collegati sono cambiati: annullamento non disponibile. Annulla prima eventuali revisioni successive.')
    external_activities = {row[field] for row in before['duplicate_candidates'] for field in ('activity_id','other_id')
                           if row[field] not in (event.activity_id,event.other_id)}
    if external_activities and db.query(Activity.id).filter(Activity.id.in_(external_activities)).count() != len(external_activities):
        raise Unavailable('Un’attività collegata non esiste più: annullamento non disponibile.')
    workouts = {row['workout_id'] for key in ('session_feedback','workout_evaluations') for row in before[key]}
    if workouts and db.query(Workout.id).filter(Workout.id.in_(workouts)).count() != len(workouts):
        raise Unavailable('Una seduta collegata non esiste più: annullamento non disponibile.')
    for key, model in TABLES.items():
        after_ids = {row['id'] for row in after[key]}
        deleted_ids = [row['id'] for row in before[key] if row['id'] not in after_ids]
        if deleted_ids and db.query(model.id).filter(model.id.in_(deleted_ids)).first():
            raise Unavailable('Un identificativo precedente è stato riutilizzato: annullamento non disponibile.')
        for field in ('source_id', 'source_key', 'workout_id'):
            if not hasattr(model, field) or (field == 'workout_id' and model is not SessionFeedback):
                continue
            values = [row[field] for row in before[key] if row[field] is not None]
            before_ids = {row['id'] for row in before[key]}
            occupied = db.query(model.id).filter(getattr(model, field).in_(values), ~model.id.in_(before_ids | after_ids)).first() if values else None
            if occupied:
                raise Unavailable('Un’identità esterna o un collegamento è ora occupato: annullamento non disponibile.')
    return before, after


def _restore(db, before, after):
    for model in reversed(MODELS):
        identifiers = [row['id'] for row in after[model.__tablename__]]
        if identifiers:
            db.execute(model.__table__.delete().where(model.id.in_(identifiers)))
    for model in MODELS:
        for source in before[model.__tablename__]:
            row = dict(source)
            for column in model.__table__.columns:
                if isinstance(column.type, DateTime) and row[column.name] is not None:
                    row[column.name] = datetime.fromisoformat(row[column.name])
                elif isinstance(column.type, Float) and isinstance(row[column.name], dict):
                    row[column.name] = float(row[column.name]['invalid_number'])
            db.execute(model.__table__.insert().values(**row))
    db.expire_all()


def undo(db, identifier):
    with _writer(db):
        event = db.get(DuplicateReview, identifier)
        if not event:
            raise HTTPException(404, 'Revisione non trovata.')
        before, after = _undo_guard(db, event)
        _restore(db, before, after)
        event.status, event.undone_at = 'undone', datetime.utcnow()
        result = {'status': 'undone', 'review_id': event.id, 'activity_id': event.activity_id, 'other_id': event.other_id}
        db.flush()
    return result


def history(db, limit=20):
    output = []
    for event in db.query(DuplicateReview).order_by(DuplicateReview.id.desc()).limit(limit).all():
        try:
            _undo_guard(db, event)
            available, reason = True, 'Annullamento disponibile finché i dati collegati restano invariati.'
        except Unavailable as exc:
            available, reason = False, str(exc)
        try:
            summary = _json_object(event.summary_json)
        except Unavailable:
            summary = {}
        output.append({'id': event.id, 'candidate_id': event.candidate_id, 'action': event.action, 'status': event.status,
                       'activity_id': event.activity_id, 'other_id': event.other_id,
                       'created_at': _encode(event.created_at), 'undone_at': _encode(event.undone_at),
                       'summary': summary, 'undo_available': available, 'undo_reason': reason})
    return {'reviews': output, 'limits': ['Solo le revisioni registrate da questa versione sono annullabili.',
                                        'Modifiche successive ai dati collegati impediscono l’annullamento. Le revisioni concatenate si annullano dalla più recente.']}
