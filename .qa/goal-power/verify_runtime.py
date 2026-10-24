"""Read-only runtime checks. Personal responses remain in private local QA."""
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlsplit
import hashlib,json,re,sqlite3
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
ORIGIN='http://127.0.0.1:8000';responses={}
def read(path):
    with urlopen(ORIGIN+path,timeout=30) as response:
        assert response.status==200
        result=json.load(response);responses[path]=result;return result
for path in ['/ready','/health/metrics','/progress/week','/progress/recovery','/dashboard/home',
             '/coach/today','/activities/duplicates/review-history?limit=20','/planning/week',
             '/coach/workouts','/dashboard/training-response?lag=1','/athlete/derived-profile']:
    read(path)
page=read('/activities/diary?limit=50');assert len(page['items'])<=50
assert page['consistency']=='keyset_live_view'
identifiers=[item['id'] for item in page['items']]
if page['has_more']:
    from urllib.parse import urlencode
    following=read('/activities/diary?'+urlencode({'limit':50,'cursor':page['next_cursor']}))
    assert not set(identifiers).intersection(item['id'] for item in following['items'])
    assert page['snapshot']==following['snapshot']
read('/activities/diary?limit=1&sport=other')
power_check=[]
for item in [row for row in page['items'] if row['sport_category']=='cycling'][:3]:
    detail=read(f"/activities/{item['id']}/detail")
    curve=detail['best_efforts']
    assert [row['duration_seconds'] for row in curve['durations']]==[5,15,30,60,300,600,1200,3600]
    assert curve['source'] in ('stored_observation_streams','intervals_remote_streams')
    available=[row for row in curve['durations'] if row['status']=='available']
    for row in available:
        assert row['mean_power_w']>=0 and row['coverage_pct']==100
        assert abs(row['end_s']-row['start_s']-row['duration_seconds'])<1e-5
    power_check.append(dict(activity_id=item['id'],status=curve['status'],available=len(available),
                            raw_time_samples=detail['stream_quality']['raw_time_samples'],source=curve['source']))
    if available:break
with urlopen(ORIGIN+'/app') as response:markup=response.read().decode('utf-8')
assert markup== (ROOT/'web/index.html').read_bytes().decode('utf-8')
assets=[]
for url in re.findall(r'(?:src|href)="([^"]+)"',markup):
    local=ROOT/'web'/urlsplit(url).path.rsplit('/',1)[-1]
    if not url.startswith('/web/') or not local.is_file():continue
    with urlopen(ORIGIN+url) as response:raw=response.read()
    assert raw==local.read_bytes(),url
    assets.append({'path':str(local.relative_to(ROOT)),'sha256':hashlib.sha256(raw).hexdigest()})
assert any(item['path'].replace('\\','/')=='web/power-duration.js' for item in assets)
with sqlite3.connect((HERE/'runtime-before-restart.db').as_uri()+'?mode=ro',uri=True) as before,sqlite3.connect((ROOT/'data/coach.db').as_uri()+'?mode=ro',uri=True) as after:
    tables=[row[0] for row in before.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    changes=[]
    for name in tables:
        quoted='"'+name.replace('"','""')+'"'
        left=sorted(map(repr,before.execute('SELECT * FROM '+quoted).fetchall()))
        right=sorted(map(repr,after.execute('SELECT * FROM '+quoted).fetchall()))
        if left!=right:changes.append({'table':name,'before':len(left),'after':len(right)})
    assert not changes,changes
    assert after.execute("SELECT name FROM sqlite_master WHERE name='ix_activities_start_id'").fetchone()
    plan=after.execute('EXPLAIN QUERY PLAN SELECT * FROM activities WHERE id<=999999 ORDER BY start_time DESC,id DESC LIMIT 50').fetchall()
    assert any('ix_activities_start_id' in item[3] for item in plan)
report=dict(api_reads=len(responses),assets_verified=len(assets),assets=assets,
    ready=responses['/ready'],tables_compared=len(tables),changed_tables=changes,
    all_previous_rows_unchanged=True,physical_index_present=True,index_plan=plan,
    diary_page_size=len(page['items']),diary_archive_total=page['archive_total'],
    power_curves=power_check,
    visual_verification=False,browser_status='unavailable')
(HERE/'runtime-responses.json').write_text(json.dumps(responses,indent=2),encoding='utf-8')
(HERE/'runtime-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({key:value for key,value in report.items() if key not in ('assets','diary_archive_total','index_plan')}))
