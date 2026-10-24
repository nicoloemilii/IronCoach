"""Explicit historical analysis; bounded workers, derived data, workspace ownership.

The single-process beta keeps live worker tokens in memory. Persisted jobs from
a previous process are reported as interrupted, never silently restarted.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass, field
import json
from threading import BoundedSemaphore, Event, RLock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.effort_lock import database_key, database_lock
from app.db.effort_models import EffortAnalysis, EffortScan
from app.db.models import Activity
from app.services import athlete_clock

ACTIVE = ('queued', 'running', 'cancel_requested')
_GUARD = RLock()
_CAPACITY = BoundedSemaphore(10)  # two running and at most eight waiting
_RUNS = {}
_POOL = None
_ACCEPTING = True


@dataclass
class _Run:
    factory: object
    stopped: Event = field(default_factory=Event)


def start():
    global _ACCEPTING
    with _GUARD:
        _ACCEPTING = True


def stop():
    global _ACCEPTING
    with _GUARD:
        _ACCEPTING = False
        for run in _RUNS.values():
            run.stopped.set()


def _present(bind, identifier):
    with _GUARD:
        return _RUNS.get((database_key(bind), identifier))


def scan_payload(row, bind):
    if row is None:
        return None
    result = {key: getattr(row, key) for key in (
        'id', 'status', 'mode', 'refresh', 'total', 'processed', 'with_power',
        'unavailable', 'errors', 'reused', 'started_at', 'finished_at', 'error_code')}
    if row.status in ACTIVE and _present(bind, row.id) is None:
        result.update(status='interrupted', error_code='server_restart')
    for key in ('started_at', 'finished_at'):
        value = result[key]
        result[key] = value.isoformat() + 'Z' if value else None
    return result


def scans(db):
    rows = db.scalars(select(EffortScan).order_by(EffortScan.started_at.desc(), EffortScan.id.desc()).limit(12)).all()
    values = [scan_payload(row, db.get_bind()) for row in rows]
    return {'active_scan': next((row for row in values if row['status'] in ACTIVE), None),
            'last_scan': values[0] if values else None}


def get_scan(db, identifier):
    row = db.get(EffortScan, identifier)
    if row is None:
        raise HTTPException(404, 'Analisi non trovata in questo profilo.')
    return scan_payload(row, db.get_bind())


def launch(db, *, mode='stored', refresh=False):
    from app.services.effort_records import provider_available
    global _POOL
    if mode not in ('stored', 'intervals'):
        raise HTTPException(422, 'Modalità di analisi non valida.')
    if mode == 'intervals' and not provider_available():
        raise HTTPException(409, 'Collega Intervals.icu prima di recuperare le serie.')
    bind = db.get_bind()
    with database_lock(bind):
        db.rollback()
        existing = db.scalars(select(EffortScan).where(EffortScan.status.in_(ACTIVE))).all()
        for row in existing:
            if _present(bind, row.id) is not None:
                return scan_payload(row, bind)
            row.status = 'interrupted'
            row.finished_at = row.updated_at = athlete_clock.utc_now_naive()
            row.error_code = 'server_restart'
        db.commit()
        with _GUARD:
            if not _ACCEPTING or not _CAPACITY.acquire(False):
                raise HTTPException(503, 'Analisi occupata. Riprova tra poco.')
            if _POOL is None:
                _POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='iron-records')
        identifier = uuid4().hex
        registry_key = (database_key(bind), identifier)
        try:
            now = athlete_clock.utc_now_naive()
            identifiers = list(db.scalars(select(Activity.id).where(Activity.start_time <= now).order_by(Activity.id)))
            row = EffortScan(id=identifier, status='queued', mode=mode, refresh=refresh,
                total=len(identifiers), processed=0, with_power=0, unavailable=0, errors=0, reused=0,
                max_activity_id=max(identifiers, default=0), activity_ids_json=json.dumps(identifiers),
                cutoff_utc=now, started_at=now, updated_at=now)
            db.add(row)
            db.commit()
            run = _Run(sessionmaker(bind=bind, expire_on_commit=False, autoflush=False))
            with _GUARD:
                _RUNS[registry_key] = run
            context = copy_context()
            _POOL.submit(context.run, _execute, identifier, run, bind)
            return scan_payload(row, bind)
        except Exception:
            db.rollback()
            with _GUARD:
                _RUNS.pop(registry_key, None)
            _CAPACITY.release()
            raise


def cancel(db, identifier):
    with database_lock(db.get_bind()):
        db.rollback()
        row = db.get(EffortScan, identifier)
        if row is None:
            raise HTTPException(404, 'Analisi non trovata in questo profilo.')
        if row.status in ACTIVE:
            row.status = 'cancel_requested' if _present(db.get_bind(), identifier) else 'interrupted'
            row.updated_at = athlete_clock.utc_now_naive()
            if row.status == 'interrupted':
                row.finished_at = row.updated_at
                row.error_code = 'server_restart'
            db.commit()
        return scan_payload(row, db.get_bind())


@contextmanager
def _provider_gate(mode):
    acquired = []
    if mode == 'intervals':
        from app.services.sync_jobs import LOCKS
        from app.api.routes_sync import SYNC_LOCK
        try:
            for gate in (LOCKS['intervals'], SYNC_LOCK):
                if not gate.acquire(False):
                    raise HTTPException(409, 'provider_busy')
                acquired.append(gate)
            yield
        finally:
            for gate in reversed(acquired):
                gate.release()
    else:
        yield


def _finish(identifier, run, bind, status, error_code=None):
    with database_lock(bind), run.factory() as db:
        row = db.get(EffortScan, identifier)
        if row is not None and row.status in ACTIVE:
            row.status = status
            row.error_code = error_code
            row.finished_at = row.updated_at = athlete_clock.utc_now_naive()
            db.commit()


def _write_step(identifier, activity_id, fingerprint, result, reused, run, bind):
    """Restore and this short transaction share the same database lock.

    A restore clears job UUIDs. A worker which still holds an older snapshot
    cannot recreate its job or write a derived result into the restored data.
    """
    from app.services.effort_records import cache_valid, source_snapshot
    with database_lock(bind), run.factory() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = db.get(EffortScan, identifier)
        if row is None or row.status not in ACTIVE:
            return False
        if run.stopped.is_set():
            row.status = 'interrupted'; row.error_code = 'server_restart'
            row.finished_at = row.updated_at = athlete_clock.utc_now_naive()
            db.commit()
            return False
        activity = db.get(Activity, activity_id)
        current = source_snapshot(db, activity) if activity else None
        valid = (current is not None and current['fingerprint'] == fingerprint
                 and athlete_clock.as_utc_naive(activity.start_time) <= row.cutoff_utc)
        cached = db.get(EffortAnalysis, activity_id)
        retain_previous = (cached is not None and valid and result is not None
                           and str(result.get('error_code') or '').startswith('provider_')
                           and cache_valid(cached, current, row.cutoff_utc))
        if result is not None and valid and not reused and not retain_previous:
            values = {key: value for key, value in result.items() if key not in ('stop_batch', 'provider_status')}
            values['payload_json'] = json.dumps(values['payload_json'], allow_nan=False, separators=(',', ':'))
            if cached is None:
                db.add(EffortAnalysis(activity_id=activity_id, **values))
            else:
                for key, value in values.items():
                    setattr(cached, key, value)
        row.processed += 1
        available = valid and result is not None and result.get('status') == 'available'
        row.with_power += int(available)
        row.unavailable += int(not available)
        row.reused += int(reused and valid)
        row.errors += int(not valid or (result is not None and bool(result.get('error_code'))))
        row.updated_at = athlete_clock.utc_now_naive()
        cancelled = row.status == 'cancel_requested'
        if cancelled:
            row.status = 'cancelled'; row.finished_at = row.updated_at
        db.commit()
        return not cancelled


def _execute(identifier, run, bind):
    from app.services.effort_records import analyze_snapshot, cache_valid, source_snapshot
    try:
        with database_lock(bind), run.factory() as db:
            row = db.get(EffortScan, identifier)
            if row is None or row.status not in ACTIVE:
                return
            if row.status == 'cancel_requested':
                _finish(identifier, run, bind, 'cancelled'); return
            row.status = 'running'; row.updated_at = athlete_clock.utc_now_naive()
            identifiers = json.loads(row.activity_ids_json)
            cutoff, mode, refresh = row.cutoff_utc, row.mode, row.refresh
            db.commit()
        for activity_id in identifiers:
            if run.stopped.is_set():
                _finish(identifier, run, bind, 'interrupted', 'server_restart'); return
            with database_lock(bind), run.factory() as db:
                row = db.get(EffortScan, identifier)
                if row is None or row.status not in ACTIVE:
                    return
                if row.status == 'cancel_requested':
                    _finish(identifier, run, bind, 'cancelled'); return
                activity = db.get(Activity, activity_id)
                snapshot = source_snapshot(db, activity) if activity else None
                previous = db.get(EffortAnalysis, activity_id)
                reuse = (not refresh and snapshot is not None and previous is not None
                         and cache_valid(previous, snapshot, cutoff))
                result = None
                if reuse:
                    payload = json.loads(previous.payload_json)
                    clipped = (payload.get('stream_quality') or {}).get('clipped_timestamp_count', 0) > 0
                    # A partial but valid remotely derived curve cannot be
                    # improved from a local summary that has no raw series.
                    # Keep its dated provenance until an explicit remote scan.
                    reuse = not clipped or (mode == 'stored' and previous.source_fetched_at is not None)
                    # A local missing-stream result must not prevent an explicit
                    # request to retrieve the provider's detailed series.
                    if mode == 'intervals' and (previous.status in ('missing_streams', 'unavailable')
                            or previous.status == 'no_power' and previous.source_fetched_at is None):
                        reuse = False
                if reuse:
                    result = {'status': previous.status, 'error_code': previous.error_code}
            if snapshot is not None and not reuse:
                with _provider_gate(mode):
                    result = analyze_snapshot(snapshot, now=cutoff, mode=mode, refresh=refresh)
                # The numerical cutoff is frozen when the scan starts. The
                # provenance timestamp records completion of this activity,
                # which may be much later in a large archive.
                result['analyzed_at'] = athlete_clock.utc_now_naive()
                if result.get('source_fetched_at') is not None:
                    result['source_fetched_at'] = result['analyzed_at']
            if not _write_step(identifier, activity_id, snapshot['fingerprint'] if snapshot else None,
                               result, reuse, run, bind):
                return
            if result and result.get('stop_batch'):
                _finish(identifier, run, bind, 'failed', result.get('error_code') or 'provider_unavailable'); return
            # No retry loop. Remote activities are sequential, and cancellation
            # remains responsive between requests/calculations.
            if mode == 'intervals' and run.stopped.wait(.25):
                _finish(identifier, run, bind, 'interrupted', 'server_restart'); return
        _finish(identifier, run, bind, 'completed')
    except HTTPException as error:
        _finish(identifier, run, bind, 'failed', 'provider_busy' if error.detail == 'provider_busy' else 'analysis_failed')
    except Exception:
        # Provider response bodies, credentials and raw telemetry never enter
        # job errors or logs. Completed earlier rows remain available.
        _finish(identifier, run, bind, 'failed', 'analysis_failed')
    finally:
        with _GUARD:
            _RUNS.pop((database_key(bind), identifier), None)
        _CAPACITY.release()
