"""Disposable reproduction of the same-provider duplicate detection blind spot."""
from datetime import date, datetime
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.database import Base
from app.db.iron_models import DuplicateCandidate
from app.db.models import Activity
from app.db.repository import save_activity
from app.services.training_insights import training_insights

engine = create_engine('sqlite://')
Base.metadata.create_all(engine)
with Session(engine) as db:
    row = dict(source='intervals', sport='cycling', name='Synthetic private-free sample',
               start_time=datetime(2026, 10, 17, 8), duration_seconds=3600,
               distance_meters=20000, elevation_gain_meters=100, load=35)
    first = save_activity(db, {**row, 'source_id': 'synthetic-a'})
    second = save_activity(db, {**row, 'source_id': 'synthetic-b'})
    assert first.id != second.id
    assert db.query(Activity).count() == 2
    assert db.query(DuplicateCandidate).count() == 0
    body = training_insights(db, date(2026, 10, 18), now=datetime(2026, 10, 18, 20))
    assert body['volume']['current']['activities'] == 2
    assert body['volume']['current']['minutes'] == 120
    assert body['volume']['current']['load'] == 70
    assert body['continuity']['active_days'] == 1
    assert save_activity(db, {**row, 'source_id': 'synthetic-b'}).id == second.id
    assert db.query(DuplicateCandidate).count() == 0
    save_activity(db, {**row, 'source': 'fit', 'source_id': 'synthetic-c'})
    assert db.query(DuplicateCandidate).count() == 2
    print(json.dumps({'reproduced': True, 'same_provider_pending': 0,
                      'exact_same_provider_recorded_minutes': 120, 'single_record_minutes': 60,
                      'cross_provider_pending': 2, 'real_data_modified': False}))
engine.dispose()
