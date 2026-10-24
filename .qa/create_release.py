from pathlib import Path
import difflib,json,zipfile,hashlib,re
root=Path.cwd();before=root/'.qa/revision-before';parts=[]
def diff(rel,old,new):
 parts.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+rel if old else '/dev/null',tofile='b/'+rel))
for old in sorted(before.rglob('*')):
 if old.is_file():
  rel=old.relative_to(before).as_posix();diff(rel,old.read_text(encoding='utf-8'),(root/rel).read_text(encoding='utf-8-sig'))
# Exact narrow integration changes; retain surrounding original implementation.
rel='app/main.py';s=(root/rel).read_text(encoding='utf-8');old=s
for line in ['from app.api.routes_health_reports import router as health_reports_router\n','from app.db.health_reports import ensure_health_reports_schema\n','ensure_health_reports_schema(engine)\n','app.include_router(health_reports_router)\n']:old=old.replace(line,'')
diff(rel,old,s)
rel='app/api/routes_iron.py';s=(root/rel).read_text(encoding='utf-8');old=s.replace('from app.db.health_reports import HealthReport\n','').replace("    from app.services.health_reports import active_restrictions\n    current['restrictions'] = active_restrictions(db, today)\n",'')
a=old.index('    from app.services.activity_rating import activity_ratings',old.index('def activity_assessment'));b=old.index('\n\n',a);old=old[:a]+'    return assess(db, row)'+old[b:];diff(rel,old,s)
rel='app/api/routes_dashboard.py';s=(root/rel).read_text(encoding='utf-8');old=s.replace('from types import SimpleNamespace\n','').replace('from app.services.activity_rating import activity_ratings, recorded_loads\n','').replace('def _serialize(activity: Activity, ftp_watts: float, rating=None, load=None):','def _serialize(activity: Activity, ftp_watts: float):')
a=old.index('        # An unmapped view');b=old.index('\n    }',a);old=old[:a]+'        "load": activity_load(activity, ftp_watts),'+old[b:]
old=old.replace('    loads = recorded_loads(db, activities)\n    ratings = activity_ratings(db, activities, loads=loads)\n    return [_serialize(activity, ftp_watts, ratings[activity.id], loads[activity.id]) for activity in activities]','    return [_serialize(activity, ftp_watts) for activity in activities]');diff(rel,old,s)
rel='app/services/workout_engine.py';s=(root/rel).read_text(encoding='utf-8');diff(rel,s.replace('min(3,policy.get("max_rpe") or 3) if kind','3 if kind'),s)
rel='app/services/backups.py';s=(root/rel).read_text(encoding='utf-8');old=s.replace('from app.db import health_reports  # register optional personal symptom journal\n','').replace("'goal_preparation_items', 'health_reports'","'goal_preparation_items'").replace('    from app.services.health_reports import HealthReportInput\n','');a=old.index("        for row in data.get('health_reports', []):");b=old.index("        for row in data.get('race_preparation', []):",a);old=old[:a]+old[b:];diff(rel,old,s)
rel='web/tests/package.json';s=(root/rel).read_text(encoding='utf-8');diff(rel,s.replace(' metric-status.test.cjs wellness.test.cjs',' metric-status.test.cjs'),s)
new=['web/wellness.js','web/tests/wellness.test.cjs','app/db/health_reports.py','app/services/health_reports.py','app/services/athlete_snapshot.py','app/services/activity_rating.py','app/api/routes_health_reports.py','tests/test_health_reports.py','tests/test_health_report_integration.py','tests/test_activity_rating.py','requirements-dev.txt','docs/REVISIONE_USABILITA_2026_10_18.md']
for rel in new:
 p=root/rel
 if p.exists():diff(rel,'',p.read_text(encoding='utf-8-sig'))
(root/'.qa/IronCoach-modifiche.diff').write_text(''.join(parts),encoding='utf-8')
paths=[];extensions={'.py','.js','.css','.html','.cjs','.json','.txt','.md','.swift','.yml'}
for folder in ['app','web','tests','docs','desktop','ios']:
 for p in (root/folder).rglob('*'):
  if p.is_file() and p.suffix.lower() in extensions and not any(part in {'node_modules','__pycache__','.secrets'} for part in p.parts):paths.append(p)
paths += [p for p in root.iterdir() if p.is_file() and (p.name=='.gitignore' or p.suffix.lower() in {'.md','.txt','.bat'})]
paths=sorted(set(paths));manifest=[]
for p in paths:
 rel=p.relative_to(root).as_posix();content=p.read_bytes()
 assert not any(part in {'.qa','data','.venv-web','node_modules'} for part in p.relative_to(root).parts)
 assert not re.search(rb'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',content)
 manifest.append({'path':rel,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
archive=root/'.qa/IronCoach-sorgenti.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in paths:z.write(p,p.relative_to(root).as_posix())
(root/'.qa/IronCoach-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps({'files':len(paths),'archive_bytes':archive.stat().st_size,'diff_bytes':(root/'.qa/IronCoach-modifiche.diff').stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}))
