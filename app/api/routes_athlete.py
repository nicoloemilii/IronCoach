from fastapi import APIRouter
from app.services.athlete_profile import load_profile, save_profile

router = APIRouter()

@router.get("/athlete/profile")
def get_profile():
    return load_profile()

@router.put("/athlete/profile")
def update_profile(payload: dict):
    from app.services.athlete_profile import _merge, PROFILE_LOCK
    with PROFILE_LOCK:
        return save_profile(_merge(load_profile(), payload))
