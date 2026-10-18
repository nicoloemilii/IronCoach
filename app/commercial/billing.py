"""Server-defined price and signed, idempotent Stripe subscription events."""
import time
import requests
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.commercial.accounts import Account, BillingEvent
from app.commercial.routes import store, identity, throttle

router=APIRouter(prefix='/billing',tags=['Abbonamento'])


def remote(cfg,method,path,data=None,key=None):
    try:
        response=requests.request(method,'https://api.stripe.com/v1/'+path,auth=(cfg.stripe_key,''),data=data,
            headers={'Idempotency-Key':key} if key else {},timeout=15,allow_redirects=False)
        if not response.ok: raise ValueError()
        return response.json()
    except (requests.RequestException,ValueError): raise HTTPException(502,'Il servizio pagamenti non è disponibile. Riprova tra poco.') from None


def configured(service):
    if not service.settings.billing_ready: raise HTTPException(503,'Il titolare non ha ancora attivato i pagamenti.')
    return service.settings


def customer(service,identifier):
    cfg=configured(service)
    with service.db() as db:
        row=db.get(Account,identifier)
        if row.stripe_customer: return row.stripe_customer
        value=remote(cfg,'POST','customers',{'email':row.email,'name':row.name,'metadata[account_id]':row.id},f'customer-{row.id}')
        row.stripe_customer=value['id'];return row.stripe_customer


@router.get('/status')
def status(request:Request,service=Depends(store)):
    account=identity(request);cfg=service.settings
    return {'enabled':cfg.billing_ready,'access':service.access(account)}


@router.get('/price')
def price(request:Request,service=Depends(store)):
    configured(service);value=remote(service.settings,'GET',f'prices/{service.settings.stripe_price}')
    return {'amount_minor':value.get('unit_amount'),'currency':value.get('currency'),'interval':value.get('recurring',{}).get('interval'),'interval_count':value.get('recurring',{}).get('interval_count',1)}


@router.post('/checkout')
def checkout(request:Request,service=Depends(store)):
    throttle(request,service,'checkout',5);cfg=configured(service);account=identity(request)
    with service.db() as db:
        row=db.get(Account,account['id'])
        if row.subscription_status in ('active','trialing','past_due','unpaid','paused'): raise HTTPException(409,'Gestisci l’abbonamento esistente dal portale di fatturazione.')
        if row.checkout_session:
            previous=remote(cfg,'GET',f'checkout/sessions/{row.checkout_session}')
            if previous.get('status')=='open': return {'url':previous['url']}
            if previous.get('status')=='complete': raise HTTPException(409,'Pagamento in attesa di conferma. Aggiorna lo stato tra qualche istante.')
    identifier=customer(service,account['id'])
    value=remote(cfg,'POST','checkout/sessions',{'mode':'subscription','customer':identifier,'line_items[0][price]':cfg.stripe_price,'line_items[0][quantity]':'1',
        'client_reference_id':account['id'],'subscription_data[metadata][account_id]':account['id'],
        'success_url':cfg.public_url+'/app#account','cancel_url':cfg.public_url+'/app#account'},f'checkout-{account["id"]}-{int(time.time()//1800)}')
    with service.db() as db: db.get(Account,account['id']).checkout_session=value['id']
    return {'url':value['url']}


@router.post('/portal')
def portal(request:Request,service=Depends(store)):
    throttle(request,service,'portal',10);cfg=configured(service);identifier=customer(service,identity(request)['id'])
    return {'url':remote(cfg,'POST','billing_portal/sessions',{'customer':identifier,'return_url':cfg.public_url+'/app#account'})['url']}


def process_event(service,event):
    cfg=service.settings
    with service.db() as db:
        if db.get(BillingEvent,event['id']): return {'received':True,'duplicate':True}
    if event['type'].startswith('customer.subscription.'):
        obj=event['data']['object'];identifier=obj.get('id')
        # Retrieve current state: late delivery cannot reinstate an older status.
        current=remote(cfg,'GET',f'subscriptions/{identifier}')
        customer_id=current.get('customer');items=current.get('items',{}).get('data',[])
        matching=[item for item in items if item.get('price',{}).get('id')==cfg.stripe_price]
        eligible=bool(matching)
        ends=[item.get('current_period_end') or current.get('current_period_end') or 0 for item in matching]
        until=max(ends,default=0)
        try:
            with service.db() as db:
                if db.get(BillingEvent,event['id']): return {'received':True,'duplicate':True}
                row=db.scalar(select(Account).where(Account.stripe_customer==customer_id)) if customer_id else None
                if row and (eligible or row.subscription_id==identifier) and not (row.subscription_id and row.subscription_id!=identifier and row.subscription_status in ('active','trialing','past_due','unpaid','paused')):
                    row.subscription_id=identifier;row.subscription_status=current.get('status','unknown') if eligible else 'ineligible';row.subscription_until=until;row.checkout_session=None
                    service.audit(db,row.id,'subscription_updated')
                db.add(BillingEvent(id=event['id'],at=time.time()))
        except IntegrityError: return {'received':True,'duplicate':True}
    else:
        try:
            with service.db() as db: db.add(BillingEvent(id=event['id'],at=time.time()))
        except IntegrityError: pass
    return {'received':True}


@router.post('/webhook')
async def webhook(request:Request,service=Depends(store)):
    cfg=configured(service)
    try: event=stripe.Webhook.construct_event(await request.body(),request.headers.get('stripe-signature',''),cfg.stripe_webhook_secret).to_dict()
    except (ValueError,stripe.SignatureVerificationError): raise HTTPException(400,'Firma del pagamento non valida.') from None
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(process_event,service,event)
