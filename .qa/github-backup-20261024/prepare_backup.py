"""Freeze development sources and QA tools for a byte-verifiable GitHub backup."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / 'snapshot'
spec = importlib.util.spec_from_file_location('source_package', ROOT / '.qa/goal-distance/package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')


def collect():
    files = package.source_files()
    files['web/tests/.gitignore'] = (ROOT / 'web/tests/.gitignore').read_bytes()
    qa = ROOT / '.qa'
    directories = [qa] + [p for p in qa.iterdir() if p.is_dir() and p != HERE]
    for directory in directories:
        if directory.name.endswith('-before') or directory.name == 'revision-before':
            continue
        try:
            children = list(directory.iterdir())
        except PermissionError:
            continue  # These directories are generated test environments, not tools.
        for path in children:
            if path.is_file() and path.suffix.lower() in {'.py', '.ps1', '.cjs', '.js', '.md'}:
                assert path.resolve().is_relative_to(ROOT)
                files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    files['.qa/github-backup-20261024/prepare_backup.py'] = Path(__file__).read_bytes()
    launcher = ROOT.parent / 'Apri_Iron_Coach.bat'
    files['backup/2026-10-24/external-launcher/Apri_Iron_Coach.bat'] = launcher.read_bytes()
    return files


def main():
    if '--check-current' in sys.argv:
        manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
        current = collect()
        expected = {entry['path']: entry for entry in manifest['files']}
        differences = [path for path in sorted(current.keys() | expected.keys())
                       if path not in current or path not in expected or hashlib.sha256(current[path]).hexdigest() != expected[path]['sha256']]
        print(json.dumps({'current_matches_snapshot': not differences, 'changed_paths': differences, 'files': len(current)}))
        return
    assert not (HERE / 'manifest.json').exists(), 'Snapshot already frozen; never overwrite it.'
    files = collect()
    hits = []
    for path, raw in files.items():
        raw.decode('utf-8')
        for name, pattern in package.SECRET_PATTERNS.items():
            if re.search(pattern, raw):
                hits.append({'path': path, 'pattern': name})
    assert not hits, json.dumps(hits)
    manifest = {'date': '2026-10-24', 'purpose': 'Development backup, including unfinished distance/swimming work; not a release.',
                'excluded': ['Personal databases and wellness/activity data', 'Credentials, private keys and filled environment files',
                             'Virtual environments, node_modules and caches', 'Generated runtime output and duplicate staging copies'],
                'files': [{'path': path, 'bytes': len(raw), 'git_blob_sha': package.git_object('blob', raw),
                           'sha256': hashlib.sha256(raw).hexdigest()} for path, raw in sorted(files.items())],
                'secret_scan': {'files': len(files), 'patterns': sorted(package.SECRET_PATTERNS), 'hits': []}}
    dump(HERE / 'manifest.json', manifest)
    files['backup/2026-10-24/manifest.json'] = (HERE / 'manifest.json').read_bytes()
    files['backup/2026-10-24/README.md'] = (
        '# Iron Coach: backup sviluppo del 24 ottobre 2026\n\n'
        'Copia dei sorgenti, test, documentazione, strumenti QA originali e launcher esterno. '
        'Comprende il lavoro in corso sui record di distanza e nuoto. E un backup, non una versione validata per il rilascio.\n\n'
        'I documenti in docs/ descrivono le verifiche delle rispettive revisioni; non certificano le modifiche successive. '
        'La verifica completa dell integrazione distanza/nuoto e ancora da completare.\n\n'
        'Il manifest elenca dimensioni, SHA-256 e SHA Git dei file acquisiti. I dati personali, database, FIT originali, '
        'credenziali, ambienti virtuali, dipendenze installate e output runtime sono esclusi.\n\n'
        'Gli script .qa/ conservano i percorsi originali. Alcuni dipendono da fixture/output generati esclusi e alcuni '
        'script di riavvio contengono PID storici: rivederli prima di eseguirli. '
        'Il launcher in external-launcher/ appartiene alla cartella che contiene ironman_coach/.\n\n'
        'Per riprendere: leggere docs/REQUIREMENTS_STATUS_2026_10_23.md e docs/HISTORICAL_POWER_RECORDS_2026_10_23.md, '
        'verificare lo stato effettivo del codice e completare API/job/test dei record distanza e nuoto. '
        'Non confondere test sintetici con verifica visiva, provider reali o prove Garmin su dispositivo.\n'
    ).encode('utf-8')
    for path, raw in files.items():
        destination = SNAPSHOT / path
        assert destination.resolve().is_relative_to(SNAPSHOT.resolve())
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
        dump(HERE / f'part-{index}.json', part)
    tree_sha = package.tree_hash({path: package.git_object('blob', raw) for path, raw in files.items()})
    archive_path = HERE / 'IronCoach-development-backup-20261024.zip'
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, raw in sorted(files.items()):
            archive.writestr(path, raw)
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == set(files)
        assert all(archive.read(path) == raw for path, raw in files.items())
    result = {'files': len(files), 'core_files': len(package.source_files()) + 1,
              'qa_tools': sum(path.startswith('.qa/') for path in files), 'bytes': sum(map(len, files.values())),
              'tree_sha': tree_sha, 'parts': len(parts), 'zip_sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest()}
    dump(HERE / 'snapshot-result.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
