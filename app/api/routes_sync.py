import hashlib
import ipaddress
import json
import math
from datetime import datetime
from threading import Lock
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Activity
from app.db.sync_models import IntervalsState
from app.db.repository import save_activity
from app.integrations import intervals_credentials as vault
from app.integrations.intervals import activity_to_data, list_activities, verify_connection, IntervalsError

router = APIRouter(tags=['Intervals.icu'])
SYNC_LOCK = Lock()


def stamp():
    return datetime.utcnow().isoformat() + 'Z'


def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest() if key else None


def local_only(request: Request):
    if getattr(request.state,'account',None):
        return  # Hosted identity and CSRF were checked at the ASGI boundary.
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        local = False
    if not local:
        raise HTTPException(403, 'Configura e sincronizza Intervals.icu dal computer che ospita l’app.')
    origin = request.headers.get('origin')
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
            raise HTTPException(403, 'Origine della richiesta non autorizzata.')


def config():
    try:
        return vault.credentials()
    except (RuntimeError, OSError, ValueError):
        raise HTTPException(503, 'Impossibile leggere la chiave protetta. Ricollega Intervals.icu dallo stesso utente Windows.') from None


def row_for(db):
    row = db.get(IntervalsState, 1)
    if not row:
        row = IntervalsState(id=1); db.add(row)
    return row


@router.get('/integrations/intervals/status')
def status(db: Session = Depends(get_db)):
    settings = config(); row = db.get(IntervalsState, 1)
    configured = bool(settings.get('api_key'))
    verified = bool(configured and row and row.credential_hash == fingerprint(settings['api_key']) and row.verified_at)
    return {'configured': configured, 'connected': verified, 'credential_source': settings.get('source'),
            'athlete_id': row.athlete_id if verified else None, 'athlete_name': row.athlete_name if verified else None,
            'verified_at': row.verified_at if verified else None,
            'last_attempt_at': row.last_attempt_at if row else None, 'last_success_at': row.last_success_at if row else None,
            'last_result': json.loads(row.last_result_json) if row and row.last_result_json else None,
            'message': 'Collegamento verificato.' if verified else 'Chiave presente: verifica il collegamento.' if configured else 'Inserisci la chiave API personale per collegare Intervals.icu.'}


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr
    athlete_id: str = Field('0', pattern=r'^[A-Za-z0-9_-]{1,40}$')


@router.post('/integrations/intervals/connect', dependencies=[Depends(local_only)])
def connect(payload: Connection, db: Session = Depends(get_db)):
    from app.services.sync_jobs import LOCKS
    if LOCKS['intervals'].locked():
        raise HTTPException(409, 'Attendi il completamento della sincronizzazione Intervals.icu.')
    key = payload.api_key.get_secret_value().strip()
    if not 8 <= len(key) <= 512 or any(c.isspace() for c in key):
        raise HTTPException(422, 'Inserisci una chiave API valida, senza spazi.')
    if not SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Una connessione o sincronizzazione è già in corso.')
    try:
        athlete = verify_connection({'api_key': key, 'athlete_id': payload.athlete_id})
        vault.store(key, payload.athlete_id)
        row = row_for(db)
        if row.athlete_id != athlete['id']:
            row.last_success_at = None; row.last_attempt_at = None; row.last_result_json = None
        row.athlete_id = athlete['id']; row.athlete_name = athlete['name']
        row.credential_hash = fingerprint(key); row.verified_at = stamp(); db.commit()
        return status(db)
    except IntervalsError as error:
        raise HTTPException(error.status, error.message) from None
    except (RuntimeError, OSError):
        raise HTTPException(503, 'Profilo verificato, ma impossibile salvare la chiave protetta sul server.') from None
    finally:
        SYNC_LOCK.release()


@router.post('/integrations/intervals/verify', dependencies=[Depends(local_only)])
def verify(db: Session = Depends(get_db)):
    from app.services.sync_jobs import LOCKS
    if LOCKS['intervals'].locked():
        raise HTTPException(409, 'Attendi il completamento della sincronizzazione Intervals.icu.')
    try:
        settings = config(); athlete = verify_connection(settings); row = row_for(db)
        row.athlete_id = athlete['id']; row.athlete_name = athlete['name']
        row.credential_hash = fingerprint(settings['api_key']); row.verified_at = stamp(); db.commit()
        return status(db)
    except IntervalsError as error:
        raise HTTPException(error.status, error.message) from None


