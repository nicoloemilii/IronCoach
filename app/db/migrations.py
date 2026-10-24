"""Versioned additive migration, with SQLite online backup before first upgrade."""
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from app.db.database import Base
from app.db.iron_models import SchemaVersion, User, HealthMetric


def migrate(engine):
    from app.db import progress_models, review_models
    from app.db.effort_schema import ensure_effort_schema
    from app.db.history_index import ensure_history_index
    inspector = inspect(engine)
    applied = set()
    if inspector.has_table('iron_schema_versions'):
        with Session(engine) as db:
            applied = {row.version for row in db.query(SchemaVersion).all()}
        if {1, 2, 3} <= applied:
            ensure_effort_schema(engine)
            ensure_history_index(engine)
            return
    filename = engine.url.database
    backup_created = False
    if filename and filename != ':memory:' and Path(filename).exists():
        original = Path(filename).resolve()
        folder = original.parent / 'backups'; folder.mkdir(exist_ok=True)
        next_version = next(version for version in (1, 2, 3) if version not in applied)
        backup = folder / f'{original.stem}-before-iron-v{next_version}-{datetime.utcnow():%Y%m%d%H%M%S%f}.db'
        with closing(sqlite3.connect(str(original))) as source, closing(sqlite3.connect(str(backup))) as target:
            source.backup(target)
        backup_created = True
    ensure_effort_schema(engine, backup=not backup_created)
    Base.metadata.create_all(engine)
    ensure_history_index(engine)
    from app.db.models import DailyMetrics
    from app.services.health import METRICS
    with Session(engine) as db:
        if not db.get(User, 1):
            db.add(User(id=1, timezone='Europe/Rome'))
        now = datetime.utcnow().isoformat() + 'Z'
        for row in ([] if 1 in applied else db.query(DailyMetrics).all()):
            for kind, spec in METRICS.items():
                value = getattr(row, kind, None)
                if value is not None and not db.query(HealthMetric).filter_by(date=row.date, kind=kind, source='manual').first():
                    db.add(HealthMetric(date=row.date, kind=kind, value=value, unit=spec['unit'],
                                        source='manual', observed_at=row.date, synced_at=now))
        for version in (1, 2, 3):
            if version not in applied: db.add(SchemaVersion(version=version))
        db.commit()
