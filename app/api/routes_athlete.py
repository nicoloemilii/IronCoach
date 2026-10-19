from datetime import date
import math
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.services.athlete_profile import load_profile, save_profile

router = APIRouter()


class LegacyFields(BaseModel):
    # Older desktop/iOS clients preserve extra profile fields on round trips.
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)

    @model_validator(mode='before')
    @classmethod
    def safe_json(cls, value):
        def inspect(item, depth=0):
            if depth > 30:
                raise ValueError('Il profilo contiene troppi livelli annidati.')
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError('Il profilo non ammette numeri non finiti.')
            if isinstance(item, dict):
                for nested in item.values():
                    inspect(nested, depth + 1)
            elif isinstance(item, list):
                for nested in item:
                    inspect(nested, depth + 1)
        inspect(value)
        return value


HeartRate = Annotated[float, Field(strict=True, ge=20, le=250)]
Power = Annotated[float, Field(strict=True, gt=0, le=1000)]


class CyclingPatch(LegacyFields):
    ftp_watts: Power | None = None
    threshold_hr_bpm: HeartRate | None = None
    max_hr_bpm: HeartRate | None = None


class RunningPatch(LegacyFields):
    threshold_pace_sec_per_km: Annotated[float, Field(strict=True, gt=0, le=3600)] | None = None
    threshold_hr_bpm: HeartRate | None = None
    max_hr_bpm: HeartRate | None = None


class SwimmingPatch(LegacyFields):
    css_sec_per_100m: Annotated[float, Field(strict=True, gt=0, le=600)] | None = None


class GoalsPatch(LegacyFields):
    event: str | None = Field(None, max_length=160)
    event_date: date | None = None
    target_time: str | None = Field(None, max_length=80)


class AthletePatch(LegacyFields):
    name: str | None = Field(None, max_length=160)
    age: int | None = Field(None, strict=True, ge=0, le=120)
    weight_kg: float | None = Field(None, strict=True, gt=0, le=500)
    height_cm: float | None = Field(None, strict=True, ge=50, le=250)
    ftp_watts: Power | None = None
    cycling: CyclingPatch = Field(default_factory=CyclingPatch)
    running: RunningPatch = Field(default_factory=RunningPatch)
    swimming: SwimmingPatch = Field(default_factory=SwimmingPatch)
    goals: GoalsPatch = Field(default_factory=GoalsPatch)


@router.get("/athlete/profile")
def get_profile():
    return load_profile()

@router.put("/athlete/profile")
def update_profile(payload: AthletePatch):
    from app.services.athlete_profile import _merge, PROFILE_LOCK
    with PROFILE_LOCK:
        return save_profile(_merge(load_profile(), payload.model_dump(mode='json', exclude_unset=True)))
