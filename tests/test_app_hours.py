import asyncio
import json
import time

import pytest

import app
import person_hours as ph
from helpers import add_message


class FakeTimer:
    started = []

    def __init__(self, delay, fn):
        self.delay, self.daemon = delay, False

    def start(self):
        FakeTimer.started.append(self.delay)


@pytest.fixture
def timers(monkeypatch):
    FakeTimer.started = []
    monkeypatch.setattr(app.threading, "Timer", FakeTimer)
    monkeypatch.setattr(app, "_token_seconds_left", lambda: 3600.0)
    return FakeTimer.started


def test_hours_endpoint_reports_a_disabled_worker(conn, monkeypatch):
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", False)
    add_message(conn, "s1", "2026-09-20T09:00:00")
    out = asyncio.run(app.hours(30))
    assert out["worker"]["state"] == "paused" and out["worker"]["reason"] == "disabled"
    assert {"interactive", "scheduled", "by_project", "days"} <= out.keys()


def test_session_hours_endpoint(conn):
    add_message(conn, "s1", "2026-09-20T09:00:00")
    add_message(conn, "s1", "2026-09-20T09:03:00")
    out = asyncio.run(app.session_hours("s1"))
    assert [d["date"] for d in out] == ["2026-09-20"] and out[0]["status"] == "provisional"


def test_cached_five_hour_pct_uses_memory_then_disk(monkeypatch, tmp_path):
    monkeypatch.setitem(app._usage_cache, "data", {"five_hour_pct": 42.0, "five_hour_resets_at": None})
    monkeypatch.setitem(app._usage_cache, "fetched_at", time.monotonic())
    assert app._cached_five_hour_pct() == 42.0
    monkeypatch.setitem(app._usage_cache, "data", None)
    monkeypatch.setattr(app, "QUOTA_CACHE_FILE", tmp_path / "missing.json")
    assert app._cached_five_hour_pct() is None


def test_token_seconds_left(monkeypatch, tmp_path):
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr(app, "CREDENTIALS_FILE", creds)
    assert app._token_seconds_left() is None
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "t",
                                                   "expiresAt": (time.time() + 3600) * 1000}}))
    assert 3500 < app._token_seconds_left() <= 3600


def test_hours_tick_reschedules_after_a_failed_tick(timers, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(app.person_hours, "run_tick", boom)
    app._hours_tick()
    assert timers == [ph.TICK_SECONDS]
    assert not app._hours_lock.locked()


def test_hours_tick_reschedules_even_when_logging_fails(timers, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    def strict_print(*args, **kwargs):  # the launcher's stdout is a strict cp1252 file
        raise UnicodeEncodeError("charmap", "x", 0, 1, "can't encode")

    monkeypatch.setattr(app.person_hours, "run_tick", boom)
    monkeypatch.setattr("builtins.print", strict_print)
    with pytest.raises(UnicodeEncodeError):
        app._hours_tick()
    assert timers == [ph.TICK_SECONDS]
    assert not app._hours_lock.locked()
