"""Recorded-work judgments remain distinct from execution grades and clinical claims."""
import pytest
from app.services.dashboard_coach import weekly_assessments, recorded_overview


def week(load=100, **changes):
    return {'hours':3,'activities':3,'elevation_m':100,'load':load,**changes}


@pytest.mark.parametrize('load,tone', [(69,'watch'),(70,'positive'),(90,'good'),(100,'good'),(110,'good'),(111,'watch'),(121,'caution'),(130,'caution'),(131,'critical')])
def test_personal_load_comparisons_have_explicit_bounded_rules(load,tone):
    assessment=weekly_assessments(week(load),[week(),week(),week()],[])['load']
    assert assessment['tone']==tone
    assert assessment['score'] is None and assessment['scope']=='recorded_training'
    assert assessment['actions'] and 'mediana' in assessment['explanation']


@pytest.mark.parametrize('balance,tone', [(-30,'critical'),(-25,'caution')])
def test_observed_balance_can_override_an_ordinary_weekly_load(balance,tone):
    assessment=weekly_assessments(week(),[week(),week()],[{'kind':'tsb','stale':False,'latest':{'value':balance,'source':'intervals'}}])['load']
    assert assessment['tone']==tone and 'bilancio' in assessment['explanation']


def test_assumed_balance_cannot_trigger_a_critical_judgment():
    result=weekly_assessments(week(),[week(),week()],[{'kind':'tsb','stale':False,'latest':{'value':-40,'source':'local_estimate'}}])
    assert result['load']['tone']=='good'


def test_partial_elevation_and_missing_load_are_never_assumed_zero():
    current=week(None,elevation_m=None,elevation_known_m=125,elevation_known_activities=1,elevation_missing_activities=2)
    result=weekly_assessments(current,[week(),week()],[])
    assert result['elevation_m']['tone']=='unknown'
    assert 'Almeno 125 m' in result['elevation_m']['explanation']
    assert result['load']['tone']=='unknown'


def test_recorded_work_can_be_judged_without_a_fake_execution_score():
    current=week()
    value=recorded_overview(current,weekly_assessments(current,[week(),week()],[]))
    assert value['judgment']=='Carico coerente con lo storico'
    assert value['score'] is None
    assert '3 attività' in value['explanation']


def test_empty_or_short_history_produces_context_instead_of_fake_optimality():
    current=week()
    result=weekly_assessments(current,[],[])
    assert result['load']['tone']=='neutral'
    empty=week(0,hours=0,activities=0)
    assert 'Nessuna attività registrata' in recorded_overview(empty,result)['judgment']


@pytest.mark.parametrize('count',[0,3])
def test_observed_high_fatigue_remains_visible_even_without_a_complete_weekly_load(count):
    current=week(None,activities=count)
    assessments=weekly_assessments(current,[],[{'kind':'tsb','stale':False,'latest':{'value':-35,'source':'intervals'}}])
    assert recorded_overview(current,assessments)['tone']=='critical'


def test_context_reduces_load_despite_balanced_tsb_and_respects_goal():
    from app.services.dashboard_coach import contextual_assessment
    from app.services.workout_engine import generate_workout
    current=week(149, date='2026-10-18', sports={'run':0})
    condition=[{'kind':'tsb','stale':False,'latest':{'value':-4.9,'source':'intervals'}}]
    assessment, metrics=contextual_assessment(current,weekly_assessments(current,[week(),week()],condition),condition,{'readiness':54},
        {'name':'Triathlon','date':'2027-06-25','legs':[{'sport':'running','distance_km':42,'elevation_gain_m':1000}]})
    assert assessment['tone']=='critical'
    assert assessment['session_policy']['max_duration_min']==30
    assert 'specificità manca' in assessment['explanation']
    assert metrics['tsb']['actions']==assessment['actions']
    workout=generate_workout([],{}, {'tsb':-4.9}, {'readiness':54}, sport='running',
        decision={'recommendation':'easy','session_policy':assessment['session_policy'],'reasons':assessment['actions']})
    assert workout['duration_min']==sum(s['duration_min'] for s in workout['steps'])==30
    assert workout['expected_rpe']<=3
    assert all(s['target_rpe']<=3 and 'target_power_w' not in s for s in workout['steps'])


def test_context_prioritizes_pain_even_with_normal_load_and_good_recovery():
    from app.services.dashboard_coach import contextual_assessment
    current=week(subjective={'pain':True})
    result,_=contextual_assessment(current,weekly_assessments(current,[week(),week()],[]),[],{'readiness':90},None)
    assert result['session_policy']['mode']=='rest'
    assert result['tone']=='critical'
