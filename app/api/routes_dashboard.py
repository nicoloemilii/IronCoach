from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from types import SimpleNamespace

from app.db.database import get_db
from app.db.models import Activity
from app.services.analysis import training_summary
from app.services.training_load import activity_load, fitness_metrics, fitness_metrics_by_sport, data_quality
from app.services.activity_rating import activity_ratings, recorded_loads

router = APIRouter()


def _serialize(activity: Activity, ftp_watts: float, rating=None, load=None):
    return {
        "id": activity.id,
        "source": activity.source,
        "sport": activity.sport,
        "name": activity.name,
        "start_time": activity.start_time,
        "duration_seconds": activity.duration_seconds,
        "distance_meters": activity.distance_meters,
        "elevation_gain_meters": activity.elevation_gain_meters,
        "avg_hr": activity.avg_hr,
        "max_hr": activity.max_hr,
        "avg_power": activity.avg_power,
        "normalized_power": activity.normalized_power,
        "avg_speed": activity.avg_speed,
        "avg_cadence": activity.avg_cadence,
        "calories": activity.calories,
        # An unmapped view avoids a separate observation query for every row.
        "load": activity_load(SimpleNamespace(sport=activity.sport, source=activity.source,
                              duration_seconds=activity.duration_seconds, normalized_power=activity.normalized_power,
                              load=load), ftp_watts),
        "coach_rating": rating,
    }


@router.get("/activities")
def get_activities(
    ftp_watts: float = Query(170.0, gt=0, description="FTP ciclismo in watt usato per il calcolo TSS"),
    db: Session = Depends(get_db),
):
    activities = db.query(Activity).order_by(Activity.start_time.desc()).all()
    loads = recorded_loads(db, activities)
    ratings = activity_ratings(db, activities, loads=loads)
    return [_serialize(activity, ftp_watts, ratings[activity.id], loads[activity.id]) for activity in activities]


@router.get("/analysis/summary")
def get_analysis_summary(
    days: int = Query(28, ge=1, le=365),
    ftp_watts: float = Query(170.0, gt=0, description="FTP ciclismo in watt usato per il calcolo TSS"),
    db: Session = Depends(get_db),
):
    return training_summary(db, days=days, ftp_watts=ftp_watts)


@router.get("/analysis/fitness")
def get_fitness_metrics_legacy(
    ftp_watts: float = Query(170.0, gt=0),
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
    ftp_watts: float = Query(170.0, gt=0),
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
    ftp_watts: float = Query(170.0, gt=0),
    db: Session = Depends(get_db),
):
    activities = db.query(Activity).order_by(Activity.start_time.asc()).all()
    return data_quality(activities, ftp_watts)
