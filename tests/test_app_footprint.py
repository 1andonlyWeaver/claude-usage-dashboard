from datetime import date, timedelta

from fastapi.testclient import TestClient

import app
from helpers import add_message


def client():
    return TestClient(app.app, base_url="http://127.0.0.1:8080")


def _yesterday():
    return f"{(date.today() - timedelta(days=1)).isoformat()}T10:00:00"


def test_footprint_endpoint(conn):
    add_message(conn, "s1", _yesterday(), cache_read_tokens=100_000, output_tokens=2000)
    res = client().get("/api/footprint?days=7")
    assert res.status_code == 200
    data = res.json()
    assert data["days"] == 7
    assert set(data["energy_wh"]) == {"low", "central", "high"}
    assert data["energy_wh"]["central"] > 0
    assert set(data["comparisons"]) == {"home_days", "showers", "shower_minutes", "car_miles"}
    assert [r["model"] for r in data["breakdown"]] == ["claude-sonnet-5"]


def test_session_footprint_endpoint(conn):
    add_message(conn, "s1", _yesterday(), output_tokens=1000)
    res = client().get("/api/session/s1/footprint")
    assert res.status_code == 200
    assert res.json()["energy_wh"]["central"] > 0
    assert client().get("/api/session/nope/footprint").status_code == 404
