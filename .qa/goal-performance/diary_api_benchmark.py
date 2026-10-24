"""Isolated production diary-route latency, SQL and ORM-load measurements."""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ['IRON_DATA_DIR'] = str(QA / 'isolated-diary-benchmark-data')
os.environ['IRON_AUTO_SYNC'] = '0'
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api.routes_dashboard import router
from app.db.database import Base, get_db
from app.db.models import Activity
from app.db.iron_models import HealthMetric
from app.services import activity_diary

NOW = datetime(2026, 10, 22, 12)
activity_diary.utc_now_naive = lambda: NOW


def state(engine):
    result = {}
    with engine.connect() as connection:
        tables = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").scalars().all()
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            rows = sorted(map(repr, connection.exec_driver_sql('SELECT * FROM ' + quoted).fetchall()))
            result[table] = dict(rows=len(rows), sha256=hashlib.sha256('\n'.join(rows).encode()).hexdigest())
    return result


results = []
for density in ('dense_35_days', 'spread_12_hours'):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    span = 35 * 86400 if density == 'dense_35_days' else 9999 * 43200
    end = NOW - timedelta(hours=1)
    rows = [dict(id=index + 1, source='fit', source_id=f'synthetic-{index + 1}', sport='cycling',
        name='Sparse needle' if index in (0, 9999) else f'Synthetic {index + 1}',
        start_time=end - timedelta(seconds=(9999 - index) * span // 9999),
        duration_seconds=3600, load=50, avg_hr=130, avg_power=150, normalized_power=155)
        for index in range(10000)]
    with Session(engine) as db:
        db.bulk_insert_mappings(Activity, rows)
        db.commit()
    app = FastAPI(); app.include_router(router)
    def isolated_db():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = isolated_db
    before = state(engine)
    with TestClient(app, client=('127.0.0.1', 55004)) as client:
        for name, parameters in [('first_page', {'limit': 50}), ('sparse_search', {'limit': 50, 'q': 'Sparse needle'})]:
            statements = []; loaded = []
            def sql(conn, cursor, statement, params, context, many):
                if statement.lstrip().upper().startswith('SELECT'):
                    statements.append(statement)
            def loaded_row(instance, context):
                loaded.append(instance.id)
            event.listen(engine, 'before_cursor_execute', sql)
            event.listen(Activity, 'load', loaded_row)
            start = perf_counter()
            response = client.get('/activities/diary', params=parameters)
            elapsed = perf_counter() - start
            event.remove(Activity, 'load', loaded_row)
            event.remove(engine, 'before_cursor_execute', sql)
            assert response.status_code == 200, response.text[:200]
            value = response.json()
            assert value['archive_total'] == 10000
            assert len(value['items']) == (50 if name == 'first_page' else 2)
            assert value['total'] == (10000 if name == 'first_page' else 2)
            if density == 'spread_12_hours':
                assert len(set(loaded)) <= (136 if name == 'first_page' else 172)
            results.append(dict(density=density, scenario=name, total_rows=10000,
                milliseconds=round(elapsed * 1000, 3), select_queries=len(statements),
                observation_selects=sum('FROM activity_observations' in statement for statement in statements),
                orm_activity_load_events=len(loaded), unique_activity_ids_loaded=len(set(loaded)),
                response_rows=len(value['items']), response_bytes=len(response.content),
                count=value['total'], all_statements_select=True,
                notes='Dense contexts legitimately include all 35 days; sparse histories use the union of exact 42-day windows.'))
    after = state(engine)
    assert before == after
    results[-1]['all_table_hashes_unchanged'] = True
    results[-2]['all_table_hashes_unchanged'] = True
    engine.dispose()
report = dict(synthetic=True, source='production FastAPI router + isolated in-memory DB', now=NOW.isoformat(),
    cases=results, notes=['GET requests only; fixture setup and table hashing excluded from latency.',
        'No personal database, provider calls or app.main import.',
        'Latencies are local single observations, not an SLA.'])
target = QA / 'diary_api_benchmark.json'
target.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
