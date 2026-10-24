"""Add only rebuildable effort tables, preserving a pre-upgrade SQLite copy."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import secrets
import sqlite3

from sqlalchemy import inspect

from app.db.effort_lock import database_lock


def _backup_existing_database(engine):
    filename = engine.url.database
    if engine.dialect.name != 'sqlite' or not filename or filename == ':memory:':
        return None
    original = Path(filename).expanduser().resolve()
    if not original.is_file():
        return None
    folder = original.parent / 'backups'
    folder.mkdir(exist_ok=True)
    target = folder / (f'{original.stem}-before-effort-records-'
        f'{datetime.utcnow():%Y%m%d%H%M%S%f}-{secrets.token_hex(3)}.db')
    try:
        with closing(sqlite3.connect(str(original))) as source, closing(sqlite3.connect(str(target))) as backup:
            source.backup(backup)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def ensure_effort_schema(engine, *, backup=True):
    """Startup/migration only; return whether any table needed creation.

    Existing files with personal tables receive one online backup before the
    new tables are created. Complete schemas return without DDL or a backup.
    A caller that already made a migration backup may pass ``backup=False``.
    """
    from app.db.effort_models import EffortAnalysis, EffortScan, DistanceAnalysis

    tables = (EffortAnalysis.__table__, EffortScan.__table__, DistanceAnalysis.__table__)
    with database_lock(engine):
        existing = set(inspect(engine).get_table_names())
        scan_columns = {column['name'] for column in inspect(engine).get_columns('effort_scans')} if 'effort_scans' in existing else set()
        missing_counters = [name for name in ('with_distance', 'with_lengths') if name not in scan_columns]
        if all(table.name in existing for table in tables) and not missing_counters:
            return False
        if backup and existing:
            _backup_existing_database(engine)
        with engine.begin() as connection:
            if engine.dialect.name == 'sqlite':
                connection.exec_driver_sql('BEGIN IMMEDIATE')
            for table in tables:
                table.create(connection, checkfirst=True)
            if 'effort_scans' in existing:
                # Old progress remains valid; new metrics start with no results.
                for name in missing_counters:
                    connection.exec_driver_sql(f'ALTER TABLE effort_scans ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
        return True
