from __future__ import annotations

import argparse
from pathlib import Path

from app.db.database import Base, SessionLocal, engine
from app.integrations.fit import parse_fit
from app.db.repository import save_activity
from app.db import experience_models

Base.metadata.create_all(bind=engine)


def import_fit_file(file_path: Path):
    data = parse_fit(file_path)
    db = SessionLocal()
    try:
        existing = db.query(__import__('app.db.models', fromlist=['Activity']).Activity).filter_by(source_id=data['source_id']).first()
        activity = save_activity(db, data)
        return activity, existing is not None
    finally:
        db.close()


def import_fit_directory(directory: str | Path = 'data/fit'):
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"Cartella FIT non trovata: {directory}")

    files = sorted(directory.glob('*.fit')) + sorted(directory.glob('*.FIT'))
    # Remove duplicate paths on case-insensitive filesystems.
    files = list(dict.fromkeys(files))
    if not files:
        raise FileNotFoundError(f"Nessun file FIT trovato in {directory}")

    imported = 0
    skipped = 0
    errors = []

    for fit_file in files:
        try:
            _, already_exists = import_fit_file(fit_file)
            if already_exists:
                skipped += 1
            else:
                imported += 1
        except Exception as exc:
            errors.append({'file': str(fit_file), 'error': str(exc)})

    return {
        'directory': str(directory.resolve()),
        'files_found': len(files),
        'imported': imported,
        'skipped_existing': skipped,
        'errors': errors,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Importa tutte le attività FIT in data/fit.')
    parser.add_argument('--directory', default='data/fit', help='Cartella contenente i file FIT')
    args = parser.parse_args()

    result = import_fit_directory(args.directory)
    print('IMPORTAZIONE FIT COMPLETATA')
    print('---------------------------')
    print(f"File trovati:      {result['files_found']}")
    print(f"Nuove attività:    {result['imported']}")
    print(f"Già presenti:      {result['skipped_existing']}")
    print(f"Errori:             {len(result['errors'])}")
    for error in result['errors']:
        print(f"ERRORE: {error['file']} -> {error['error']}")
