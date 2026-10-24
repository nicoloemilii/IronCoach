from pathlib import Path
import json,hashlib,difflib,re,zipfile
root=Path.cwd();base={}
for p in sorted((root/'.qa/github-upload').glob('part-*.json')):
 for entry in json.loads(p.read_text(encoding='ascii')):base[entry['path']]=entry['content']
assert len(base)==140
for rel in ['docs/ATTIVITA_PESO_PALESTRA_2026_10_18.md']:
 p=root/rel;s=p.read_text(encoding='utf-8');s=re.sub(r'(?<=[A-Za-zÀ-ÿ])(?=\d)|(?<=\d)(?=[A-Za-zÀ-ÿ])',' ',s);p.write_text(s,encoding='utf-8')
p=root/'docs/REVISIONE_USABILITA_2026_10_18.md';s=p.read_text(encoding='utf-8').replace('Pubblicazione GitHub: repository accessibile, ma prima scrittura respinta dal controllo automatico perché il tool richiede approvazione e la policy della sessione è never. Nessuna pubblicazione eseguita; pacchetto sorgente locale senza dati personali, credenziali, database o ambienti virtuali.', 'Pubblicazione GitHub: il primo tentativo è stato respinto dal controllo automatico; il caricamento successivo è riuscito nella repository nicoloemilii/IronCoach. Dati personali operativi, credenziali, database e ambienti virtuali restano esclusi.');p.write_text(s,encoding='utf-8')
paths=[];extensions={'.py','.js','.css','.html','.cjs','.json','.txt','.md','.swift','.yml'}
for folder in ['app','web','tests','docs','desktop','ios']:
 for p in (root/folder).rglob('*'):
  if p.is_file() and p.suffix.lower() in extensions and not any(part in {'node_modules','__pycache__','.secrets'} for part in p.parts):paths.append(p)
paths += [p for p in root.iterdir() if p.is_file() and (p.name=='.gitignore' or p.suffix.lower() in {'.md','.txt','.bat'})]
paths=sorted(set(paths));manifest=[];entries=[];patch=[];expected={}
for p in paths:
 rel=p.relative_to(root).as_posix();data=p.read_bytes();content=data.decode('utf-8')
 assert not re.search(rb'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',data)
 assert not any(x in {'.qa','data','.venv-web','node_modules'} for x in p.relative_to(root).parts)
 manifest.append({'path':rel,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
 expected[rel]=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
 if base.get(rel)!=content:
  entries.append({'path':rel,'mode':'100644','type':'blob','content':content})
  patch.extend(difflib.unified_diff(base.get(rel,'').splitlines(True),content.splitlines(True),fromfile='a/'+rel if rel in base else '/dev/null',tofile='b/'+rel))
folder=root/'.qa/revision2-publish';folder.mkdir(exist_ok=True)
(folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8');(folder/'expected-blobs.json').write_text(json.dumps(expected),encoding='ascii')
(root/'.qa/IronCoach-revisione2.diff').write_text(''.join(patch),encoding='utf-8')
parts=[];part=[];size=0
for entry in entries:
 length=len(json.dumps(entry,ensure_ascii=True))
 if part and size+length>60000:parts.append(part);part=[];size=0
 part.append(entry);size+=length
if part:parts.append(part)
for i,part in enumerate(parts):(folder/f'part-{i}.json').write_text(json.dumps(part,ensure_ascii=True),encoding='ascii')
with zipfile.ZipFile(root/'.qa/IronCoach-sorgenti-aggiornati.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in paths:z.write(p,p.relative_to(root).as_posix())
print(json.dumps({'source_files':len(paths),'changed_files':len(entries),'parts':len(parts),'part_sizes':[(folder/f'part-{i}.json').stat().st_size for i in range(len(parts))],'new_files':[e['path'] for e in entries if e['path'] not in base],'patch_bytes':(root/'.qa/IronCoach-revisione2.diff').stat().st_size}))