@router.delete('/integrations/intervals', dependencies=[Depends(local_only)])
def disconnect(db: Session = Depends(get_db)):
    from app.services.sync_jobs import LOCKS
    if LOCKS['intervals'].locked():
        raise HTTPException(409, 'Attendi il completamento della sincronizzazione Intervals.icu.')
    if not SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Attendi la fine della sincronizzazione prima di scollegare.')
    try:
        vault.disconnect()
        row = row_for(db); row.credential_hash = None; row.verified_at = None; db.commit()
        return status(db)
    except (RuntimeError, OSError):
        raise HTTPException(503, 'Impossibile scollegare la configurazione protetta.') from None
    finally:
        SYNC_LOCK.release()


@router.post('/sync/intervals', dependencies=[Depends(local_only)])
def sync_intervals(days: int = Query(90, ge=1, le=3650), db: Session = Depends(get_db),
                   resources: Literal['activities', 'wellness', 'both'] = 'activities',
                   background: bool = False, background_tasks: BackgroundTasks = None):
    if background or resources != 'activities':
        from app.services.sync_jobs import launch
        return launch('intervals', days, resources, db, background_tasks if background else None)
    from app.services.sync_jobs import LOCKS
    if not LOCKS['intervals'].acquire(False):
        raise HTTPException(409, 'Sincronizzazione già in corso.')
    try:
        return sync_activity_records(days, db)
    finally:
        LOCKS['intervals'].release()


@router.post('/sync/refresh', dependencies=[Depends(local_only)])
def refresh_connected(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Refresh connected providers; reuse an active job rather than duplicate it."""
    from app.services.sync_jobs import launch, LOCKS
    from app.integrations import strava
    from app.db.iron_models import SyncRun
    runs = []; messages = []
    for source in ('intervals', 'strava'):
        try:
            configured = bool(config().get('api_key')) if source == 'intervals' else bool(strava.read_tokens().get('refresh_token'))
            if not configured: continue
            if LOCKS[source].locked():
                active = db.query(SyncRun).filter_by(source=source, status='running').order_by(SyncRun.id.desc()).first()
                if active:
                    runs.append({'source':source, 'run_id':active.id, 'reused':True}); continue
                messages.append(f'{source}: operazione già in corso; riprova al termine.'); continue
            job = launch(source, 35 if source == 'intervals' else 90, 'both' if source == 'intervals' else 'activities', db, background_tasks)
            runs.append({'source':source, 'run_id':job['run_id'], 'reused':False})
        except Exception:
            db.rollback(); messages.append(f'{source}: sincronizzazione non avviata. Verifica collegamento e autorizzazioni.')
    return {'runs':runs, 'messages':messages}


def sync_activity_records(days, db):
    if not SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Sincronizzazione già in corso. Attendi il completamento.')
    try:
        row = row_for(db); row.last_attempt_at = stamp(); db.commit()
        try:
            remote = list_activities(days)
        except IntervalsError as error:
            row.last_result_json = json.dumps({'status': 'error', 'message': error.message})
            if error.status in (401, 403):
                row.verified_at = None
            db.commit()
            raise HTTPException(error.status, error.message) from None
        created = matched = 0; errors = []
        for item in remote:
            try:
                if not isinstance(item, dict) or not item.get('id') or not (item.get('start_date') or item.get('start_date_local')):
                    raise ValueError('Attività incompleta o non accessibile tramite API.')
                data = activity_to_data(item)
                duration = float(data.get('duration_seconds') or 0)
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError('Durata dell’attività assente o non valida.')
                for name in ('distance_meters', 'load', 'avg_power', 'normalized_power', 'avg_hr', 'elevation_gain_meters'):
                    value = data.get(name)
                    if value is not None and (not math.isfinite(float(value)) or float(value) < 0):
                        raise ValueError('Valori numerici dell’attività non validi.')
                before = db.query(Activity).count()
                row = save_activity(db, data)
                # Refresh observations owned by this provider; preserve richer FIT originals.
                if row.source == 'intervals':
                    for name, value in data.items():
                        if name not in ('id', 'source_id', 'source') and value is not None:
                            setattr(row, name, value)
                    db.commit()
                if db.query(Activity).count() > before:
                    created += 1
                else:
                    matched += 1
            except Exception:
                db.rollback()
                errors.append({'id': str(item.get('id', ''))[:100] if isinstance(item, dict) else '',
                               'error': 'Attività non importata: dati mancanti, non validi o non disponibili tramite API.'})
        row = row_for(db)
        result = {'source': 'intervals', 'remote_activities': len(remote), 'new_activities': created,
                  'already_synced_or_matched': matched, 'errors': errors, 'days': days,
                  'status': 'partial' if errors else 'success', 'finished_at': stamp()}
        row.last_result_json = json.dumps(result)
        if not errors:
            row.last_success_at = result['finished_at']
        db.commit()
        return result
    finally:
        SYNC_LOCK.release()
