from datetime import date, timedelta

import db
from helpers import add_message


def _day(n):
    return (date.today() - timedelta(days=n)).isoformat()


def _estimate(conn, sid, day, hours, *, scheduled=0, status="done", summary=None):
    conn.execute(
        "INSERT OR REPLACE INTO person_hour_estimates (session_id, date, status, is_scheduled,"
        " hours_low, hours_likely, hours_high, summary, role, rationale, model, last_attempt_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Engineer', 'Why.', 'claude-sonnet-5', ?)",
        (sid, day, status, scheduled, hours / 2, hours, hours * 2, summary, f"{day}T12:00:00"))
    conn.commit()


def _row(active, hours=None, scheduled=0, project="p"):
    return {"session_id": "x", "date": "2026-09-20", "project": project, "active_hours": active,
            "is_scheduled": scheduled, "hours_likely": hours}


def test_session_day_rows_caps_idle_gaps(conn):
    for ts in ("2026-09-20T10:00:00", "2026-09-20T10:02:00", "2026-09-20T10:30:00"):
        add_message(conn, "s1", ts)
    [row] = db._session_day_rows(conn, "2026-09-01")
    assert abs(row["active_hours"] - (120 + 300) / 3600) < 1e-6
    assert row["hours_likely"] is None and row["is_scheduled"] == 0 and row["project"] == "proj"


def test_leverage_falls_back_until_enough_samples():
    rows = [_row(1.0, 6.0) for _ in range(9)]
    assert db.leverage_from_rows(rows) == {"interactive": db.DEFAULT_LEVERAGE,
                                           "scheduled": db.DEFAULT_LEVERAGE}
    rows += [_row(1.0, 8.0), _row(0.05, 99.0)]  # 10th usable sample; tiny active time ignored
    assert db.leverage_from_rows(rows)["interactive"] == 6.0


def test_hours_summary_splits_scheduled_and_marks_provisional():
    leverage = {"interactive": 4.0, "scheduled": 10.0}
    rows = [_row(1.0, 6.0, project="a"), _row(0.5, None, project="b"),
            _row(0.2, 3.0, scheduled=1), _row(0.1, None, scheduled=1)]
    out = db.hours_summary(rows, leverage)
    assert out["interactive"] == {"hours": 8.0, "judged_hours": 6.0, "provisional_hours": 2.0,
                                  "session_days": 2, "provisional_days": 1}
    assert out["scheduled"] == {"hours": 4.0, "runs": 2}
    assert out["active_hours"] == 1.5 and out["leverage"] == 5.3 and out["work_weeks"] == 0.2
    assert out["by_project"] == [{"project": "a", "hours": 6.0}, {"project": "b", "hours": 2.0}]


def test_hours_summary_groups_small_projects_into_other():
    rows = [_row(1.0, h, project=p)
            for p, h in (("a", 9.0), ("b", 7.0), ("c", 5.0), ("d", 2.0), ("e", 1.0))]
    out = db.hours_summary(rows, {"interactive": 5.0, "scheduled": 5.0})
    assert out["by_project"][-1] == {"project": "other", "hours": 3.0}
    assert [p["project"] for p in out["by_project"]] == ["a", "b", "c", "other"]


def test_hours_summary_without_active_time_has_no_leverage():
    assert db.hours_summary([], {"interactive": 5.0, "scheduled": 5.0})["leverage"] is None


def test_person_hours(conn):
    for sid, n in (("s1", 1), ("s2", 2)):
        add_message(conn, sid, f"{_day(n)}T10:00:00")
        add_message(conn, sid, f"{_day(n)}T10:05:00")
    add_message(conn, "old", f"{_day(40)}T10:00:00")
    add_message(conn, "desk", f"{_day(1)}T10:00:00", source="claude-desktop")
    _estimate(conn, "s1", _day(1), 6.0)
    out = db.person_hours(30)
    assert out["days"] == 30 and out["model"] == "claude-sonnet-5"
    assert out["interactive"]["session_days"] == 2 and out["interactive"]["provisional_days"] == 1
    assert out["interactive"]["hours"] == round(6.0 + (5 / 60) * db.DEFAULT_LEVERAGE, 1)


def test_session_list_includes_person_hours(conn):
    add_message(conn, "s1", f"{_day(1)}T10:00:00")
    add_message(conn, "s1", f"{_day(1)}T10:05:00")
    add_message(conn, "s1", f"{_day(0)}T09:00:00")  # second day, not judged yet
    add_message(conn, "desk", f"{_day(1)}T10:00:00", source="claude-desktop")
    _estimate(conn, "s1", _day(1), 6.0)
    by_id = {s["session_id"]: s for s in db.session_list(7)}
    assert by_id["s1"]["person_hours"] == 6.0 and by_id["s1"]["hours_status"] == "partial"
    assert by_id["desk"]["person_hours"] is None and by_id["desk"]["hours_status"] is None


def test_session_hours_lists_each_day(conn):
    add_message(conn, "s1", f"{_day(1)}T10:00:00")
    for ts in ("09:00:00", "09:03:00", "09:06:00"):  # two 3-minute gaps: 0.1 active hours
        add_message(conn, "s1", f"{_day(0)}T{ts}")
    _estimate(conn, "s1", _day(1), 6.0, summary="Did the thing.")
    days = db.session_hours("s1")
    assert [d["date"] for d in days] == [_day(1), _day(0)]
    assert days[0]["status"] == "done" and days[0]["summary"] == "Did the thing."
    assert days[0]["hours_likely"] == 6.0 and days[0]["provisional_hours"] is None
    assert days[1]["status"] == "provisional"
    assert days[1]["provisional_hours"] == round(0.1 * db.DEFAULT_LEVERAGE, 1)
    assert db.session_hours("nope") == []
