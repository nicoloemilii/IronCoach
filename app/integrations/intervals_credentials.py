"""Personal credentials: Windows user-bound DPAPI, never plaintext or browser storage."""
import ctypes
import json
import os
from pathlib import Path
from threading import RLock
from app.db.database import DATA_DIR

SECRET_PATH = DATA_DIR / '.secrets' / 'intervals.dpapi'
LOCK = RLock()


def _protect(data: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('La memorizzazione protetta richiede Windows. Su altri sistemi configura INTERVALS_API_KEY sul server.')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError('Impossibile accedere alla chiave protetta per questo utente Windows. Ricollega Intervals.icu.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def credentials() -> dict:
    with LOCK:
        if SECRET_PATH.exists():
            return json.loads(_protect(SECRET_PATH.read_bytes(), decrypt=True))
        key = os.getenv('INTERVALS_API_KEY', '').strip()
        return {'api_key': key, 'athlete_id': os.getenv('INTERVALS_ATHLETE_ID', '0').strip() or '0', 'source': 'environment' if key else None}


def store(api_key: str, athlete_id: str):
    content = _protect(json.dumps({'api_key': api_key, 'athlete_id': athlete_id, 'source': 'protected_local'}).encode())
    with LOCK:
        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SECRET_PATH.with_suffix('.tmp')
        temporary.write_bytes(content)
        temporary.replace(SECRET_PATH)


def disconnect():
    # A disabled record also disables an inherited environment credential.
    store('', '0')
