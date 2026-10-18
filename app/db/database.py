from pathlib import Path
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from contextvars import ContextVar
from contextlib import contextmanager
from threading import RLock
from uuid import UUID
from sqlalchemy.pool import NullPool


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv('IRON_DATA_DIR', str(BASE_DIR / 'data'))).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR / 'coach.db'}"

engine = create_engine(
    DATABASE_URL,
    hide_parameters=True,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()
WORKSPACE = ContextVar('iron_workspace', default=None)
WORKSPACE_KEY = ContextVar('iron_workspace_key', default=None)
_WORKSPACE_ENGINES = {}
_ENGINE_LOCK = RLock()


def workspace_dir():
    identifier = WORKSPACE.get()
    if identifier is None: return None
    if UUID(identifier).hex != identifier: raise ValueError('Workspace non valido.')
    parent = (DATA_DIR / 'workspaces').resolve()
    result = (parent / identifier).resolve()
    if result.parent != parent: raise ValueError('Percorso workspace non valido.')
    return result


@contextmanager
def workspace_scope(identifier):
    token = WORKSPACE.set(identifier)
    try: yield
    finally: WORKSPACE.reset(token)


def workspace_engine():
    folder = workspace_dir()
    if folder is None: return engine
    key = str(folder)
    with _ENGINE_LOCK:
        if key not in _WORKSPACE_ENGINES:
            folder.mkdir(parents=True, exist_ok=True)
            owned = create_engine(f'sqlite:///{folder / "coach.db"}', connect_args={'check_same_thread':False,'timeout':30}, poolclass=NullPool,hide_parameters=True)
            from app.db.migrations import migrate
            from app.db.health_reports import ensure_health_reports_schema
            from app.db.training_models import ensure_training_schema
            migrate(owned); ensure_health_reports_schema(owned); ensure_training_schema(owned)
            if len(_WORKSPACE_ENGINES)>=128: _WORKSPACE_ENGINES.pop(next(iter(_WORKSPACE_ENGINES))).dispose()
            _WORKSPACE_ENGINES[key] = owned
        return _WORKSPACE_ENGINES[key]


def current_session():
    return SessionLocal() if WORKSPACE.get() is None else sessionmaker(bind=workspace_engine(),autoflush=False)()


def get_db():
    db = current_session()
    try:
        yield db
    finally:
        db.close()
