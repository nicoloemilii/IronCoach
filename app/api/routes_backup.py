"""Local-only personal backup download, validation preview and restore."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.routes_sync import local_only
from app.db.database import get_db
from app.services import backups

router = APIRouter(prefix='/backups', tags=['Backup personali'], dependencies=[Depends(local_only)])


@router.get('/export')
def export(db: Session = Depends(get_db)):
    content = backups._json(backups.export_backup(db)).encode('utf-8')
    filename = f'iron-coach-{datetime.utcnow():%Y%m%d-%H%M%S}.json'
    return Response(content, media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store',
    })


@router.post('/preview')
async def preview(request: Request, db: Session = Depends(get_db)):
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'Carica il file JSON esportato da Iron Coach.')
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > backups.MAX_BYTES:
            raise HTTPException(413, 'Il backup supera 20 MB.')
    result = backups.preview_backup(db, bytes(content))
    return result


class RestoreInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_token: str = Field(min_length=40, max_length=100)
    confirmation: str = Field(pattern=r'^RIPRISTINA$')


@router.post('/restore')
def restore(payload: RestoreInput, db: Session = Depends(get_db)):
    return backups.restore_backup(db, payload.preview_token)
