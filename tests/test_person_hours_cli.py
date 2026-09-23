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
    out = capsys.readouterr().out
    assert "1/1" in out and "1 judged this run" in out


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


def test_cli_refreshes_the_transcript_index_each_pass(conn, tmp_path, monkeypatch):
    first = queued_session(conn, tmp_path, "s1", NOW)
    both = {**first, **queued_session(conn, tmp_path, "s2", NOW)}  # s2 queued by the worker meanwhile
    seen = iter([first])
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: next(seen, both))
    monkeypatch.setattr(ph, "MAX_PER_TICK", 1)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")
    monkeypatch.setattr(ph.subprocess, "run", FakeRun(stdout=envelope(GOOD_ESTIMATE)))
    assert ph.main([]) == 0
    status = {r["session_id"]: r["status"]
              for r in conn.execute("SELECT session_id, status FROM person_hour_estimates")}
    assert status == {"s1": "done", "s2": "done"}


def test_cli_stops_after_three_failed_calls_in_a_row(conn, tmp_path, monkeypatch, capsys):
    index = {}
    for sid in ("a", "b", "c", "d"):
        index.update(queued_session(conn, tmp_path, sid, NOW))
    run = FakeRun(stdout="Error: not logged in", returncode=1)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "MAX_PER_TICK", 1)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")
    monkeypatch.setattr(ph.subprocess, "run", run)
    assert ph.main([]) == 1
    captured = capsys.readouterr()
    assert "3 failed calls in a row" in captured.err
    assert "exit 1: Error: not logged in" in captured.err
    assert len(run.calls) == 3 and "Finished:" in captured.out


def test_cli_keeps_going_after_an_isolated_failure(conn, tmp_path, monkeypatch):
    index = {}
    for sid in ("a", "b", "c"):
        index.update(queued_session(conn, tmp_path, sid, NOW))
    replies = iter(["Error: flaky", envelope(GOOD_ESTIMATE), envelope(GOOD_ESTIMATE)])

    class Flaky(FakeRun):
        def __call__(self, cmd, **kwargs):
            self.stdout = next(replies)
            return super().__call__(cmd, **kwargs)

    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "MAX_PER_TICK", 1)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")
    monkeypatch.setattr(ph.subprocess, "run", Flaky())
    assert ph.main([]) == 0
    assert ph.queue_counts(conn)["pending"] == 1  # the failed day waits out its retry


def test_cli_rejects_a_non_positive_limit():
    with pytest.raises(SystemExit):
        ph.main(["--limit", "0"])
