"""Personal Strava reader. No Strava activity enters the coaching database.

2026 policy: transient, isolated display cache; no analysis, combining or LLM.
The cache expires after 24h including during network failure and is never backed up.
"""
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from urllib.parse import urlencode, urlsplit
import requests
from app.integrations.intervals_credentials import _protect
from app.db.database import DATA_DIR

LOCK = RLock()
SECRET_PATH = DATA_DIR / '.secrets' / 'strava.dpapi'
CACHE = {'activities': [], 'expires_at': 0, 'checked_at': 0}
PENDING = {}
COOLDOWN = 0


class StravaError(Exception):
    def __init__(self, message, status=502, retry_after=None):
        self.message = message; self.status = status; self.retry_after = retry_after
        super().__init__(message)


def configuration():
    return {'client_id': os.getenv('STRAVA_CLIENT_ID', ''), 'client_secret': os.getenv('STRAVA_CLIENT_SECRET', ''),
            'redirect_uri': os.getenv('STRAVA_REDIRECT_URI', 'http://127.0.0.1:8000/integrations/strava/callback')}


def read_tokens():
    with LOCK:
        return json.loads(_protect(SECRET_PATH.read_bytes(), decrypt=True)) if SECRET_PATH.exists() else {}


def store_tokens(value):
    with LOCK:
        encoded = _protect(json.dumps(value).encode())
        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SECRET_PATH.with_suffix('.tmp'); temporary.write_bytes(encoded); temporary.replace(SECRET_PATH)


def purge_cache():
    with LOCK:
        CACHE.update(activities=[], expires_at=0, checked_at=0)


def clear_tokens():
    store_tokens({}); purge_cache()


def request(method, path, *, data=None, params=None, token=None, auth=None):
    global COOLDOWN
    if time.time() < COOLDOWN:
        raise StravaError('Limite Strava: attendi prima di riprovare.', 429, max(1, int(COOLDOWN - time.time())))
    url = 'https://www.strava.com' + path
    for attempt in range(2):
        try:
            response = requests.request(method, url, data=data, params=params,
                                        headers={'Authorization': f'Bearer {token}'} if token else {}, auth=auth, timeout=20, allow_redirects=False)
        except requests.RequestException:
            if method == 'GET' and attempt == 0:
                time.sleep(.5); continue
            raise StravaError('Strava non è raggiungibile. Riprova più tardi.') from None
        if response.status_code == 429:
            try: delay = max(60, int(response.headers.get('Retry-After', 86400)))
            except (TypeError, ValueError): delay = 86400
            COOLDOWN = time.time() + delay
            raise StravaError('Limite di richieste Strava raggiunto.', 429, delay)
        if response.status_code >= 500 and method == 'GET' and attempt == 0:
            time.sleep(.5); continue
        if response.status_code in (401, 403):
            raise StravaError('Autorizzazione Strava assente, revocata o insufficiente. Ricollega l’account.', response.status_code)
        if not 200 <= response.status_code < 300:
            raise StravaError('Strava non ha completato la richiesta.')
        if not response.content: return {}
        try: return response.json()
        except ValueError: raise StravaError('Risposta Strava non valida.') from None


def authorize():
    config = configuration()
    if not config['client_id'] or not config['client_secret']:
        raise StravaError('Configura STRAVA_CLIENT_ID e STRAVA_CLIENT_SECRET sul server locale.', 409)
    parsed = urlsplit(config['redirect_uri'])
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.path != '/integrations/strava/callback' or parsed.query or parsed.fragment or parsed.username:
        raise StravaError('STRAVA_REDIRECT_URI deve essere il callback HTTP sul computer locale.', 409)
    state = secrets.token_urlsafe(32); browser_secret = secrets.token_urlsafe(32)
    with LOCK:
        for key in list(PENDING):
            if PENDING[key]['expires'] < time.time(): PENDING.pop(key)
        PENDING[state] = {'cookie': browser_secret, 'expires': time.time() + 600}
    url = 'https://www.strava.com/oauth/authorize?' + urlencode({'client_id': config['client_id'], 'redirect_uri': config['redirect_uri'],
             'response_type': 'code', 'approval_prompt': 'auto', 'scope': 'read,activity:read_all', 'state': state})
    return url, browser_secret


