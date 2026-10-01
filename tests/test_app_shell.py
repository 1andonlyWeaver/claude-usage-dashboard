"""The endpoints the desktop shell uses: which program is on this port, and bring its window forward."""
import os

from fastapi.testclient import TestClient

import app
import paths
import version


def client():
    return TestClient(app.app, base_url="http://127.0.0.1:8765")


def test_info_names_the_app_and_says_whether_a_window_is_attached(monkeypatch):
    info = client().get("/api/app/info").json()
    assert info == {"name": "ClaudeUsageDashboard", "version": version.__version__, "desktop": False,
                    "pid": os.getpid(), "data_dir": str(paths.DATA_DIR), "log_dir": str(paths.LOG_DIR)}
    monkeypatch.setattr(app, "desktop_show", lambda: None)
    assert client().get("/api/app/info").json()["desktop"] is True


def test_show_brings_the_window_forward(monkeypatch):
    shown = []
    monkeypatch.setattr(app, "desktop_show", lambda: shown.append(True))
    response = client().post("/api/app/show")
    assert response.status_code == 200
    assert response.json() == {"shown": True}
    assert shown == [True]


def test_show_without_a_desktop_window_is_a_conflict():
    response = client().post("/api/app/show")
    assert response.status_code == 409
    assert "on its own" in response.json()["detail"]


def test_a_web_page_cannot_raise_the_window(monkeypatch):
    shown = []
    monkeypatch.setattr(app, "desktop_show", lambda: shown.append(True))
    response = client().post("/api/app/show", headers={"Origin": "https://example.com"})
    assert response.status_code == 403
    assert shown == []
