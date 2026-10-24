"""Validate aggregate feature output against the local database in mode=ro."""
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from app.db.repository import duplicate_summary
from app.services.training_insights import training_insights


def readonly_connection():
    connection = sqlite3.connect((PROJECT / 'data' / 'coach.db').as_uri() + '?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    return connection


engine = create_engine('sqlite://', creator=readonly_connection)
statements = []
@event.listens_for(engine, 'before_cursor_execute')
def observed_sql(connection, cursor, statement, parameters, context, executemany):
    statements.append(statement.split(None, 1)[0].upper())

started = perf_counter()
with Session(engine) as db:
    summary, _ = duplicate_summary(db)
    insights = training_insights(db, date(2026, 10, 19), now=datetime(2026, 10, 19, 12))
assert set(statements) == {'SELECT'}
print(json.dumps({
    'scanned_activities': summary['scanned_activities'],
    'possible_pairs': summary['possible_pairs'],
    'pending_candidates': summary['pending_candidates'],
    'unreviewed_pairs': summary['unreviewed_pairs'],
    'quality': {key: value for key, value in insights['quality'].items() if key != 'summary'},
    'comparison_suppressed': insights['volume']['minutes_change_pct'] is None and not insights['volume']['comparable'],
    'elapsed_ms': round((perf_counter() - started) * 1000),
    'select_count': len(statements),
    'readonly_sqlite_enforced': True,
}, indent=2))
engine.dispose()
