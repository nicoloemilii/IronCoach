"""Additive derived schemas use only synthetic temporary SQLite databases."""
from datetime import datetime
import sqlite3
from threading import Event, Thread

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.db.effort_lock import database_lock
from app.db.effort_models import DistanceAnalysis, EffortAnalysis, EffortScan
from app.db.effort_schema import ensure_effort_schema
from app.db.iron_models import SchemaVersion
from app.db.migrations import migrate
from app.db.models import Activity


def activity():
    return Activity(id=1, source='fit', source_id='synthetic-one', sport='cycling',
                    name='Original synthetic activity', start_time=datetime(2026, 1, 1))


def test_effort_schema_is_idempotent_and_models_have_no_raw_stream_column():
    engine = create_engine('sqlite://')
    assert ensure_effort_schema(engine) is True
    assert ensure_effort_schema(engine) is False
    with Session(engine) as db:
        assert db.query(EffortAnalysis).count() == db.query(EffortScan).count() == db.query(DistanceAnalysis).count() == 0
    assert set(EffortAnalysis.__table__.columns.keys()) == {
        'activity_id','fingerprint','algorithm_version','status','payload_json','source_identity',
        'source_hash','source_fetched_at','analyzed_at','cutoff_utc','error_code'}
    assert 'activity_ids_json' in EffortScan.__table__.columns
    engine.dispose()


def test_existing_database_is_backed_up_before_new_tables_once(tmp_path):
    path = tmp_path / 'synthetic.db'
    engine = create_engine('sqlite:///' + str(path))
    Activity.__table__.create(engine)
    with Session(engine) as db:
        db.add(activity()); db.commit()
    assert ensure_effort_schema(engine) is True
    copies = list((tmp_path / 'backups').glob('*before-effort-records-*.db'))
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as copied:
        tables = {row[0] for row in copied.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'activities' in tables and 'effort_analyses' not in tables and 'effort_scans' not in tables
        assert copied.execute('SELECT name FROM activities WHERE id=1').fetchone()[0] == 'Original synthetic activity'
    with Session(engine) as db:
        assert db.get(Activity,1).name == 'Original synthetic activity'
    assert ensure_effort_schema(engine) is False
    assert len(list((tmp_path / 'backups').glob('*.db'))) == 1
    engine.dispose()


def test_failed_backup_prevents_additive_ddl(tmp_path, monkeypatch):
    from app.db import effort_schema
    engine = create_engine('sqlite:///' + str(tmp_path / 'synthetic.db'))
    Activity.__table__.create(engine)
    def fail(_):
        raise OSError('synthetic backup failure')
    monkeypatch.setattr(effort_schema, '_backup_existing_database', fail)
    with pytest.raises(OSError):
        ensure_effort_schema(engine)
    assert 'effort_analyses' not in inspect(engine).get_table_names()
    assert 'effort_scans' not in inspect(engine).get_table_names()
    engine.dispose()


def test_migration_keeps_versions_1_2_3_and_adds_effort_tables(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'synthetic.db'))
    Activity.__table__.create(engine); SchemaVersion.__table__.create(engine)
    with Session(engine) as db:
        db.add(activity()); db.add_all([SchemaVersion(version=value) for value in (1,2,3)]); db.commit()
    migrate(engine); migrate(engine)
    assert {'effort_analyses','effort_scans','distance_analyses'} <= set(inspect(engine).get_table_names())
    with Session(engine) as db:
        assert {row.version for row in db.query(SchemaVersion)} == {1,2,3}
        assert db.get(Activity,1).source_id == 'synthetic-one'
    assert len(list((tmp_path / 'backups').glob('*before-effort-records-*.db'))) == 1
    engine.dispose()


def test_derived_tables_are_excluded_from_portable_personal_backup():
    from app.services import backups
    from app.db.planning_models import AthleteSettings
    engine = create_engine('sqlite://')
    Activity.__table__.create(engine); ensure_effort_schema(engine)
    AthleteSettings.__table__.create(engine)
    with Session(engine) as db:
        db.add(activity()); db.flush()
        db.add(AthleteSettings(id=1,revision=0,payload_json='{}'))
        db.add(EffortAnalysis(activity_id=1,fingerprint='a'*64,status='available',payload_json='{"durations":[]}'))
        db.add(DistanceAnalysis(activity_id=1,fingerprint='b'*64,algorithm_version='synthetic',status='unavailable'))
        db.add(EffortScan(id='a'*32,activity_ids_json='[1]')); db.commit()
        value = backups.export_backup(db)
    assert 'activities' in value['data']
    assert 'effort_analyses' not in value['data'] and 'effort_scans' not in value['data']
    assert 'distance_analyses' not in value['data']
    assert 'fingerprint' not in backups._json(value)
    engine.dispose()


def test_existing_power_scan_progress_survives_distance_counter_upgrade(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'power-era.db'))
    Activity.__table__.create(engine)
    EffortAnalysis.__table__.create(engine)
    DistanceAnalysis.__table__.create(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE effort_scans (id TEXT PRIMARY KEY, processed INTEGER, with_power INTEGER)')
        connection.exec_driver_sql("INSERT INTO effort_scans VALUES ('old-scan', 7, 3)")
    assert ensure_effort_schema(engine) is True
    with engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT * FROM effort_scans').one() == ('old-scan', 7, 3, 0, 0)
    copies = list((tmp_path / 'backups').glob('*.db'))
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as copied:
        assert copied.execute('SELECT * FROM effort_scans').fetchone() == ('old-scan', 7, 3)
    assert ensure_effort_schema(engine) is False
    assert len(list((tmp_path / 'backups').glob('*.db'))) == 1
    engine.dispose()


def test_file_lock_is_shared_across_engines_and_memory_engines_are_isolated(tmp_path):
    path = tmp_path / 'synthetic.db'
    first = create_engine('sqlite:///' + str(path))
    second = create_engine('sqlite:///' + str(path.parent / '.' / path.name))
    one, two = database_lock(first), database_lock(second)
    assert one is two
    acquired, completed = Event(), Event()
    def contend():
        with database_lock(second):
            acquired.set()
        completed.set()
    with one:
        # Reentrant in the same thread; a different thread must wait.
        with database_lock(first):
            worker = Thread(target=contend); worker.start()
            assert not acquired.wait(.05)
    assert completed.wait(2); worker.join(2)
    memory_one, memory_two = create_engine('sqlite://'), create_engine('sqlite://')
    assert database_lock(memory_one) is not database_lock(memory_two)
    for engine in (first,second,memory_one,memory_two):
        engine.dispose()
