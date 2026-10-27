"""The tray icon: its image, its menu, and when it badges or notifies."""
import json

import pytest
import pystray

import autostart
import tray

URL = "http://127.0.0.1:8765/"
CONNECTED = {"state": "connected", "title": "Connected to Claude.", "detail": ""}
EXPIRED = {"state": "token-expired", "title": "Your sign-in token expired at 3:12 AM.",
           "detail": "It renews the next time you use Claude Code. Quota figures are paused until then."}
UNAVAILABLE = {"state": "unavailable", "title": "Couldn't reach Anthropic for quota figures.",
               "detail": "Retrying automatically."}
SIGNED_OUT = {"state": "signed-out", "title": "Not signed in to Claude Code.",
              "detail": "Sign in to Claude Code to see your quota."}
LOGIN = {"state": "login-required", "title": "Your Claude sign-in has expired.",
         "detail": "Sign in again to bring back the quota gauges."}
RELEASE = "https://github.com/1andonlyWeaver/claude-usage-dashboard/releases/tag/2.1.0"


class FakeIcon:
    def __init__(self):
        self.icon = None
        self.title = tray.TITLE
        self.visible = False
        self.notes = []
        self.menu_updates = 0
        self.stopped = False
        self.ran = False

    def run(self, setup=None):
        self.ran = True
        if setup is not None:
            setup(self)

    def notify(self, message, title=None):
        self.notes.append((message, title))

    def update_menu(self):
        self.menu_updates += 1

    def stop(self):
        self.stopped = True


class FakeShell:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def quit(self):
        self.calls.append("quit")


def make_tray(monkeypatch, answers):
    """A tray whose /api/connection answers come from `answers`: a status dict, raw bytes,
    or an exception to raise."""
    answers = iter(answers)

    def fake_call(port, path, method="GET", timeout=5.0):
        assert (port, path, method) == (8765, "/api/connection", "GET")
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()

    monkeypatch.setattr(tray.instance, "call", fake_call)
    return tray.Tray(FakeShell(), URL, 8765, icon=FakeIcon())


def test_the_badge_is_the_only_difference_between_the_two_images():
    plain, badged = tray.icon_image(False), tray.icon_image(True)
    assert plain.size == badged.size == (64, 64)
    assert plain.mode == badged.mode == "RGBA"
    assert tray.TILE in plain.get_flattened_data()
    assert tray.BADGE not in plain.get_flattened_data()
    assert tray.BADGE in badged.get_flattened_data()


def test_one_notification_per_problem_and_the_badge_follows_the_state(monkeypatch):
    t = make_tray(monkeypatch, [CONNECTED, SIGNED_OUT, SIGNED_OUT, LOGIN, CONNECTED])
    t.poll_once()
    assert t.icon.notes == [] and t.icon.title == tray.TITLE
    t.poll_once()
    assert t.icon.notes == [("Not signed in to Claude Code. Sign in to Claude Code to see your quota.",
                             tray.TITLE)]
    assert t.icon.title == "Claude Usage Dashboard: Not signed in to Claude Code."
    assert tray.BADGE in t.icon.icon.get_flattened_data()
    t.poll_once()
    assert len(t.icon.notes) == 1
    t.poll_once()  # a different problem gets its own notification
    assert len(t.icon.notes) == 2
    assert t.icon.notes[-1][0] == "Your Claude sign-in has expired. Sign in again to bring back the quota gauges."
    t.poll_once()
    assert len(t.icon.notes) == 2
    assert t.icon.title == tray.TITLE
    assert tray.BADGE not in t.icon.icon.get_flattened_data()


def test_starting_in_an_alert_state_notifies_once(monkeypatch):
    t = make_tray(monkeypatch, [SIGNED_OUT, SIGNED_OUT])
    t.poll_once()
    t.poll_once()
    assert len(t.icon.notes) == 1


