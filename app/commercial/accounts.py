"""Opaque, revocable sessions. This database never contains training records."""
import hashlib
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
import smtplib
import ssl
from uuid import uuid4
from pathlib import Path

from fastapi import HTTPException
from pwdlib import PasswordHash
from sqlalchemy import Boolean, Column, Float, Integer, String, create_engine, delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
PASSWORDS = PasswordHash.recommended()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
COOKIE = '__Host-iron_session'
SESSION_SECONDS = 7*86400
IDLE_SECONDS = 86400


def digest(value): return hashlib.sha256(value.encode()).hexdigest()


class Account(Base):
    __tablename__='accounts'
    id=Column(String,primary_key=True)
    email=Column(String,nullable=False,unique=True)
    name=Column(String,nullable=False)
    password_hash=Column(String,nullable=False)
    created_at=Column(Float,nullable=False)
    trial_until=Column(Float,nullable=False)
    verified=Column(Boolean,nullable=False,default=False)
    active=Column(Boolean,nullable=False,default=True)
    legal_version=Column(String,nullable=False)
    consented_at=Column(Float,nullable=False)
    deletion_requested_at=Column(Float)
    stripe_customer=Column(String,unique=True)
    subscription_id=Column(String)
    subscription_status=Column(String,nullable=False,default='none')
    subscription_until=Column(Float)
    checkout_session=Column(String)


class LoginSession(Base):
    __tablename__='login_sessions'
    token_hash=Column(String,primary_key=True)
    account_id=Column(String,nullable=False,index=True)
    csrf=Column(String,nullable=False)
    created_at=Column(Float,nullable=False)
    last_seen=Column(Float,nullable=False)
    expires_at=Column(Float,nullable=False)


class ActionToken(Base):
    __tablename__='action_tokens'
    token_hash=Column(String,primary_key=True)
    account_id=Column(String)
    email=Column(String,nullable=False)
    purpose=Column(String,nullable=False)
    expires_at=Column(Float,nullable=False)
    used_at=Column(Float)


class RateBucket(Base):
    __tablename__='rate_buckets'
    key=Column(String,primary_key=True)
    count=Column(Integer,nullable=False)
    window=Column(Integer,nullable=False)


class Audit(Base):
    __tablename__='account_audit'
    id=Column(Integer,primary_key=True)
    account_id=Column(String,index=True)
    action=Column(String,nullable=False)
    at=Column(Float,nullable=False)


class BillingEvent(Base):
    __tablename__='billing_events'
    id=Column(String,primary_key=True)
    at=Column(Float,nullable=False)


