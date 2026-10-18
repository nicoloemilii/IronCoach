@echo off
setlocal
set "SRC=%~dp0"
set "DST=C:\IronmanCoach"
if not exist "%DST%" mkdir "%DST%"
echo Installazione Ironman Coach 1.0...
robocopy "%SRC%app" "%DST%app" /E /R:1 /W:1 >nul
robocopy "%SRC%desktop" "%DST%desktop" /E /R:1 /W:1 >nul
copy /Y "%SRC%requirements.txt" "%DST%requirements.txt" >nul
copy /Y "%SRC%IronmanCoach.bat" "%DST%IronmanCoach.bat" >nul
if not exist "%DST%\.venv\Scripts\python.exe" ( py -3 -m venv "%DST%\.venv" || goto fail & "%DST%\.venv\Scripts\python.exe" -m pip install --upgrade pip || goto fail & "%DST%\.venv\Scripts\python.exe" -m pip install -r "%DST%\requirements.txt" || goto fail )
"%DST%\.venv\Scripts\python.exe" -m compileall "%DST%\app" "%DST%\desktop" >nul || goto fail
echo Installazione completata.
start "" "%DST%\IronmanCoach.bat"
exit /b 0
:fail
echo Installazione fallita.
pause
exit /b 1
