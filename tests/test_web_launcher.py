"""Local launcher handles stopped, running and unrelated services truthfully."""
import io
from urllib.error import HTTPError, URLError

import pytest

from app import web_launcher


def test_existing_app_is_recognized_without_starting_a_second_server(monkeypatch, capsys):
    def existing(request, timeout):
        assert request.full_url == 'http://127.0.0.1:8000/'
        assert request.get_header('Accept') == 'application/json'
        return io.StringIO('{"app":"Ironman Coach","status":"online"}')
    monkeypatch.setattr(web_launcher, 'urlopen', existing)
    assert web_launcher.main() == 0
    assert 'già in esecuzione' in capsys.readouterr().out


@pytest.mark.parametrize('body', ['{}', '[]', 'null', '<html>Other service</html>'])
def test_other_service_does_not_get_reused_or_stopped(monkeypatch, body):
    monkeypatch.setattr(web_launcher, 'urlopen', lambda *args, **kwargs: io.StringIO(body))
    with pytest.raises(RuntimeError, match='altro servizio'):
        web_launcher.already_running()


def test_closed_port_starts_only_loopback_server(monkeypatch, capsys):
    import uvicorn
    def disconnected(*args, **kwargs):
        raise URLError('Connection refused')
    calls = []
    monkeypatch.setattr(web_launcher, 'urlopen', disconnected)
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    assert web_launcher.main() == 0
    assert calls == [(('app.main:app',), {'host':'127.0.0.1', 'port':8000, 'access_log':False})]
    assert 'http://127.0.0.1:8000/app' in capsys.readouterr().out


def test_http_error_is_reported_and_does_not_start_another_service(monkeypatch, capsys):
    def other(*args, **kwargs):
        raise HTTPError(web_launcher.BASE_URL, 404, 'Not found', {}, None)
    monkeypatch.setattr(web_launcher, 'urlopen', other)
    assert web_launcher.main() == 1
    assert 'Avvio non riuscito' in capsys.readouterr().out
