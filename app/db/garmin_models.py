"""Local delivery ledger. A row is not evidence of publication on Garmin."""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint

from app.db.database import Base


class GarminDelivery(Base):
    __tablename__ = "garmin_deliveries"
    __table_args__ = (
        UniqueConstraint("workout_id", "content_hash", name="uq_garmin_workout_revision"),
    )

    id = Column(Integer, primary_key=True)
    workout_id = Column(Integer, nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)
    status = Column(String, nullable=False, default="blocked")
    reason_code = Column(String, nullable=False)
    remote_published = Column(Boolean, nullable=False, default=False)
    request_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