def test_an_expired_token_or_an_outage_needs_nobody(monkeypatch):
    t = make_tray(monkeypatch, [EXPIRED, UNAVAILABLE])
    t.poll_once()
    t.poll_once()
    assert t.icon.notes == []
    assert t.icon.title == tray.TITLE
    assert not t.alerts.attention


def test_an_unreachable_or_garbled_server_changes_nothing(monkeypatch):
    t = make_tray(monkeypatch, [SIGNED_OUT, ConnectionRefusedError(10061, "refused"), b"<html>", b"[]",
                                SIGNED_OUT])
    for _ in range(5):
        t.poll_once()
    assert len(t.icon.notes) == 1
    assert t.alerts.state == "signed-out"


def test_menu_items_and_their_actions(monkeypatch):
    t = make_tray(monkeypatch, [])
    menu = t.menu()
    assert [item.text for item in menu.items if item.visible] == [
        "Open dashboard", "Open in browser", "Start at login", "Check for updates",
        pystray.Menu.SEPARATOR.text, "Quit"]
    assert menu.items[0].default
    items = {item.text: item for item in menu.items}
    opened = []
    monkeypatch.setattr(tray.webbrowser, "open", opened.append)
    items["Open dashboard"](t.icon)
    items["Open in browser"](t.icon)
    items["Quit"](t.icon)
    assert t.shell.calls == ["show", "quit"]
    assert opened == [URL]


def test_start_at_login_toggles_and_reads_back(monkeypatch):
    t = make_tray(monkeypatch, [])
    item = {i.text: i for i in t.menu().items}["Start at login"]
    assert item.checked is False
    item(t.icon)
    assert autostart.is_enabled() and item.checked is True
    item(t.icon)
    assert not autostart.is_enabled() and item.checked is False
    assert t.icon.menu_updates == 2


def test_a_registry_refusal_is_reported_not_raised(monkeypatch):
    t = make_tray(monkeypatch, [])

    def denied():
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(tray.autostart, "enable", denied)
    t.toggle_autostart()
    assert t.icon.notes == [("Couldn't change Start at login: Access is denied", tray.TITLE)]
    assert t.icon.menu_updates == 1


def test_stop_ends_the_polls_and_removes_the_icon(monkeypatch):
    t = make_tray(monkeypatch, [])
    t.stop()
    assert t.icon.stopped
    t._run_polls(t.icon)  # returns at once instead of polling every 30 s
    assert t.icon.visible


def test_start_runs_the_icon_on_its_own_thread_and_join_waits_for_it(monkeypatch):
    t = make_tray(monkeypatch, [])
    t.stop()  # so the setup callback's poll loop returns at once
    t.start()
    t.join(timeout=2)
    assert t.icon.ran and t.icon.visible
    assert not t._thread.is_alive()


def updating_tray(monkeypatch, answer):
    """A tray whose POST /api/update/check answers with `answer`: a dict, raw bytes, or an
    exception to raise. Returns the tray and the list of pages it opened."""
    def fake_call(port, path, method="GET", timeout=5.0):
        assert (port, path, method) == (8765, "/api/update/check", "POST")
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()

    monkeypatch.setattr(tray.instance, "call", fake_call)
    opened = []
    monkeypatch.setattr(tray.webbrowser, "open", opened.append)
    return tray.Tray(FakeShell(), URL, 8765, icon=FakeIcon()), opened


def test_check_for_updates_opens_the_download_page_when_a_newer_version_is_out(monkeypatch):
    t, opened = updating_tray(monkeypatch, {"available": True, "latest": "2.1.0", "current": "2.0.0",
                                            "url": RELEASE, "error": None})
    t._report_update_check()
    assert t.icon.notes == [("Version 2.1.0 is available. Opening the download page.", tray.TITLE)]
    assert opened == [RELEASE]


