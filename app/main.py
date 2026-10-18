from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pathlib import Path
from contextlib import asynccontextmanager
from asyncio import Lock
from starlette.responses import JSONResponse
from app.db.database import Base, engine
from app.db import models
from app.api.routes_dashboard import router as dashboard_router
from app.api.routes_import import router as import_router
from app.api.routes_sync import router as sync_router
from app.api.routes_analysis import router as analysis_router
from app.api.routes_athlete import router as athlete_router
from app.api.routes_coach import router as coach_router
from app.api.routes_experience import router as experience_router
from app.api.routes_planning import router as planning_router
from app.api.routes_garmin import router as garmin_router
from app.api.routes_iron import router as iron_router
from app.api.routes_strava import router as strava_router
from app.api.routes_progress import router as progress_router
from app.api.routes_backup import router as backup_router
from app.api.routes_health_reports import router as health_reports_router
from app.api.routes_activity_detail import router as activity_detail_router
from app.db.health_reports import ensure_health_reports_schema
from app.api.routes_training import router as training_router
from app.db.training_models import ensure_training_schema
from app.db.migrations import migrate
from app.services import sync_jobs

migrate(engine)
ensure_health_reports_schema(engine)
ensure_training_schema(engine)


@asynccontextmanager
async def lifespan(app):
    sync_jobs.start()
    yield
    sync_jobs.stop()

app = FastAPI(
    title="Ironman Coach",
    description="Adaptive coaching engine locale per la preparazione Ironman",
    version="1.3.0",
    lifespan=lifespan,
)

app.include_router(dashboard_router)
app.include_router(import_router)
app.include_router(sync_router)
app.include_router(analysis_router)
app.include_router(athlete_router)
app.include_router(coach_router)
app.include_router(experience_router)
app.include_router(planning_router)
app.include_router(garmin_router)
app.include_router(iron_router)
app.include_router(strava_router)
app.include_router(progress_router)
app.include_router(backup_router)
app.include_router(health_reports_router)
app.include_router(activity_detail_router)
app.include_router(training_router)

# A restore must not race a request that read an old workout/profile and then
# writes after replacement. Read-only navigation remains concurrent.
WRITE_GATE = Lock()


@app.middleware('http')
async def serialize_mutations(request, call_next):
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return await call_next(request)
    if WRITE_GATE.locked():
        return JSONResponse({'detail': 'Un’altra operazione di salvataggio è in corso. Attendi il completamento e riprova.'}, status_code=409)
    await WRITE_GATE.acquire()
    try:
        return await call_next(request)
    finally:
        WRITE_GATE.release()

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")


@app.middleware('http')
async def refresh_web_assets(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path in ('/', '/app') or (path.startswith('/web/') and Path(path).suffix in ('.js', '.css')):
        response.headers['Cache-Control'] = 'no-cache'
    return response

@app.get("/app", include_in_schema=False)
def web_app():
    return FileResponse(WEB_DIR / "index.html")

@app.get("/")
def home(request: Request):
    if 'text/html' in request.headers.get('accept', ''):
        return RedirectResponse('/app', status_code=307)
    return {"app": "Ironman Coach", "status": "online", "version": "1.3.0"}

@app.get("/health")
def health():
    return {"status": "ok", "database": "connected"}
