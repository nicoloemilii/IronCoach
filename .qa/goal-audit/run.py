"""Launch one isolated hidden helper; always stop only that owned process."""
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
project = folder.parents[1]
sys.stdout.reconfigure(encoding='utf-8')
with socket.socket() as probe:
    if probe.connect_ex(('127.0.0.1',8777)) == 0:
        raise SystemExit('8777 is in use; no existing process was touched.')
env = {key:value for key,value in os.environ.items()
       if not key.upper().startswith('IRON_') and not re.search(r'INTERVALS|STRAVA|GARMIN|TOKEN|SECRET|API_KEY|CREDENTIAL|STRIPE|SMTP',key,re.I)}
env.update(IRON_AUTO_SYNC='0',IRON_MODE='local',IRON_DATA_DIR=str(folder/('data-'+uuid4().hex)))
Path(env['IRON_DATA_DIR']).mkdir()
subprocess.run([sys.executable,str(folder/'seed.py')],cwd=project,env=env,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
# On Windows the venv executable is a launcher that can outlive its child.
# Start the same base interpreter with this venv's import paths so the owned
# Popen handle belongs to the actual Uvicorn process, making finally reliable.
import_paths = [str(project)] + [entry for entry in sys.path if 'site-packages' in entry]
bootstrap = 'import runpy,sys;sys.path[:0]=' + repr(import_paths) + ';runpy.run_module("uvicorn",run_name="__main__")'
with (folder/'helper.log').open('w',encoding='utf-8') as log:
    process = subprocess.Popen([sys._base_executable,'-c',bootstrap,'app.main:app','--host','127.0.0.1','--port','8777','--no-access-log'],
        cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    (folder/'helper-pid.txt').write_text(str(process.pid),encoding='ascii')
    try:
        deadline = time.monotonic()+30
        while True:
            if process.poll() is not None:
                raise RuntimeError('Isolated helper exited before readiness.')
            try:
                with urllib.request.urlopen('http://127.0.0.1:8777/health',timeout=1) as response:
                    if response.status == 200: break
            except OSError:
                if time.monotonic()>deadline: raise RuntimeError('Helper startup timed out.')
                time.sleep(.1)
        outcome = subprocess.run(['node',str(folder/'harness.cjs')],cwd=project,env=env,capture_output=True,text=True,encoding='utf-8',
            timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
        (folder/'harness.log').write_text(outcome.stdout+outcome.stderr,encoding='utf-8')
        print(outcome.stdout+outcome.stderr)
        if outcome.returncode: raise SystemExit(outcome.returncode)
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=5)
        stopped = process.poll() is not None
        with socket.socket() as probe:
            port_free = probe.connect_ex(('127.0.0.1',8777)) != 0
        (folder/'helper-stop.json').write_text(json.dumps({'owned_pid':process.pid,'stopped':stopped,'port_free':port_free}),encoding='utf-8')
        print(json.dumps({'isolated_helper_pid':process.pid,'stopped':stopped,'port_free':port_free}))
        if not port_free: raise RuntimeError('8777 remains bound; inspect ownership before stopping any other PID.')
