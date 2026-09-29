import asyncio
import json

import pytest

import app
import auth
from helpers import FakePopen, write_credentials


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(app, "_usage_cache", dict(app._usage_cache))


@pytest.fixture
def no_fetch(monkeypatch):
    async def fake():
        return {}
    monkeypatch.setattr(app, "_get_usage_data", fake)


def test_quota_response_carries_the_connection(no_fetch, monkeypatch):
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: None)
    out = asyncio.run(app.get_quota())
    assert out["connection"]["state"] == "not-installed"
    assert out["connection"]["install_url"] == "https://code.claude.com/docs/en/setup"


def test_connection_never_leaks_tokens(isolated_credentials, no_fetch, monkeypatch):
    write_credentials(isolated_credentials, token="sk-ant-oat-SECRET", refresh="sk-ant-ort-SECRET")
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: "C:/claude.exe")
    out = asyncio.run(app.connection())
    assert out["state"] == "connected"
    assert "SECRET" not in json.dumps(out)


def test_sign_in_endpoint_needs_the_cli(monkeypatch):
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: None)
    with pytest.raises(app.HTTPException) as err:
        app.connection_login()
    assert err.value.status_code == 409


def test_sign_in_endpoint_reports_the_open_console(monkeypatch):
    FakePopen.launched = []
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: "C:/claude.exe")
    monkeypatch.setattr(auth.subprocess, "Popen", FakePopen)
    assert app.connection_login()["login_running"] is True
    assert len(FakePopen.launched) == 1


def test_renew_endpoint_forces_one_attempt_and_refetches(no_fetch, monkeypatch):
    monkeypatch.setattr(auth, "refresh_token", lambda force=False: force)
    app._usage_cache["retry_after"] = 1e12
    out = asyncio.run(app.connection_renew())
    assert out["renewed"] is True and app._usage_cache["retry_after"] == 0.0


def test_successful_fetch_records_when(monkeypatch):
    monkeypatch.setattr(app, "_fetch_usage_sync", lambda: {
        "ok": True, "retry_after": None,
        "data": {"five_hour": {"utilization": 1.0}, "seven_day": {"utilization": 2.0}}})
    monkeypatch.setattr(app, "_maybe_write_snapshot", lambda *a: None)
    monkeypatch.setattr(app, "QUOTA_CACHE_FILE", app.paths.DATA_DIR / "__unused__" / "q.json")
    app._usage_cache.update(data=None, retry_after=0.0, last_ok_at=None)
    asyncio.run(app._get_usage_data())
    assert app._usage_cache["last_ok_at"] is not None
