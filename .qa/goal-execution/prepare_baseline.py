"""Retain the verified duplicate-review source baseline, byte for byte."""
import hashlib
import importlib.util
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
PRIOR = ROOT / '.qa/goal-review'
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('review_package', PRIOR / 'package.py')
package = importlib.util.module_from_spec(spec); spec.loader.exec_module(package)
files = package.published_files()
audit = json.loads((PRIOR / 'audit.json').read_bytes())
for metadata in audit['delta_parts']:
    raw = (PRIOR / metadata['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == metadata['sha256']
    for entry in json.loads(raw):
        if entry['content'] is None: files.pop(entry['path'])
        else: files[entry['path']] = entry['content'].encode('utf-8')
tree = package.tree_hash({name: package.git_object('blob', content) for name, content in files.items()})
assert len(files) == 217 and tree == 'f05550a12c499c0c27d177ade9bdb92b94206dc3'
snapshot = package.json_bytes([{'path': path, 'content': content.decode('utf-8')} for path, content in sorted(files.items())])
target = HERE / 'before.json'
if target.exists(): assert target.read_bytes() == snapshot
else: target.write_bytes(snapshot)
builder = (PRIOR / 'package.py').read_text(encoding='utf-8')
builder = builder.replace('FOLDER = QA / "goal-review"', 'FOLDER = QA / "goal-execution"')
builder = builder.replace('PUBLISHED_TREE = "75e087c2204ae52fa296123acda5f04a04cc1fa3"', f'PUBLISHED_TREE = "{tree}"')
builder = builder.replace('assert len(base) == 209, len(base)', 'assert len(base) == 217, len(base)')
builder = builder.replace('IronCoach-Duplicate-review', 'IronCoach-Plan-execution')
(HERE / 'package.py').write_text(builder, encoding='utf-8')
print(json.dumps({'files':len(files),'tree':tree,'publication':'local_only'}))
