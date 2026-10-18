@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
if not exist .venv-web\Scripts\python.exe (
  python -m venv .venv-web
  if errorlevel 1 goto fail
)
.venv-web\Scripts\python.exe -c "import fastapi, uvicorn, sqlalchemy, requests, garmin_fit_sdk; from zoneinfo import ZoneInfo; ZoneInfo('Europe/Rome')" >nul 2>&1
if errorlevel 1 (
  echo Preparazione delle dipendenze web...
  .venv-web\Scripts\python.exe -m pip install -r requirements-web.txt
  if errorlevel 1 goto fail
)
.venv-web\Scripts\python.exe -m app.web_launcher
if errorlevel 1 goto fail
pause
exit /b 0
:fail
echo.
echo Iron Coach non e' stato avviato. Controlla il messaggio di errore qui sopra.
echo Avvia questo file dalla cartella ironman_coach. L'indirizzo corretto e' http://127.0.0.1:8000/app
pause
exit /b 1
