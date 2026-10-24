$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$pythonPath = Join-Path $projectRoot '.venv-web\Scripts\python.exe'
$serverProcess = Get-CimInstance Win32_Process -Filter 'ProcessId = 11456'
if (-not $serverProcess -or $serverProcess.ParentProcessId -ne 3760 -or $serverProcess.CommandLine -notmatch '-m uvicorn app\.main:app --host 127\.0\.0\.1 --port 8000 --no-access-log') {
  throw 'Il processo non corrisponde al server locale verificato; nessun processo arrestato.'
}
$liveIndex = (Invoke-WebRequest 'http://127.0.0.1:8000/app' -UseBasicParsing).Content
$localIndex = Get-Content -LiteralPath (Join-Path $projectRoot 'web\index.html') -Encoding utf8 -Raw
if ($liveIndex -ne $localIndex) { throw 'Il server non serve gli asset di questo progetto; nessun processo arrestato.' }
& $pythonPath -c 'from pathlib import Path; import sqlite3; root=Path.cwd(); source=sqlite3.connect((root/"data/coach.db").as_uri()+"?mode=ro",uri=True); target=sqlite3.connect(root/".qa/goal-response/runtime-before-final.db"); source.backup(target); target.close(); source.close(); print("Backup locale SQLite completato.")'
if ($LASTEXITCODE -ne 0) { throw 'Backup non completato; server conservato.' }
Stop-Process -Id $serverProcess.ProcessId
$launcher = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'runtime-final-stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'runtime-final-stderr.log') -PassThru
$launcher.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-final-launcher-pid.txt')
Write-Output ('Server aggiornato avviato; launcher PID ' + $launcher.Id)
