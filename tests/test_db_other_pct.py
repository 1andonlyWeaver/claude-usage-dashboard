import db


def test_judge_calls_count_as_local_activity(conn):
    conn.executemany("INSERT INTO quota_snapshots VALUES (?, ?, ?)", [
        ("2026-09-20T10:00:00", 10, 5), ("2026-09-20T10:05:00", 12, 5),
        ("2026-09-20T10:10:00", 15, 5)])
    conn.execute("INSERT INTO person_hour_estimates (session_id, date, status, last_attempt_at)"
                 " VALUES ('s', '2026-09-20', 'done', '2026-09-20T10:07:00')")
    conn.commit()
    out = db.detect_other_pct("2026-09-20T09:00:00", "2026-09-20T11:00:00", "5h")
    # 10:00-10:05 (+2) had no local activity; 10:05-10:10 (+3) had a judge call.
    assert out == {"other_pct": 2.0, "has_snapshots": True}
