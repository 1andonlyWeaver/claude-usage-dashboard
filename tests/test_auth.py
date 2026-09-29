import json
import time
from datetime import datetime

import pytest

import auth
from helpers import FakePopen, FakeResponse, fake_urlopen, http_error, write_credentials


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


NOW = datetime(2026, 9, 29, 9, 0).timestamp()


def creds(token="tok", expires_in=3600, expiry=True):
    oauth = {"accessToken": token}
    if expiry:
        oauth["expiresAt"] = (NOW + expires_in) * 1000
    return {"claudeAiOauth": oauth}


BASE = dict(creds=creds(), creds_exists=True, cli_path="C:/claude.exe", rejected=False,
            last_error=None, last_ok_at=NOW - 60, now=NOW)


@pytest.mark.parametrize("change, state, actions", [
    ({}, "connected", []),
    ({"creds": creds(expiry=False)}, "connected", []),  # no-expiry
    ({"creds": None, "creds_exists": False, "cli_path": None}, "not-installed", ["install"]),
    ({"creds": None, "creds_exists": False}, "signed-out", ["sign-in"]),
    ({"creds": creds(token="")}, "signed-out", ["sign-in"]),
    ({"creds": None}, "signed-out", ["sign-in"]),
    ({"creds": creds(token=""), "cli_path": None}, "signed-out", ["install"]),
    ({"rejected": True}, "login-required", ["sign-in"]),
    ({"last_error": "login-required"}, "login-required", ["sign-in"]),
    ({"creds": creds(expires_in=-60)}, "token-expired", ["renew", "sign-in"]),
    ({"creds": creds(expires_in=-60), "cli_path": None}, "token-expired", ["renew"]),
    ({"creds": creds(expires_in=-60), "rejected": True}, "login-required", ["sign-in"]),
    ({"last_error": "rate-limited"}, "unavailable", []),
    ({"last_error": "http-503"}, "unavailable", []),
])
def test_connection_states(change, state, actions):
    out = auth.connection_status(**{**BASE, **change})
    assert (out["state"], out["actions"]) == (state, actions)
    assert out["title"]


def test_signed_out_detail_explains_a_blank_or_unreadable_file():
    blank = auth.connection_status(**{**BASE, "creds": creds(token="")})
    assert "desktop app" in blank["detail"]
    unreadable = auth.connection_status(**{**BASE, "creds": None})
    assert "couldn't be read" in unreadable["detail"]


def test_unavailable_says_how_old_the_figures_are():
    out = auth.connection_status(**{**BASE, "last_error": "rate-limited",
                                    "last_ok_at": datetime(2026, 9, 29, 8, 42).timestamp()})
    assert "8:42 AM" in out["detail"]


def test_clock_adds_the_date_for_another_day():
    ts = datetime(2026, 9, 29, 3, 12).timestamp()
    assert auth._clock(ts, NOW) == "3:12 AM"
    assert auth._clock(ts, datetime(2026, 9, 30, 9, 0).timestamp()) == "Sep 29, 3:12 AM"


def test_diagnostics_never_include_tokens(isolated_credentials):
    write_credentials(isolated_credentials, token="sk-ant-oat-SECRET", refresh="sk-ant-ort-SECRET")
    status = auth.connection_status(**BASE)
    text = auth.diagnostics(status, cli_path="C:/claude.exe", auto_refresh=False, login_running=False)
    assert "SECRET" not in text
    assert "State: connected" in text and str(isolated_credentials) in text


def test_sign_in_opens_one_console_at_a_time(monkeypatch):
    FakePopen.launched = []
    monkeypatch.setattr(auth.subprocess, "Popen", FakePopen)
    assert auth.launch_login("C:/claude.exe") is True
    assert auth.launch_login("C:/claude.exe") is False
    assert FakePopen.launched == [["C:/claude.exe", "auth", "login", "--claudeai"]]
    assert auth.login_running()


def test_sign_in_can_be_retried_after_the_console_closes(monkeypatch):
    FakePopen.launched = []
    monkeypatch.setattr(auth.subprocess, "Popen", FakePopen)
    auth.launch_login("C:/claude.exe")
    auth._login_proc.returncode = 1  # closed without signing in
    assert not auth.login_running()
    assert auth.launch_login("C:/claude.exe") is True
    assert len(FakePopen.launched) == 2


def test_redact_hides_token_text():
    text = auth.redact("Invalid header value b'Bearer sk-ant-oat-ABC123' then sk-ant-ort_X-9")
    assert "ABC123" not in text and "X-9" not in text
    assert "Bearer sk-ant-" in text
