import asyncio
import json
import time

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


def test_sign_in_endpoint_explains_a_cli_that_will_not_start(monkeypatch):
    def broken(args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: "C:/claude.exe")
    monkeypatch.setattr(auth.subprocess, "Popen", broken)
    with pytest.raises(app.HTTPException) as err:
        app.connection_login()
    assert err.value.status_code == 500
    assert "cannot find the file" in err.value.detail
    FakePopen.launched = []
    monkeypatch.setattr(auth.subprocess, "Popen", FakePopen)
    assert app.connection_login()["login_running"] is True  # the failed launch left nothing held


def test_renew_endpoint_forces_one_attempt_and_refetches(no_fetch, monkeypatch):
    monkeypatch.setattr(auth, "refresh_token", lambda force=False: force)
    app._usage_cache["retry_after"] = 1e12
    out = asyncio.run(app.connection_renew())
    assert out["renewed"] is True and app._usage_cache["retry_after"] == 0.0


class LongUptime:
    """app's view of the time module on a PC that has been up for 30 days.

    Uptime is what monotonic() counts, so this makes a cache stamped at 0.0 look 30 days old
    whatever machine runs the test. asyncio keeps the real clock.
    """

    def __getattr__(self, name):
        return getattr(time, name)

    def monotonic(self):
        return time.monotonic() + 30 * 86400


@pytest.fixture
def failing_refetch(tmp_path, monkeypatch):
    """Cached figures from 10 s ago, a long-running PC, and a usage API that can't be reached."""
    monkeypatch.setattr(app, "time", LongUptime())
    monkeypatch.setattr(app, "QUOTA_CACHE_FILE", tmp_path / "quota_cache.json")
    monkeypatch.setattr(app, "_fetch_usage_sync",
                        lambda: {"ok": False, "error": "network-error", "retry_after": None})
    app._usage_cache.update(
        data={"five_hour_pct": 40.0, "five_hour_resets_at": None,
              "seven_day_pct": 55.0, "seven_day_resets_at": None},
        fetched_at=app.time.monotonic() - 10, retry_after=0.0, error=None, fail_count=0)


def renew_now(monkeypatch):
    monkeypatch.setattr(auth, "refresh_token", lambda force=False: force)
    asyncio.run(app.connection_renew())


def reingest_now(monkeypatch):
    monkeypatch.setattr(app, "_run_ingest_background", lambda force=False: None)
    asyncio.run(app.refresh(force=False))


@pytest.mark.parametrize("trigger", [renew_now, reingest_now])
def test_a_failed_refetch_keeps_the_cached_figures(failing_refetch, monkeypatch, trigger):
    trigger(monkeypatch)
    out = asyncio.run(app._get_usage_data())
    assert (out["five_hour_pct"], out["seven_day_pct"]) == (40.0, 55.0)


def test_a_renewal_starts_the_fetch_backoff_over(failing_refetch, monkeypatch):
    app._usage_cache["fail_count"] = 5  # an hour-long backoff by now
    renew_now(monkeypatch)
    assert app._usage_cache["retry_after"] - app.time.monotonic() <= app.CACHE_MIN_RETRY


def test_successful_fetch_records_when(monkeypatch):
    monkeypatch.setattr(app, "_fetch_usage_sync", lambda: {
        "ok": True, "retry_after": None,
        "data": {"five_hour": {"utilization": 1.0}, "seven_day": {"utilization": 2.0}}})
    monkeypatch.setattr(app, "_maybe_write_snapshot", lambda *a: None)
    monkeypatch.setattr(app, "QUOTA_CACHE_FILE", app.paths.DATA_DIR / "__unused__" / "q.json")
    app._usage_cache.update(data=None, retry_after=0.0, last_ok_at=None)
    asyncio.run(app._get_usage_data())
    assert app._usage_cache["last_ok_at"] is not None
