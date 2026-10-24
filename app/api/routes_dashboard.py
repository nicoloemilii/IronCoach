from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Activity
from app.services.analysis import training_summary
from app.services.training_load import fitness_metrics, fitness_metrics_by_sport, data_quality, sport_key
from app.services.activity_rating import activity_ratings
from app.services.observed_load import resolve_observed_loads, load_values
from app.services.activity_diary import diary_page, serialize_activity

router = APIRouter()


def _serialize(activity: Activity, ftp_watts: float, rating=None, load=None, load_evidence=None):
    return serialize_activity(activity,rating,load,load_evidence)


@router.get('/activities/diary')
def get_activity_diary(limit: int = Query(50,ge=1,le=100), sport: str | None = None,
                       q: str = Query('',max_length=200), cursor: str | None = Query(None,max_length=2048),
                       db: Session = Depends(get_db)):
    return diary_page(db,limit=limit,sport=sport,q=q,cursor=cursor)


@router.get("/activities")
def get_activities(
    ftp_watts: float = Query(170.0, gt=0, allow_inf_nan=False, description="Parametro di compatibilità; il diario espone soltanto il carico registrato"),
    db: Session = Depends(get_db),
):
    activities = db.query(Activity).order_by(Activity.start_time.desc()).all()
    resolved = resolve_observed_loads(db, activities)
    loads = load_values(resolved)
    ratings = activity_ratings(db, activities, loads=loads)
    return [_serialize(activity, ftp_watts, ratings[activity.id], loads[activity.id], resolved[activity.id]) for activity in activities]


@router.get("/analysis/summary")
def get_analysis_summary(
    days: int = Query(28, ge=1, le=365),
    ftp_watts: float = Query(170.0, gt=0, allow_inf_nan=False, description="FTP ciclismo in watt usato per il calcolo TSS"),
    db: Session = Depends(get_db),
):
    return training_summary(db, days=days, ftp_watts=ftp_watts)


@router.get("/analysis/fitness")
def get_fitness_metrics_legacy(
    ftp_watts: float = Query(170.0, gt=0, allow_inf_nan=False),
    db: Session = Depends(get_db),
):
    activities = db.query(Activity).order_by(Activity.start_time.asc()).all()
    return {
        **fitness_metrics(activities, ftp_watts),
        "ftp_watts": ftp_watts,
        "load_method": "bike TSS from NP/FTP when available; sport-specific Intervals.icu load otherwise; CTL=42d EMA; ATL=7d EMA; TSB=CTL-ATL",
        "activities_used": len(activities),
    }


@router.get("/analysis/fitness/sports")
def get_fitness_by_sport_legacy(
    ftp_watts: float = Query(170.0, gt=0, allow_inf_nan=False),
    db: Session = Depends(get_db),
):
    activities = db.query(Activity).order_by(Activity.start_time.asc()).all()
    return {
        "ftp_watts": ftp_watts,
        "ctl_time_constant_days": 42,
        "atl_time_constant_days": 7,
        "sports": fitness_metrics_by_sport(activities, ftp_watts),
    }


@router.get("/analysis/quality")
def get_data_quality_legacy(
    ftp_watts: float = Query(170.0, gt=0, allow_inf_nan=False),
    db: Session = Depends(get_db),
):
    activities = db.query(Activity).order_by(Activity.start_time.asc()).all()
    return data_quality(activities, ftp_watts)
