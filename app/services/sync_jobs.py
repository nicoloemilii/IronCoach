"""One worker per provider; persisted run status, bounded automatic schedule."""
import json
import os
from datetime import datetime, timedelta
from threading import Event, Thread, Lock
from sqlalchemy.orm import sessionmaker, Session
from fastapi import HTTPException
from app.db.database import SessionLocal
from app.db.iron_models import SyncRun

LOCKS = {'intervals': Lock(), 'strava': Lock()}
STOP = Event()
THREAD = None


def launch(source, days, resources, db, background_tasks=None):
    if source == 'intervals':
        from app.api.routes_sync import SYNC_LOCK
        if SYNC_LOCK.locked(): raise HTTPException(409, 'Connessione o sincronizzazione Intervals.icu già in corso.')
    if not LOCKS[source].acquire(False): raise HTTPException(409, 'Sincronizzazione già in corso.')
    try:
        row = SyncRun(source=source, days=days, resources=resources, status='running')
        db.add(row); db.commit(); db.refresh(row)
        factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
        if background_tasks is not None: background_tasks.add_task(execute, row.id, factory)
        else: execute(row.id, factory)
        return {'run_id': row.id, 'status': 'running' if background_tasks else 'finished'}
    except Exception:
        if LOCKS[source].locked(): LOCKS[source].release()
        raise


def execute(identifier, factory=SessionLocal):
    source = None
    try:
        with factory() as db:
            row = db.get(SyncRun, identifier)
            if not row: return
            source = row.source; errors = []; result = {}
            try:
                if source == 'strava':
                    from app.integrations import strava
                    result = strava.sync(row.days)
                else:
                    from app.api.routes_sync import sync_activity_records, SYNC_LOCK, row_for, stamp
                    from app.integrations.intervals import get_remote
                    from app.services.health import import_wellness
                    from app.services.athlete_clock import athlete_today
                    state = row_for(db); previous_success = state.last_success_at
                    # Legacy activity path still owns the original lock; wellness uses it too.
                    if row.resources in ('activities', 'both'):
                        try:
                            result = sync_activity_records(row.days, db)
                            errors.extend(result.get('errors', []))
                        except HTTPException as error:
                            errors.append({'resource': 'activities', 'error': error.detail})
                            if error.status_code == 429: result['retry_after'] = 86400
                    if row.resources in ('wellness', 'both'):
                        if not SYNC_LOCK.acquire(False):
                            errors.append({'resource': 'wellness', 'error': 'Sincronizzazione occupata.'})
                        else:
                            try:
                                end = athlete_today()
                                remote = get_remote('/wellness', params={'oldest': str(end - timedelta(days=row.days)), 'newest': str(end)})
                                count, issues = import_wellness(db, remote)
                                result['wellness_days'] = count; errors.extend(issues)
                            except Exception as error:
                                if getattr(error, 'status', None) == 429: result['retry_after'] = 86400
                                db.rollback(); errors.append({'resource': 'wellness', 'error': 'Wellness non sincronizzato: verifica connessione e autorizzazioni.'})
                            finally: SYNC_LOCK.release()
                    result['errors'] = errors
                    if errors: result['status'] = 'partial' if result.get('new_activities', 0) or result.get('already_synced_or_matched', 0) or result.get('wellness_days', 0) else 'error'
                    else: result['status'] = 'success'
                    state = row_for(db); state.last_attempt_at = stamp(); state.last_result_json = json.dumps(result)
                    # The legacy activity path may have marked success before a wellness failure.
                    if not errors: state.last_success_at = stamp()
                    else: state.last_success_at = previous_success
                status = result.get('status', 'success')
            except Exception as error:
                db.rollback()
                from app.integrations.strava import StravaError
                result = {'message': error.message if isinstance(error, StravaError) else 'Sincronizzazione non completata. Verifica collegamento e permessi.'}
                if isinstance(error, StravaError) and error.retry_after: result['retry_after'] = error.retry_after
                status = 'error'
            row = db.get(SyncRun, identifier); row.status = status; row.result_json = json.dumps(result); row.finished_at = datetime.utcnow(); db.commit()
    finally:
        if source and LOCKS[source].locked(): LOCKS[source].release()


def automatic_tick():
    from app.integrations import intervals_credentials, strava
    with SessionLocal() as db:
        for source in ('intervals', 'strava'):
            try:
                configured = bool(intervals_credentials.credentials().get('api_key')) if source == 'intervals' else bool(strava.read_tokens().get('refresh_token'))
                if not configured: continue
                last = db.query(SyncRun).filter_by(source=source).order_by(SyncRun.id.desc()).first()
                delay = 1800
                if last and last.result_json:
                    delay = max(delay, json.loads(last.result_json).get('retry_after', 0))
                if last and (datetime.utcnow() - (last.finished_at or last.started_at)).total_seconds() < delay: continue
                successful = db.query(SyncRun).filter_by(source=source, status='success').order_by(SyncRun.id.desc()).first()
                # Strava display refresh replaces the complete requested range to reflect deletions.
                days = 90 if source == 'strava' or not successful else 7
                launch(source, days, 'both' if source == 'intervals' else 'activities', db)
            except Exception:
                db.rollback()  # A failed provider must not stop the personal server.


def start():
    global THREAD
    if os.getenv('IRON_AUTO_SYNC', '1') == '0': return
    # Interrupted runs cannot be reported as still progressing after restart.
    with SessionLocal() as db:
        for row in db.query(SyncRun).filter_by(status='running'):
            row.status = 'error'; row.finished_at = datetime.utcnow(); row.result_json = json.dumps({'message': 'Sincronizzazione interrotta dal riavvio del server.'})
        db.commit()
    STOP.clear()
    def loop():
        while not STOP.is_set():
            automatic_tick()
            STOP.wait(30)
    THREAD = Thread(target=loop, name='iron-sync', daemon=True); THREAD.start()


def stop():
    STOP.set()
    if THREAD: THREAD.join(timeout=1)
