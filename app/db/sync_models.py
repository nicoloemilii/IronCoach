from sqlalchemy import Column, Integer, String
from app.db.database import Base


class IntervalsState(Base):
    __tablename__ = 'intervals_sync_state'
    id = Column(Integer, primary_key=True)
    athlete_id = Column(String)
    athlete_name = Column(String)
    credential_hash = Column(String)
    verified_at = Column(String)
    last_attempt_at = Column(String)
    last_success_at = Column(String)
    last_result_json = Column(String)
