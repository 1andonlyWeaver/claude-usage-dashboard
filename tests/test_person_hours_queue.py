from datetime import datetime

import person_hours as ph
from helpers import add_message, user, write_jsonl

NOW = datetime(2026, 9, 20, 12, 0, 0)


def _rows(conn):
    return {(r["session_id"], r["date"]): dict(r)
            for r in conn.execute("SELECT * FROM person_hour_estimates")}


def test_discover_queues_quiet_claude_code_session_days_only(conn, tmp_path):
    for sid, ts, source in (("quiet", "2026-09-20T11:00:00", "claude-code"),
                            ("busy", "2026-09-20T11:45:00", "claude-code"),     # 15 min ago
                            ("desk", "2026-09-20T09:00:00", "claude-desktop"),
                            ("old", "2026-06-01T09:00:00", "claude-code"),      # outside 90 days
                            ("gone", "2026-09-20T09:00:00", "claude-code")):    # no transcript
        add_message(conn, sid, ts, source=source)
    index = {sid: tmp_path / f"{sid}.jsonl" for sid in ("quiet", "busy", "desk", "old")}
    assert ph.discover(conn, NOW, index) == 1
    rows = _rows(conn)
    assert list(rows) == [("quiet", "2026-09-20")]
    assert rows[("quiet", "2026-09-20")]["status"] == "pending"
    assert rows[("quiet", "2026-09-20")]["is_scheduled"] == 0


def test_discover_marks_every_day_of_a_scheduled_session(conn, tmp_path):
    main = write_jsonl(tmp_path / "p" / "sched.jsonl", [
        user("2026-09-19T06:00:00", '<scheduled-task name="daily">go</scheduled-task>')])
    add_message(conn, "sched", "2026-09-19T06:01:00")
    add_message(conn, "sched", "2026-09-20T06:01:00")
    ph.discover(conn, NOW, index={"sched": main})
    assert [r["is_scheduled"] for r in _rows(conn).values()] == [1, 1]


def test_discover_requeues_judged_days_once_quiet_again(conn, tmp_path):
    index = {"s1": tmp_path / "s1.jsonl"}
    add_message(conn, "s1", "2026-09-20T09:00:00")
    ph.discover(conn, NOW, index)
    conn.execute("UPDATE person_hour_estimates SET status = 'done', hours_likely = 3,"
                 " judged_through = '2026-09-20T09:00:00'")
    conn.commit()
    ph.discover(conn, NOW, index)
    assert _rows(conn)[("s1", "2026-09-20")]["status"] == "done"
    add_message(conn, "s1", "2026-09-20T11:45:00")              # new work, only 15 min ago
    ph.discover(conn, NOW, index)
    assert _rows(conn)[("s1", "2026-09-20")]["status"] == "done"
    ph.discover(conn, datetime(2026, 9, 20, 12, 30, 0), index)  # quiet for 45 min now
    row = _rows(conn)[("s1", "2026-09-20")]
    assert row["status"] == "pending" and row["hours_likely"] == 3


def test_discover_drops_unjudged_rows_whose_messages_moved(conn, tmp_path):
    add_message(conn, "s1", "2026-09-20T09:00:00")
    ph.discover(conn, NOW, {"s1": tmp_path / "s1.jsonl"})
    conn.execute("INSERT INTO person_hour_estimates (session_id, date, status)"
                 " VALUES ('kept', '2026-09-19', 'done')")
    conn.execute("UPDATE messages SET session_id = 's2'")  # a resumed session re-ingested them
    conn.commit()
    ph.discover(conn, NOW, {})
    assert ("s1", "2026-09-20") not in _rows(conn)
    assert ("kept", "2026-09-19") in _rows(conn)   # judged rows are never dropped


def test_pending_rows_newest_first_with_hourly_retry_backoff(conn):
    conn.executemany(
        "INSERT INTO person_hour_estimates (session_id, date, status, attempts, last_attempt_at)"
        " VALUES (?, ?, ?, ?, ?)",
        [("a", "2026-09-18", "pending", 0, None), ("b", "2026-09-20", "pending", 0, None),
         ("c", "2026-09-19", "error", 2, "2026-09-20T10:30:00"),
         ("d", "2026-09-19", "error", 3, "2026-09-20T09:00:00"),
         ("e", "2026-09-19", "done", 0, "2026-09-20T09:00:00"),
         ("f", "2026-09-19", "error", 1, "2026-09-20T11:30:00"),   # failed 30 min ago
         ("g", "2026-09-19", "pending", 0, "2026-09-20T11:40:00")])   # re-queued 20 min ago
    conn.commit()
    assert [r["session_id"] for r in ph.pending_rows(conn, 10, NOW)] == ["b", "c", "a"]
    assert len(ph.pending_rows(conn, 2, NOW)) == 2
    assert ph.queue_counts(conn) == {"pending": 5, "errors": 1}


def test_calls_last_hour_counts_recent_attempts(conn):
    conn.executemany(
        "INSERT INTO person_hour_estimates (session_id, date, status, last_attempt_at)"
        " VALUES (?, '2026-09-20', 'done', ?)",
        [("a", "2026-09-20T11:30:00"), ("b", "2026-09-20T10:30:00"), ("c", None)])
    conn.commit()
    assert ph.calls_last_hour(conn, NOW) == 1
