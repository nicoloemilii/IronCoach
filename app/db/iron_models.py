"""Additive models for the personal Iron Coach experience."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint
from app.db.database import Base


class User(Base):
    __tablename__ = 'iron_users'
    id = Column(Integer, primary_key=True)
    timezone = Column(String, nullable=False, default='Europe/Rome')


class HealthMetric(Base):
    __tablename__ = 'health_metrics'
    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    value = Column(Float, nullable=True)
    unit = Column(String, nullable=False)
    source = Column(String, nullable=False)
    observed_at = Column(String, nullable=False)
    synced_at = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint('date', 'kind', 'source'),)


class RaceGoal(Base):
    __tablename__ = 'race_goals'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    date = Column(String, nullable=False)
    priority = Column(String, nullable=False, default='finish')
    target_time = Column(String)
    legs_json = Column(String, nullable=False)


class IntegrationConnection(Base):
    __tablename__ = 'integration_connections'
    provider = Column(String, primary_key=True)
    athlete_id = Column(String)
    scopes = Column(String)
    status = Column(String, nullable=False, default='disconnected')


class SyncRun(Base):
    __tablename__ = 'sync_runs'
    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    days = Column(Integer, nullable=False)
    resources = Column(String, nullable=False)
    status = Column(String, nullable=False, default='running')
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime)
    result_json = Column(String)


class DuplicateCandidate(Base):
    __tablename__ = 'duplicate_candidates'
    id = Column(Integer, primary_key=True)
    activity_id = Column(Integer, ForeignKey('activities.id'), nullable=False)
    other_id = Column(Integer, ForeignKey('activities.id'), nullable=False)
    status = Column(String, nullable=False, default='pending')
    __table_args__ = (UniqueConstraint('activity_id', 'other_id'),)


class ActivityObservation(Base):
    __tablename__ = 'activity_observations'
    id = Column(Integer, primary_key=True)
    activity_id = Column(Integer, ForeignKey('activities.id'), nullable=False)
    source_key = Column(String, unique=True, nullable=False)
    payload_json = Column(String, nullable=False)


class SchemaVersion(Base):
    __tablename__ = 'iron_schema_versions'
    version = Column(Integer, primary_key=True)
    applied_at = Column(DateTime, default=datetime.utcnow, nullable=False)
