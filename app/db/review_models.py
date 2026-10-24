"""Private, transactional journal of explicit duplicate-review decisions."""
from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String
from app.db.database import Base


class DuplicateReview(Base):
    __tablename__ = 'duplicate_reviews'
    __table_args__ = {'sqlite_autoincrement': True}
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, nullable=False)
    action = Column(String, nullable=False)
    status = Column(String, nullable=False, default='applied')
    activity_id = Column(Integer, nullable=False)
    other_id = Column(Integer, nullable=False)
    before_json = Column(String, nullable=False)
    after_json = Column(String, nullable=False)
    before_fingerprint = Column(String, nullable=False)
    after_fingerprint = Column(String, nullable=False)
    summary_json = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    undone_at = Column(DateTime)
