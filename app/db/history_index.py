"""An additive physical index; record formats and schema versions are unchanged."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.schema import CreateIndex


def ensure_history_index(engine):
    from app.db.models import Activity
    name='ix_activities_start_id'
    inspector=inspect(engine)
    if not inspector.has_table('activities') or any(item['name']==name for item in inspector.get_indexes('activities')):
        return
    filename=engine.url.database
    if filename and filename!=':memory:' and Path(filename).is_file():
        original=Path(filename).resolve(); folder=original.parent/'backups';folder.mkdir(exist_ok=True)
        backup=folder/f'{original.stem}-before-history-index-{datetime.utcnow():%Y%m%d%H%M%S%f}-{uuid4().hex[:8]}.db'
        with closing(sqlite3.connect(str(original))) as source,closing(sqlite3.connect(str(backup))) as target:
            source.backup(target)
    index=next(index for index in Activity.__table__.indexes if index.name==name)
    with engine.begin() as connection:
        connection.execute(CreateIndex(index,if_not_exists=True))
