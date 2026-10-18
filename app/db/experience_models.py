from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String
from app.db.database import Base


class AthleteCheckin(Base):
    __tablename__ = 'athlete_checkins'
    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False, unique=True)
    payload_json = Column(String, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SessionFeedback(Base):
    __tablename__ = 'session_feedback'
    id = Column(Integer, primary_key=True)
    workout_id = Column(Integer, nullable=False, unique=True)
    activity_id = Column(Integer, nullable=True, unique=True)
    payload_json = Column(String, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RacePreparation(Base):
    __tablename__ = 'race_preparation'
    id = Column(Integer, primary_key=True)
    payload_json = Column(String, nullable=False)


class ActivityIdentity(Base):
    __tablename__ = 'activity_identities'
    id = Column(Integer, primary_key=True)
    source_key = Column(String, nullable=False, unique=True)
    activity_id = Column(Integer, nullable=False, index=True)
