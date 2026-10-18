"""Explicit athlete decisions: no inferred completion or race readiness."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint

from app.db.database import Base


class WorkoutResolution(Base):
    __tablename__ = 'workout_resolutions'
    id = Column(Integer, primary_key=True)
    workout_id = Column(Integer, ForeignKey('workouts.id'), nullable=False, index=True)
    kind = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    prior_status = Column(String, nullable=False, default='planned')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    undone_at = Column(DateTime)
    __table_args__ = (
        Index('uq_workout_active_resolution', 'workout_id', unique=True,
              sqlite_where=undone_at.is_(None)),
    )


class GoalPreparationItem(Base):
    __tablename__ = 'goal_preparation_items'
    id = Column(Integer, primary_key=True)
    goal_id = Column(Integer, ForeignKey('race_goals.id'), nullable=False, index=True)
    key = Column(String, nullable=False)
    done = Column(Boolean, nullable=False, default=False)
    notes = Column(String, nullable=False, default='')
    context_hash = Column(String, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('goal_id', 'key'),)
