"""Progress of a user-built gym routine, separate from recorded activities."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.db.database import Base


class GymProgress(Base):
    __tablename__ = 'gym_progress'

    id = Column(Integer, primary_key=True)
    workout_id = Column(Integer, nullable=False, unique=True, index=True)
    revision = Column(Integer, nullable=False, default=1)
    payload_json = Column(String, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


def ensure_training_schema(engine):
    GymProgress.__table__.create(engine, checkfirst=True)
