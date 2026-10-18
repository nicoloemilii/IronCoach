from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.commercial.accounts import COOKIE, SESSION_SECONDS

router=APIRouter(prefix='/account',tags=['Account'])


def store(request:Request):
    value=getattr(request.app.state,'accounts',None)
    if value is None: raise HTTPException(404,'Gli account sono disponibili nella modalità hosted.')
    return value


def identity(request:Request):
    value=getattr(request.state,'account',None)
    if not value: raise HTTPException(401,'Accedi al tuo account.')
    return value


class Credentials(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email:str=Field(min_length=3,max_length=254,pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    password:SecretStr

    @field_validator('password')
    @classmethod
    def valid_password(cls,value):
        if not 12<=len(value.get_secret_value())<=128: raise ValueError('La password deve contenere da 12 a 128 caratteri.')
        return value


class Registration(Credentials):
    name:str=Field(min_length=1,max_length=100)
    invitation:str=Field('',max_length=256)
    terms_accepted:bool=False
    health_data_consent:bool=False

    @field_validator('name')
    @classmethod
    def valid_name(cls,value):
        if not value.strip(): raise ValueError('Indica il tuo nome.')
        return value.strip()


class EmailRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email:str=Field(min_length=3,max_length=254)


class TokenRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token:str=Field(min_length=20,max_length=256)


class ResetRequest(TokenRequest):
    password:SecretStr
    _validate_password=field_validator('password')(Credentials.valid_password.__func__)


class PasswordChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    password:SecretStr
    current_password:SecretStr
    _validate_password=field_validator('password')(Credentials.valid_password.__func__)


def throttle(request,service,kind,maximum=10):
    service.limit(f'{kind}:{request.client.host if request.client else "unknown"}',maximum)


@router.get('/config')
def configuration(request:Request):
    cfg=request.app.state.settings
    return {'mode':cfg.mode,'registration_open':cfg.registration_open and cfg.email_ready,'legal_ready':cfg.legal_ready,
        'privacy_url':cfg.privacy_url,'terms_url':cfg.terms_url,'legal_version':cfg.legal_version,'support_email':cfg.support_email,
        'billing_enabled':cfg.billing_ready,'billing_required':cfg.billing_required,'trial_days':cfg.trial_days,'email_enabled':cfg.email_ready}


@router.post('/register',status_code=201)
def register(value:Registration,request:Request,service=Depends(store)):
    throttle(request,service,'register',5)
    return service.register(value)


@router.post('/login')
def login(value:Credentials,request:Request,response:Response,service=Depends(store)):
    throttle(request,service,'login')
    token,csrf=service.login(value.email,value.password.get_secret_value())
    response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,secure=True,httponly=True,samesite='lax',path='/')
    return {'authenticated':True,'csrf':csrf}


@router.get('/me')
def me(request:Request):
    if not request.app.state.settings.hosted: return {'mode':'local','authenticated':False}
    account=identity(request);service=store(request)
    return {'mode':'hosted','authenticated':True,'name':account['name'],'email':account['email'],'csrf':account['csrf'],'access':service.access(account)}


@router.post('/logout')
def logout(request:Request,response:Response,all_sessions:bool=False,service=Depends(store)):
    service.logout(identity(request),all_sessions)
    response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='lax')
    return {'authenticated':False}


@router.post('/forgot-password',status_code=202)
def forgot(value:EmailRequest,request:Request,service=Depends(store)):
    throttle(request,service,'reset',5);service.request_link(value.email,'reset')
    return {'message':'Se l’indirizzo è registrato, riceverai le istruzioni per recuperare l’accesso.'}


@router.post('/resend-verification',status_code=202)
def resend(value:EmailRequest,request:Request,service=Depends(store)):
    throttle(request,service,'verify',5);service.request_link(value.email,'verify')
    return {'message':'Se l’account è disponibile, riceverai il collegamento di conferma.'}


@router.post('/verify')
def verify(value:TokenRequest,request:Request,service=Depends(store)):
    throttle(request,service,'token',10);service.complete_token(value.token,'verify')
    return {'message':'Indirizzo confermato. Ora puoi accedere.'}


@router.post('/reset-password')
def reset(value:ResetRequest,request:Request,service=Depends(store)):
    throttle(request,service,'token',10);service.complete_token(value.token,'reset',value.password.get_secret_value())
    return {'message':'Password aggiornata. Accedi con la nuova password.'}


@router.put('/password')
def password(value:PasswordChange,request:Request,response:Response,service=Depends(store)):
    throttle(request,service,'password',5);service.change_password(identity(request),value.current_password.get_secret_value(),value.password.get_secret_value())
    response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='lax')
    return {'message':'Password aggiornata. Accedi di nuovo su tutti i dispositivi.'}


@router.get('/export')
def export(request:Request,response:Response,db:Session=Depends(get_db),service=Depends(store)):
    from app.services.backups import export_backup
    response.headers['Content-Disposition']='attachment; filename="iron-coach-account.json"'
    return {'account':service.snapshot(identity(request)),'training':export_backup(db)}


@router.post('/delete',status_code=202)
def remove(value:Credentials,request:Request,response:Response,service=Depends(store)):
    throttle(request,service,'delete',5);service.request_deletion(identity(request),value.email,value.password.get_secret_value())
    response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='lax')
    return {'message':'Account disattivato e richiesta di cancellazione registrata. Il supporto gestirà la rimozione secondo l’informativa del servizio.'}
