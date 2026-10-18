"""Start the local web client, or show its address if it is already running."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = 'http://127.0.0.1:8000'
APP_URL = BASE_URL + '/app'


def already_running():
    try:
        request = Request(BASE_URL + '/', headers={'Accept': 'application/json'})
        with urlopen(request, timeout=2) as response:
            identity = json.load(response)
    except HTTPError as error:
        raise RuntimeError('La porta 8000 risponde con un altro servizio. Non è stato arrestato alcun programma.') from error
    except (URLError, TimeoutError, OSError):
        return False
    except (ValueError, UnicodeError) as error:
        raise RuntimeError('La porta 8000 è già occupata da un altro servizio.') from error
    if not isinstance(identity, dict) or identity.get('app') != 'Ironman Coach':
        raise RuntimeError('La porta 8000 è già occupata da un altro servizio.')
    return True


def main():
    print('\nIRON COACH — INTERFACCIA WEB', flush=True)
    print(f'Apri nel browser: {APP_URL}\n', flush=True)
    try:
        active = already_running()
    except RuntimeError as error:
        print(f'Avvio non riuscito: {error}', flush=True)
        return 1
    if active:
        print('Iron Coach è già in esecuzione. Usa il link qui sopra.', flush=True)
        return 0
    print('Questa finestra mantiene attivo il server. Premi Ctrl+C per arrestarlo.', flush=True)
    import uvicorn
    uvicorn.run('app.main:app', host='127.0.0.1', port=8000, access_log=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
