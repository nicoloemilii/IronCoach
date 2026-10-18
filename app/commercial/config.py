from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    mode: str = 'local'
    public_url: str = ''
    vault_key: str = field(default='', repr=False)
    registration_open: bool = False
    privacy_url: str = ''
    terms_url: str = ''
    legal_version: str = ''
    support_email: str = ''
    billing_required: bool = False
    trial_days: int = 14
    stripe_key: str = field(default='', repr=False)
    stripe_webhook_secret: str = field(default='', repr=False)
    stripe_price: str = ''
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_user: str = ''
    smtp_password: str = field(default='', repr=False)
    mail_from: str = ''

    @property
    def hosted(self): return self.mode == 'hosted'

    @property
    def legal_ready(self): return bool(self.privacy_url and self.terms_url and self.legal_version and self.support_email)

    @property
    def email_ready(self): return bool(self.smtp_host and self.mail_from)

    @property
    def billing_ready(self): return bool(self.stripe_key and self.stripe_price and self.stripe_webhook_secret)

    def validate(self):
        if self.mode not in ('local', 'hosted'): raise ValueError('IRON_MODE deve essere local oppure hosted.')
        if not 0 <= self.trial_days <= 90: raise ValueError('IRON_TRIAL_DAYS deve essere tra 0 e 90.')
        if self.hosted:
            parsed=urlsplit(self.public_url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path not in ('','/'):
                raise ValueError('La modalità hosted richiede IRON_PUBLIC_URL come origine HTTPS, senza percorso.')
            from cryptography.fernet import Fernet
            try: Fernet(self.vault_key.encode())
            except Exception: raise ValueError('Configura una chiave Fernet valida in IRON_VAULT_KEY.') from None
            if self.billing_required and not self.billing_ready: raise ValueError('Configura Stripe prima di abilitare IRON_BILLING_REQUIRED.')
            for value in (self.privacy_url,self.terms_url):
                if value and (urlsplit(value).scheme!='https' or not urlsplit(value).hostname): raise ValueError('Le pagine legali devono usare HTTPS.')
        return self


def from_env():
    return Settings(mode=os.getenv('IRON_MODE','local'),public_url=os.getenv('IRON_PUBLIC_URL','').rstrip('/'),
        vault_key=os.getenv('IRON_VAULT_KEY',''),registration_open=os.getenv('IRON_REGISTRATION_OPEN','0')=='1',
        privacy_url=os.getenv('IRON_PRIVACY_URL',''),terms_url=os.getenv('IRON_TERMS_URL',''),legal_version=os.getenv('IRON_LEGAL_VERSION',''),support_email=os.getenv('IRON_SUPPORT_EMAIL',''),
        billing_required=os.getenv('IRON_BILLING_REQUIRED','0')=='1',trial_days=int(os.getenv('IRON_TRIAL_DAYS','14')),
        stripe_key=os.getenv('IRON_STRIPE_SECRET_KEY',''),stripe_webhook_secret=os.getenv('IRON_STRIPE_WEBHOOK_SECRET',''),stripe_price=os.getenv('IRON_STRIPE_PRICE_ID',''),
        smtp_host=os.getenv('IRON_SMTP_HOST',''),smtp_port=int(os.getenv('IRON_SMTP_PORT','587')),smtp_user=os.getenv('IRON_SMTP_USER',''),
        smtp_password=os.getenv('IRON_SMTP_PASSWORD',''),mail_from=os.getenv('IRON_MAIL_FROM','')).validate()
