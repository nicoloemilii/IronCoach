"""Hosted identity and tenant isolation using disposable SQLite and mocked providers."""
from dataclasses import replace
import json
import time
from uuid import uuid4
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.commercial.config import Settings
from app.commercial.setup import install
from app.commercial.accounts import Account, LoginSession, ActionToken, PASSWORDS
from app.db import database
from app.api import routes_athlete, routes_planning, routes_training, routes_iron, routes_backup, routes_sync, routes_activity_detail


@pytest.fixture
def hosted(tmp_path,monkeypatch):
    monkeypatch.setattr(database,'DATA_DIR',tmp_path)
    monkeypatch.setenv('INTERVALS_API_KEY','must-not-leak-from-local-environment')
    cfg=Settings(mode='hosted',public_url='https://product.test',vault_key=Fernet.generate_key().decode(),
        privacy_url='https://product.test/privacy',terms_url='https://product.test/terms',legal_version='test-v1',support_email='support@product.test')
    app=FastAPI()
    for module in (routes_athlete,routes_planning,routes_training,routes_iron,routes_backup,routes_sync,routes_activity_detail):app.include_router(module.router)
    @app.get('/app')
    def shell():return {'app':True}
    @app.get('/health')
    def health():return {'status':'ok'}
    install(app,cfg,tmp_path)
    store=app.state.accounts;clients=[]
    def client():
        value=TestClient(app,base_url=cfg.public_url,client=('203.0.113.7',12345));clients.append(value);return value
    def signup(email='athlete@example.test',name='Atleta'):
        value=client();token=store.invite(email)
        response=value.post('/account/register',json={'name':name,'email':email,'password':'valid-test-password-2026','invitation':token,'terms_accepted':True,'health_data_consent':True})
        assert response.status_code==201,response.text
        response=value.post('/account/login',json={'email':email,'password':'valid-test-password-2026'});assert response.status_code==200,response.text
        value.headers['X-CSRF-Token']=response.json()['csrf'];return value
    yield app,store,client,signup,tmp_path
    for value in clients:value.close()
    store.engine.dispose()
    for key in list(database._WORKSPACE_ENGINES):
        if str(tmp_path) in key:database._WORKSPACE_ENGINES.pop(key).dispose()


def test_hosted_requires_https_origin_and_separate_encryption_key():
    with pytest.raises(ValueError):Settings(mode='hosted',public_url='http://example.com').validate()
    with pytest.raises(ValueError):Settings(mode='hosted',public_url='https://example.com').validate()
    with pytest.raises(ValueError):Settings(mode='unsafe').validate()


@pytest.mark.parametrize('path',['/athlete/profile','/planning/profile','/planning/week','/health/metrics','/goals','/coach/overview','/backup/export','/account/export','/openapi.json'])
def test_personal_apis_require_authentication(hosted,path):
    _,_,client,_,_=hosted;response=client().get(path)
    assert response.status_code==401
    assert response.headers['cache-control']=='no-store'
    assert response.headers['x-request-id']


def test_login_redirect_secure_cookie_and_no_plaintext_password_or_tokens(hosted):
    _,store,client,signup,_=hosted
    response=client().get('/app',follow_redirects=False);assert response.status_code==303;assert response.headers['location']=='/login'
    first=signup();cookie=first.cookies.get('__Host-iron_session');assert cookie
    raw=first.post('/account/login',json={'email':'athlete@example.test','password':'valid-test-password-2026'})
    header=raw.headers['set-cookie'];assert 'HttpOnly' in header and 'Secure' in header and 'SameSite=lax' in header
    with store.db() as db:
        account=db.scalar(select(Account));session=db.scalar(select(LoginSession))
        assert account.password_hash.startswith('$argon2id$');assert PASSWORDS.verify('valid-test-password-2026',account.password_hash)
        assert session.token_hash!=cookie and cookie not in session.token_hash
    me=first.get('/account/me');assert 'password_hash' not in me.text and 'session_hash' not in me.text


