"""Create the public source backup without personal observations in old reports."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('source_package', ROOT / '.qa/goal-distance/package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def main():
    original = HERE / 'snapshot'
    output = HERE / 'public-snapshot'
    files = {path.relative_to(original).as_posix(): path.read_bytes() for path in original.rglob('*') if path.is_file()}
    manifest_path = 'backup/2026-10-24/manifest.json'
    readme_path = 'backup/2026-10-24/README.md'
    files.pop(manifest_path)
    privacy = json.loads((HERE / 'redactions.json').read_text(encoding='utf-8'))
    for entry in privacy:
        path = entry['path']
        text = files[path].decode('utf-8')
        lines = text.splitlines(keepends=True)
        if 'line_ranges' in entry:
            omitted = {index for start, end in entry['line_ranges'] for index in range(start - 1, end)}
            text = ''.join(line for index, line in enumerate(lines) if index not in omitted)
        for sentence in entry.get('sentence_starts', []):
            pattern = re.escape(sentence) + r'.*?\.(?=\s|$)\s*'
            text, count = re.subn(pattern, '', text)
            assert count == 1, (path, sentence, count)
        assert text != files[path].decode('utf-8'), path
        files[path] = text.encode('utf-8')
    files['.qa/github-backup-20261024/prepare_public.py'] = Path(__file__).read_bytes()
    files['.qa/github-backup-20261024/redactions.json'] = (HERE / 'redactions.json').read_bytes()
    files[readme_path] += ('\n## Privacy della copia pubblica\n\n'
        'Alcune frasi con osservazioni personali nei rapporti storici sono state omesse soltanto in questa copia. '
        'I file interessati e la motivazione sono elencati nel manifest; i sorgenti eseguibili del prodotto restano identici. '
        'Gli originali sono conservati esclusivamente nel backup locale.\n').encode('utf-8')
    for path, raw in files.items():
        for name, pattern in package.SECRET_PATTERNS.items():
            assert not re.search(pattern, raw), (path, name)
    manifest = {'date': '2026-10-24', 'purpose': 'Public development backup, including unfinished work; not a release.',
                'excluded': ['Personal databases, activity/wellness data and original FIT files', 'Credentials and private keys',
                             'Installed dependencies, caches and generated runtime output'],
                'redactions': privacy,
                'files': [{'path': path, 'bytes': len(raw), 'git_blob_sha': package.git_object('blob', raw),
                           'sha256': hashlib.sha256(raw).hexdigest()} for path, raw in sorted(files.items())],
                'secret_scan': {'files': len(files), 'hits': []}}
    files[manifest_path] = (json.dumps(manifest, ensure_ascii=True, indent=2) + '\n').encode('utf-8')
    for path, raw in files.items():
        destination = output / path
        assert destination.resolve().is_relative_to(output.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    entries = [{'path': path, 'mode': '100644', 'type': 'blob', 'content': raw.decode('utf-8')} for path, raw in sorted(files.items())]
    parts, part = [], []
    for entry in entries:
        if part and len(json.dumps(part + [entry], ensure_ascii=True)) > 100000:
            parts.append(part)
            part = []
        part.append(entry)
    if part:
        parts.append(part)
    for index, part in enumerate(parts):
        (HERE / f'public-part-{index}.json').write_text(json.dumps(part, ensure_ascii=True), encoding='utf-8')
    archive_path = HERE / 'IronCoach-public-development-backup-20261024.zip'
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, raw in sorted(files.items()):
            archive.writestr(path, raw)
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == set(files)
        assert all(archive.read(path) == raw for path, raw in files.items())
    result = {'files': len(files), 'redacted_files': len(privacy), 'parts': len(parts),
              'tree_sha': package.tree_hash({path: package.git_object('blob', raw) for path, raw in files.items()}),
              'zip_sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest()}
    (HERE / 'public-result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
