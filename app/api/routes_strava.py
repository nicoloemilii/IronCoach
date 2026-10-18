from fastapi import APIRouter, Depends, HTTPException, Request, Response, BackgroundTasks, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.api.routes_sync import local_only
from app.integrations import strava

router = APIRouter(tags=['Strava personale'])


def safe_error(error):
    return HTTPException(error.status, error.message)


@router.get('/integrations/strava/status', dependencies=[Depends(local_only)])
def status():
    config = strava.configuration()
    try: tokens = strava.read_tokens()
    except (OSError, ValueError, RuntimeError):
        return {'configured': bool(config['client_id'] and config['client_secret']), 'connected': False, 'message': 'Impossibile leggere le credenziali protette: ricollega dallo stesso utente Windows.'}
    return {'configured': bool(config['client_id'] and config['client_secret']), 'connected': bool(tokens.get('refresh_token')),
            'athlete_id': tokens.get('athlete_id'), 'scopes': tokens.get('scopes', []),
            'message': 'Consultazione personale temporanea, esclusa dalle analisi Coach.' if tokens.get('refresh_token') else 'Configura l’app Strava e autorizza il tuo account da questo computer.'}


@router.post('/integrations/strava/authorize', dependencies=[Depends(local_only)])
def authorize(response: Response):
    try: url, cookie = strava.authorize()
    except strava.StravaError as error: raise safe_error(error) from None
    response.set_cookie('iron_strava_oauth', cookie, max_age=600, httponly=True, samesite='lax', path='/integrations/strava/callback')
    return {'authorization_url': url}


@router.get('/integrations/strava/callback', dependencies=[Depends(local_only)])
def callback(request: Request, state: str = '', code: str = '', scope: str = '', error: str = ''):
    try: strava.callback(state, request.cookies.get('iron_strava_oauth'), code, scope, bool(error))
    except strava.StravaError as exc: raise safe_error(exc) from None
    except (OSError, RuntimeError, ValueError): raise HTTPException(503, 'Impossibile salvare le credenziali Strava protette.') from None
    response = RedirectResponse('/app#connections', status_code=303)
    response.delete_cookie('iron_strava_oauth', path='/integrations/strava/callback')
    return response


@router.delete('/integrations/strava', dependencies=[Depends(local_only)])
def disconnect():
    try: return strava.disconnect()
    except (OSError, ValueError, RuntimeError): raise HTTPException(503, 'Impossibile eliminare la configurazione protetta Strava.') from None


@router.get('/integrations/strava/activities', dependencies=[Depends(local_only)])
def activities():
    return strava.cached_activities()


@router.post('/sync/strava', status_code=202, dependencies=[Depends(local_only)])
def sync(background_tasks: BackgroundTasks, days: int = Query(90, ge=1, le=3650), db: Session = Depends(get_db)):
    from app.services.sync_jobs import launch
    return launch('strava', days, 'activities', db, background_tasks)
