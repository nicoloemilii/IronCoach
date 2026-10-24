"""Freeze the prior local follow-up without changing its pending upload."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIOR = ROOT / '.qa/goal-followup'
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('followup_package', PRIOR / 'package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
files = package.published_files()
for index in range(8):
    for entry in json.loads((PRIOR / f'part-{index}.json').read_bytes()):
        if entry['content'] is None:
            files.pop(entry['path'])
        else:
            files[entry['path']] = entry['content'].encode('utf-8')
tree = package.tree_hash({name: package.git_object('blob', content) for name, content in files.items()})
assert len(files) == 203 and tree == 'ef0aba510e86a9223ed38190b4641a3a452ce3dc'
snapshot = package.json_bytes([{'path': path, 'content': content.decode('utf-8')} for path, content in sorted(files.items())])
target = HERE / 'before.json'
if target.exists():
    assert target.read_bytes() == snapshot
else:
    target.write_bytes(snapshot)
builder = (PRIOR / 'package.py').read_bytes().decode('utf-8')
builder = builder.replace('FOLDER = QA / "goal-followup"', 'FOLDER = QA / "goal-response"')
builder = builder.replace('PUBLISHED_COMMIT = "935968a96029cb410b6fa15f3b5e95aff3136d40"', 'PUBLISHED_COMMIT = None  # Local baseline; this follow-up is not published.')
builder = builder.replace('PUBLISHED_TREE = "c43a4f357e46f6a04de52eaf54c24ecda8d2813b"', f'PUBLISHED_TREE = "{tree}"')
builder = builder.replace('assert len(base) == 202, len(base)', 'assert len(base) == 203, len(base)')
builder = builder.replace('IronCoach-App-followup', 'IronCoach-Training-response')
(HERE / 'package.py').write_bytes(builder.encode('utf-8'))
print(json.dumps({'baseline_files': len(files), 'baseline_tree': tree, 'snapshot_sha256': hashlib.sha256(snapshot).hexdigest(), 'publication': 'local_only'}))
