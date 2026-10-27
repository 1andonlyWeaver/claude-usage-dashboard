"""The update endpoints, the hourly tick behind the daily check, and the notice's place in the page."""
import json
import math
import re

import pytest
from fastapi.testclient import TestClient

import app
import desktop
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
    for marker in ('id="updateBanner"', 'id="updateLive"', 'id="updateTitle"', 'id="updateLink"',
                   'onclick="dismissUpdate()"', 'id="setUpdates"',
                   "saveSetting('update_check', this)", 'id="updateStatus"',
                   'onclick="checkForUpdates()"'):
        assert marker in page, marker


def signed_release(path):
    path.write_text(json.dumps({"checked_at": 1.0, "latest": "2.1.0", "error": None,
                                "installer_size": 1000, "signed": True}), encoding="utf-8")


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setattr(updates, "SIGNING_KEY", "a key")  # installable() only asks whether one is set


def test_only_the_installed_desktop_app_can_install(client, isolated_updates, signing_key, monkeypatch):
    signed_release(isolated_updates)
    body = client.get("/api/update").json()
    assert body["installable"] and not body["can_install"]  # app.py on its own, or from source
    monkeypatch.setattr(app, "desktop_update", lambda installer: None)
    assert client.get("/api/update").json()["can_install"]


def test_install_starts_the_download_and_hands_over_to_the_desktop_app(client, isolated_updates,
                                                                       signing_key, monkeypatch):
    signed_release(isolated_updates)
    hook = object()
    started = []
    monkeypatch.setattr(app, "desktop_update", hook)

    def fake_install(hand_over):
        started.append(hand_over)
        return {**updates.status(), "install": {"state": "downloading", "version": "2.1.0", "error": None}}

    monkeypatch.setattr(updates, "install", fake_install)
    body = client.post("/api/update/install").json()
    assert started == [hook]
    assert body["install"]["state"] == "downloading" and body["can_install"]


def test_install_without_a_desktop_app_is_a_conflict(client, isolated_updates, signing_key):
    signed_release(isolated_updates)
    r = client.post("/api/update/install")
    assert r.status_code == 409 and "installed desktop app" in r.json()["detail"]


def test_install_with_nothing_to_install_is_a_conflict(client, isolated_updates, monkeypatch):
    signed_release(isolated_updates)  # but no SIGNING_KEY here
    monkeypatch.setattr(app, "desktop_update", lambda installer: pytest.fail("handed over"))
    r = client.post("/api/update/install")
    assert r.status_code == 409 and "can't be installed" in r.json()["detail"]


def test_install_from_another_site_is_refused(client, monkeypatch):
    monkeypatch.setattr(app, "desktop_update", lambda installer: pytest.fail("handed over"))
    r = client.post("/api/update/install", headers={"Origin": "https://example.com"})
    assert r.status_code == 403


def test_the_tick_clears_old_downloads_unless_an_install_is_under_way(timers, monkeypatch):
    cleared = []
    monkeypatch.setattr(app.updates, "due", lambda: False)
    monkeypatch.setattr(app.updates, "clear_downloads", lambda: cleared.append(True))
    app._update_tick()
    assert cleared == [True]
    monkeypatch.setattr(updates, "_install", {"state": "downloading", "version": "2.1.0", "error": None})
    app._update_tick()
    assert cleared == [True]


def test_the_page_carries_the_install_buttons(client):
    page = client.get("/").text
    for marker in ('id="updateInstall"', 'id="btnInstallUpdate"', 'onclick="installUpdate()"',
                   'id="updateDetail"', 'id="updateDismiss"'):
        assert marker in page, marker


def page_constant(client, name):
    """A `const NAME = 60 * 1000;` line in the served dashboard.js: the product of its numbers."""
    js = client.get("/static/dashboard.js").text
    match = re.search(rf"^const {name} = ([\d\s*]+);", js, re.MULTILINE)
    assert match, name
    return math.prod(int(n) for n in match.group(1).split("*"))


def test_an_open_window_shows_a_new_check_within_a_minute_or_so(client):
    """The server's first check comes 60 s after the app starts. A page that re-read /api/update
    only every 30 minutes kept the notice hidden that long in a window left open."""
    assert page_constant(client, "UPDATE_POLL_MS") <= 2 * 60 * 1000


def test_the_window_says_it_is_installing_before_it_closes(client):
    """The app waits longer than the page's install poll between starting Setup and quitting.
    Quitting at once closed the window before the page ever showed "Installing"."""
    assert desktop.INSTALL_QUIT_DELAY * 1000 > page_constant(client, "INSTALL_POLL_MS")
