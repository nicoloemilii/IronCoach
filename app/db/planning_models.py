"""Persisted athlete preferences and reversible calendar revisions."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from app.db.database import Base


class AthleteSettings(Base):
    __tablename__ = "athlete_settings"
    id = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    payload_json = Column(String, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PlanRevision(Base):
    __tablename__ = "plan_revisions"
    __table_args__ = (UniqueConstraint("week_start", "version"),)
    id = Column(Integer, primary_key=True)
    week_start = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    action = Column(String, nullable=False)
    snapshot_json = Column(String, nullable=False)
    metadata_json = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    undone_at = Column(DateTime)
