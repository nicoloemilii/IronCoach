"""ASGI boundary: identity stays bound through streaming and background tasks."""
import ipaddress
import json
import logging
import secrets
import time
from urllib.parse import urlsplit
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.datastructures import MutableHeaders
from starlette.concurrency import run_in_threadpool
from app.db.database import WORKSPACE, WORKSPACE_KEY
from app.commercial.accounts import COOKIE

logger=logging.getLogger('iron.requests')
ASSETS={'index.html','login.html','style.css','account.js','app.js','charts.js','manual-training.js','metric-status.js','plan-coach.js','progress.js','usability.js','wellness.js'}
PUBLIC_API={'/account/config','/account/register','/account/login','/account/forgot-password','/account/resend-verification','/account/reset-password','/account/verify','/billing/webhook','/health','/ready','/'}
PUBLIC_FILES={'/login','/favicon.ico'}


class ProductBoundary:
    def __init__(self,app,settings,accounts=None): self.app=app;self.settings=settings;self.accounts=accounts

    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        request=Request(scope);path=request.url.path;cfg=self.settings;started=time.monotonic();identifier=secrets.token_hex(12);status=500;response_started=False
        workspace_token=WORKSPACE.set(None);key_token=WORKSPACE_KEY.set(None)
        async def protected_send(message):
            nonlocal status,response_started
            if message['type']=='http.response.start':
                response_started=True;status=message['status'];headers=MutableHeaders(scope=message)
                headers['X-Request-ID']=identifier;headers['X-Content-Type-Options']='nosniff';headers['Referrer-Policy']='same-origin';headers['X-Frame-Options']='DENY'
                headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
                if not path.startswith('/web/') and path not in ('/app','/','/login'): headers['Cache-Control']='no-store'
                if cfg.hosted:
                    headers['Strict-Transport-Security']='max-age=31536000'
                    headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
            await send(message)
        try:
            if path.startswith('/web/') and path.removeprefix('/web/') not in ASSETS: raise HTTPException(404,'Risorsa non disponibile.')
            host=request.headers.get('host','')
            if cfg.hosted:
                if host.lower()!=urlsplit(cfg.public_url).netloc.lower(): raise HTTPException(400,'Host non autorizzato.')
            else:
                try: local=ipaddress.ip_address(request.client.host).is_loopback
                except (ValueError,AttributeError): local=False
                if not local or request.url.hostname not in ('127.0.0.1','localhost','::1'): raise HTTPException(403,'La modalità personale è accessibile soltanto dal computer locale.')
            unsafe=request.method not in ('GET','HEAD','OPTIONS')
            if unsafe and path!='/billing/webhook':
                origin=request.headers.get('origin')
                expected=cfg.public_url if cfg.hosted else str(request.base_url).rstrip('/')
                if (origin and origin.rstrip('/')!=expected) or request.headers.get('sec-fetch-site')=='cross-site': raise HTTPException(403,'Origine della richiesta non autorizzata.')
            if cfg.hosted:
                public=path in PUBLIC_API or path in PUBLIC_FILES or path.startswith('/web/')
                account=await run_in_threadpool(self.accounts.authenticate,request.cookies.get(COOKIE))
                if not public and not account:
                    response=RedirectResponse('/login',303) if path=='/app' else JSONResponse({'detail':'Accedi al tuo account.'},401)
                    return await response(scope,receive,protected_send)
                if account:
                    scope.setdefault('state',{})['account']=account
                    WORKSPACE.set(account['id']);WORKSPACE_KEY.set(cfg.vault_key)
                    if not path.startswith('/web/'): await run_in_threadpool(self.accounts.limit,'account:'+account['id'],300,60)
                    if unsafe and not public and not secrets.compare_digest(request.headers.get('x-csrf-token',''),account['csrf']): raise HTTPException(403,'Sessione da aggiornare. Ricarica la pagina prima di salvare.')
                    premium=unsafe and (path in ('/planning/week','/coach/workouts','/training/manual','/planning/coach/apply') or path.endswith('/preview') and path.startswith('/planning/'))
                    if premium and not self.accounts.access(account)['allowed']: raise HTTPException(402,'Il periodo di prova è terminato. Gestisci l’abbonamento dalla sezione Account.')
                # The local Strava cache is deliberately not shared by hosted accounts.
                if path.startswith('/integrations/strava') or path=='/sync/strava':
                    if path=='/integrations/strava/status': response=JSONResponse({'configured':False,'connected':False,'message':'Strava non è abilitato in questa distribuzione ospitata.'})
                    elif path=='/integrations/strava/activities': response=JSONResponse({'activities':[],'message':'Collegamento Strava non disponibile.'})
                    else: raise HTTPException(409,'Strava richiede una configurazione ospitata e autorizzazioni dedicate.')
                    return await response(scope,receive,protected_send)
                if path=='/import/fit': raise HTTPException(404,'Usa il caricamento FIT dall’interfaccia; l’importazione da cartelle del server non è disponibile.')
            # Bound streamed and chunked request bodies, not only Content-Length.
            if unsafe:
                limit=16384 if path.startswith('/account/') else 1048576 if path=='/billing/webhook' else 21*1024*1024
                try: length=int(request.headers.get('content-length','0'))
                except ValueError: raise HTTPException(400,'Dimensione della richiesta non valida.') from None
                if length<0 or length>limit: raise HTTPException(413,'La richiesta supera la dimensione consentita.')
                parts=[];size=0
                while True:
                    message=await receive()
                    if message['type']=='http.disconnect': return
                    body=message.get('body',b'');size+=len(body)
                    if size>limit: raise HTTPException(413,'La richiesta supera la dimensione consentita.')
                    parts.append(body)
                    if not message.get('more_body',False): break
                delivered=False
                async def replay():
                    nonlocal delivered
                    if not delivered: delivered=True;return {'type':'http.request','body':b''.join(parts),'more_body':False}
                    return await receive()
                await self.app(scope,replay,protected_send)
            else: await self.app(scope,receive,protected_send)
        except HTTPException as error:
            if response_started: raise
            await JSONResponse({'detail':error.detail},error.status_code,headers=error.headers)(scope,receive,protected_send)
        except Exception as error:
            if response_started: raise
            logger.error(json.dumps({'request_id':identifier,'error_type':type(error).__name__}))
            await JSONResponse({'detail':'Operazione non completata. Riprova e comunica al supporto il codice richiesta.','request_id':identifier},500)(scope,receive,protected_send)
        finally:
            WORKSPACE.reset(workspace_token);WORKSPACE_KEY.reset(key_token)
            logger.info(json.dumps({'request_id':identifier,'method':request.method,'path':path,'status':status,'duration_ms':round((time.monotonic()-started)*1000)},separators=(',',':')))
