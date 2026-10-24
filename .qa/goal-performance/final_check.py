"""Verify the frozen local artifact against final live source and test evidence."""
import hashlib,importlib.util,json
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('release_package',HERE/'package.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
files=package.source_files();tree=package.tree_hash({path:package.git_object('blob',raw) for path,raw in files.items()})
audit=json.loads((HERE/'audit.json').read_bytes());assert tree==audit['expected_tree_sha']
for field in ('zip','diff'):
    path=package.ROOT/audit[field+'_path'];assert hashlib.sha256(path.read_bytes()).hexdigest()==audit[field+'_sha256']
benchmark=json.loads((HERE/'optimized_timings_final.json').read_bytes())
for path,digest in benchmark['source_sha256'].items():assert hashlib.sha256(files[path]).hexdigest()==digest
integrated=json.loads((HERE/'review_dom.json').read_bytes());assert integrated['ok']
for path,digest in integrated['source_sha256'].items():assert hashlib.sha256(files[path]).hexdigest()==digest
runtime=json.loads((HERE/'runtime-verification.json').read_bytes());assert runtime['all_previous_rows_unchanged']
for asset in runtime['assets']:assert hashlib.sha256(files[asset['path'].replace('\\','/')]).hexdigest()==asset['sha256']
assert '1152 passed' in (HERE/'backend-final-v2.log').read_text()
assert '# pass 286' in (HERE/'frontend-final.log').read_text()
result=dict(files=len(files),changed_files=audit['changed_files'],tree=tree,source_artifacts_tests_runtime_exact=True,
    backend=1152,frontend=286,benchmark_scenarios=6,all_benchmarks_exact=benchmark['all_exact'],
    personal_tables_preserved=runtime['tables_compared'],visual_verification=False,local_only=True)
(HERE/'final-source-check.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result))
