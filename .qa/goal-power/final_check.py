"""Verify final local source, artifacts and runtime evidence together."""
import hashlib,importlib.util,json
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('local_package',HERE/'package.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
files=package.source_files();audit=json.loads((HERE/'audit.json').read_bytes())
tree=package.tree_hash({path:package.git_object('blob',raw) for path,raw in files.items()})
assert tree==audit['expected_tree_sha']
for kind in ('zip','diff'):
    assert hashlib.sha256((package.ROOT/audit[kind+'_path']).read_bytes()).hexdigest()==audit[kind+'_sha256']
for report_name in ('review_math.json','review_api.json'):
    report=json.loads((HERE/report_name).read_bytes());assert report['status']=='passed' and report['source_unchanged']
    for path,digest in report['production_sha256'].items():assert hashlib.sha256(files[path]).hexdigest()==digest
benchmark=json.loads((HERE/'benchmark-250000.json').read_bytes())
assert hashlib.sha256(files['app/services/power_duration.py']).hexdigest()==benchmark['source_sha256']
integrated=json.loads((HERE/'review_dom.json').read_bytes());assert integrated['ok']
for path,digest in integrated['source_sha256'].items():assert hashlib.sha256(files[path]).hexdigest()==digest
runtime=json.loads((HERE/'runtime-verification.json').read_bytes());assert runtime['all_previous_rows_unchanged']
for asset in runtime['assets']:assert hashlib.sha256(files[asset['path'].replace('\\','/')]).hexdigest()==asset['sha256']
assert '1173 passed' in (HERE/'backend-final.log').read_text()
assert '# pass 307' in (HERE/'frontend-final.log').read_text()
result=dict(files=len(files),changed_files=audit['changed_files'],tree=tree,
    source_artifacts_tests_runtime_exact=True,backend=1173,frontend=307,
    raw_benchmark_points=250000,personal_tables_preserved=runtime['tables_compared'],
    visual_verification=False,local_only=True,goal_complete=False)
(HERE/'final-source-check.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result))