def test_accounts_cannot_read_or_modify_other_training_profile_or_secrets(hosted):
    _,store,_,signup,root=hosted;a=signup();b=signup('second@example.test','Secondo')
    assert a.put('/athlete/profile',json={'name':'Profilo A','height_cm':181}).status_code==200
    assert b.get('/athlete/profile').json()['name'] is None
    profile=a.get('/planning/profile').json();profile['profile']['name']='Solo A'
    assert a.put('/planning/profile',json=profile['profile'],headers={'If-Match':str(profile['revision'])}).status_code==200
    assert b.get('/planning/profile').json()['profile']['name']==''
    today=time.strftime('%Y-%m-%d')
    from app.services.athlete_clock import athlete_today
    payload={'date':str(athlete_today()),'sport':'strength','name':'Scheda privata','duration_min':30,'target_rpe':4,'exercises':[{'name':'Plank','sets':2,'work_seconds':30,'rest_seconds':45}]}
    response=a.post('/training/manual',json=payload);assert response.status_code==201,response.text;identifier=response.json()['id']
    assert b.get(f'/training/gym/{identifier}/progress').status_code==404
    assert b.get('/account/export').json()['training']['data']['workouts']==[]
    with store.db() as db:ids=[r.id for r in db.scalars(select(Account))]
    assert len(list((root/'workspaces').glob('*/coach.db')))==2
    from app.integrations import intervals_credentials as vault
    from app.db.database import workspace_scope,WORKSPACE_KEY
    key=WORKSPACE_KEY.set(store.settings.vault_key)
    try:
        with workspace_scope(ids[0]):vault.store('tenant-a-private-key','123');protected=vault.credentials();assert protected['api_key']=='tenant-a-private-key';assert b'tenant-a-private-key' not in (database.workspace_dir()/'.secrets'/'intervals.fernet').read_bytes()
        with workspace_scope(ids[1]):assert vault.credentials()['api_key']==''
        with workspace_scope(ids[1]):
            source=(root/'workspaces'/ids[0]/'.secrets'/'intervals.fernet').read_bytes()
            with pytest.raises(ValueError):vault._protect(source,decrypt=True)
    finally:WORKSPACE_KEY.reset(key)
    assert database.WORKSPACE.get() is None


@pytest.mark.parametrize('headers',[{'X-CSRF-Token':''},{'X-CSRF-Token':'forged'},{'Origin':'https://attacker.test'},{'Sec-Fetch-Site':'cross-site'}])
def test_writes_reject_forged_csrf_or_foreign_origin(hosted,headers):
    _,_,_,signup,_=hosted;value=signup();response=value.put('/athlete/profile',json={'name':'forged'},headers=headers);assert response.status_code==403
    assert value.get('/athlete/profile').json()['name'] is None


def test_logout_all_revokes_other_devices_and_idle_sessions_expire(hosted):
    _,store,client,signup,_=hosted;a=signup();b=client();assert b.post('/account/login',json={'email':'athlete@example.test','password':'valid-test-password-2026'}).status_code==200
    assert a.post('/account/logout?all_sessions=true').status_code==200;assert b.get('/account/me').status_code==401
    assert b.post('/account/login',json={'email':'athlete@example.test','password':'valid-test-password-2026'}).status_code==200
    with store.db() as db:
        for session in db.scalars(select(LoginSession)):session.last_seen=time.time()-86401
    assert b.get('/account/me').status_code==401


def test_reset_is_single_use_and_revokes_all_sessions_without_exposing_account_existence(hosted,monkeypatch):
    _,store,client,signup,_=hosted;a=signup();sent=[];monkeypatch.setattr(store,'mail',lambda *args:sent.append(args))
    public=client();one=public.post('/account/forgot-password',json={'email':'athlete@example.test'});two=public.post('/account/forgot-password',json={'email':'absent@example.test'});assert one.status_code==two.status_code==202;assert one.json()==two.json()
    token=sent[0][2].split('#reset=')[1];body={'token':token,'password':'replacement-test-password'}
    assert public.post('/account/reset-password',json=body).status_code==200;assert a.get('/account/me').status_code==401
    assert public.post('/account/reset-password',json=body).status_code==400
    assert public.post('/account/login',json={'email':'athlete@example.test','password':'replacement-test-password'}).status_code==200


def test_signed_in_password_change_revokes_current_and_other_sessions(hosted):
    _,_,_,signup,_=hosted;value=signup()
    assert value.put('/account/password',json={'current_password':'valid-test-password-2026','password':'replacement-test-password'}).status_code==200
    assert value.get('/account/me').status_code==401


