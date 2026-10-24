"""Freeze the prior local response feature; never touch pending upload files."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIOR = ROOT / '.qa/goal-response'
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('response_package', PRIOR / 'package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
files = package.published_files()
audit = json.loads((PRIOR / 'audit.json').read_bytes())
for metadata in audit['delta_parts']:
    raw = (PRIOR / metadata['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == metadata['sha256']
    for entry in json.loads(raw):
        if entry['content'] is None:
            files.pop(entry['path'])
        else:
            files[entry['path']] = entry['content'].encode('utf-8')
tree = package.tree_hash({name: package.git_object('blob', content) for name, content in files.items()})
assert len(files) == 209 and tree == '75e087c2204ae52fa296123acda5f04a04cc1fa3'
snapshot = package.json_bytes([{'path': path, 'content': content.decode('utf-8')} for path, content in sorted(files.items())])
target = HERE / 'before.json'
if target.exists():
    assert target.read_bytes() == snapshot
else:
    target.write_bytes(snapshot)
builder = (PRIOR / 'package.py').read_bytes().decode('utf-8')
builder = builder.replace('FOLDER = QA / "goal-response"', 'FOLDER = QA / "goal-review"')
builder = builder.replace('PUBLISHED_TREE = "ef0aba510e86a9223ed38190b4641a3a452ce3dc"', f'PUBLISHED_TREE = "{tree}"')
builder = builder.replace('assert len(base) == 203, len(base)', 'assert len(base) == 209, len(base)')
builder = builder.replace('IronCoach-Training-response', 'IronCoach-Duplicate-review')
(HERE / 'package.py').write_bytes(builder.encode('utf-8'))
print(json.dumps({'baseline_files': len(files), 'baseline_tree': tree, 'snapshot_sha256': hashlib.sha256(snapshot).hexdigest(), 'publication': 'local_only'}))
