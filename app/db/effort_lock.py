"""One synchronous mutation mutex per database, shared by restore and workers."""
import os
from pathlib import Path
from threading import Lock, RLock
from weakref import WeakValueDictionary


_LOCKS = WeakValueDictionary()
_REGISTRY_LOCK = Lock()


def database_key(bind):
    engine = getattr(bind, 'engine', bind)
    url = engine.url
    filename = url.database
    if url.get_backend_name() == 'sqlite':
        if not filename or filename == ':memory:' or url.query.get('mode') == 'memory':
            return 'sqlite-memory', engine
        return 'sqlite-file', os.path.normcase(str(Path(filename).expanduser().resolve()))
    return 'database-url', url.render_as_string(hide_password=False)


def database_lock(bind):
    """Return an RLock; callers retain it with ``with database_lock(bind):``.

    Distinct engine objects pointing at the same SQLite file share the mutex.
    In-memory engines are isolated. Weak values avoid retaining inactive
    workspace locks forever; registry access itself is synchronized.
    """
    key = database_key(bind)
    with _REGISTRY_LOCK:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _LOCKS[key] = lock
        return lock
