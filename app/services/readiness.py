from __future__ import annotations
from datetime import datetime, timedelta, date
from statistics import mean
from app.services.training_load import activity_load
from app.services.health import usable_metric_value
from app.services.athlete_clock import activity_day, athlete_today, as_utc_naive, utc_now_naive


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(x)))

def _sleep_score(hours):
    if hours is None: return None
    return _clamp(50 + (float(hours) - 6.0) * 16, 0, 100)

def calculate_daily_state(activities, metrics=None, ftp=170.0, day: date | None = None):
    now = utc_now_naive()
    day = day or athlete_today(now)
    metrics = {kind: usable_metric_value(kind, value) for kind, value in (metrics or {}).items()}
    valid = [a for a in activities if getattr(a, 'start_time', None) and as_utc_naive(a.start_time) <= now]
    def on_date(a, d): return activity_day(a.start_time) == d
    d0=[a for a in valid if on_date(a,day)]; d3=[a for a in valid if day-timedelta(days=2)<=activity_day(a.start_time)<=day]; d7=[a for a in valid if day-timedelta(days=6)<=activity_day(a.start_time)<=day]
    load24=sum(activity_load(a,ftp) for a in d0); load3=sum(activity_load(a,ftp) for a in d3); load7=sum(activity_load(a,ftp) for a in d7)
    factors={}
    factors['sleep']=_sleep_score(metrics.get('sleep_hours'))
    hrv=metrics.get('hrv_ms'); hb=metrics.get('hrv_baseline_ms')
    factors['hrv']=_clamp(50+(float(hrv)/float(hb)-1)*250) if hrv is not None and hb else None
    rhr=metrics.get('resting_hr_bpm'); rb=metrics.get('resting_hr_baseline_bpm')
    factors['resting_hr']=_clamp(50-(float(rhr)-float(rb))*10) if rhr is not None and rb else None
    stress=metrics.get('stress_score'); factors['stress']=None if stress is None else _clamp(100-float(stress))
    steps=metrics.get('steps'); factors['steps']=None if steps is None else _clamp(100-max(0,float(steps)-6000)/12000*60)
    da=metrics.get('daily_activity_load'); factors['daily_activity']=None if da is None else _clamp(100-float(da)*0.5)
    bb=metrics.get('body_battery'); factors['body_battery']=None if bb is None else _clamp(bb)
    training=_clamp(100-min(100,load24*.35+load3*.08)); factors['training_load']=training
    physiological=[v for k,v in factors.items() if k!='training_load' and v is not None]
    if not physiological:
        score=None; status='insufficient_data'
    else:
        score=round(mean([training]+physiological),1)
        status='high' if score>=80 else 'good' if score>=65 else 'moderate' if score>=50 else 'low' if score>=35 else 'very_low'
    observed=len(physiological); quality=round(observed/7*100,1)
    return {'date':day,'readiness':score,'status':status,'factors':{k:None if v is None else round(v,1) for k,v in factors.items()},'raw':{'load_24h':round(load24,1),'load_3d':round(load3,1),'load_7d':round(load7,1),'steps':steps,'sleep_hours':metrics.get('sleep_hours'),'stress_score':stress,'hrv_ms':hrv,'resting_hr_bpm':rhr,'body_battery':bb},'data_quality':quality,'confidence':quality}
