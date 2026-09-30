"""The server's startup work runs through FastAPI's lifespan hook, not the deprecated on_event."""
from fastapi.testclient import TestClient

import app


def test_startup_is_not_registered_as_a_deprecated_event_handler():
    assert not getattr(app.app.router, "on_startup", [])


def test_starting_the_server_runs_startup_once(monkeypatch):
    calls = []

    async def fake_startup():
        calls.append("startup")

    monkeypatch.setattr(app, "startup", fake_startup)
    with TestClient(app.app, base_url="http://127.0.0.1:8080") as client:
        assert calls == ["startup"]
        assert client.get("/api/settings").status_code == 200
    assert calls == ["startup"]
