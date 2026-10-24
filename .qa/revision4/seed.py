import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.main import app
from app.db.database import SessionLocal
from app.db.iron_models import HealthMetric
from app.services.athlete_clock import athlete_today
today=str(athlete_today())
with SessionLocal() as db:
    db.add(HealthMetric(date=today,kind='weight_kg',value=70,unit='kg',source='manual',observed_at=today,synced_at=today))
    db.commit()
