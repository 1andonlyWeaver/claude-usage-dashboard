from datetime import datetime

import pytest

import person_hours as ph
from helpers import GOOD_ESTIMATE, FakeRun, envelope, queued_session

NOW = datetime(2026, 9, 20, 12, 0, 0)


def _now_str():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def test_dry_run_prints_summaries_without_calling(conn, tmp_path, monkeypatch, capsys):
    index = queued_session(conn, tmp_path, "s1", NOW)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: pytest.fail("dry run looked for the CLI"))
    assert ph.main(["--dry-run", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "=== s1 2026-09-20 ===" in out and "Fix the calendar" in out
    assert ph.queue_counts(conn)["pending"] == 1


def test_cli_judges_pending_session_days(conn, tmp_path, monkeypatch, capsys):
    index = queued_session(conn, tmp_path, "s1", NOW)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")
    monkeypatch.setattr(ph.subprocess, "run", FakeRun(stdout=envelope(GOOD_ESTIMATE)))
    assert ph.main(["--limit", "1"]) == 0
    assert ph.queue_counts(conn) == {"pending": 0, "errors": 0}
    assert "1/1" in capsys.readouterr().out


def test_cli_without_claude_exits_1(conn, tmp_path, monkeypatch):
    index = queued_session(conn, tmp_path, "s1", NOW)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: None)
    assert ph.main([]) == 1


def test_cli_stops_when_the_rest_are_waiting_out_a_retry(conn, tmp_path, monkeypatch):
    index = queued_session(conn, tmp_path, "s1", NOW)
    conn.execute("UPDATE person_hour_estimates SET status = 'error', attempts = 1,"
                 " last_attempt_at = ?", (_now_str(),))
    conn.commit()
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")
    monkeypatch.setattr(ph.time, "sleep", lambda s: pytest.fail("waited for nothing"))
    assert ph.main([]) == 0


def test_cli_waits_when_the_hourly_cap_is_reached(conn, tmp_path, monkeypatch):
    index = queued_session(conn, tmp_path, "s1", NOW)
    conn.execute("INSERT INTO person_hour_estimates (session_id, date, status, last_attempt_at)"
                 " VALUES ('other', '2026-09-19', 'done', ?)", (_now_str(),))
    conn.commit()
    monkeypatch.setattr(ph, "MAX_CALLS_PER_HOUR", 1)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")

    class Waited(Exception):
        pass

    def fake_sleep(seconds):
        raise Waited

    monkeypatch.setattr(ph.time, "sleep", fake_sleep)
    with pytest.raises(Waited):
        ph.main([])
