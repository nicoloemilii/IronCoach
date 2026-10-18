"""Exercise the built image with disposable accounts and an owned Docker volume.

No external providers, production credentials or public ports are used.
Run on a Docker-enabled test host after building iron-coach:ci.
"""
import base64
import json
import os
import secrets
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    name='iron-ci-'+uuid4().hex;volume=name+'-data';created=False
    environment={**os.environ,'IRON_MODE':'hosted','IRON_PUBLIC_URL':'https://product.test','IRON_AUTO_SYNC':'0',
        'IRON_PRIVACY_URL':'https://product.test/privacy','IRON_TERMS_URL':'https://product.test/terms','IRON_LEGAL_VERSION':'ci-v1','IRON_SUPPORT_EMAIL':'ci@product.test',
        'IRON_VAULT_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),'IRON_BACKUP_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}
    def docker(*args,check=True):return subprocess.run(['docker',*args],env=environment,capture_output=True,text=True,check=check,timeout=90)
    if docker('volume','inspect',volume,check=False).returncode==0:raise RuntimeError('The generated test volume already exists; nothing was changed.')
    docker('volume','create',volume);created=True
    try:
        command=['run','-d','--name',name,'--read-only','--memory','1g','--pids-limit','128','--tmpfs','/tmp:rw,size=256m,mode=1777','--mount',f'type=volume,source={volume},target=/data','-p','127.0.0.1::8000']
        for key in ('IRON_MODE','IRON_PUBLIC_URL','IRON_AUTO_SYNC','IRON_PRIVACY_URL','IRON_TERMS_URL','IRON_LEGAL_VERSION','IRON_SUPPORT_EMAIL','IRON_VAULT_KEY','IRON_BACKUP_KEY'):command+=['--env',key]
        docker(*command,'iron-coach:ci');binding=docker('port',name,'8000/tcp').stdout.strip().splitlines()[0];origin='http://'+binding
        def http(path,method='GET',body=None,cookie=None,csrf=None):
            headers={'Host':'product.test','Content-Type':'application/json','Origin':'https://product.test'}
            if cookie:headers['Cookie']=cookie
            if csrf:headers['X-CSRF-Token']=csrf
            request=Request(origin+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers=headers)
            try:
                with urlopen(request,timeout=20) as response:return response.status,json.load(response),response.headers
            except HTTPError as error:return error.code,json.load(error),error.headers
        until=time.monotonic()+45
        while True:
            try:
                if http('/ready')[0]==200:break
            except (OSError,URLError):pass
            if time.monotonic()>until:raise RuntimeError('Container did not become ready.')
            time.sleep(.25)
        assert http('/planning/profile')[0]==401
        def account(email):
            invitation=json.loads(docker('exec',name,'python','-m','app.commercial.cli','invite',email).stdout)['invitation_url'].split('#invite=')[1];password=secrets.token_urlsafe(24)
            status,_,_=http('/account/register','POST',{'name':'CI athlete','email':email,'password':password,'invitation':invitation,'terms_accepted':True,'health_data_consent':True});assert status==201
            status,value,headers=http('/account/login','POST',{'email':email,'password':password});assert status==200
            cookie=headers['Set-Cookie'].split(';')[0];assert '__Host-' in cookie and 'Secure' in headers['Set-Cookie'];return cookie,value['csrf']
        first,csrf=account('first@product.test')
        status,current,_=http('/planning/profile',cookie=first);assert status==200 and current['profile']['name']==''
        from datetime import datetime
        from zoneinfo import ZoneInfo
        status,saved,_=http('/training/manual','POST',{'date':str(datetime.now(ZoneInfo('Europe/Rome')).date()),'sport':'strength','name':'CI gym','duration_min':30,'target_rpe':4,'exercises':[{'name':'Plank','sets':2,'work_seconds':30,'rest_seconds':60}]},first,csrf);assert status==201
        assert http(f'/training/gym/{saved["id"]}/progress',cookie=first)[0]==200
        second,_=account('second@product.test');status,exported,_=http('/account/export',cookie=second);assert status==200 and exported['training']['data']['workouts']==[]
        snapshot=json.loads(docker('exec',name,'python','-m','app.commercial.cli','backup','--output','/tmp/ci.enc').stdout);assert snapshot['files']>=3
        recovered=json.loads(docker('exec',name,'python','-m','app.commercial.cli','restore','--input','/tmp/ci.enc','--target','/data/restore-check').stdout);assert recovered['sessions_revoked']
        docker('exec',name,'python','-c',"import pathlib,sqlite3; assert not pathlib.Path('/srv/iron-coach/web/tests').exists(); db=sqlite3.connect('/data/restore-check/accounts.db'); assert db.execute('select count(*) from login_sessions').fetchone()[0]==0")
        print('Container smoke passed: hosted login, private workspaces, gym, export, encrypted backup and restore.')
    except Exception:
        print(docker('logs',name,check=False).stdout);print(docker('logs',name,check=False).stderr);raise
    finally:
        docker('rm','-f',name,check=False)
        if created:docker('volume','rm',volume,check=False)


if __name__=='__main__':main()
