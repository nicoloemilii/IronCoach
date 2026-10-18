"""Portable personal data only; validated, previewed and atomically restored.

The JSON format never contains credentials, connection state or remote delivery
references.  An SQLite online snapshot is retained locally before each restore.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
from contextlib import contextmanager, closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint, inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.db.database import Base
from app.db import models, experience_models, iron_models, planning_models, progress_models  # register allowlisted tables
from app.db import health_reports  # register optional personal symptom journal
from app.db.garmin_models import GarminDelivery

MAX_BYTES = 20 * 1024 * 1024
MAX_RECORDS = 100_000
PREVIEW_SECONDS = 600
CORE_TABLES = (
    'activities', 'daily_metrics', 'workouts', 'workout_evaluations', 'athlete_adaptations',
    'athlete_checkins', 'session_feedback', 'race_preparation', 'activity_identities',
    'iron_users', 'health_metrics', 'race_goals', 'duplicate_candidates', 'activity_observations',
    'athlete_settings', 'plan_revisions',
)
# New additive tables are optional when reading an older format-version-1 file.
PROGRESS_TABLES = ('workout_resolutions', 'goal_preparation_items', 'health_reports')
_PREVIEWS: dict[str, dict] = {}
_PREVIEW_LOCK = Lock()


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _fail(message):
    raise HTTPException(422, f'Backup non valido: {message}')


def tables_for(db):
    # Import only our own known schema; never reflect a supplied table name.
    names = set(CORE_TABLES + PROGRESS_TABLES)
    available = set(inspect(db.connection()).get_table_names())
    return [table for table in Base.metadata.sorted_tables if table.name in names and table.name in available]


def _serial(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def dataset(db, *, effective_profile=False):
    result = {}
    for table in tables_for(db):
        statement = select(table).order_by(*table.primary_key.columns)
        result[table.name] = [{key: _serial(value) for key, value in row.items()}
                              for row in db.execute(statement).mappings()]
    if effective_profile and not result.get('athlete_settings'):
        # The old profile file is personal data too; snapshot its validated
        # effective value, without copying the file or any integration config.
        from app.services.planning import load_settings
        result['athlete_settings'] = [{'id': 1, 'revision': 0,
            'payload_json': _json(load_settings(db)), 'updated_at': _now().replace(tzinfo=None).isoformat()}]
    return result


def fingerprint(db):
    content = dataset(db)
    if not content.get('athlete_settings'):
        from app.services.planning import load_settings
        content['_effective_legacy_profile'] = load_settings(db)
    # Local delivery status affects which restored workouts can be changed.
    if inspect(db.connection()).has_table('garmin_deliveries'):
        content['_local_delivery'] = [{key: _serial(value) for key, value in row.items()}
            for row in db.execute(select(GarminDelivery.__table__).order_by(GarminDelivery.id)).mappings()]
    return hashlib.sha256(_json(content).encode('utf-8')).hexdigest()


def export_backup(db):
    with _read_snapshot(db):
        return {'format_version': 1, 'generated_at': _stamp(_now()), 'data': dataset(db, effective_profile=True)}


@contextmanager
def _read_snapshot(db):
    # sqlite3's legacy transaction mode does not start a read transaction for
    # SELECT. Begin it explicitly so a concurrent sync cannot split the export
    # across incompatible versions of activities and their references.
    db.rollback()
    with db.begin():
        if db.get_bind().dialect.name == 'sqlite':
            db.connection().exec_driver_sql('BEGIN')
        yield


def _parse_json(content):
    def reject_constant(_):
        raise ValueError('numero non finito')
    def unique_keys(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError('chiave ripetuta')
            output[key] = value
        return output
    try:
        return json.loads(content, parse_constant=reject_constant, object_pairs_hook=unique_keys)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail('JSON illeggibile, chiavi ripetute o numeri non finiti.')


def _safe_json_tree(value, depth=0):
    if depth > 40:
        _fail('struttura JSON troppo profonda.')
    if isinstance(value, float) and not math.isfinite(value):
        _fail('numero non finito.')
    if isinstance(value, dict):
        for item in value.values():
            _safe_json_tree(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _safe_json_tree(item, depth + 1)


def _datetime(value, label):
    if not isinstance(value, str) or len(value) > 40 or 'T' not in value:
        _fail(f'{label}: data/ora ISO non valida.')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        _fail(f'{label}: data/ora ISO non valida.')


def _day(value, label):
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError()
        date.fromisoformat(value)
    except ValueError:
        _fail(f'{label}: usare YYYY-MM-DD.')


def _validate_value(column, value, label):
    if value is None:
        if not column.nullable:
            _fail(f'{label}: valore obbligatorio.')
        return None
    kind = column.type
    if isinstance(kind, Boolean):
        if not isinstance(value, bool):
            _fail(f'{label}: serve un booleano.')
    elif isinstance(kind, Integer):
        if isinstance(value, bool) or not isinstance(value, int) or not -(2**63) <= value < 2**63:
            _fail(f'{label}: intero non valido.')
        if (column.primary_key or column.name.endswith('_id')) and value <= 0:
            _fail(f'{label}: identificativo non valido.')
    elif isinstance(kind, Float):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            _fail(f'{label}: numero non valido.')
    elif isinstance(kind, DateTime):
        value = _datetime(value, label)
        if value.tzinfo is not None:
            try:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            except OverflowError:
                _fail(f'{label}: data/ora fuori intervallo.')
    elif isinstance(kind, String):
        if not isinstance(value, str) or len(value) > 2 * 1024 * 1024 or '\x00' in value:
            _fail(f'{label}: testo non valido.')
        if kind.length and len(value) > kind.length:
            _fail(f'{label}: testo troppo lungo.')
        if column.name in ('date', 'week_start'):
            _day(value, label)
        if column.name in ('synced_at', 'observed_at'):
            if column.name == 'observed_at' and len(value) == 10:
                _day(value, label)
            else:
                _datetime(value, label)
        if column.name.endswith('_json'):
            parsed = _parse_json(value)
            _safe_json_tree(parsed)
            expected = list if column.name in ('snapshot_json', 'legs_json') else dict
            if not isinstance(parsed, expected):
                _fail(f'{label}: struttura JSON non valida.')
    else:
        _fail(f'{label}: tipo non supportato.')
    return value


def _validate_relations(data, tables):
    ids = {name: {row.get('id') for row in rows} for name, rows in data.items()}
    links = {
        'activity_id': 'activities', 'other_id': 'activities', 'workout_id': 'workouts',
        'source_workout_id': 'workouts', 'goal_id': 'race_goals',
    }
    for table in tables:
        seen = {}
        unique = [tuple(column.name for column in table.primary_key.columns)]
        unique += [tuple(column.name for column in constraint.columns)
                   for constraint in table.constraints if isinstance(constraint, UniqueConstraint)]
        unique += [(column.name,) for column in table.columns if column.unique]
        unique += [tuple(column.name for column in index.columns) for index in table.indexes
                   if index.unique and index.dialect_options['sqlite'].get('where') is None]
        unique = list(dict.fromkeys(unique))
        for row in data[table.name]:
            for key, target in links.items():
                if row.get(key) is not None and row[key] not in ids.get(target, set()):
                    _fail(f'{table.name}.{key}: riferimento inesistente.')
            for columns in unique:
                values = tuple(row[key] for key in columns)
                if any(value is None for value in values):
                    continue
                if values in seen.setdefault(columns, set()):
                    _fail(f'{table.name}: chiave duplicata ({", ".join(columns)}).')
                seen[columns].add(values)
    for singleton in ('athlete_settings', 'iron_users', 'race_preparation'):
        if len(data.get(singleton, [])) > 1 or any(row['id'] != 1 for row in data.get(singleton, [])):
            _fail(f'{singleton}: è previsto il solo profilo personale 1.')
    active = [row['workout_id'] for row in data.get('workout_resolutions', []) if row['undone_at'] is None]
    if len(active) != len(set(active)):
        _fail('una seduta ha più riconciliazioni attive.')


def _validate_semantics(data):
    from app.services.planning import PlanningProfile
    from app.api.routes_iron import GoalInput
    from app.api.routes_experience import Checkin, Preparation
    from app.api.routes_progress import ResolutionInput, PreparationState
    from app.services.health_reports import HealthReportInput
    try:
        for row in data.get('athlete_settings', []):
            if row['revision'] < 0 or row['revision'] >= 2**63 - 1:
                raise ValueError()
            profile = PlanningProfile.model_validate(_parse_json(row['payload_json']))
            goal_id = profile.race_goal_id
            if goal_id is not None and goal_id not in {g['id'] for g in data.get('race_goals', [])}:
                raise ValueError()
        for row in data.get('race_goals', []):
            GoalInput.model_validate({key: value for key, value in row.items() if key not in ('id', 'legs_json')}
                                     | {'legs': _parse_json(row['legs_json'])})
        for row in data.get('athlete_checkins', []):
            checkin = Checkin.model_validate(_parse_json(row['payload_json']))
            if checkin.date and str(checkin.date) != row['date']:
                raise ValueError()
        for row in data.get('health_reports', []):
            HealthReportInput.model_validate({key: value for key, value in row.items()
                if key not in ('id', 'created_at', 'updated_at')})
            if row['updated_at'] < row['created_at']:
                raise ValueError()
        for row in data.get('race_preparation', []):
            Preparation.model_validate(_parse_json(row['payload_json']))
        for row in data.get('iron_users', []):
            ZoneInfo(row['timezone'])
        workouts = {row['id']: row for row in data.get('workouts', [])}
        for row in data.get('workout_resolutions', []):
            ResolutionInput.model_validate({'status': row['kind'], 'reason': row['reason']})
            if row['prior_status'] != 'planned' or (row['undone_at'] is None and workouts[row['workout_id']]['status'] != 'skipped'):
                raise ValueError()
            if row['undone_at'] is not None and row['undone_at'] < row['created_at']:
                raise ValueError()
        for row in data.get('goal_preparation_items', []):
            PreparationState.model_validate({'done': row['done'], 'notes': row['notes']})
            if (not row['key'] or len(row['key']) > 150 or len(row['context_hash']) != 64
                    or any(c not in '0123456789abcdef' for c in row['context_hash'])):
                raise ValueError()
    except (ValueError, TypeError, KeyError):
        _fail('profilo, obiettivo o diario non coerente con il formato dell’app.')


def validate_backup(db, raw):
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, 'Il backup supera 20 MB.')
    payload = _parse_json(raw)
    if not isinstance(payload, dict) or set(payload) != {'format_version', 'generated_at', 'data'}:
        _fail('intestazione o chiavi sconosciute.')
    if type(payload['format_version']) is not int or payload['format_version'] != 1:
        _fail('versione non supportata.')
    _datetime(payload['generated_at'], 'generated_at')
    source = payload['data']
    tables = tables_for(db)
    allowed = {table.name for table in tables}
    required = allowed.intersection(CORE_TABLES)
    if not isinstance(source, dict) or set(source) - allowed or required - set(source):
        _fail('tabelle sconosciute o tabelle personali mancanti.')
    data = {}; total = 0
    for table in tables:
        rows = source.get(table.name, [])
        if not isinstance(rows, list):
            _fail(f'{table.name}: serve un elenco di record.')
        total += len(rows)
        if total > MAX_RECORDS:
            _fail('troppi record (massimo 100000).')
        columns = {column.name: column for column in table.columns}
        data[table.name] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(columns):
                _fail(f'{table.name}: colonne sconosciute o mancanti.')
            data[table.name].append({key: _validate_value(columns[key], value, f'{table.name}.{key}')
                                     for key, value in row.items()})
    _validate_relations(data, tables)
    _validate_semantics(data)
    return {'format_version': 1, 'generated_at': payload['generated_at'], 'data': data}


def _summary(data):
    return [{'name': name, 'count': len(rows)} for name, rows in sorted(data.items())]


def preview_backup(db, raw):
    payload = validate_backup(db, raw)
    with _read_snapshot(db):
        digest = fingerprint(db)
    now = _now(); token = secrets.token_urlsafe(32); expires = now + timedelta(seconds=PREVIEW_SECONDS)
    with _PREVIEW_LOCK:
        for key in list(_PREVIEWS):
            if _PREVIEWS[key]['expires_at'] <= now:
                _PREVIEWS.pop(key)
        while len(_PREVIEWS) >= 3:
            _PREVIEWS.pop(next(iter(_PREVIEWS)))
        _PREVIEWS[token] = {'payload': payload, 'fingerprint': digest, 'expires_at': expires,
                            'database': str(db.get_bind().url), 'engine_id': id(db.get_bind())}
    return {'preview_token': token, 'expires_at': _stamp(expires), 'fingerprint': digest,
            'generated_at': payload['generated_at'], 'tables': _summary(payload['data']),
            'total_records': sum(map(len, payload['data'].values())),
            'message': 'Il ripristino sostituirà i dati personali. Connessioni e credenziali restano sul computer; i riferimenti locali agli invii Garmin saranno rimossi. Una copia SQLite sarà conservata prima del ripristino.'}


@contextmanager
def _sync_exclusion():
    from app.services.sync_jobs import LOCKS
    from app.api.routes_sync import SYNC_LOCK
    acquired = []
    try:
        for lock in (LOCKS['intervals'], LOCKS['strava'], SYNC_LOCK):
            if not lock.acquire(False):
                raise HTTPException(409, 'Attendi il completamento della sincronizzazione prima di ripristinare.')
            acquired.append(lock)
        yield
    finally:
        for lock in reversed(acquired):
            lock.release()


def _online_backup(db):
    filename = db.get_bind().url.database
    if not filename or filename == ':memory:':
        return False
    original = Path(filename).resolve()
    folder = original.parent / 'backups'
    folder.mkdir(exist_ok=True)
    target_path = folder / f'{original.stem}-before-restore-{_now():%Y%m%d%H%M%S%f}-{secrets.token_hex(3)}.db'
    try:
        with closing(sqlite3.connect(str(original))) as source, closing(sqlite3.connect(str(target_path))) as target:
            source.backup(target)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    return True


def restore_backup(db, token):
    # Taking the token consumes it, including on stale/failed attempts. A fresh
    # preview is deliberately required before another destructive replacement.
    with _PREVIEW_LOCK:
        preview = _PREVIEWS.pop(token, None)
    if (not preview or preview['expires_at'] <= _now() or
        preview['database'] != str(db.get_bind().url) or preview['engine_id'] != id(db.get_bind())):
        raise HTTPException(409, 'Anteprima scaduta o già utilizzata. Carica nuovamente il backup.')
    if db.get_bind().dialect.name != 'sqlite':
        raise HTTPException(503, 'Ripristino disponibile soltanto sul database SQLite locale.')
    try:
        with _sync_exclusion():
            db.rollback()
            with db.begin():
                # Prevent any other SQLite writer from changing the fingerprint
                # or a planner snapshot while the replacement is in progress.
                db.connection().exec_driver_sql('BEGIN IMMEDIATE')
                if fingerprint(db) != preview['fingerprint']:
                    raise HTTPException(409, 'I dati sono cambiati dopo l’anteprima. Carica nuovamente il backup per verificarli.')
                backup_created = _online_backup(db)
                old = db.get(planning_models.AthleteSettings, 1)
                old_revision = old.revision if old else 0
                data = preview['payload']['data']
                profile_rows = data.get('athlete_settings', [])
                imported_revision = profile_rows[0]['revision'] if profile_rows else 0
                revision = max(old_revision, imported_revision) + 1
                if revision >= 2**63 - 1:
                    raise HTTPException(409, 'Revisione del profilo fuori intervallo.')
                tables = tables_for(db)
                removed_remote = db.query(GarminDelivery).delete(synchronize_session=False)
                for table in reversed(tables):
                    db.execute(table.delete())
                for table in tables:
                    rows = data[table.name]
                    if table.name == 'athlete_settings':
                        if not rows:
                            from app.services.planning import PlanningProfile
                            rows = [{'id': 1, 'payload_json': _json(PlanningProfile().model_dump(mode='json')),
                                     'updated_at': _now().replace(tzinfo=None)}]
                        rows = [{**row, 'revision': revision} for row in rows]
                        data[table.name] = rows
                    if rows:
                        db.execute(table.insert(), rows)
            db.expire_all()
    except HTTPException:
        raise
    except (SQLAlchemyError, OSError, sqlite3.Error):
        db.rollback()
        raise HTTPException(503, 'Ripristino non completato: nessun dato è stato sostituito. Verifica che il database sia disponibile e ripeti l’anteprima.') from None
    return {'restored': True, 'tables': _summary(data), 'total_records': sum(map(len, data.values())),
            'backup_created': backup_created, 'profile_revision': revision,
            'removed_remote_references': removed_remote, 'reload_required': True,
            'message': 'Dati personali ripristinati. Ricarica l’app prima di continuare. Connessioni conservate; nessun allenamento è stato pubblicato o revocato sui servizi remoti.'}
