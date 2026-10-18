@echo off
cd /d C:\IronmanCoach
if not exist .venv\Scripts\python.exe ( echo Ambiente Python non trovato. & echo Avvia INSTALL_FINAL.bat. & pause & exit /b 1 )
.venv\Scripts\python.exe -m desktop.main
