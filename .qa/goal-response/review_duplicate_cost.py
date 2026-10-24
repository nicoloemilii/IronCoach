"""Synthetic read-only cost probe for many already-reviewed exact groups."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session
from app.db.database import Base
from app.db.models import Activity
from app.db.iron_models import DuplicateCandidate
from app.services.training_response import _duplicate_dates


def run():
    reports = []
    for groups in (250, 1000, 3000):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        start = datetime(2026, 10, 1)
        activities, decisions = [], []
        for index in range(groups):
            for offset in (1, 2):
                identifier = index * 2 + offset
                activities.append(dict(id=identifier, source='fit', source_id=f'synthetic-{identifier}',
                                       sport='running', name='Synthetic reviewed pair',
                                       start_time=start + timedelta(seconds=index), duration_seconds=3600,
                                       distance_meters=10000, load=30))
            decisions.append(dict(activity_id=index * 2 + 2, other_id=index * 2 + 1, status='kept'))
        with Session(engine) as db:
            db.execute(insert(Activity), activities)
            db.execute(insert(DuplicateCandidate), decisions)
            db.commit()
            rows = db.query(Activity).all()
            begin = time.perf_counter()
            result = _duplicate_dates(db, rows, start, start + timedelta(days=1))
            elapsed = time.perf_counter() - begin
            assert result == (set(), False, 0, 0), result
            reports.append({'activities': len(rows), 'exact_groups': groups,
                            'resolved_pairs': groups, 'helper_ms': round(elapsed * 1000, 2),
                            'unresolved_days': 0})
        engine.dispose()
    output = {'scope': 'Synthetic in-memory helper timing; not a production SLA',
              'source_sha256': hashlib.sha256((ROOT / 'app/services/training_response.py').read_bytes()).hexdigest(),
              'reports': reports}
    target = 'review_duplicate_cost_after.json' if '--after' in sys.argv else 'review_duplicate_cost.json'
    Path(__file__).with_name(target).write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(json.dumps(output))


if __name__ == '__main__':
    run()
