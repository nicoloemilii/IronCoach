"""Rebuildable power curves and bounded local scan progress; never raw streams."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String

from app.db.database import Base
from app.db.models import Activity  # Register the referenced table for standalone schema setup.


class EffortAnalysis(Base):
    __tablename__ = 'effort_analyses'

    activity_id = Column(Integer, ForeignKey('activities.id'), primary_key=True)
    fingerprint = Column(String, nullable=False)
    algorithm_version = Column(String, nullable=False, default='raw-power-duration-1')
    status = Column(String, nullable=False)
    payload_json = Column(String, nullable=False, default='{}')
    source_identity = Column(String)
    source_hash = Column(String)
    source_fetched_at = Column(DateTime)
    analyzed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    cutoff_utc = Column(DateTime, nullable=False, default=datetime.utcnow)
    error_code = Column(String)


class EffortScan(Base):
    __tablename__ = 'effort_scans'

    id = Column(String(32), primary_key=True)
    status = Column(String, nullable=False, default='queued')
    mode = Column(String, nullable=False, default='stored')
    refresh = Column(Boolean, nullable=False, default=False)
    total = Column(Integer, nullable=False, default=0)
    processed = Column(Integer, nullable=False, default=0)
    with_power = Column(Integer, nullable=False, default=0)
    with_distance = Column(Integer, nullable=False, default=0)
    with_lengths = Column(Integer, nullable=False, default=0)
    unavailable = Column(Integer, nullable=False, default=0)
    errors = Column(Integer, nullable=False, default=0)
    reused = Column(Integer, nullable=False, default=0)
    max_activity_id = Column(Integer, nullable=False, default=0)
    activity_ids_json = Column(String, nullable=False, default='[]')
    cutoff_utc = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime)
    error_code = Column(String)


class DistanceAnalysis(Base):
    """Independent provenance for running series or explicit FIT pool lengths."""
    __tablename__ = 'distance_analyses'

    activity_id = Column(Integer, ForeignKey('activities.id'), primary_key=True)
    fingerprint = Column(String, nullable=False)
    algorithm_version = Column(String, nullable=False)
    status = Column(String, nullable=False)
    payload_json = Column(String, nullable=False, default='{}')
    source_identity = Column(String)
    source_hash = Column(String)
    source_fetched_at = Column(DateTime)
    analyzed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    cutoff_utc = Column(DateTime, nullable=False, default=datetime.utcnow)
    error_code = Column(String)
