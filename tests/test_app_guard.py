import pytest
from fastapi.testclient import TestClient

import app
import settings


@pytest.fixture
def client():
    return TestClient(app.app, base_url="http://127.0.0.1:8080")


@pytest.mark.parametrize("method, host, origin, blocked", [
    ("GET", "127.0.0.1:8080", None, False),
    ("GET", "localhost:8080", None, False),
    ("GET", "[::1]:8080", None, False),
    ("GET", "127.0.0.1", None, False),
    ("GET", "evil.example:8080", None, True),
    ("GET", "127.0.0.1.evil.example:8080", None, True),
    ("GET", None, None, True),
    ("POST", "127.0.0.1:8080", None, False),                        # curl, scripts
    ("POST", "127.0.0.1:8080", "http://127.0.0.1:8080", False),     # the dashboard itself
    ("POST", "localhost:8080", "http://localhost:8080", False),
    ("POST", "127.0.0.1:8080", "https://evil.example", True),
    ("POST", "127.0.0.1:8080", "null", True),
    ("POST", "127.0.0.1:8080", "http://127.0.0.1:9999", True),
    ("GET", "127.0.0.1:8080", "https://evil.example", False),       # reads are safe: no CORS
])
def test_blocked(method, host, origin, blocked):
    assert (app._blocked(method, host, origin) is not None) is blocked


def test_foreign_host_gets_403(client):
    assert client.get("/api/settings", headers={"host": "evil.example:8080"}).status_code == 403


def test_cross_origin_post_is_refused_and_changes_nothing(client):
    r = client.post("/api/settings", json={"judge_enabled": True},
                    headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert settings.load()["judge_enabled"] is False


def test_same_origin_and_originless_posts_are_served(client):
    r = client.post("/api/settings", json={"auto_refresh_token": True},
                    headers={"origin": "http://127.0.0.1:8080"})
    assert r.status_code == 200 and r.json()["auto_refresh_token"] is True
    assert client.post("/api/settings", json={"auto_refresh_token": False}).status_code == 200
