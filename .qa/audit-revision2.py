from pathlib import Path
import hashlib
import json
import zipfile

root = Path.cwd()
folder = root / '.qa/revision2-publish'
manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
blobs = json.loads((folder / 'expected-blobs.json').read_text(encoding='ascii'))
base = {}
for p in (root / '.qa/github-upload').glob('part-*.json'):
    for entry in json.loads(p.read_text(encoding='ascii')):
        base[entry['path']] = entry['content']

def git_object(kind, data):
    return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def tree_hash(files):
    tree = {}
    for path, sha in files.items():
        node = tree
        parts = path.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = sha
    def digest(node):
        content = b''
        for name, value in sorted(node.items(), key=lambda entry: (entry[0] + ('/' if isinstance(entry[1], dict) else '')).encode()):
            directory = isinstance(value, dict)
            sha = digest(value) if directory else value
            content += (b'40000' if directory else b'100644') + b' ' + name.encode() + b'\0' + bytes.fromhex(sha)
        return git_object('tree', content)
    return digest(tree)

baseline = tree_hash({path: git_object('blob', content.encode('utf-8')) for path, content in base.items()})
assert baseline == '49d406d2da7c074a4efa22e964ab6bb1f0a63915', baseline
with zipfile.ZipFile(root / '.qa/IronCoach-sorgenti-aggiornati.zip') as package:
    assert set(package.namelist()) == set(blobs)
    for item in manifest:
        data = (root / item['path']).read_bytes()
        assert data == package.read(item['path'])
        assert len(data) == item['bytes']
        assert hashlib.sha256(data).hexdigest() == item['sha256']
        assert git_object('blob', data) == blobs[item['path']]
entries = []
for part in folder.glob('part-*.json'):
    entries += json.loads(part.read_text(encoding='ascii'))
assert len(entries) == len({item['path'] for item in entries})
expected_delta = {path for path in blobs if base.get(path) != (root / path).read_bytes().decode('utf-8')}
assert {entry['path'] for entry in entries} == expected_delta
for entry in entries:
    assert git_object('blob', entry['content'].encode('utf-8')) == blobs[entry['path']]
assert not (set(base) - set(blobs)), 'Unexpected source deletion'
result = {'files':len(blobs), 'changed_files':len(entries), 'baseline_tree_sha':baseline, 'expected_tree_sha':tree_hash(blobs), 'zip_sha256':hashlib.sha256((root/'.qa/IronCoach-sorgenti-aggiornati.zip').read_bytes()).hexdigest(), 'diff_sha256':hashlib.sha256((root/'.qa/IronCoach-revisione2.diff').read_bytes()).hexdigest(), 'all_exact':True}
(folder / 'audit.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
