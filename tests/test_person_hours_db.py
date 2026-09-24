import sqlite3

import pytest

import person_hours as ph
from helpers import add_message


def test_db_helper_releases_its_write_lock_even_on_error(conn):
    add_message(conn, "s1", "2026-09-20T09:00:00")
    add_message(conn, "s1", "2026-09-20T09:01:00")
    with pytest.raises(RuntimeError):
        with ph._db() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO person_hour_estimates (session_id, date, status)"
                      " VALUES ('x', '2026-09-20', 'pending')")
            c.execute("SELECT * FROM messages").fetchone()  # leave a cursor half-read
            raise RuntimeError("boom")
    other = sqlite3.connect(ph.db.DB_PATH, timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")  # raises "database is locked" if the lock leaked
        other.rollback()
    finally:
        other.close()
    assert conn.execute("SELECT COUNT(*) FROM person_hour_estimates").fetchone()[0] == 0


def test_db_helper_waits_out_other_writers():
    with ph._db() as c:
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
