from datetime import datetime

import person_hours as ph
from helpers import add_message, user, write_jsonl

NOW = datetime(2026, 9, 20, 12, 0, 0)


def _rows(conn):
    return {(r["session_id"], r["date"]): dict(r)
            for r in conn.execute("SELECT * FROM person_hour_estimates")}


def test_discover_queues_quiet_claude_code_session_days_only(conn):
    add_message(conn, "quiet", "2026-09-20T11:00:00")
    add_message(conn, "busy", "2026-09-20T11:45:00")                      # 15 min ago
    add_message(conn, "desk", "2026-09-20T09:00:00", source="claude-desktop")
    add_message(conn, "old", "2026-06-01T09:00:00")                       # outside 90 days
    assert ph.discover(conn, NOW, index={}) == 1
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


def test_discover_requeues_judged_days_with_new_messages(conn):
    add_message(conn, "s1", "2026-09-20T09:00:00")
    ph.discover(conn, NOW, index={})
    conn.execute("UPDATE person_hour_estimates SET status = 'done', hours_likely = 3,"
                 " judged_through = '2026-09-20T09:00:00'")
    conn.commit()
    ph.discover(conn, NOW, index={})
    assert _rows(conn)[("s1", "2026-09-20")]["status"] == "done"
    add_message(conn, "s1", "2026-09-20T10:00:00")
    ph.discover(conn, NOW, index={})
    row = _rows(conn)[("s1", "2026-09-20")]
    assert row["status"] == "pending" and row["hours_likely"] == 3


def test_pending_rows_newest_first_with_limited_retries(conn):
    conn.executemany(
        "INSERT INTO person_hour_estimates (session_id, date, status, attempts) VALUES (?, ?, ?, ?)",
        [("a", "2026-09-18", "pending", 0), ("b", "2026-09-20", "pending", 0),
         ("c", "2026-09-19", "error", 2), ("d", "2026-09-19", "error", 3),
         ("e", "2026-09-19", "done", 0)])
    conn.commit()
    assert [r["session_id"] for r in ph.pending_rows(conn, 10)] == ["b", "c", "a"]
    assert len(ph.pending_rows(conn, 2)) == 2
    assert ph.queue_counts(conn) == {"pending": 3, "errors": 1}


def test_calls_last_hour_counts_recent_attempts(conn):
    conn.executemany(
        "INSERT INTO person_hour_estimates (session_id, date, status, last_attempt_at)"
        " VALUES (?, '2026-09-20', 'done', ?)",
        [("a", "2026-09-20T11:30:00"), ("b", "2026-09-20T10:30:00"), ("c", None)])
    conn.commit()
    assert ph.calls_last_hour(conn, NOW) == 1
