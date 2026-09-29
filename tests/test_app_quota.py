import pytest

import app
import auth
import settings
from helpers import FakeResponse, fake_urlopen, http_error, write_credentials

USAGE = {"five_hour": {"utilization": 12.0, "resets_at": None},
         "seven_day": {"utilization": 30.0, "resets_at": None}}


def test_expired_token_is_never_sent(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    monkeypatch.setattr(app.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("sent an expired token"))
    assert app._fetch_usage_sync() == {"ok": False, "error": "token-expired", "retry_after": None}


def test_read_only_401_asks_for_sign_in(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials)
    urlopen, _ = fake_urlopen([http_error(401)])
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(auth, "refresh_token", lambda force=False: pytest.fail("renewed"))
    assert app._fetch_usage_sync()["error"] == "login-required"
    assert auth.rejected()


def test_401_after_the_file_changed_mid_request_does_not_stick(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials)

    def urlopen(*args, **kwargs):
        write_credentials(isolated_credentials, token="renewed-meanwhile")
        raise http_error(401)
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    app._fetch_usage_sync()
    assert not auth.rejected()


def test_auto_renewal_retries_once_after_a_401(isolated_credentials, monkeypatch):
    settings.update({"auto_refresh_token": True})
    write_credentials(isolated_credentials)
    urlopen, calls = fake_urlopen([http_error(401), FakeResponse(USAGE)])
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(auth, "refresh_token", lambda force=False: True)
    assert app._fetch_usage_sync()["ok"] is True and len(calls) == 2


def test_auto_renewal_refused_again_asks_for_sign_in(isolated_credentials, monkeypatch):
    settings.update({"auto_refresh_token": True})
    write_credentials(isolated_credentials)
    urlopen, calls = fake_urlopen([http_error(401), http_error(401)])
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(auth, "refresh_token", lambda force=False: True)
    assert app._fetch_usage_sync()["error"] == "login-required"
    assert len(calls) == 2 and auth.rejected()


def test_an_exception_that_quotes_the_token_stays_out_of_the_error(isolated_credentials, monkeypatch, capsys):
    write_credentials(isolated_credentials)

    def urlopen(*args, **kwargs):
        raise ValueError("Invalid header value b'Bearer sk-ant-oat-SECRET\\n'")
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    result = app._fetch_usage_sync()
    assert result["error"] == "network-error"
    assert "SECRET" not in str(result) and "SECRET" not in capsys.readouterr().out


def test_credentials_change_lifts_an_auth_backoff(monkeypatch):
    cache = {"error": "token-expired", "retry_after": 1e12, "creds_sig": ("old",)}
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("new",))
    app._retry_now_if_credentials_changed(cache)
    assert cache["retry_after"] == 0.0 and cache["creds_sig"] == ("new",)


def test_credentials_change_leaves_a_rate_limit_backoff_alone(monkeypatch):
    cache = {"error": "rate-limited", "retry_after": 1e12, "creds_sig": ("old",)}
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("new",))
    app._retry_now_if_credentials_changed(cache)
    assert cache["retry_after"] == 1e12
