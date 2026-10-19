"""Validation responses never echo personal input or non-JSON float values."""
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse


async def request_validation_error(request, exc: RequestValidationError):
    # Pydantic may include NaN/Infinity in `input`. The default JSON response
    # then fails while reporting a validation error, turning a bad request into
    # a server error. Location, message and type are sufficient for clients.
    detail = [{key: error[key] for key in ('type', 'loc', 'msg') if key in error}
              for error in exc.errors()]
    return JSONResponse(status_code=422, content={'detail': detail})
