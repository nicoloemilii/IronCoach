from types import SimpleNamespace
from datetime import datetime, timedelta
from app.services.athlete_state import build_athlete_state
from app.services.decision_engine import decide_training

def activity(hours_ago=48, sport='cycling', load=50, duration=60):
    return SimpleNamespace(start_time=datetime.utcnow()-timedelta(hours=hours_ago), duration_seconds=duration*60, load=load, sport={'cycling':'bike','running':'run','swimming':'swim'}[sport], normalized_power=None, avg_power=None, avg_hr=None)

def state(metrics=None, tsb=-5, rows=None):
    rows=rows or [activity()]
    hist={'status':'ok','confidence':85,'sports':{'cycling':{'confidence':85,'recent_90d':{'avg_session_duration_min':75,'avg_load':70},'longest_session_min':150},'running':{'confidence':100,'recent_90d':{'avg_session_duration_min':55,'avg_load':50},'longest_session_min':120},'swimming':{'confidence':25,'recent_90d':{'avg_session_duration_min':30,'avg_load':25},'longest_session_min':60}}}
    return build_athlete_state(rows, metrics or {}, {'ctl':40,'atl':45,'tsb':tsb}, hist, {'action':'none','confidence':0,'sample_size':0,'trend':'none'}, 170, datetime.utcnow().date())

def test_missing_recovery_data_does_not_green_light():
    d=decide_training(state())
    assert d['confidence'] <= 60
    assert 'recovery_data_missing' in d['cautions']

def test_low_readiness_forces_recovery():
    d=decide_training(state({'sleep_hours':4.5,'stress_score':80,'body_battery':20}))
    assert d['recommendation'] in ('recovery','easy')

def test_persistent_progression_can_progress_when_recovered():
    st=state({'sleep_hours':8,'stress_score':15,'body_battery':90}, tsb=5)
    st['adaptation']={'action':'consider_progression','confidence':100,'sample_size':3,'trend':'persistent'}
    d=decide_training(st, requested_sport='cycling')
    assert d['recommendation'] == 'progression'
