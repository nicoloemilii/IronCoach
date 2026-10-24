"""Independent worker lifecycle probes; file SQLite and synthetic source only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from threading import BoundedSemaphore, Event, Lock
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base, WORKSPACE, WORKSPACE_KEY
from app.db.effort_models import EffortAnalysis, EffortScan
from app.db.iron_models import ActivityObservation
from app.db.models import Activity
from app.db.planning_models import AthleteSettings
from app.services import backups, effort_jobs as jobs, effort_records as records

NOW = datetime(2026, 10, 23, 10)


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        Event().wait(.005)
    raise AssertionError('Synthetic worker did not reach the expected state')


@pytest.fixture
def worker_env(monkeypatch, tmp_path):
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='review-records')
    releases, engines = [], []
    monkeypatch.setattr(jobs, '_POOL', pool)
    monkeypatch.setattr(jobs, '_CAPACITY', BoundedSemaphore(10))
    monkeypatch.setattr(jobs, '_RUNS', {})
    monkeypatch.setattr(jobs, '_ACCEPTING', True)
    monkeypatch.setattr(jobs.athlete_clock, 'utc_now_naive', lambda: NOW)
    monkeypatch.setattr(records, 'provider_available', lambda: True)
    monkeypatch.setattr(records, 'cache_valid', lambda *args: False, raising=False)
    def forbidden_provider(*args, **kwargs):
        raise AssertionError('Tests must explicitly stub remote analysis')
    monkeypatch.setattr(records.intervals, 'get_activity_remote', forbidden_provider)
    def make_db(name, count=2):
        engine = create_engine('sqlite:///' + str(tmp_path / (name + '.db')),
            connect_args={'check_same_thread':False, 'timeout':5})
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(AthleteSettings(id=1,revision=0,payload_json='{}'))
            for identifier in range(1,count + 1):
                db.add(Activity(id=identifier,source='intervals',source_id='review-' + str(identifier),
                    name=name + str(identifier),sport='cycling',start_time=NOW-timedelta(days=identifier),duration_seconds=10))
                db.add(ActivityObservation(activity_id=identifier,source_key='intervals:review-' + str(identifier),
                    payload_json=json.dumps({'elapsed_time':10,'streams':[
                        {'type':'time','data':[0,4,10]}, {'type':'watts','data':[0,120,0]}]})))
            db.commit()
        engines.append(engine)
        return engine
    yield make_db, releases
    jobs.stop()
    for release in releases:
        release.set()
    pool.shutdown(wait=True)
    assert jobs._RUNS == {}
    for engine in engines:
        engine.dispose()


def launch(engine, **kwargs):
    with Session(engine) as db:
        return jobs.launch(db, **kwargs)['id']


def terminal(engine, identifier):
    def read():
        with Session(engine) as db:
            row = db.get(EffortScan,identifier)
            if row is None:
                return 'deleted'
            return jobs.scan_payload(row,engine) if row.status not in jobs.ACTIVE else None
    value = wait_for(read)
    wait_for(lambda: jobs._present(engine,identifier) is None)
    return value


def pause_first(monkeypatch, releases):
    arrived, release = Event(), Event(); releases.append(release)
    original = records.analyze_snapshot
    calls = []
    def analyze(snapshot, **kwargs):
        calls.append(snapshot['activity'].id)
        if len(calls) == 1:
            arrived.set()
            assert release.wait(4), 'test failed to release analysis'
        return original(snapshot, **kwargs)
    monkeypatch.setattr(records, 'analyze_snapshot', analyze)
    return arrived, release, calls


def test_changed_payload_same_source_id_is_discarded_before_upsert(worker_env,monkeypatch):
    make_db,releases = worker_env; engine = make_db('source-change',count=1)
    arrived,release,calls = pause_first(monkeypatch,releases)
    identifier = launch(engine); assert arrived.wait(2)
    with Session(engine) as db:
        observation = db.query(ActivityObservation).one()
        observation.payload_json = observation.payload_json.replace('120','999')
        db.commit()
    release.set(); result = terminal(engine,identifier)
    assert result['status'] == 'completed' and result['processed'] == result['total'] == 1
    assert result['with_power'] == 0 and result['unavailable'] == result['errors'] == 1
    with Session(engine) as db:
        assert db.query(EffortAnalysis).count() == 0


def test_running_cancel_finishes_current_activity_only(worker_env,monkeypatch):
    make_db,releases = worker_env; engine = make_db('cancel-running',count=2)
    arrived,release,calls = pause_first(monkeypatch,releases)
    identifier = launch(engine); assert arrived.wait(2)
    with Session(engine) as db:
        assert jobs.cancel(db,identifier)['status'] == 'cancel_requested'
    release.set(); result = terminal(engine,identifier)
    assert result['status'] == 'cancelled' and result['processed'] == 1 and result['total'] == 2
    assert calls == [1]
    with Session(engine) as db:
        assert db.query(EffortAnalysis).count() == 1


def test_deleted_launch_candidate_still_counts_unavailable(worker_env,monkeypatch):
    make_db,releases = worker_env; engine = make_db('deleted',count=2)
    arrived,release,calls = pause_first(monkeypatch,releases)
    identifier = launch(engine); assert arrived.wait(2)
    with Session(engine) as db:
        db.query(ActivityObservation).filter_by(activity_id=2).delete()
        db.query(Activity).filter_by(id=2).delete(); db.commit()
    release.set(); result = terminal(engine,identifier)
    assert result['status'] == 'completed' and result['processed'] == result['total'] == 2
    assert result['with_power'] == 1 and result['unavailable'] == result['errors'] == 1
    assert calls == [1]


def test_shutdown_does_not_write_current_snapshot_or_start_next(worker_env,monkeypatch):
    make_db,releases = worker_env; engine = make_db('shutdown',count=2)
    arrived,release,calls = pause_first(monkeypatch,releases)
    identifier = launch(engine); assert arrived.wait(2)
    jobs.stop(); release.set(); result = terminal(engine,identifier)
    assert result['status'] == 'interrupted' and result['error_code'] == 'server_restart'
    assert result['processed'] == 0 and calls == [1]
    with Session(engine) as db:
        assert db.query(EffortAnalysis).count() == 0


def test_two_databases_capture_context_and_never_cross_results(worker_env,monkeypatch):
    make_db,_ = worker_env; one,two = make_db('workspace-one',1),make_db('workspace-two',1)
    original = records.analyze_snapshot
    seen = []
    def analyze(snapshot,**kwargs):
        seen.append((snapshot['activity'].name,WORKSPACE.get(),WORKSPACE_KEY.get()))
        return original(snapshot,**kwargs)
    monkeypatch.setattr(records,'analyze_snapshot',analyze)
    identifiers = []
    for engine,workspace,key in [(one,'a'*32,b'workspace-one'),(two,'b'*32,b'workspace-two')]:
        workspace_token = WORKSPACE.set(workspace); key_token = WORKSPACE_KEY.set(key)
        try:
            identifiers.append(launch(engine))
        finally:
            WORKSPACE_KEY.reset(key_token); WORKSPACE.reset(workspace_token)
    for engine,identifier in zip((one,two),identifiers):
        assert terminal(engine,identifier)['status'] == 'completed'
        with Session(engine) as db:
            assert db.query(EffortAnalysis).count() == 1
            assert db.query(EffortScan).count() == 1
            assert db.get(EffortScan,identifiers[1] if engine is one else identifiers[0]) is None
    assert sorted(seen) == [('workspace-one1','a'*32,b'workspace-one'),('workspace-two1','b'*32,b'workspace-two')]


def test_provider_fatal_result_stops_once_and_preserves_prior_curve(worker_env,monkeypatch):
    make_db,_ = worker_env; engine = make_db('rate-limit',count=3)
    original = records.analyze_snapshot; calls = []
    def analyze(snapshot,**kwargs):
        calls.append(snapshot['activity'].id)
        if len(calls) == 2:
            result = records._empty_analysis(snapshot,kwargs['now'],'unavailable','provider_rate_limited')
            result.update(stop_batch=True,provider_status=429)
            return result
        return original(snapshot,now=kwargs['now'],mode='stored')
    monkeypatch.setattr(records,'analyze_snapshot',analyze)
    identifier = launch(engine,mode='intervals')
    result = terminal(engine,identifier)
    assert result['status'] == 'failed' and result['error_code'] == 'provider_rate_limited'
    assert result['processed'] == 2 and result['total'] == 3 and result['with_power'] == 1
    assert calls == [1,2]
    with Session(engine) as db:
        assert db.get(EffortAnalysis,1).status == 'available'
        assert db.get(EffortAnalysis,3) is None


def test_restore_while_computing_removes_token_and_blocks_late_write(worker_env,monkeypatch):
    make_db,releases = worker_env; engine = make_db('restore',count=1)
    # Real portable preview, not a manually deleted job row.
    with Session(engine) as db:
        exported = backups.export_backup(db)
        preview = backups.preview_backup(db,json.dumps(exported))
    arrived,release,calls = pause_first(monkeypatch,releases)
    identifier = launch(engine); assert arrived.wait(2)
    with Session(engine) as db:
        result = backups.restore_backup(db,preview['preview_token'])
        assert result['restored'] is True
    release.set(); assert terminal(engine,identifier) == 'deleted'
    with Session(engine) as db:
        assert db.query(EffortScan).count() == db.query(EffortAnalysis).count() == 0
        assert db.get(Activity,1).name == 'restore1'
    assert calls == [1]


def test_two_workers_eight_waiting_queue_full_and_queued_cancel_release_capacity(worker_env,monkeypatch):
    make_db,releases = worker_env
    engines = [make_db('bounded-%02d' % index,count=1) for index in range(11)]
    release,two_running = Event(),Event(); releases.append(release)
    state_lock = Lock(); state = {'active':0,'peak':0,'calls':[]}
    original = records.analyze_snapshot
    def analyze(snapshot,**kwargs):
        with state_lock:
            state['active'] += 1; state['peak'] = max(state['peak'],state['active'])
            state['calls'].append(snapshot['activity'].name)
            if state['active'] == 2:
                two_running.set()
        try:
            assert release.wait(4), 'test failed to release bounded worker'
            return original(snapshot,**kwargs)
        finally:
            with state_lock:
                state['active'] -= 1
    monkeypatch.setattr(records,'analyze_snapshot',analyze)
    identifiers = [launch(engine) for engine in engines[:10]]
    assert two_running.wait(2)
    with pytest.raises(HTTPException) as failure:
        launch(engines[10])
    assert failure.value.status_code == 503
    with Session(engines[10]) as db:
        assert db.query(EffortScan).count() == 0
    # Repeated launch in the same workspace returns its existing token.
    assert launch(engines[0]) == identifiers[0]
    with Session(engines[9]) as db:
        assert jobs.cancel(db,identifiers[9])['status'] == 'cancel_requested'
    release.set()
    for index,(engine,identifier) in enumerate(zip(engines[:10],identifiers)):
        result = terminal(engine,identifier)
        assert result['status'] == ('cancelled' if index == 9 else 'completed')
        if index == 9:
            assert result['processed'] == 0
    assert state['peak'] == 2 and len(state['calls']) == 9
    assert 'bounded-091' not in state['calls']
    # Completed/cancelled tasks release admission slots, including the queued one.
    identifier = launch(engines[10])
    assert terminal(engines[10],identifier)['status'] == 'completed'


def test_rate_limited_refresh_preserves_valid_remote_curve_and_provenance_verbatim(worker_env,monkeypatch):
    make_db,_ = worker_env; engine = make_db('retain-remote',count=1)
    monkeypatch.setattr(records,'cache_valid',lambda row,snapshot,now: records._validated_cache(row,snapshot,now) is not None)
    with Session(engine) as db:
        snapshot = records.source_snapshot(db,db.get(Activity,1))
        prior = records.analyze_snapshot(snapshot,now=NOW-timedelta(hours=1),mode='stored')
        prior['source_hash'] = 'c'*64
        prior['source_fetched_at'] = NOW-timedelta(hours=1)
        prior['payload_json']['curve']['source'] = 'intervals_remote_streams'
        values = {key:value for key,value in prior.items() if key not in ('stop_batch','provider_status')}
        values['payload_json'] = json.dumps(values['payload_json'],sort_keys=True)
        cached = EffortAnalysis(activity_id=1,**values); db.add(cached); db.commit()
        assert records.cache_valid(cached,snapshot,NOW)
        original = {column.key:getattr(cached,column.key) for column in EffortAnalysis.__table__.columns}
    calls = []
    def limited(snapshot,**kwargs):
        calls.append((snapshot['activity'].id,kwargs['refresh']))
        value = records._empty_analysis(snapshot,kwargs['now'],'unavailable','provider_rate_limited')
        value.update(stop_batch=True,provider_status=429)
        return value
    monkeypatch.setattr(records,'analyze_snapshot',limited)
    identifier = launch(engine,mode='intervals',refresh=True)
    result = terminal(engine,identifier)
    assert result['status'] == 'failed' and result['error_code'] == 'provider_rate_limited'
    assert calls == [(1,True)]
    with Session(engine) as db:
        cached = db.get(EffortAnalysis,1)
        current = {column.key:getattr(cached,column.key) for column in EffortAnalysis.__table__.columns}
        assert current == original
