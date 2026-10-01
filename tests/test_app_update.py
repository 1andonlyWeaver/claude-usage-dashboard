"""The update endpoints, the hourly tick behind the daily check, and the notice's place in the page."""
import json

import pytest
from fastapi.testclient import TestClient

import app
import updates
import version
from helpers import FakeResponse, fake_urlopen


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(version, "__version__", "2.0.0")
    return TestClient(app.app, base_url="http://127.0.0.1:8080")


def test_get_update_reports_the_last_check_without_asking_github(client, isolated_updates):
    isolated_updates.write_text(json.dumps({"checked_at": 1.0, "latest": "2.1.0", "error": None}),
                                encoding="utf-8")
    body = client.get("/api/update").json()  # the autouse guard fails the test if GitHub is asked
    assert body["current"] == "2.0.0" and body["latest"] == "2.1.0" and body["notify"]


def test_check_now_asks_github_and_returns_the_answer(client, monkeypatch):
    urlopen, calls = fake_urlopen([FakeResponse({"tag_name": "2.1.0"})])
    monkeypatch.setattr(updates, "_urlopen", urlopen)
    body = client.post("/api/update/check").json()
    assert len(calls) == 1
    assert body["available"] and body["url"].endswith("/releases/tag/2.1.0")


def test_check_now_from_another_site_is_refused(client):
    r = client.post("/api/update/check", headers={"Origin": "https://example.com"})
    assert r.status_code == 403  # and the guard means GitHub was never asked


class FakeTimer:
    started = []

    def __init__(self, interval, function):
        self.interval, self.function, self.daemon = interval, function, False

    def start(self):
        FakeTimer.started.append(self)


@pytest.fixture
def timers(monkeypatch):
    FakeTimer.started = []
    monkeypatch.setattr(app.threading, "Timer", FakeTimer)
    return FakeTimer.started


def test_the_tick_checks_only_when_due_and_always_reschedules(timers, monkeypatch):
    checks = []
    monkeypatch.setattr(app.updates, "check", lambda: checks.append("checked"))
    monkeypatch.setattr(app.updates, "due", lambda: False)
    app._update_tick()
    assert checks == []
    monkeypatch.setattr(app.updates, "due", lambda: True)
    app._update_tick()
    assert checks == ["checked"]
    assert [(t.interval, t.function, t.daemon) for t in timers] == \
        [(app.UPDATE_TICK_SECONDS, app._update_tick, True)] * 2


def test_a_failing_tick_still_reschedules(timers, monkeypatch, capsys):
    def broken():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(app.updates, "due", broken)
    app._update_tick()
    assert len(timers) == 1
    assert "tick failed - RuntimeError: disk on fire" in capsys.readouterr().out


def test_the_page_carries_the_update_notice_and_its_settings(client):
    page = client.get("/").text
    for marker in ('id="updateBanner"', 'id="updateTitle"', 'id="updateLink"',
                   'onclick="dismissUpdate()"', 'id="setUpdates"',
                   "saveSetting('update_check', this)", 'id="updateStatus"',
                   'onclick="checkForUpdates()"'):
        assert marker in page, marker
