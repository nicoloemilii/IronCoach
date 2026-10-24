$ErrorActionPreference='Stop'
$projectRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$pythonPath=Join-Path $projectRoot '.venv-web\Scripts\python.exe'
$listening=netstat -ano -p tcp | Select-String 'LISTENING' | Select-String '127\.0\.0\.1:8000\s'
if ($listening) { throw 'La porta 8000 è già occupata: nessun processo modificato.' }
if ($env:IRON_DATA_DIR -and [IO.Path]::GetFullPath($env:IRON_DATA_DIR) -ne (Join-Path $projectRoot 'data')) { throw 'Archivio ambiente diverso dal progetto: avvio sospeso.' }
$backupPath=Join-Path $PSScriptRoot 'runtime-before-start.db'
if (Test-Path -LiteralPath $backupPath) { throw 'La copia precedente esiste già: non sovrascriverla.' }
& $pythonPath -c 'from pathlib import Path; import sqlite3; root=Path.cwd(); source=sqlite3.connect((root/"data/coach.db").as_uri()+"?mode=ro",uri=True); target=sqlite3.connect(root/".qa/goal-execution/runtime-before-start.db"); source.backup(target); target.close(); source.close(); print("Copia SQLite precedente all avvio completata.")'
if ($LASTEXITCODE -ne 0) { throw 'Backup non completato.' }
$launcher=Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'runtime-stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'runtime-stderr.log') -PassThru
$launcher.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-launcher-pid.txt')
Write-Output ('Server locale avviato; launcher ' + $launcher.Id)
