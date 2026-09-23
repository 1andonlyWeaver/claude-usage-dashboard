from datetime import datetime

import pytest

import person_hours as ph
from helpers import GOOD_ESTIMATE, FakeRun, add_message, envelope, queued_session

NOW = datetime(2026, 9, 20, 12, 0, 0)
GATES = {"auth_dead": False, "ingest_running": False, "five_hour_pct": 10.0,
         "token_seconds_left": 3600}
BASE = {**GATES, "cli_found": True, "calls_last_hour": 0}


@pytest.fixture(autouse=True)
def reset_worker():
    ph._worker.update(reason=None, last_tick=None)
    yield
    ph._worker.update(reason=None, last_tick=None)


@pytest.mark.parametrize("change, reason", [
    ({}, None),
    ({"cli_found": False, "auth_dead": True}, "unavailable"),
    ({"auth_dead": True, "ingest_running": True}, "auth"),
    ({"five_hour_pct": 95, "token_seconds_left": None}, "auth"),
    ({"ingest_running": True, "five_hour_pct": 95}, "ingest"),
    ({"five_hour_pct": 80}, "quota"),
    ({"five_hour_pct": None}, None),
    ({"token_seconds_left": None}, "auth"),
    ({"token_seconds_left": 599}, "token"),
    ({"calls_last_hour": 20}, "rate"),
])
def test_skip_reason(change, reason):
    assert ph.skip_reason(**{**BASE, **change}) == reason


def test_worker_status_states():
    ph.set_worker_reason("quota", NOW)
    assert ph.worker_status({"pending": 4, "errors": 0}) == {
        "state": "paused", "reason": "quota", "pending": 4, "errors": 0}
    ph.set_worker_reason("unavailable", NOW)
    assert ph.worker_status({"pending": 4, "errors": 0})["state"] == "unavailable"
    ph.set_worker_reason("rate", NOW)
    assert ph.worker_status({"pending": 4, "errors": 1}) == {
        "state": "running", "reason": None, "pending": 4, "errors": 1}
    ph.set_worker_reason(None, NOW)
    assert ph.worker_status({"pending": 0, "errors": 0})["state"] == "idle"


def test_run_tick_judges_within_the_hourly_budget(conn, tmp_path, monkeypatch):
    index = {}
    for sid in ("a", "b", "c"):
        index.update(queued_session(conn, tmp_path, sid, NOW))
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "MAX_CALLS_PER_HOUR", 2)
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    assert ph.run_tick(NOW, GATES, runner=run, now_fn=lambda: NOW) == {"done": 2}
    assert ph.run_tick(NOW, GATES, runner=run, now_fn=lambda: NOW) == {"skipped": "rate"}


def test_run_tick_does_nothing_while_ingesting(monkeypatch):
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: pytest.fail("scanned during ingest"))
    assert ph.run_tick(NOW, {**GATES, "ingest_running": True}) == {"skipped": "ingest"}
    assert ph._worker["reason"] == "ingest"


def test_run_tick_still_queues_when_paused(conn, tmp_path, monkeypatch):
    add_message(conn, "s1", "2026-09-20T09:00:00")
    monkeypatch.setattr(ph, "find_claude_cli", lambda: None)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: {"s1": tmp_path / "s1.jsonl"})
    assert ph.run_tick(NOW, GATES) == {"skipped": "unavailable"}
    assert ph.queue_counts(conn)["pending"] == 1


def test_worker_status_treats_unknown_reasons_as_paused():
    ph.set_worker_reason("something-new", NOW)
    assert ph.worker_status({"pending": 1, "errors": 0})["state"] == "paused"


def test_token_threshold_covers_a_whole_tick_of_calls():
    assert ph.TOKEN_MIN_SECONDS >= (ph.MAX_PER_TICK // ph.MAX_CONCURRENCY) * ph.CALL_TIMEOUT_S


def test_judge_pending_rechecks_the_hourly_cap_when_claiming(conn, tmp_path, monkeypatch):
    index = {}
    for sid in ("a", "b", "c"):
        index.update(queued_session(conn, tmp_path, sid, NOW))
    monkeypatch.setattr(ph, "MAX_CALLS_PER_HOUR", 2)
    conn.execute("INSERT INTO person_hour_estimates (session_id, date, status, last_attempt_at)"
                 " VALUES ('other', '2026-09-19', 'done', '2026-09-20T11:50:00')")  # another process
    conn.commit()
    out = ph.judge_pending(5, "claude", index, runner=FakeRun(stdout=envelope(GOOD_ESTIMATE)),
                           now_fn=lambda: NOW)
    assert out == {"done": 1}
