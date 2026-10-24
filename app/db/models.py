from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Index

from app.db.database import Base


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (Index('ix_activities_start_id', 'start_time', 'id'),)

    id = Column(Integer, primary_key=True, index=True)

    # Origine dell'attività
    source = Column(String, nullable=False)
    source_id = Column(String, nullable=False, unique=True, index=True)

    # Informazioni principali
    sport = Column(String, nullable=False)
    name = Column(String)

    start_time = Column(DateTime, nullable=False)

    # Dati di allenamento
    duration_seconds = Column(Float)
    distance_meters = Column(Float)

    elevation_gain_meters = Column(Float)

    # Frequenza cardiaca
    avg_hr = Column(Float)
    max_hr = Column(Float)

    # Potenza
    avg_power = Column(Float)
    normalized_power = Column(Float)

    # Velocità / cadenza
    avg_speed = Column(Float)
    avg_cadence = Column(Float)

    # Energia
    calories = Column(Float)

    # Carico allenante
    load = Column(Float)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
class DailyMetrics(Base):
    __tablename__ = 'daily_metrics'
    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, unique=True, index=True)
    sleep_hours = Column(Float)
    hrv_ms = Column(Float)
    hrv_baseline_ms = Column(Float)
    resting_hr_bpm = Column(Float)
    resting_hr_baseline_bpm = Column(Float)
    stress_score = Column(Float)
    steps = Column(Integer)
    daily_activity_load = Column(Float)
    body_battery = Column(Float)

class Workout(Base):
    __tablename__ = 'workouts'
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    date = Column(String, nullable=False, index=True)
    sport = Column(String, nullable=False)
    name = Column(String, nullable=False)
    duration_min = Column(Float, nullable=False)
    payload_json = Column(String, nullable=False)
    status = Column(String, default='planned', nullable=False)

class WorkoutEvaluation(Base):
    __tablename__ = 'workout_evaluations'
    id = Column(Integer, primary_key=True, index=True)
    workout_id = Column(Integer, nullable=False, index=True)
    activity_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    rpe = Column(Float)
    payload_json = Column(String, nullable=False)


class AthleteAdaptation(Base):
    __tablename__ = "athlete_adaptations"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sport = Column(String, nullable=False)
    adaptation = Column(String, nullable=False)
    source_workout_id = Column(Integer)
    confidence = Column(Float)
    note = Column(String)
