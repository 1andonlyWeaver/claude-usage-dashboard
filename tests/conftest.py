"""Shared fixtures. Every test gets its own throwaway usage DB, so none can touch data/usage.db."""
import pytest

import db
import ingest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point db.py and ingest.py at a per-test DB path, even for tests that never open it."""
    path = tmp_path / "usage.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(ingest, "DB_PATH", path)
    return path


@pytest.fixture
def conn(isolated_db):
    """An open connection to the per-test DB, with the full schema."""
    c = db.get_conn()
    ingest.init_db(c)
    yield c
    c.close()