@pytest.mark.parametrize("answer, note", [
    ({"available": False, "latest": "2.0.0", "current": "2.0.0", "url": None, "error": None},
     "You have the latest version, 2.0.0."),
    ({"available": False, "latest": None, "current": "2.0.0", "url": None, "error": "network-error"},
     "Couldn't reach GitHub to check for updates. Try again later."),
    (OSError("connection refused"), "Couldn't check for updates. Try again in a minute."),
    (b"not json", "Couldn't check for updates. Try again in a minute."),
    (b"[]", "Couldn't check for updates. Try again in a minute."),
])
def test_check_for_updates_reports_without_opening_anything(monkeypatch, answer, note):
    t, opened = updating_tray(monkeypatch, answer)
    t._report_update_check()
    assert t.icon.notes == [(note, tray.TITLE)]
    assert opened == []


def test_check_for_updates_runs_off_the_menu_thread(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            started.append((target, daemon))

        def start(self):
            started.append("started")

    t = make_tray(monkeypatch, [])
    monkeypatch.setattr(tray.threading, "Thread", FakeThread)
    {item.text: item for item in t.menu().items}["Check for updates"](t.icon)
    assert started == [(t._report_update_check, True), "started"]


UPDATE = {"current": "2.0.0", "latest": "2.1.0", "available": True, "notify": True, "url": RELEASE,
          "error": None, "installable": True, "can_install": True,
          "install": {"state": "idle", "version": None, "error": None}}
NO_UPDATE = {**UPDATE, "latest": "2.0.0", "available": False, "notify": False, "url": None,
             "installable": False, "can_install": False}


def test_a_new_version_gets_one_note_the_badge_and_a_menu_item(monkeypatch):
    t = make_tray(monkeypatch, [])
    t.observe_update(NO_UPDATE)
    assert t.icon.notes == [] and t.icon.title == tray.TITLE
    menu = t.menu()
    assert [item.text for item in menu.items if item.visible][-3:-2] == ["Check for updates"]
    t.observe_update(UPDATE)
    t.observe_update(UPDATE)
    assert t.icon.notes == [("Version 2.1.0 is available. Choose Install update in this icon's menu.", tray.TITLE)]
    assert tray.BADGE in t.icon.icon.get_flattened_data()
    assert t.icon.title == "Claude Usage Dashboard: version 2.1.0 is available"
    visible = [item.text for item in t.menu().items if item.visible]
    assert visible[4] == "Install update 2.1.0"
    assert t.icon.menu_updates == 2  # once for 2.0.0, once for 2.1.0


def test_a_copy_that_cannot_install_points_to_the_dashboard(monkeypatch):
    t = make_tray(monkeypatch, [])
    t.observe_update({**UPDATE, "can_install": False})
    assert t.icon.notes == [("Version 2.1.0 is available. Open the dashboard to download it.", tray.TITLE)]
    assert "Install update 2.1.0" not in [item.text for item in t.menu().items if item.visible]


def test_a_dismissed_version_gets_no_note_and_no_badge(monkeypatch):
    t = make_tray(monkeypatch, [])
    t.observe_update({**UPDATE, "notify": False})
    assert t.icon.notes == [] and t.icon.title == tray.TITLE
    assert "Install update 2.1.0" in [item.text for item in t.menu().items if item.visible]


def test_the_sign_in_comes_first_in_the_tooltip(monkeypatch):
    t = make_tray(monkeypatch, [SIGNED_OUT, CONNECTED])
    t.observe_update(UPDATE)
    t.poll_once()
    assert t.icon.title == "Claude Usage Dashboard: Not signed in to Claude Code."
    t.poll_once()
    assert t.icon.title == "Claude Usage Dashboard: version 2.1.0 is available"
    assert tray.BADGE in t.icon.icon.get_flattened_data()


def test_poll_update_reads_the_last_check(monkeypatch):
    def fake_call(port, path, method="GET", timeout=5.0):
        assert (port, path, method) == (8765, "/api/update", "GET")
        return json.dumps(UPDATE).encode()

    monkeypatch.setattr(tray.instance, "call", fake_call)
    t = tray.Tray(FakeShell(), URL, 8765, icon=FakeIcon())
    t.poll_update()
    assert t.update == UPDATE and len(t.icon.notes) == 1


@pytest.mark.parametrize("answer", [OSError("refused"), b"<html>", b"[]"])
def test_poll_update_ignores_a_bad_answer(monkeypatch, answer):
    def fake_call(port, path, method="GET", timeout=5.0):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(tray.instance, "call", fake_call)
    t = tray.Tray(FakeShell(), URL, 8765, icon=FakeIcon())
    t.poll_update()
    assert t.update == {} and t.icon.notes == []


def test_check_for_updates_points_to_the_menu_when_it_can_install(monkeypatch):
    t, opened = updating_tray(monkeypatch, UPDATE)
    t._report_update_check()
    assert t.icon.notes == [("Version 2.1.0 is available. Choose Install update in this icon's menu.", tray.TITLE)]
    assert opened == []
    assert "Install update 2.1.0" in [item.text for item in t.menu().items if item.visible]


def install_tray(monkeypatch, answers):
    """A tray whose install POST and the /api/update polls after it answer in turn from `answers`."""
    answers = iter(answers)
    paths = []

    def fake_call(port, path, method="GET", timeout=5.0):
        paths.append((path, method))
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()

    monkeypatch.setattr(tray.instance, "call", fake_call)
    monkeypatch.setattr(tray, "INSTALL_POLL_SECONDS", 0)
    t = tray.Tray(FakeShell(), URL, 8765, icon=FakeIcon())
    t.update = UPDATE
    return t, paths


def downloading(state="downloading", error=None):
    return {**UPDATE, "install": {"state": state, "version": "2.1.0", "error": error}}


def test_install_from_the_menu_follows_the_download_until_the_app_quits(monkeypatch):
    t, paths = install_tray(monkeypatch, [downloading(), downloading(), downloading("starting"),
                                          ConnectionRefusedError(10061, "refused")])
    t._run_install()
    assert paths == [("/api/update/install", "POST")] + [("/api/update", "GET")] * 3
    assert t.icon.notes == [("Downloading version 2.1.0. The dashboard restarts once it's installed.", tray.TITLE)]


@pytest.mark.parametrize("error, note", [
    ("bad-signature", "The update wasn't installed: its signature didn't check out."),
    ("download-failed", "Couldn't download the update. Try again later."),
    ("launch-failed", "Windows wouldn't start the update's installer."),
    ("something-new", "The update wasn't installed."),
])
def test_a_failed_install_from_the_menu_says_why(monkeypatch, error, note):
    t, _ = install_tray(monkeypatch, [downloading(), downloading("failed", error)])
    t._run_install()
    assert t.icon.notes[-1] == (note, tray.TITLE)


@pytest.mark.parametrize("answer", [OSError("HTTP 409 Conflict"), b"[]"])
def test_an_install_the_server_refuses_points_to_the_download(monkeypatch, answer):
    t, paths = install_tray(monkeypatch, [answer])
    t._run_install()
    assert paths == [("/api/update/install", "POST")]
    assert t.icon.notes == [("Couldn't start the update. Open the dashboard to download it instead.", tray.TITLE)]


def test_install_from_the_menu_gives_up_following_after_a_while(monkeypatch):
    t, paths = install_tray(monkeypatch, [downloading()] * 3)
    ticks = iter([0, 0, tray.INSTALL_GIVE_UP_SECONDS + 1])
    t._run_install(clock=lambda: next(ticks))
    assert len(paths) == 2


def test_quitting_stops_following_the_install(monkeypatch):
    t, paths = install_tray(monkeypatch, [downloading()])
    t.stop()
    t._run_install()
    assert paths == [("/api/update/install", "POST")]


def test_install_update_runs_off_the_menu_thread(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            started.append((target, daemon))

        def start(self):
            started.append("started")

    t = make_tray(monkeypatch, [])
    t.update = UPDATE
    monkeypatch.setattr(tray.threading, "Thread", FakeThread)
    {item.text: item for item in t.menu().items}["Install update 2.1.0"](t.icon)
    assert started == [(t._run_install, True), "started"]