class Store:
    def __init__(self,folder,settings):
        self.settings=settings;folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        self.engine=create_engine(f'sqlite:///{folder/"accounts.db"}',connect_args={'check_same_thread':False,'timeout':30},hide_parameters=True)
        Base.metadata.create_all(self.engine);self.factory=sessionmaker(bind=self.engine,expire_on_commit=False)
        with self.engine.connect() as connection: connection.exec_driver_sql('PRAGMA journal_mode=WAL')

    @contextmanager
    def db(self):
        with self.factory() as db:
            try: yield db;db.commit()
            except Exception: db.rollback();raise

    def audit(self,db,identifier,action): db.add(Audit(account_id=identifier,action=action,at=time.time()))

    def limit(self,key,maximum=10,seconds=600):
        window=int(time.time()//seconds)*seconds
        with self.db() as db:
            statement=insert(RateBucket).values(key=digest(key),count=1,window=window)
            statement=statement.on_conflict_do_update(index_elements=['key'],set_={'count':RateBucket.count+1,'window':window},where=RateBucket.window==window)
            result=db.execute(statement)
            if result.rowcount==0: db.execute(update(RateBucket).where(RateBucket.key==digest(key)).values(count=1,window=window))
            db.execute(delete(RateBucket).where(RateBucket.window<time.time()-86400))
            count=db.get(RateBucket,digest(key)).count
        if count>maximum: raise HTTPException(429,'Troppi tentativi. Riprova tra qualche minuto.',headers={'Retry-After':str(seconds)})

    def token(self,db,email,purpose,identifier=None,seconds=3600):
        raw=secrets.token_urlsafe(32)
        db.add(ActionToken(token_hash=digest(raw),account_id=identifier,email=email,purpose=purpose,expires_at=time.time()+seconds))
        return raw

    def consume(self,db,raw,purpose):
        row=db.get(ActionToken,digest(raw))
        if not row or row.purpose!=purpose or row.used_at is not None or row.expires_at<time.time(): raise HTTPException(400,'Collegamento non valido o scaduto.')
        changed=db.execute(update(ActionToken).where(ActionToken.token_hash==row.token_hash,ActionToken.used_at.is_(None)).values(used_at=time.time()))
        if changed.rowcount!=1: raise HTTPException(400,'Collegamento già utilizzato.')
        return row

    def invite(self,email,days=7):
        with self.db() as db: return self.token(db,email.strip().casefold(),'invite',seconds=days*86400)

    def register(self,value):
        cfg=self.settings
        if not cfg.legal_ready: raise HTTPException(503,'Registrazione non disponibile: il titolare deve completare le informazioni del servizio.')
        if not value.terms_accepted or not value.health_data_consent: raise HTTPException(422,'Leggi le condizioni e conferma il consenso richiesto prima di creare l’account.')
        email=value.email.strip().casefold();now=time.time();identifier=uuid4().hex
        password_hash=PASSWORDS.hash(value.password.get_secret_value())
        try:
            with self.db() as db:
                invited=False
                if value.invitation:
                    invitation=self.consume(db,value.invitation,'invite')
                    if invitation.email!=email: raise HTTPException(400,'L’invito appartiene a un altro indirizzo email.')
                    invited=True
                elif not cfg.registration_open or not cfg.email_ready: raise HTTPException(403,'La registrazione richiede un invito.')
                db.add(Account(id=identifier,email=email,name=value.name.strip(),password_hash=password_hash,created_at=now,trial_until=now+cfg.trial_days*86400,
                    verified=invited,active=True,legal_version=cfg.legal_version,consented_at=now,subscription_status='none'))
                self.audit(db,identifier,'registered_with_invite' if invited else 'registered')
                verification=None if invited else self.token(db,email,'verify',identifier,86400)
        except IntegrityError: raise HTTPException(409,'Non è stato possibile creare l’account. Prova ad accedere o recuperare la password.') from None
        if verification: self.mail(email,'Conferma il tuo account',f'{cfg.public_url}/login#verify={verification}')
        return {'status':'ready' if invited else 'verification_required','message':'Account creato. Puoi accedere.' if invited else 'Controlla la posta per confermare l’indirizzo email.'}

    def mail(self,email,subject,url):
        cfg=self.settings
        if not cfg.email_ready: return
        message=EmailMessage();message['From']=cfg.mail_from;message['To']=email;message['Subject']=f'Iron Coach · {subject}'
        message.set_content(f'{subject}\n\nApri questo collegamento personale:\n{url}\n\nSe non hai richiesto questa operazione, ignora questa email.')
        try:
            with smtplib.SMTP(cfg.smtp_host,cfg.smtp_port,timeout=10) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if cfg.smtp_user: smtp.login(cfg.smtp_user,cfg.smtp_password)
                smtp.send_message(message)
        except (OSError,smtplib.SMTPException):
            with self.db() as db: self.audit(db,None,'email_delivery_failed')

    def login(self,email,password):
        with self.db() as db:
            row=db.scalar(select(Account).where(Account.email==email.strip().casefold()))
            try: valid=PASSWORDS.verify(password,row.password_hash if row else DUMMY_HASH)
            except Exception: valid=False
            if not row or not valid or not row.active: raise HTTPException(401,'Email o password non validi.')
            if not row.verified: raise HTTPException(403,'Conferma il tuo indirizzo email prima di accedere.')
            now=time.time();raw=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            db.execute(delete(LoginSession).where(LoginSession.expires_at<now))
            db.add(LoginSession(token_hash=digest(raw),account_id=row.id,csrf=csrf,created_at=now,last_seen=now,expires_at=now+SESSION_SECONDS));self.audit(db,row.id,'login')
            return raw,csrf

    def authenticate(self,raw):
        if not raw or len(raw)>256: return None
        with self.db() as db:
            session=db.get(LoginSession,digest(raw));now=time.time()
            if not session or session.expires_at<=now or session.last_seen<=now-IDLE_SECONDS: return None
            row=db.get(Account,session.account_id)
            if not row or not row.active or not row.verified: return None
            if now-session.last_seen>60: session.last_seen=now
            return {'id':row.id,'email':row.email,'name':row.name,'csrf':session.csrf,'session_hash':session.token_hash,
                'trial_until':row.trial_until,'subscription_status':row.subscription_status,'subscription_until':row.subscription_until}

    def access(self,account):
        paid=account['subscription_status'] in ('active','trialing') and (account.get('subscription_until') or 0)>time.time()
        trial=account['trial_until']>time.time()
        return {'allowed':not self.settings.billing_required or paid or trial,'paid':paid,'trial':trial,'trial_until':account['trial_until'],'billing_required':self.settings.billing_required,'subscription_status':account['subscription_status']}

    def logout(self,account,all_sessions=False):
        with self.db() as db:
            db.execute(delete(LoginSession).where(LoginSession.account_id==account['id']) if all_sessions else delete(LoginSession).where(LoginSession.token_hash==account['session_hash']))
            self.audit(db,account['id'],'logout_all' if all_sessions else 'logout')

    def request_link(self,email,purpose):
        with self.db() as db:
            row=db.scalar(select(Account).where(Account.email==email.strip().casefold(),Account.active.is_(True)))
            token=self.token(db,row.email,purpose,row.id) if row else None
        if token: self.mail(row.email,'Recupero password' if purpose=='reset' else 'Conferma il tuo account',f'{self.settings.public_url}/login#{purpose}={token}')

    def complete_token(self,raw,purpose,password=None):
        password_hash=PASSWORDS.hash(password) if password is not None else None
        with self.db() as db:
            token=self.consume(db,raw,purpose);row=db.get(Account,token.account_id)
            if not row or not row.active: raise HTTPException(400,'Account non disponibile.')
            if purpose=='reset':
                row.password_hash=password_hash
                db.execute(delete(LoginSession).where(LoginSession.account_id==row.id))
                db.execute(update(ActionToken).where(ActionToken.account_id==row.id,ActionToken.purpose=='reset').values(used_at=time.time()))
            else: row.verified=True
            self.audit(db,row.id,'password_reset' if purpose=='reset' else 'email_verified')

    def change_password(self,account,current,new):
        password_hash=PASSWORDS.hash(new)
        with self.db() as db:
            row=db.get(Account,account['id'])
            if not PASSWORDS.verify(current,row.password_hash): raise HTTPException(400,'Password attuale non valida.')
            row.password_hash=password_hash;db.execute(delete(LoginSession).where(LoginSession.account_id==row.id));self.audit(db,row.id,'password_changed')

    def request_deletion(self,account,email,password):
        with self.db() as db:
            row=db.get(Account,account['id'])
            if email.casefold()!=row.email or not PASSWORDS.verify(password,row.password_hash): raise HTTPException(400,'Conferma email e password per chiudere l’account.')
            if row.subscription_status in ('active','trialing','past_due','unpaid','paused'): raise HTTPException(409,'Chiudi prima l’abbonamento dal portale di fatturazione. I pagamenti restano gestibili da lì.')
            row.active=False;row.deletion_requested_at=time.time();db.execute(delete(LoginSession).where(LoginSession.account_id==row.id));self.audit(db,row.id,'erasure_requested')

    def snapshot(self,account):
        with self.db() as db:
            row=db.get(Account,account['id'])
            return {'id':row.id,'email':row.email,'name':row.name,'created_at':datetime.fromtimestamp(row.created_at,timezone.utc).isoformat(),
                'legal_version':row.legal_version,'consented_at':datetime.fromtimestamp(row.consented_at,timezone.utc).isoformat()}
