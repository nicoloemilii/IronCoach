$ErrorActionPreference='Stop'
$projectRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$pythonPath=Join-Path $projectRoot '.venv-web\Scripts\python.exe'
$serverProcess=Get-CimInstance Win32_Process -Filter 'ProcessId = 9568'
if (-not $serverProcess -or $serverProcess.ParentProcessId -ne 12932 -or $serverProcess.CommandLine -notmatch '-m uvicorn app\.main:app --host 127\.0\.0\.1 --port 8000 --no-access-log') { throw 'Processo non verificato: nessun arresto.' }
$live=(Invoke-WebRequest 'http://127.0.0.1:8000/app' -UseBasicParsing).Content
$local=Get-Content -LiteralPath (Join-Path $projectRoot 'web\index.html') -Encoding utf8 -Raw
if ($live -ne $local) { throw 'Gli asset non corrispondono: nessun arresto.' }
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'runtime-before-network-restart.db')) { throw 'Backup già presente: non sovrascriverlo.' }
& $pythonPath -c 'from pathlib import Path; import sqlite3; root=Path.cwd(); source=sqlite3.connect((root/"data/coach.db").as_uri()+"?mode=ro",uri=True); target=sqlite3.connect(root/".qa/goal-execution/runtime-before-network-restart.db"); source.backup(target); target.close(); source.close(); print("Copia SQLite completata.")'
if ($LASTEXITCODE -ne 0) { throw 'Backup fallito: processo conservato.' }
Stop-Process -Id $serverProcess.ProcessId
$launcher=Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'runtime-final-stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'runtime-final-stderr.log') -PassThru
$launcher.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-final-launcher-pid.txt')
Write-Output ('Server con rete disponibile avviato; launcher ' + $launcher.Id)
