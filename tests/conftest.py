"""Shared fixtures: a throwaway usage DB with the full schema."""
import pytest

import db
import ingest


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """An empty usage DB with the full schema; db.py and ingest.py both point at it."""
    path = tmp_path / "usage.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(ingest, "DB_PATH", path)
    c = db.get_conn()
    ingest.init_db(c)
    yield c
    c.close()