def test_consent_invite_ownership_body_limit_and_generic_validation_errors(hosted):
    _,store,client,_,_=hosted;value=client();token=store.invite('athlete@example.test')
    body={'name':'A','email':'athlete@example.test','password':'valid-test-password-2026','invitation':token,'terms_accepted':False,'health_data_consent':True}
    assert value.post('/account/register',json=body).status_code==422
    body['terms_accepted']=True;body['email']='other@example.test';assert value.post('/account/register',json=body).status_code==400
    body['email']='athlete@example.test';assert value.post('/account/register',json=body).status_code==201
    bad=value.post('/account/login',json={'email':'athlete@example.test','password':'tiny-secret'});assert bad.status_code==422;assert 'tiny-secret' not in bad.text
    assert value.post('/account/login',content=b'x'*16385).status_code==413
    assert value.post('/account/login',json={'email':'athlete@example.test','password':'valid-test-password-2026'},headers={'Host':'attacker.test'}).status_code==400


def test_unverified_email_does_not_sign_in_until_single_use_verification(hosted,monkeypatch):
    _,store,client,_,_=hosted;store.settings=replace(store.settings,registration_open=True,smtp_host='smtp.test',mail_from='mail@product.test')
    sent=[];monkeypatch.setattr(store,'mail',lambda *args:sent.append(args));value=client()
    body={'name':'A','email':'new@example.test','password':'valid-test-password-2026','terms_accepted':True,'health_data_consent':True}
    assert value.post('/account/register',json=body).json()['status']=='verification_required'
    assert value.post('/account/login',json={'email':body['email'],'password':body['password']}).status_code==403
    token=sent[0][2].split('#verify=')[1];assert value.post('/account/verify',json={'token':token}).status_code==200
    assert value.post('/account/verify',json={'token':token}).status_code==400
    assert value.post('/account/login',json={'email':body['email'],'password':body['password']}).status_code==200


def test_billing_trial_enforced_on_server_and_expired_access_retains_data_export(hosted):
    app,store,_,signup,_=hosted;value=signup();store.settings=replace(store.settings,billing_required=True)
    # Boundary references the same store; only entitlement policy changes here.
    with store.db() as db:db.scalar(select(Account)).trial_until=time.time()-1
    assert value.post('/planning/week',json={}).status_code==402
    assert value.get('/account/export').status_code==200
    assert value.get('/planning/profile').status_code==200


def test_hosted_strava_and_server_folder_import_never_expose_local_state(hosted):
    _,_,_,signup,_=hosted;value=signup()
    assert value.get('/integrations/strava/status').json()['connected'] is False
    assert value.get('/integrations/strava/activities').json()['activities']==[]
    assert value.post('/import/fit?directory=C:/').status_code==404


def test_deletion_disables_only_own_account_and_records_request(hosted):
    _,store,_,signup,_=hosted;a=signup();b=signup('second@example.test')
    response=a.post('/account/delete',json={'email':'athlete@example.test','password':'valid-test-password-2026'});assert response.status_code==202
    assert a.get('/account/me').status_code==401;assert b.get('/account/me').status_code==200
    with store.db() as db:assert db.scalar(select(Account).where(Account.email=='athlete@example.test')).deletion_requested_at


def test_rate_limit_is_shared_and_does_not_store_email_in_keys(hosted):
    _,store,_,_,_=hosted
    for _ in range(3):store.limit('private-user@example.test',maximum=3)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:store.limit('private-user@example.test',maximum=3)
    assert error.value.status_code==429
    assert 'private-user@example.test' not in (store.engine.url.database and Path(store.engine.url.database).read_bytes().decode('latin1'))


def test_rate_windows_of_different_lengths_do_not_reset_each_other(hosted):
    _,store,_,_,_=hosted
    store.limit('login:ip',maximum=1,seconds=600);store.limit('account:id',maximum=300,seconds=60)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:store.limit('login:ip',maximum=1,seconds=600)
    assert failure.value.status_code==429


def test_internal_errors_are_redacted_and_have_a_traceable_request_id(hosted):
    app,_,client,signup,_=hosted
    @app.get('/failure')
    def failure():raise ValueError('sensitive-private-payload')
    value=signup();response=value.get('/failure');assert response.status_code==500
    assert 'sensitive-private-payload' not in response.text;assert response.json()['request_id']==response.headers['x-request-id']
    assert 'frame-ancestors' in response.headers['content-security-policy']


@pytest.mark.parametrize('path',['/web/tests/app.test.cjs','/web/tests/node_modules/jsdom/package.json','/web/../data/coach.db'])
def test_test_sources_and_server_data_are_not_static_assets(hosted,path):
    _,_,client,_,_=hosted;assert client().get(path).status_code in (401,404)