def callback(state, browser_secret, code, scope, denied=False):
    with LOCK:
        pending = PENDING.pop(state, None)
        if not pending or pending['expires'] < time.time() or not secrets.compare_digest(pending['cookie'], browser_secret or ''):
            raise StravaError('Autorizzazione scaduta o stato OAuth non valido. Riprova da Connessioni.', 400)
        if denied or not code: raise StravaError('Autorizzazione Strava annullata.', 400)
        scopes = set(scope.replace(',', ' ').split())
        if not scopes.intersection({'activity:read', 'activity:read_all'}):
            raise StravaError('Strava non ha concesso la lettura delle attività.', 403)
        config = configuration()
        result = request('POST', '/oauth/token', data={'client_id': config['client_id'], 'client_secret': config['client_secret'], 'grant_type': 'authorization_code', 'code': code})
        if not all(result.get(k) for k in ('access_token', 'refresh_token', 'expires_at')) or not result.get('athlete', {}).get('id'):
            raise StravaError('Risposta OAuth Strava incompleta.')
        value = {k: result[k] for k in ('access_token', 'refresh_token', 'expires_at')}
        value.update(athlete_id=str(result['athlete']['id']), scopes=sorted(scopes))
        store_tokens(value); purge_cache()
        return value


def access_token():
    # Caller holds this same RLock through a full sync/revoke: refresh cannot race.
    with LOCK:
        value = read_tokens()
        if not value.get('refresh_token'): raise StravaError('Collega il tuo account Strava.', 409)
        if value.get('expires_at', 0) <= time.time() + 60:
            config = configuration()
            result = request('POST', '/oauth/token', data={'client_id': config['client_id'], 'client_secret': config['client_secret'], 'grant_type': 'refresh_token', 'refresh_token': value['refresh_token']})
            if not all(result.get(k) for k in ('access_token', 'refresh_token', 'expires_at')):
                raise StravaError('Rinnovo Strava incompleto. Ricollega l’account.')
            value.update({k: result[k] for k in ('access_token', 'refresh_token', 'expires_at')}); store_tokens(value)
        return value['access_token']


def sync(days):
    with LOCK:
        # Entire range is replaced, so remotely deleted activities disappear.
        # On failure retain only until 24h from last full check, then hide/purge.
        token = access_token(); before = int(time.time()); after = before - days * 86400
        activities = []
        try:
            for page in range(1, 101):
                rows = request('GET', '/api/v3/athlete/activities', token=token, params={'after': after, 'before': before, 'page': page, 'per_page': 100})
                if not isinstance(rows, list): raise StravaError('Elenco attività Strava non valido.')
                for row in rows:
                    if not isinstance(row, dict) or not str(row.get('id', '')).isdigit(): raise StravaError('Attività Strava non valida.')
                    activities.append({k: row.get(k) for k in ('id', 'name', 'sport_type', 'start_date', 'moving_time', 'distance', 'total_elevation_gain', 'device_name')})
                if len(rows) < 100: break
            else: raise StravaError('Intervallo Strava troppo ampio: scegli un periodo più breve.', 422)
        except StravaError as error:
            if error.status in (401, 403): clear_tokens()
            if CACHE['expires_at'] <= time.time(): purge_cache()
            raise
        CACHE.update(activities=activities, checked_at=time.time(), expires_at=time.time() + 86400)
        return {'status': 'success', 'message': 'Consultazione personale aggiornata; dati esclusi dalle analisi e non archiviati nel database.'}


def cached_activities():
    with LOCK:
        if CACHE['expires_at'] <= time.time(): purge_cache()
        return {'activities': list(CACHE['activities']), 'expires_at': datetime.fromtimestamp(CACHE['expires_at'], timezone.utc).isoformat() if CACHE['expires_at'] else None,
                'message': 'Dati personali temporanei (massimo 24 ore), separati da Coach, riepiloghi e obiettivi.'}


def disconnect():
    with LOCK:
        tokens = read_tokens(); config = configuration(); error = None
        if tokens.get('refresh_token'):
            try:
                request('POST', '/oauth/revoke', data={'token': tokens['refresh_token'], 'token_type_hint': 'refresh_token'},
                        auth=(config['client_id'], config['client_secret']))
            except StravaError as exc: error = exc
        clear_tokens()
        return {'deleted': True, 'remote_revoked': error is None, 'message': 'Credenziali e dati temporanei eliminati.' if error is None else 'Dati locali eliminati. Revoca anche l’app dalle impostazioni Strava: il servizio non ha confermato la revoca.'}
