from datetime import datetime

import person_hours as ph
from helpers import (GOOD_ESTIMATE, FakeRun, add_message, envelope, queued_session, user,
                     write_jsonl)

NOW = datetime(2026, 9, 20, 12, 0, 0)


def _row(conn, sid, date="2026-09-20"):
    return dict(conn.execute("SELECT * FROM person_hour_estimates WHERE session_id = ? AND date = ?",
                             (sid, date)).fetchone())


def _judge(index, runner, sid="s1", date="2026-09-20"):
    return ph.judge_session_day(sid, date, index, "claude", runner=runner, now_fn=lambda: NOW)


def test_judge_session_day_stores_the_estimate(conn, tmp_path):
    index = queued_session(conn, tmp_path, "s1", NOW)
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    assert _judge(index, run) == "done"
    row = _row(conn, "s1")
    assert row["status"] == "done" and row["hours_likely"] == 3.0
    assert row["summary"] == "Fixed the duplicate month bug." and row["role"] == "Software engineer"
    assert row["judged_through"] == "2026-09-20T09:05:00"
    assert row["model"] == "claude-sonnet-5" and row["prompt_version"] == ph.PROMPT_VERSION
    assert row["attempts"] == 0 and row["last_attempt_at"] == "2026-09-20T12:00:00"
    assert row["judge_in_tokens"] == 4600 and row["judge_cost_usd"] == 0.05
    assert "Project: Projects / www" in run.calls[0][1]["input"]


def test_judge_sends_previous_day_summary(conn, tmp_path):
    main = write_jsonl(tmp_path / "p" / "s1.jsonl", [
        user("2026-09-19T09:00:00", "Start"), user("2026-09-20T09:00:00", "Continue")])
    add_message(conn, "s1", "2026-09-19T09:05:00")
    add_message(conn, "s1", "2026-09-20T09:05:00")
    ph.discover(conn, NOW, index={"s1": main})
    conn.execute("UPDATE person_hour_estimates SET status = 'done', summary = 'Set up the branch.'"
                 " WHERE date = '2026-09-19'")
    conn.commit()
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    _judge({"s1": main}, run)
    sent = run.calls[0][1]["input"]
    assert "day 2 of a longer session" in sent
    assert "Earlier in this session: Set up the branch." in sent


def test_failures_count_attempts_and_stop_after_three(conn, tmp_path):
    index = queued_session(conn, tmp_path, "s1", NOW)
    run = FakeRun(stdout=envelope("no estimate here"))
    for n in (1, 2, 3):
        assert _judge(index, run) == "error"
        assert _row(conn, "s1")["attempts"] == n
    assert ph.pending_rows(conn, 10) == []
    row = _row(conn, "s1")
    assert row["status"] == "error" and row["error"] == "no JSON object in response"


def test_missing_transcript_fails_for_good_without_a_call(conn, tmp_path):
    queued_session(conn, tmp_path, "gone", NOW)  # queued while its transcript still existed
    run = FakeRun()
    assert _judge({}, run, sid="gone") == "skipped"
    row = _row(conn, "gone")
    assert row["error"] == "no_source" and row["attempts"] == ph.MAX_ATTEMPTS
    assert row["last_attempt_at"] is None and run.calls == []


def test_success_after_failure_resets_attempts(conn, tmp_path):
    index = queued_session(conn, tmp_path, "s1", NOW)
    _judge(index, FakeRun(stdout="junk"))
    assert _row(conn, "s1")["error"] == "exit 0: junk"
    _judge(index, FakeRun(stdout=envelope(GOOD_ESTIMATE)))
    row = _row(conn, "s1")
    assert row["status"] == "done" and row["attempts"] == 0 and row["error"] is None


def test_judge_pending_processes_up_to_limit(conn, tmp_path):
    index = {}
    for sid in ("a", "b", "c"):
        index.update(queued_session(conn, tmp_path, sid, NOW))
    out = ph.judge_pending(2, "claude", index, runner=FakeRun(stdout=envelope(GOOD_ESTIMATE)),
                           now_fn=lambda: NOW)
    assert out == {"done": 2}
    assert ph.queue_counts(conn)["pending"] == 1
    assert ph.judge_pending(0, "claude", index) == {}


def test_judge_pending_records_unexpected_crashes(conn, tmp_path, monkeypatch):
    index = queued_session(conn, tmp_path, "s1", NOW)

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ph, "summarize_day", boom)
    assert ph.judge_pending(5, "claude", index, runner=FakeRun(), now_fn=lambda: NOW) == {"error": 1}
    row = _row(conn, "s1")
    assert row["status"] == "error" and row["attempts"] == 1
    assert row["error"] == "crash: disk on fire" and row["last_attempt_at"] == "2026-09-20T12:00:00"


def test_judge_pending_claims_rows_before_calling(conn, tmp_path):
    index = {}
    for sid in ("a", "b"):
        index.update(queued_session(conn, tmp_path, sid, NOW))
    seen = []

    class Watching(FakeRun):
        def __call__(self, cmd, **kwargs):
            c = ph.db.get_conn()
            seen.append((ph.calls_last_hour(c, NOW), len(ph.pending_rows(c, 10, NOW))))
            c.close()
            return super().__call__(cmd, **kwargs)

    ph.judge_pending(2, "claude", index, runner=Watching(stdout=envelope(GOOD_ESTIMATE)),
                     now_fn=lambda: NOW)
    # While any call runs, both rows already count toward the cap and nobody else can take them.
    assert len(seen) == 2 and all(calls == 2 and waiting == 0 for calls, waiting in seen)


def test_day_with_no_transcript_events_fails_for_good(conn, tmp_path):
    index = queued_session(conn, tmp_path, "s1", NOW)
    add_message(conn, "s1", "2026-09-19T08:00:00")  # the DB has a day the transcript lacks
    ph.discover(conn, NOW, index)
    assert _judge(index, FakeRun(), date="2026-09-19") == "skipped"
    assert _row(conn, "s1", "2026-09-19")["error"] == "no_events"


def test_day_whose_messages_moved_is_not_judged(conn, tmp_path):
    index = queued_session(conn, tmp_path, "s1", NOW)
    conn.execute("UPDATE messages SET session_id = 's2'")  # a resumed session re-ingested them
    conn.commit()
    run = FakeRun()
    assert _judge(index, run) == "skipped"
    assert _row(conn, "s1")["error"] == "no_messages" and run.calls == []
