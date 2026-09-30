"""Shared fixtures. Every test gets its own throwaway usage DB, settings file and Claude
credentials path, so none can touch data/ or ~/.claude."""
import pytest

import auth
import autostart
import db
import ingest
import instance
import person_hours
import settings
from fake_winreg import FakeWinreg


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point db.py and ingest.py at a per-test DB path, even for tests that never open it."""
    path = tmp_path / "usage.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(ingest, "DB_PATH", path)
    return path


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Settings live in a per-test file, so every test starts from the defaults."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    return path


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    """Point auth at a per-test credentials file (absent until a test writes one) and reset its state."""
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(auth, "CREDENTIALS_FILE", path)
    monkeypatch.setattr(auth, "_auth_dead", False)
    monkeypatch.setattr(auth, "_auth_dead_creds_sig", None)
    monkeypatch.setattr(auth, "_last_token_refresh_attempt", 0.0)
    monkeypatch.setattr(auth, "_last_refresh_transient", False)
    monkeypatch.setattr(auth, "_login_proc", None)
    return path


@pytest.fixture
def conn(isolated_db):
    """An open connection to the per-test DB, with the full schema."""
    c = db.get_conn()
    ingest.init_db(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def no_real_cli(monkeypatch):
    """No test may start a real process: the claude CLI would spend quota or open a sign-in window."""
    def forbidden(*args, **kwargs):
        pytest.fail("a test tried to run a real subprocess")
    monkeypatch.setattr(person_hours.subprocess, "run", forbidden)
    monkeypatch.setattr(person_hours.subprocess, "Popen", forbidden)


@pytest.fixture(autouse=True)
def no_wsl(monkeypatch):
    """Tests run as on a PC without WSL: none asks wsl.exe or reads a real distro, which would start it."""
    monkeypatch.setattr(ingest, "_wsl_exe", lambda: None, raising=False)
    monkeypatch.setattr(ingest, "_wsl_checked_at", None, raising=False)
    monkeypatch.setattr(ingest, "_wsl_running", [], raising=False)


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch):
    """No test reads or writes the real registry: autostart gets an in-memory winreg."""
    registry = FakeWinreg()
    monkeypatch.setattr(autostart, "winreg", registry)
    return registry


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """runtime.json goes to a per-test file, so no test can point a real second launch at a test server."""
    path = tmp_path / "runtime.json"
    monkeypatch.setattr(instance, "RUNTIME_FILE", path)
    return path
