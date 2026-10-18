"""Athlete-reported symptoms; descriptions are not clinical diagnoses."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.db.database import Base


class HealthReport(Base):
    __tablename__ = 'health_reports'

    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False, index=True)
    kind = Column(String(20), nullable=False)
    body_location = Column(String(160), nullable=False, default='')
    severity = Column(Integer, nullable=False)
    status = Column(String(12), nullable=False, default='active')
    resolved_on = Column(String(10))
    suspected_cause = Column(String(1200), nullable=False, default='')
    notes = Column(String(2000), nullable=False, default='')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


def ensure_health_reports_schema(engine):
    """Create this additive table even when the original v2 schema is current."""
    HealthReport.__table__.create(engine, checkfirst=True)
