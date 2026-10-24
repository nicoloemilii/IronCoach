"""Launch only an isolated hidden helper, then always terminate that owned PID."""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

folder = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding='utf-8')
project = folder.parents[1]
with socket.socket() as probe:
    if probe.connect_ex(('127.0.0.1', 8777)) == 0:
        raise SystemExit('Port 8777 already in use; no existing process was touched.')
env = {key: value for key, value in os.environ.items()
       if not re.search(r'INTERVALS|STRAVA|GARMIN|TOKEN|SECRET|API_KEY|CREDENTIAL', key, re.I)}
env['IRON_AUTO_SYNC'] = '0'
env['IRON_DATA_DIR'] = str(folder / ('data-' + uuid4().hex))
Path(env['IRON_DATA_DIR']).mkdir()
subprocess.run([sys.executable, str(folder / 'seed.py')], cwd=project, env=env, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
with (folder / 'helper.log').open('w', encoding='utf-8') as log:
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8777', '--no-access-log'],
        cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW)
    (folder / 'helper-pid.txt').write_text(str(process.pid))
    try:
        until = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                raise RuntimeError('Isolated server exited during startup.')
            try:
                with urllib.request.urlopen('http://127.0.0.1:8777/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() > until:
                    raise RuntimeError('Isolated server did not become ready.')
                time.sleep(.1)
        outcome = subprocess.run(['node', str(folder / 'harness.cjs')], cwd=project, env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW)
        (folder / 'harness.log').write_text(outcome.stdout + outcome.stderr, encoding='utf-8')
        print(outcome.stdout + outcome.stderr)
        if outcome.returncode:
            raise SystemExit(outcome.returncode)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        print(json.dumps({'isolated_helper_pid': process.pid, 'stopped': process.poll() is not None}))
