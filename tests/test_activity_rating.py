import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import routes_dashboard, routes_iron
from app.db.database import Base
from app.db.iron_models import ActivityObservation, HealthMetric
from app.db.models import Activity
from app.services.activity_rating import activity_ratings, recorded_loads

NOW = datetime(2026, 10, 18, 18)


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def activity(db, day=0, **values):
    payload = dict(source='manual', source_id=f'a-{len(db.new)}-{day}-{db.query(Activity).count()}',
                   sport='cycling', start_time=NOW-timedelta(days=day, hours=3), duration_seconds=3600, load=50,
                   avg_hr=135, avg_power=150, normalized_power=155)
    payload.update(values)
    row = Activity(**payload); db.add(row); db.flush()
    return row


def history(db):
    return [activity(db, day=day) for day in range(3, 43, 3)]


def wellness(db, day, kind, value):
    db.add(HealthMetric(date=str(day), kind=kind, value=value, source='intervals', unit='test',
                        observed_at=str(day), synced_at=NOW.isoformat()))
    db.flush()


def test_unmatched_session_gets_explainable_score_without_inventing_performance(db):
    history(db); row = activity(db)
    result = activity_ratings(db, now=NOW)[row.id]
    assert 0 <= result['score'] <= 100
    assert result['scope'] == 'recorded_session'
    assert result['components'] and result['actions'] and result['confidence_percent'] > 50
    weighted = sum(item['score']*item['weight'] for item in result['components']) / sum(item['weight'] for item in result['components'])
    assert result['score'] == round(weighted)
    assert 'non misura la prestazione assoluta' in result['limits'][0]


def test_large_dose_and_preceding_recovery_reduce_rating(db):
    history(db); normal = activity(db)
    before = activity_ratings(db, now=NOW)[normal.id]
    normal.duration_seconds = 14400; normal.load = 220
    wellness(db, normal.start_time.date()-timedelta(days=1), 'tsb', -35)
    result = activity_ratings(db, now=NOW)[normal.id]
    assert result['score'] < before['score'] and result['score'] < 40
    assert result['level'] == 1 and result['tone'] == 'critical'
    assert any(item['key'] == 'recovery' and item['score'] == 20 for item in result['components'])


def test_future_or_same_day_wellness_does_not_rewrite_past_rating(db):
    history(db); row = activity(db)
    before = activity_ratings(db, now=NOW)[row.id]
    wellness(db, row.start_time.date(), 'tsb', -90)
    wellness(db, row.start_time.date()+timedelta(days=1), 'sleep_hours', 2)
    activity(db, day=-1, duration_seconds=20000, load=1000)
    after = activity_ratings(db, now=NOW)[row.id]
    assert after == before


def test_duration_only_is_scored_with_lower_confidence(db):
    row = activity(db, load=None, avg_hr=None, avg_power=None, normalized_power=None)
    result = activity_ratings(db, now=NOW)[row.id]
    assert result['score'] is not None and result['confidence_percent'] == 40
    assert any('Intensità non verificata' in limit for limit in result['limits'])
    row.load = 40; row.avg_hr = 140
    full = activity_ratings(db, now=NOW)[row.id]
    assert full['confidence_percent'] > result['confidence_percent']
    assert full['score'] == result['score']  # Data completeness is not performance.


@pytest.mark.parametrize('duration,load,expected', [(None, 30, True), (0, 0, False), (None, None, False), (-1, -2, False)])
def test_only_real_usable_work_receives_a_score(db, duration, load, expected):
    row = activity(db, duration_seconds=duration, load=load)
    result = activity_ratings(db, now=NOW)[row.id]
    assert (result['score'] is not None) == expected
    if not expected:
        assert result['score'] is None and result['confidence_percent'] == 0


def test_remote_explicit_null_does_not_revive_old_canonical_load(db):
    row = activity(db, load=90, duration_seconds=None)
    db.add(ActivityObservation(activity_id=row.id, source_key='intervals:one', payload_json=json.dumps({'load': None})))
    db.flush()
    assert recorded_loads(db, [row])[row.id] is None
    assert activity_ratings(db, now=NOW)[row.id]['score'] is None


def test_future_session_is_not_treated_as_completed(db):
    row = activity(db, day=-1)
    result = activity_ratings(db, now=NOW)[row.id]
    assert result['score'] is None and 'futura' in result['explanation']


def test_list_rating_uses_constant_queries_for_large_history(db, monkeypatch):
    monkeypatch.setattr('app.services.activity_rating.utc_now_naive', lambda: NOW)
    for index in range(1118):
        activity(db, day=index, source_id=f'history-{index}')
    db.commit()
    statements = []
    def capture(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        result = routes_dashboard.get_activities(ftp_watts=170, db=db)
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert len(result) == 1118 and all(row['coach_rating']['score'] is not None for row in result)
    assert len(statements) <= 6
    # Detail and list use the same rating, while retaining separate execution evidence.
    detail = routes_iron.activity_assessment(result[0]['id'], db=db)
    assert detail['score'] == result[0]['coach_rating']['score']
    assert detail['execution_assessment']['score'] is None
