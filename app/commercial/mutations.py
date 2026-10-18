"""Serialize one athlete's mutations without blocking unrelated workspaces."""
from asyncio import Lock
from weakref import WeakValueDictionary
from starlette.responses import JSONResponse

LOCAL_GATE=Lock()
HOSTED_GATES=WeakValueDictionary()


async def guard_mutations(request,call_next,hosted=False):
    if request.method not in ('POST','PUT','PATCH','DELETE'):return await call_next(request)
    if hosted:
        account=getattr(request.state,'account',None)
        if not account and request.url.path!='/billing/webhook':return await call_next(request)
        key=account['id'] if account else 'billing-webhook'
        gate=HOSTED_GATES.get(key)
        if gate is None:gate=Lock();HOSTED_GATES[key]=gate
    else:gate=LOCAL_GATE
    if gate.locked():return JSONResponse({'detail':'Un’altra operazione di salvataggio è in corso. Attendi il completamento e riprova.'},409)
    await gate.acquire()
    try:return await call_next(request)
    finally:gate.release()
