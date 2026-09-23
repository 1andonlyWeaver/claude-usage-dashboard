from helpers import add_message


def test_conn_fixture_creates_schema(conn):
    add_message(conn, "s1", "2026-09-20T10:00:00")
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
