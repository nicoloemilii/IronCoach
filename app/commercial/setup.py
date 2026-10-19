from pathlib import Path
from sqlalchemy import text
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from app.commercial.config import from_env
from app.commercial.accounts import Store
from app.commercial.middleware import ProductBoundary
from app.commercial import routes, billing
from app.api.errors import request_validation_error


def install(app,settings=None,folder=None):
    import logging
    logger=logging.getLogger('iron.requests')
    if not logger.handlers:
        handler=logging.StreamHandler();handler.setFormatter(logging.Formatter('%(message)s'));logger.addHandler(handler)
    logger.setLevel(logging.INFO);logger.propagate=False
    from app.db import database
    cfg=(settings or from_env()).validate()
    app.state.settings=cfg;app.state.accounts=Store(folder or database.DATA_DIR,cfg) if cfg.hosted else None
    app.include_router(routes.router);app.include_router(billing.router)
    app.add_middleware(ProductBoundary,settings=cfg,accounts=app.state.accounts)

    app.add_exception_handler(RequestValidationError, request_validation_error)

    @app.get('/login',include_in_schema=False)
    def login_page(): return FileResponse(Path(__file__).resolve().parents[2]/'web'/'login.html',headers={'Cache-Control':'no-cache'})

    @app.get('/ready',include_in_schema=False)
    def ready(request:Request):
        target=request.app.state.accounts.engine if cfg.hosted else database.engine
        try:
            with target.connect() as connection: connection.execute(text('SELECT 1'))
        except Exception: return JSONResponse({'status':'unavailable'},503)
        return {'status':'ready','mode':cfg.mode,'version':app.version}
