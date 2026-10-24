from pathlib import Path
import difflib, hashlib, json, re, zipfile

root=Path.cwd();folder=root/'.qa/revision4'
base={entry['path']:entry['content'] for entry in json.loads((folder/'before.json').read_text(encoding='ascii'))}
assert len(base)==159
extensions={'.py','.js','.css','.html','.cjs','.json','.txt','.md','.swift','.yml'}
paths=[]
for directory in ['app','web','tests','docs','desktop','ios']:
    paths.extend(p for p in (root/directory).rglob('*') if p.is_file() and p.suffix.lower() in extensions and not any(part in {'node_modules','__pycache__','.secrets'} for part in p.parts))
paths.extend(p for p in root.iterdir() if p.is_file() and (p.name=='.gitignore' or p.suffix.lower() in {'.md','.txt','.bat'}))
paths=sorted(set(paths));blobs={};manifest=[];entries=[];patch=[]
def git_object(kind,data):return hashlib.sha1(kind.encode()+b' '+str(len(data)).encode()+b'\0'+data).hexdigest()
def tree_hash(files):
    tree={}
    for path,sha in files.items():
        node=tree;parts=path.split('/')
        for part in parts[:-1]:node=node.setdefault(part,{})
        node[parts[-1]]=sha
    def digest(node):
        content=b''
        for name,value in sorted(node.items(),key=lambda item:(item[0]+('/' if isinstance(item[1],dict) else '')).encode()):
            directory=isinstance(value,dict);sha=digest(value) if directory else value
            content+=(b'40000' if directory else b'100644')+b' '+name.encode()+b'\0'+bytes.fromhex(sha)
        return git_object('tree',content)
    return digest(tree)
for p in paths:
    rel=p.relative_to(root).as_posix();data=p.read_bytes();content=data.decode('utf-8')
    assert not any(part in {'.qa','data','.venv-web','node_modules','.secrets'} for part in p.relative_to(root).parts)
    assert not re.search(rb'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',data)
    blobs[rel]=git_object('blob',data);manifest.append({'path':rel,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    if base.get(rel)!=content:
        entries.append({'path':rel,'mode':'100644','type':'blob','content':content})
        patch.extend(difflib.unified_diff(base.get(rel,'').splitlines(True),content.splitlines(True),fromfile='a/'+rel if rel in base else '/dev/null',tofile='b/'+rel))
assert not set(base)-set(blobs)
baseline=tree_hash({path:git_object('blob',content.encode('utf-8')) for path,content in base.items()})
assert baseline=='466ee83560cfc2476c5ad73c15c364f31009b527',baseline
(folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
(folder/'blobs.json').write_text(json.dumps(blobs),encoding='ascii')
patch_path=root/'.qa/IronCoach-IMC.diff';patch_path.write_bytes(''.join(patch).encode('utf-8'))
parts=[];part=[];size=0
for entry in entries:
    length=len(json.dumps(entry,ensure_ascii=True))
    if part and size+length>60000:parts.append(part);part=[];size=0
    part.append(entry);size+=length
if part:parts.append(part)
for i,part in enumerate(parts):(folder/f'part-{i}.json').write_text(json.dumps(part,ensure_ascii=True),encoding='ascii')
package=root/'.qa/IronCoach-IMC-sorgenti.zip'
with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as archive:
    for p in paths:archive.write(p,p.relative_to(root).as_posix())
with zipfile.ZipFile(package) as archive:
    assert set(archive.namelist())==set(blobs)
    for item in manifest:
        data=archive.read(item['path']);assert data==(root/item['path']).read_bytes()
        assert hashlib.sha256(data).hexdigest()==item['sha256'] and git_object('blob',data)==blobs[item['path']]
audit={'files':len(paths),'changed_files':len(entries),'parts':len(parts),'baseline_tree_sha':baseline,'expected_tree_sha':tree_hash(blobs),'zip_sha256':hashlib.sha256(package.read_bytes()).hexdigest(),'diff_sha256':hashlib.sha256(patch_path.read_bytes()).hexdigest(),'all_exact':True,'changed_paths':[entry['path'] for entry in entries]}
(folder/'audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8');print(json.dumps(audit))
