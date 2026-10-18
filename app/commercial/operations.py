"""Encrypted operational snapshots and offline restoration into an empty folder."""
import hashlib
import io
import json
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, delete
from app.commercial.accounts import Account, ActionToken, LoginSession, DUMMY_HASH

ALLOWED=re.compile(r'accounts\.db|workspaces/[a-f0-9]{32}/(?:coach\.db|athlete_profile\.json|\.secrets/intervals\.fernet)')
MAX_ARCHIVE=128*1024*1024
MAX_UNPACKED=2*1024*1024*1024


def backup(root,destination,key):
    root=Path(root).resolve();destination=Path(destination).resolve();box=Fernet(key.encode());files=[]
    central=root/'accounts.db'
    if not central.is_file():raise ValueError('Database account non trovato.')
    with tempfile.TemporaryDirectory(prefix='iron-snapshot-') as temporary:
        folder=Path(temporary);snap=folder/'accounts.db'
        with closing(sqlite3.connect(central)) as source,closing(sqlite3.connect(snap)) as target:source.backup(target)
        files.append(('accounts.db',snap))
        with closing(sqlite3.connect(snap)) as db:identifiers=[row[0] for row in db.execute('SELECT id FROM accounts')]
        for identifier in identifiers:
            if not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('Identificativo account non valido.')
            for leaf in ('coach.db','athlete_profile.json','.secrets/intervals.fernet'):
                rel=f'workspaces/{identifier}/{leaf}';path=(root/rel).resolve()
                if not path.is_relative_to(root):raise ValueError('Percorso dati non valido.')
                if not path.is_file():continue
                if leaf=='coach.db':
                    target=folder/f'{identifier}.db'
                    with closing(sqlite3.connect(path)) as source,closing(sqlite3.connect(target)) as db:source.backup(db)
                    path=target
                files.append((rel,path))
        manifest={'format_version':1,'created_at':time.time(),'files':{}}
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
            total=0
            for rel,path in files:
                total+=path.stat().st_size
                if total>MAX_UNPACKED:raise ValueError('Snapshot troppo grande: usa il backup del volume del servizio.')
                data=path.read_bytes();manifest['files'][rel]=hashlib.sha256(data).hexdigest();archive.writestr(rel,data)
            archive.writestr('manifest.json',json.dumps(manifest))
        if buffer.tell()>MAX_ARCHIVE:raise ValueError('Snapshot compresso oltre 128 MB: usa il backup cifrato del volume.')
        encrypted=box.encrypt(buffer.getvalue());destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('xb') as stream:stream.write(encrypted)
    return {'files':len(files),'encrypted_bytes':len(encrypted),'sha256':hashlib.sha256(encrypted).hexdigest()}


def restore(source,target,key):
    source=Path(source);target=Path(target).resolve()
    if target.exists():raise ValueError('Scegli una nuova cartella inesistente; i dati esistenti non vengono sovrascritti.')
    if source.stat().st_size>MAX_ARCHIVE*2:raise ValueError('Snapshot troppo grande.')
    try:content=Fernet(key.encode()).decrypt(source.read_bytes())
    except InvalidToken:raise ValueError('Chiave non valida o snapshot alterato.') from None
    if len(content)>MAX_ARCHIVE:raise ValueError('Snapshot troppo grande.')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='iron-restore-',dir=target.parent) as temporary:
        staging=Path(temporary)/'data';staging.mkdir()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries=archive.infolist();names=[entry.filename for entry in entries]
            if len(names)>20000 or len(set(names))!=len(names) or 'manifest.json' not in names or sum(e.file_size for e in entries)>MAX_UNPACKED:raise ValueError('Struttura snapshot non valida.')
            manifest=json.loads(archive.read('manifest.json'))
            if manifest.get('format_version')!=1 or set(manifest['files'])!=set(names)-{'manifest.json'}:raise ValueError('Manifest snapshot non valido.')
            if 'accounts.db' not in manifest['files']:raise ValueError('Database account assente.')
            for entry in entries:
                name=entry.filename
                if name=='manifest.json':continue
                if not ALLOWED.fullmatch(name) or entry.external_attr>>16&0o170000==0o120000:raise ValueError('Percorso snapshot non autorizzato.')
                data=archive.read(entry)
                if hashlib.sha256(data).hexdigest()!=manifest['files'][name]:raise ValueError('Contenuto snapshot alterato.')
                path=(staging/name).resolve()
                if not path.is_relative_to(staging):raise ValueError('Percorso snapshot non valido.')
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
                if name.endswith('.db'):
                    with closing(sqlite3.connect(path)) as db:
                        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database snapshot non integro.')
            with closing(sqlite3.connect(staging/'accounts.db')) as db:
                db.execute('DELETE FROM login_sessions');db.execute('DELETE FROM action_tokens');db.commit()
        staging.rename(target)
    return {'restored_files':len(names)-1,'sessions_revoked':True}


def purge_deletions(store,root,older_than_days=30):
    if older_than_days<1:raise ValueError('La finestra di conservazione deve essere almeno un giorno.')
    root=Path(root).resolve();parent=(root/'workspaces').resolve();purged=[]
    with store.db() as db:
        rows=list(db.scalars(select(Account).where(Account.active.is_(False),Account.deletion_requested_at<time.time()-older_than_days*86400)))
        for row in rows:
            if not re.fullmatch('[a-f0-9]{32}',row.id):raise ValueError('Account non valido.')
            target=(parent/row.id).resolve()
            if target.parent!=parent:raise ValueError('Percorso cancellazione non valido.')
            from app.db.database import _WORKSPACE_ENGINES,_ENGINE_LOCK
            with _ENGINE_LOCK:
                owned=_WORKSPACE_ENGINES.pop(str(target),None)
                if owned:owned.dispose()
            if target.exists():shutil.rmtree(target)
            email=row.email;row.email=f'deleted-{row.id}@invalid';row.name='Account cancellato';row.password_hash=DUMMY_HASH
            db.execute(delete(LoginSession).where(LoginSession.account_id==row.id));db.execute(delete(ActionToken).where((ActionToken.account_id==row.id)|(ActionToken.email==email)))
            row.deletion_requested_at=None;store.audit(db,row.id,'personal_data_erased');purged.append(row.id)
    return {'erased_accounts':purged}
