"""Bind the final byte package to the executed local evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('local_package', HERE / 'package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
files = package.source_files()
audit = json.loads((HERE / 'audit.json').read_bytes())
tree = package.tree_hash({path:package.git_object('blob', raw) for path,raw in files.items()})
assert tree == audit['expected_tree_sha']
for kind in ('zip', 'diff'):
    assert hashlib.sha256((package.ROOT / audit[kind + '_path']).read_bytes()).hexdigest() == audit[kind + '_sha256']
api = json.loads((HERE / 'review_api.json').read_bytes())
assert api['status'] == 'passed' and api['sources_unchanged']
for path,digest in api['source_sha256_after'].items():
    assert hashlib.sha256(files[path]).hexdigest() == digest
dom = json.loads((HERE / 'review_dom.json').read_bytes())
assert not dom['dom_errors'] and not dom['external_resources']
for path,digest in dom['source_sha256'].items():
    assert hashlib.sha256(files[path]).hexdigest() == digest
runtime = json.loads((HERE / 'runtime-verification.json').read_bytes())
assert runtime['changed_tables'] == [] and runtime['new_derived_tables_empty']
assert runtime['personal_scan_started'] is False
for asset in runtime['assets']:
    assert hashlib.sha256(files[asset['path'].lstrip('/')]).hexdigest() == asset['sha256']
assert '1258 passed' in (HERE / 'backend-final.log').read_text()
assert '170 passed' in (HERE / 'backend-latest-focused.log').read_text()
frontend = json.loads((HERE / 'frontend-verification.json').read_bytes())
assert frontend['passed'] == 326 and frontend['failed'] == 0
for path,digest in frontend['source_sha256'].items():
    assert hashlib.sha256(files[path]).hexdigest() == digest
result = dict(files=len(files), changed_files=audit['changed_files'], tree=tree,
              source_artifacts_runtime_exact=True, backend_full=1258,
              backend_latest_focused=170, backend_union=1261, frontend=326,
              personal_tables_preserved=runtime['previous_tables_compared'],
              personal_scan_started=False, visual_verification=False,
              local_only=True, goal_complete=False)
(HERE / 'final-source-check.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result))
