import asyncio
import threading
import time
import urllib.error

import pytest

import app
import auth
import settings
from helpers import FakeResponse, fake_urlopen, http_error, write_credentials

USAGE = {"five_hour": {"utilization": 12.0, "resets_at": None},
         "seven_day": {"utilization": 30.0, "resets_at": None}}


@pytest.fixture
def quota_cache(tmp_path, monkeypatch):
    """An empty quota cache and a fresh fetch lock, with the disk cache under tmp_path."""
    cache = {**app._usage_cache, "data": None, "fetched_at": 0.0, "retry_after": 0.0,
             "error": None, "fail_count": 0, "creds_sig": None, "last_ok_at": None}
    monkeypatch.setattr(app, "_usage_cache", cache)
    monkeypatch.setattr(app, "_fetch_lock", asyncio.Lock())
    monkeypatch.setattr(app, "QUOTA_CACHE_FILE", tmp_path / "quota_cache.json")
    monkeypatch.setattr(app, "_maybe_write_snapshot", lambda *a: None)
    return cache


def test_expired_token_is_never_sent(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    monkeypatch.setattr(app.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("sent an expired token"))
    result = app._fetch_usage_sync()
    assert (result["ok"], result["error"]) == (False, "token-expired")


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


def test_a_401_that_raced_a_token_rotation_is_retried_at_once(isolated_credentials, quota_cache,
                                                              monkeypatch):
    """/api/quota and /api/window poll together. Claude Code rewrites the credentials while
    the first poller's request is out; the second poller notices the new file before the
    old token's 401 lands. That 401 must not leave a backoff nothing will lift."""
    write_credentials(isolated_credentials, token="old")
    rewritten, release = threading.Event(), threading.Event()
    sent = []

    def urlopen(req, timeout=None):
        sent.append(req.get_header("Authorization"))
        if len(sent) == 1:
            write_credentials(isolated_credentials, token="renewed-by-claude-code")
            rewritten.set()
            release.wait(5)
            raise http_error(401)
        return FakeResponse(USAGE)
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)

    async def two_pollers():
        first = asyncio.create_task(app._get_usage_data())
        for _ in range(500):
            if rewritten.is_set():
                break
            await asyncio.sleep(0.01)
        second = asyncio.create_task(app._get_usage_data())
        await asyncio.sleep(0)  # the second poller checks the file, then queues on the fetch lock
        release.set()
        return await first, await second

    first, second = asyncio.run(two_pollers())
    assert first["error"] == "http-401"
    assert second["five_hour_pct"] == 12.0
    assert sent == ["Bearer old", "Bearer renewed-by-claude-code"]


def no_refresh_token(credentials):
    """The file has an access token but nothing to renew it with."""
    write_credentials(credentials, refresh="")
    return [http_error(401)]


def unwritable_credentials(credentials):
    """The renewal works, but the new token can't be saved."""
    write_credentials(credentials)
    (credentials.parent / (credentials.name + ".tmp")).mkdir()  # auth writes here, then renames
    return [http_error(401), FakeResponse({"access_token": "fresh", "expires_in": 3600})]


@pytest.mark.parametrize("setup", [no_refresh_token, unwritable_credentials])
def test_a_401_that_auto_renewal_cannot_fix_asks_for_sign_in(isolated_credentials, quota_cache,
                                                             monkeypatch, setup):
    settings.update({"auto_refresh_token": True})
    urlopen, _ = fake_urlopen(setup(isolated_credentials))
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: "C:/claude.exe")
    asyncio.run(app._get_usage_data())
    connection = app._connection()
    assert connection["state"] == "login-required"
    assert "sign-in" in connection["actions"]


@pytest.mark.parametrize("failure", [urllib.error.URLError("getaddrinfo failed"), http_error(503),
                                     FakeResponse({"expires_in": 3600})])  # no access_token
def test_a_renewal_that_fails_in_transit_stays_unavailable(isolated_credentials, quota_cache,
                                                           monkeypatch, failure):
    settings.update({"auto_refresh_token": True})
    write_credentials(isolated_credentials)
    urlopen, _ = fake_urlopen([http_error(401), failure])
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: "C:/claude.exe")
    asyncio.run(app._get_usage_data())
    assert app._connection()["state"] == "unavailable"
    assert not auth.rejected()
    # Same file, same token: nothing to gain from retrying before the usual backoff.
    assert quota_cache["retry_after"] - time.monotonic() > app.CACHE_MIN_RETRY - 60
