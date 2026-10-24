"""The additive review journal preserves the existing athlete database."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.iron_models import SchemaVersion, User
from app.db.migrations import migrate
from app.db.models import Activity, DailyMetrics
from app.db.review_models import DuplicateReview


def test_v2_upgrade_backs_up_original_and_adds_empty_journal_once():
    folder = Path.cwd() / f'.review-migration-{uuid4().hex}'
    folder.mkdir()
    filename = folder / 'v2.db'
    engine = create_engine(f'sqlite:///{filename}')
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables
                                           if table.name != 'duplicate_reviews'])
    try:
        with Session(engine) as db:
            db.add_all([SchemaVersion(version=1), SchemaVersion(version=2), User(id=1, timezone='Europe/Rome'),
                Activity(id=71, source='synthetic', source_id='preserve', sport='running',
                         start_time=datetime(2026, 10, 1, 8), duration_seconds=0),
                DailyMetrics(date='2026-10-01', sleep_hours=None, steps=0)])
            db.commit()
        migrate(engine)
        snapshots = list((folder / 'backups').glob('*-before-iron-v3-*.db'))
        assert len(snapshots) == 1
        with closing(sqlite3.connect(snapshots[0])) as backup:
            assert backup.execute('SELECT version FROM iron_schema_versions ORDER BY version').fetchall() == [(1,), (2,)]
            assert backup.execute("SELECT name FROM sqlite_master WHERE name='duplicate_reviews'").fetchall() == []
            assert backup.execute('SELECT id,source_id,duration_seconds FROM activities').fetchall() == [(71, 'preserve', 0)]
        assert inspect(engine).has_table('duplicate_reviews')
        with Session(engine) as db:
            assert {row.version for row in db.query(SchemaVersion)} == {1, 2, 3}
            assert db.query(DuplicateReview).count() == 0
            assert db.get(Activity, 71).source_id == 'preserve'
            assert db.query(DailyMetrics).one().sleep_hours is None
            assert db.query(DailyMetrics).one().steps == 0
        migrate(engine)
        assert len(list((folder / 'backups').glob('*.db'))) == 1
    finally:
        engine.dispose()
        filename.unlink(missing_ok=True)
        for snapshot in (folder / 'backups').glob('*.db'):
            snapshot.unlink()
        if (folder / 'backups').exists():
            (folder / 'backups').rmdir()
        folder.rmdir()
