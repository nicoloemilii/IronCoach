from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Activity
from app.services.training_load import (
    data_quality,
    fitness_metrics,
    fitness_metrics_by_sport,
)

router = APIRouter()


def _activities(db: Session):
    return db.query(Activity).order_by(Activity.start_time.asc()).all()


@router.get("/analysis/fitness")
def get_fitness_metrics(
    ftp_watts: float = Query(170.0, gt=0, allow_inf_nan=False),
    db: Session = Depends(get_db),
):
    activities = _activities(db)
    return {
        **fitness_metrics(activities, ftp_watts),
        "ftp_watts": ftp_watts,
        "load_method": "bike TSS from NP/FTP when available; sport-specific Intervals.icu load otherwise; CTL=42d EMA; ATL=7d EMA; TSB=CTL-ATL",
        "activities_used": len(activities),
    }


@router.get("/analysis/fitness/sports")
def get_fitness_by_sport(
    ftp_watts: float = Query(170.0, gt=0, allow_inf_nan=False),
    db: Session = Depends(get_db),
):
    activities = _activities(db)
    return {
        "ftp_watts": ftp_watts,
        "ctl_time_constant_days": 42,
        "atl_time_constant_days": 7,
        "sports": fitness_metrics_by_sport(activities, ftp_watts),
    }


@router.get("/analysis/quality")
def get_data_quality(
    ftp_watts: float = Query(170.0, gt=0, allow_inf_nan=False),
    db: Session = Depends(get_db),
):
    return data_quality(_activities(db), ftp_watts)
