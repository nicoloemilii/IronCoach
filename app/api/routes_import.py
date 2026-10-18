from fastapi import APIRouter, HTTPException, Query


router = APIRouter()


@router.post('/import/fit')
def import_fit(
    directory: str = Query('data/fit', description='Cartella FIT locale da importare')
):
    from app.integrations.import_fit import import_fit_directory
    try:
        return import_fit_directory(directory)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
