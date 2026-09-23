EXPECTED_COLUMNS = {
    "session_id", "date", "status", "is_scheduled", "hours_low", "hours_likely", "hours_high",
    "summary", "role", "rationale", "judged_through", "model", "prompt_version", "attempts",
    "error", "last_attempt_at", "judge_in_tokens", "judge_out_tokens", "judge_cost_usd",
}


def test_init_db_creates_person_hour_estimates(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(person_hour_estimates)")}
    assert cols == EXPECTED_COLUMNS


def test_primary_key_is_session_and_date(conn):
    info = conn.execute("PRAGMA table_info(person_hour_estimates)").fetchall()
    pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
    assert pk == ["session_id", "date"]
