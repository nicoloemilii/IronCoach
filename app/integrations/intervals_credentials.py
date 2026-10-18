"""Personal credentials: Windows user-bound DPAPI, never plaintext or browser storage."""
import ctypes
import json
import os
from pathlib import Path
from threading import RLock
from app.db.database import DATA_DIR, workspace_dir, WORKSPACE, WORKSPACE_KEY

SECRET_PATH = DATA_DIR / '.secrets' / 'intervals.dpapi'
LOCK = RLock()


def _protect(data: bytes, decrypt=False) -> bytes:
    if WORKSPACE.get() is not None:
        import base64
        from cryptography.fernet import Fernet, InvalidToken
        key=WORKSPACE_KEY.get() or os.getenv('IRON_VAULT_KEY','')
        box=Fernet(key.encode())
        if decrypt:
            try: value=json.loads(box.decrypt(data))
            except (InvalidToken,ValueError): raise RuntimeError('Credenziale protetta non leggibile. Ricollega l’account.') from None
            if value['workspace']!=WORKSPACE.get(): raise ValueError('Credenziale di un altro account.')
            return base64.b64decode(value['payload'])
        return box.encrypt(json.dumps({'workspace':WORKSPACE.get(),'payload':base64.b64encode(data).decode()}).encode())
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
        path=workspace_dir()/'.secrets'/'intervals.fernet' if workspace_dir() else SECRET_PATH
        if path.exists():
            return json.loads(_protect(path.read_bytes(), decrypt=True))
        if workspace_dir(): return {'api_key':'','athlete_id':'0','source':None}
        key = os.getenv('INTERVALS_API_KEY', '').strip()
        return {'api_key': key, 'athlete_id': os.getenv('INTERVALS_ATHLETE_ID', '0').strip() or '0', 'source': 'environment' if key else None}


def store(api_key: str, athlete_id: str):
    content = _protect(json.dumps({'api_key': api_key, 'athlete_id': athlete_id, 'source': 'protected_server' if workspace_dir() else 'protected_local'}).encode())
    with LOCK:
        path=workspace_dir()/'.secrets'/'intervals.fernet' if workspace_dir() else SECRET_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(content)
        temporary.replace(path)


def disconnect():
    # A disabled record also disables an inherited environment credential.
    store('', '0')
