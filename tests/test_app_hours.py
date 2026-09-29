import time

import pytest

import app
import person_hours as ph
import settings
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
    monkeypatch.setattr(app.auth, "token_seconds_left", lambda: 3600.0)
    return FakeTimer.started


def test_hours_endpoint_reports_a_disabled_worker(conn, monkeypatch):
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", False)
    add_message(conn, "s1", "2026-09-20T09:00:00")
    out = app.hours(30)
    assert out["worker"]["state"] == "paused" and out["worker"]["reason"] == "disabled"
    assert {"interactive", "scheduled", "by_project", "days"} <= out.keys()


def test_session_hours_endpoint(conn):
    add_message(conn, "s1", "2026-09-20T09:00:00")
    add_message(conn, "s1", "2026-09-20T09:03:00")
    out = app.session_hours("s1")
    assert [d["date"] for d in out] == ["2026-09-20"] and out[0]["status"] == "provisional"


def test_cached_five_hour_pct_uses_memory_then_disk(monkeypatch, tmp_path):
    monkeypatch.setitem(app._usage_cache, "data", {"five_hour_pct": 42.0, "five_hour_resets_at": None})
    monkeypatch.setitem(app._usage_cache, "fetched_at", time.monotonic())
    assert app._cached_five_hour_pct() == 42.0
    monkeypatch.setitem(app._usage_cache, "data", None)
    monkeypatch.setattr(app, "QUOTA_CACHE_FILE", tmp_path / "missing.json")
    assert app._cached_five_hour_pct() is None


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


def test_hours_pass_renews_a_token_that_would_expire_mid_tick_when_allowed(timers, monkeypatch):
    settings.update({"auto_refresh_token": True})
    lefts = iter([100.0, 30000.0])
    refreshed, seen = [], []
    monkeypatch.setattr(app.auth, "token_seconds_left", lambda: next(lefts))
    monkeypatch.setattr(app.auth, "refresh_token", lambda: refreshed.append(1) or True)
    monkeypatch.setattr(app, "_cached_five_hour_pct", lambda: 10.0)
    monkeypatch.setattr(app.person_hours, "run_tick", lambda now, gates: seen.append(gates))
    app._hours_tick()
    assert refreshed == [1] and seen[0]["token_seconds_left"] == 30000.0


def test_hours_tick_passes_whether_the_credentials_were_refused(timers, monkeypatch):
    seen = []
    monkeypatch.setattr(app.auth, "rejected", lambda: True)
    monkeypatch.setattr(app, "_cached_five_hour_pct", lambda: 10.0)
    monkeypatch.setattr(app.person_hours, "run_tick", lambda now, gates: seen.append(gates))
    app._hours_tick()
    assert seen[0]["auth_dead"] is True


def test_hours_pass_leaves_the_token_alone_in_read_only_mode(timers, monkeypatch):
    seen = []
    monkeypatch.setattr(app.auth, "token_seconds_left", lambda: 100.0)
    monkeypatch.setattr(app.auth, "refresh_token", lambda force=False: pytest.fail("renewed"))
    monkeypatch.setattr(app, "_cached_five_hour_pct", lambda: 10.0)
    monkeypatch.setattr(app.person_hours, "run_tick", lambda now, gates: seen.append(gates))
    app._hours_tick()
    assert seen[0]["token_seconds_left"] == 100.0


def test_hours_pass_passes_the_judge_setting(timers, monkeypatch):
    seen = []
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", True)
    monkeypatch.setattr(app, "_cached_five_hour_pct", lambda: 10.0)
    monkeypatch.setattr(app.person_hours, "run_tick", lambda now, gates: seen.append(gates))
    app._hours_pass()
    settings.update({"judge_enabled": True})
    app._hours_pass()
    assert [g["enabled"] for g in seen] == [False, True]


def test_hours_endpoint_reports_disabled_until_opted_in(conn, monkeypatch):
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", True)
    monkeypatch.setitem(ph._worker, "reason", None)
    assert app.hours(30)["worker"]["reason"] == "disabled"
    settings.update({"judge_enabled": True})
    assert app.hours(30)["worker"]["state"] == "idle"
