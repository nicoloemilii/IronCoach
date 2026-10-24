"""Read-only live verification; all personal results stay in local QA."""
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlsplit
import hashlib,json,re,sqlite3
ROOT=Path(__file__).resolve().parents[2]; HERE=Path(__file__).resolve().parent
ORIGIN='http://127.0.0.1:8000'
responses={}
paths=['/ready','/health/metrics','/progress/week','/progress/recovery','/dashboard/home','/coach/today',
       '/activities/duplicates/review-history?limit=20','/planning/week','/coach/workouts',
       '/dashboard/training-response?lag=1','/athlete/derived-profile']
for path in paths:
    with urlopen(ORIGIN+path,timeout=30) as response:
        assert response.status==200
        responses[path]=json.load(response)
workouts=responses['/coach/workouts']
if workouts:
    path=f"/experience/workouts/{workouts[0]['id']}/review"
    with urlopen(ORIGIN+path,timeout=30) as response:
        assert response.status==200;responses[path]=json.load(response)
    assert 'activity_candidates' in responses[path] and 'workout' in responses[path]
(HERE/'runtime-responses.json').write_text(json.dumps(responses,indent=2),encoding='utf-8')
with urlopen(ORIGIN+'/app') as response:markup=response.read().decode('utf-8')
assets=[]
for url in re.findall(r'(?:src|href)="([^"]+)"',markup):
    local=ROOT/'web'/urlsplit(url).path.rsplit('/',1)[-1]
    if not url.startswith('/web/') or not local.is_file():continue
    with urlopen(ORIGIN+url) as response:raw=response.read()
    assert raw==local.read_bytes(),url
    assets.append({'path':str(local.relative_to(ROOT)),'sha256':hashlib.sha256(raw).hexdigest()})
assert any(row['path'].replace('\\','/')=='web/workout-matching.js' for row in assets)
with sqlite3.connect((HERE/'runtime-before-start.db').as_uri()+'?mode=ro',uri=True) as before,sqlite3.connect((ROOT/'data/coach.db').as_uri()+'?mode=ro',uri=True) as after:
    tables=[row[0] for row in before.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    changes=[]
    for name in tables:
        quoted='"'+name.replace('"','""')+'"'
        left=sorted(map(repr,before.execute('SELECT * FROM '+quoted).fetchall()))
        right=sorted(map(repr,after.execute('SELECT * FROM '+quoted).fetchall()))
        if left!=right:changes.append({'table':name,'before':len(left),'after':len(right)})
    protected={'workouts','session_feedback','workout_evaluations','athlete_adaptations','workout_resolutions','duplicate_reviews','duplicate_candidates'}
    assert not protected.intersection(row['table'] for row in changes),changes
report={'api_reads':len(responses),'assets_verified':len(assets),'assets':assets,'ready':responses['/ready'],
        'tables_compared':len(tables),'changed_tables':changes,'training_ledger_preserved':True,
        'all_previous_rows_unchanged':not changes,'visual_verification':False,'browser_status':'unavailable'}
(HERE/'runtime-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({key:value for key,value in report.items() if key!='assets'}))