def test_stripe_signature_duplicates_current_state_and_price_ownership(hosted,monkeypatch):
    from app.commercial import billing
    import hashlib,hmac
    _,store,client,signup,_=hosted;account=signup();webhook=client()
    store.settings=replace(store.settings,stripe_key='sk_test_dummy',stripe_price='price_expected',stripe_webhook_secret='whsec_test')
    with store.db() as db:row=db.scalar(select(Account));row.stripe_customer='cus_expected';row.trial_until=time.time()-1
    current={'id':'sub_owned','customer':'cus_expected','status':'active','items':{'data':[{'price':{'id':'price_expected'},'current_period_end':time.time()+86400}]}}
    calls=[]
    def remote(cfg,method,path,data=None,key=None):calls.append(path);return current
    monkeypatch.setattr(billing,'remote',remote)
    def event(identifier,status='active',signature=True):
        body=json.dumps({'id':identifier,'object':'event','type':'customer.subscription.updated','data':{'object':{'id':'sub_owned','status':status}}}).encode();stamp=str(int(time.time()));sig=hmac.new(b'whsec_test',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
        return webhook.post('/billing/webhook',content=body,headers={'Stripe-Signature':f't={stamp},v1={sig if signature else "forged"}'})
    assert event('evt_1',signature=False).status_code==400;assert not calls
    assert event('evt_1').status_code==200;assert account.get('/account/me').json()['access']['paid'] is True
    assert event('evt_1').json()['duplicate'] is True;assert len(calls)==1
    current['status']='canceled';assert event('evt_2','active').status_code==200;assert account.get('/account/me').json()['access']['paid'] is False
    current['status']='active';current['items']['data'][0]['price']['id']='price_wrong';assert event('evt_3').status_code==200;assert account.get('/account/me').json()['access']['paid'] is False


def test_checkout_uses_only_server_price_and_reuses_open_checkout(hosted,monkeypatch):
    from app.commercial import billing
    _,store,_,signup,_=hosted;value=signup();store.settings=replace(store.settings,stripe_key='sk_test_dummy',stripe_price='price_expected',stripe_webhook_secret='whsec_test')
    calls=[]
    def remote(cfg,method,path,data=None,key=None):
        calls.append((method,path,data,key))
        if path=='customers':return {'id':'cus_expected'}
        if path=='checkout/sessions':return {'id':'cs_test_owned','url':'https://checkout.stripe.com/test-session'}
        if path=='checkout/sessions/cs_test_owned':return {'status':'open','url':'https://checkout.stripe.com/test-session'}
        raise AssertionError(path)
    monkeypatch.setattr(billing,'remote',remote)
    one=value.post('/billing/checkout',json={'price':'price_forged'});two=value.post('/billing/checkout',json={});assert one.status_code==two.status_code==200;assert one.json()==two.json()
    session_calls=[c for c in calls if c[1]=='checkout/sessions'];assert len(session_calls)==1;assert session_calls[0][2]['line_items[0][price]']=='price_expected';assert session_calls[0][3]


def test_operational_backup_is_encrypted_restorable_and_revokes_sessions(hosted):
    from app.commercial.operations import backup,restore
    import sqlite3
    _,_,_,signup,root=hosted;value=signup();assert value.put('/athlete/profile',json={'name':'Private A'}).status_code==200
    key=Fernet.generate_key().decode();source=root/'snapshot.enc';out=root/'restored'
    result=backup(root,source,key);assert result['files']>=2;assert b'Private A' not in source.read_bytes()
    assert restore(source,out,key)['sessions_revoked'] is True
    with sqlite3.connect(out/'accounts.db') as db:assert db.execute('SELECT count(*) FROM login_sessions').fetchone()[0]==0;assert db.execute('SELECT count(*) FROM accounts').fetchone()[0]==1
    with pytest.raises(ValueError):restore(source,out,key)
    with pytest.raises(ValueError):restore(source,root/'wrong-key',Fernet.generate_key().decode())


def test_operational_restore_rejects_path_traversal_even_inside_an_authenticated_snapshot(hosted):
    from app.commercial.operations import restore
    import io,zipfile,hashlib
    _,_,_,_,root=hosted;key=Fernet.generate_key();buffer=io.BytesIO();content=b'outside'
    with zipfile.ZipFile(buffer,'w') as archive:
        archive.writestr('../outside',content);archive.writestr('accounts.db',b'not-a-db')
        archive.writestr('manifest.json',json.dumps({'format_version':1,'files':{'../outside':hashlib.sha256(content).hexdigest(),'accounts.db':hashlib.sha256(b'not-a-db').hexdigest()}}))
    source=root/'malicious.enc';source.write_bytes(Fernet(key).encrypt(buffer.getvalue()))
    with pytest.raises(ValueError):restore(source,root/'rejected',key.decode())
    assert not (root/'outside').exists();assert not (root/'rejected').exists()


def test_erasure_purges_only_expired_requests_and_preserves_other_accounts(hosted):
    from app.commercial.operations import purge_deletions
    _,store,_,signup,root=hosted;a=signup();b=signup('second@example.test');a.put('/athlete/profile',json={'name':'Private A'});b.put('/athlete/profile',json={'name':'Private B'})
    assert a.post('/account/delete',json={'email':'athlete@example.test','password':'valid-test-password-2026'}).status_code==202
    with store.db() as db:row=db.scalar(select(Account).where(Account.email=='athlete@example.test'));identifier=row.id;row.deletion_requested_at=time.time()-31*86400
    result=purge_deletions(store,root,30);assert result['erased_accounts']==[identifier];assert not (root/'workspaces'/identifier).exists();assert b.get('/athlete/profile').json()['name']=='Private B'


def test_remote_detail_workers_keep_workspace_context_and_cache_boundaries(hosted,monkeypatch):
    from app.services import activity_detail
    from app.db.database import workspace_scope,WORKSPACE
    _,_,_,_,_=hosted;identifiers=[uuid4().hex,uuid4().hex]
    monkeypatch.setattr(activity_detail.intervals_credentials,'credentials',lambda:{'api_key':'same-key','athlete_id':'same-id'})
    def remote(identifier,resource='',params=None):
        assert WORKSPACE.get() in identifiers
        return [] if resource else {'owner':WORKSPACE.get()}
    monkeypatch.setattr(activity_detail.intervals,'get_activity_remote',remote)
    activity_detail._CACHE.clear()
    for identifier in identifiers:
        with workspace_scope(identifier):assert activity_detail._cached_remote('i123')[0]['owner']==identifier
    assert WORKSPACE.get() is None


def test_hosted_scheduler_binds_each_active_account_and_never_uses_global_provider_credentials(hosted,monkeypatch):
    from app.services import sync_jobs
    from threading import Event
    _,store,_,signup,_=hosted;first=signup();second=signup('second@example.test');seen=[]
    # Schema creation is covered separately; do not make scheduler timing depend
    # on first-run filesystem and antivirus costs on Windows CI.
    assert first.get('/planning/profile').status_code==second.get('/planning/profile').status_code==200
    monkeypatch.setenv('IRON_AUTO_SYNC','1');monkeypatch.setattr(sync_jobs,'STOP',Event());monkeypatch.setattr(sync_jobs,'THREAD',None)
    def tick():
        from app.integrations.intervals_credentials import credentials
        assert credentials()['api_key']=='';seen.append(database.WORKSPACE.get())
        if len(seen)==2:sync_jobs.STOP.set()
    monkeypatch.setattr(sync_jobs,'automatic_tick',tick);sync_jobs.start_hosted(store)
    try:sync_jobs.THREAD.join(timeout=20)
    finally:sync_jobs.STOP.set();sync_jobs.THREAD.join(timeout=10)
    assert not sync_jobs.THREAD.is_alive()
    assert len(seen)==2 and len(set(seen))==2 and all(seen)


@pytest.mark.parametrize('same_account',[True,False])
def test_mutations_only_block_the_same_athlete(same_account):
    import asyncio
    from starlette.requests import Request
    from starlette.responses import Response
    from app.commercial.mutations import guard_mutations
    def request(identifier):return Request({'type':'http','method':'POST','path':'/training/manual','scheme':'https','server':('product.test',443),'query_string':b'','headers':[], 'state':{'account':{'id':identifier}}})
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        async def slow(_):entered.set();await release.wait();return Response(status_code=200)
        first=asyncio.create_task(guard_mutations(request('a'),slow,True));await entered.wait()
        second=await guard_mutations(request('a' if same_account else 'b'),lambda _:asyncio.sleep(0,result=Response(status_code=200)),True)
        release.set();assert (await first).status_code==200;assert second.status_code==(409 if same_account else 200)
    asyncio.run(run())
