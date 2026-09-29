import json
import time

import pytest

import auth
from helpers import FakeResponse, fake_urlopen, http_error, write_credentials


def test_token_seconds_left(isolated_credentials):
    assert auth.token_seconds_left() is None
    isolated_credentials.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "t", "expiresAt": (time.time() + 3600) * 1000}}))
    assert 3500 < auth.token_seconds_left() <= 3600


def test_rejected_clears_once_the_credentials_file_changes(monkeypatch):
    monkeypatch.setattr(auth, "_auth_dead", True)
    monkeypatch.setattr(auth, "_auth_dead_creds_sig", ("old",))
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("old",))
    assert auth.rejected()
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("new",))
    assert not auth.rejected()


def no_refresh(*args, **kwargs):
    pytest.fail("read-only mode must not renew the token")


def test_valid_token_is_used(isolated_credentials):
    write_credentials(isolated_credentials)
    assert auth.usable_token(auto_refresh=False) == ("tok", "ok")


def test_missing_or_blank_token_means_no_credentials(isolated_credentials):
    assert auth.usable_token(auto_refresh=False) == (None, "no-credentials")
    write_credentials(isolated_credentials, token="")
    assert auth.usable_token(auto_refresh=False) == (None, "no-credentials")


def test_expired_token_is_reported_not_renewed(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    monkeypatch.setattr(auth, "refresh_token", no_refresh)
    assert auth.usable_token(auto_refresh=False) == (None, "token-expired")


def test_token_without_expiry_is_tried(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=None)
    monkeypatch.setattr(auth, "refresh_token", no_refresh)
    assert auth.usable_token(auto_refresh=False) == ("tok", "ok")


def test_expired_token_is_renewed_when_allowed(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)

    def renew(force=False):
        write_credentials(isolated_credentials, token="renewed")
        return True
    monkeypatch.setattr(auth, "refresh_token", renew)
    assert auth.usable_token(auto_refresh=True) == ("renewed", "ok")


def test_refused_credentials_need_sign_in_until_the_file_changes(isolated_credentials):
    write_credentials(isolated_credentials)
    auth.mark_rejected(auth.credentials_signature())
    assert auth.usable_token(auto_refresh=False) == (None, "login-required")
    write_credentials(isolated_credentials, token="renewed-by-claude-code")  # a different size
    assert auth.usable_token(auto_refresh=False) == ("renewed-by-claude-code", "ok")


def test_refusal_recorded_against_an_older_file_does_not_stick(isolated_credentials):
    write_credentials(isolated_credentials)
    before = auth.credentials_signature()
    write_credentials(isolated_credentials, token="renewed-meanwhile")
    auth.mark_rejected(before)
    assert not auth.rejected()


def test_refresh_writes_the_new_token(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    urlopen, _ = fake_urlopen([FakeResponse(
        {"access_token": "fresh", "refresh_token": "ref2", "expires_in": 3600})])
    monkeypatch.setattr(auth.urllib.request, "urlopen", urlopen)
    assert auth.refresh_token() is True
    oauth = json.loads(isolated_credentials.read_text())["claudeAiOauth"]
    assert (oauth["accessToken"], oauth["refreshToken"]) == ("fresh", "ref2")


def test_invalid_grant_marks_the_credentials_refused(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    urlopen, _ = fake_urlopen([http_error(400, b'{"error": "invalid_grant"}')])
    monkeypatch.setattr(auth.urllib.request, "urlopen", urlopen)
    assert auth.refresh_token() is False
    assert auth.rejected()


def test_forced_refresh_skips_the_throttle_and_the_refused_flag(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    auth.mark_rejected(auth.credentials_signature())
    urlopen, calls = fake_urlopen([FakeResponse({"access_token": "fresh", "expires_in": 3600})])
    monkeypatch.setattr(auth.urllib.request, "urlopen", urlopen)
    assert auth.refresh_token() is False and calls == []  # background renewal waits for a new file
    assert auth.refresh_token(force=True) is True and len(calls) == 1
    assert not auth.rejected()
