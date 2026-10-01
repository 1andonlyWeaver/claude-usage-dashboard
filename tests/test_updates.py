"""The update check: release numbers, the GitHub call, the daily schedule, and what the notice shows."""
import json
import urllib.error

import pytest

import settings
import updates
import version
from helpers import FakeResponse, fake_urlopen, http_error

NOW = 1_800_000_000.0
DAY = 24 * 3600
RELEASE_2_1_0 = "https://github.com/1andonlyWeaver/claude-usage-dashboard/releases/tag/2.1.0"


@pytest.fixture(autouse=True)
def running_2_0_0(monkeypatch):
    monkeypatch.setattr(version, "__version__", "2.0.0")


def github(monkeypatch, *answers):
    """GitHub answers with each item in turn: a FakeResponse, or an exception to raise."""
    urlopen, calls = fake_urlopen(list(answers))
    monkeypatch.setattr(updates, "_urlopen", urlopen)
    return calls


def save_state(path, **state):
    path.write_text(json.dumps(state), encoding="utf-8")


@pytest.mark.parametrize("tag, parsed", [
    ("2.0.0", (2, 0, 0, 0)), ("2.10", (2, 10, 0, 0)), ("3", (3, 0, 0, 0)), ("1.2.3.4", (1, 2, 3, 4)),
    ("v2.0.0", None), ("2.0.0-rc1", None), ("2.0.0 ", None), ("", None), (None, None), (2, None),
    ("1.2.3.4.5", None),
])
def test_only_bare_release_numbers_parse(tag, parsed):
    assert updates.parse(tag) == parsed


def test_versions_compare_as_numbers():
    assert updates.is_newer("2.10.0", "2.9.0")
    assert not updates.is_newer("2.9.0", "2.10.0")
    assert not updates.is_newer("2.0", "2.0.0")
    assert updates.is_newer("2.0.1", "2.0.0")
    assert not updates.is_newer("v9.0.0", "2.0.0")
    assert not updates.is_newer(None, "2.0.0")
    assert updates.is_newer("2.0.1")  # against the running version, 2.0.0 here


def test_fetch_latest_asks_github_for_the_latest_release(monkeypatch):
    calls = github(monkeypatch, FakeResponse({"tag_name": "2.1.0", "name": "2.1.0"}))
    assert updates.fetch_latest() == "2.1.0"
    (request,) = calls
    assert request.full_url == updates.LATEST_URL == \
        "https://api.github.com/repos/1andonlyWeaver/claude-usage-dashboard/releases/latest"
    assert request.get_method() == "GET"
    assert request.get_header("User-agent") == "ClaudeUsageDashboard/2.0.0"
    assert request.get_header("Accept") == "application/vnd.github+json"


def test_a_tag_that_is_not_a_release_number_is_refused(monkeypatch):
    github(monkeypatch, FakeResponse({"tag_name": "v2.1.0"}), FakeResponse(raw=b"<html>busy</html>"),
           FakeResponse(["2.1.0"]), FakeResponse({"name": "no tag"}))
    for _ in range(4):
        with pytest.raises(ValueError):
            updates.fetch_latest()


def test_a_successful_check_is_saved_and_shown(monkeypatch, isolated_updates):
    github(monkeypatch, FakeResponse({"tag_name": "2.1.0"}))
    status = updates.check(now=NOW)
    assert status == {"current": "2.0.0", "latest": "2.1.0", "available": True, "notify": True,
                      "url": RELEASE_2_1_0, "checked_at": NOW, "error": None, "enabled": True}
    assert json.loads(isolated_updates.read_text(encoding="utf-8")) == \
        {"checked_at": NOW, "latest": "2.1.0", "error": None}
    assert updates.status() == status


@pytest.mark.parametrize("failure, code", [
    (http_error(404), "http-404"),  # nothing published yet
    (http_error(403), "http-403"),  # GitHub's hourly limit for unauthenticated calls
    (urllib.error.URLError("offline"), "network-error"),
    (TimeoutError("timed out"), "network-error"),
    (FakeResponse(raw=b"<html>"), "bad-answer"),
])
def test_a_failed_check_keeps_what_was_known_and_records_why(monkeypatch, isolated_updates, failure, code):
    save_state(isolated_updates, checked_at=NOW - 2 * DAY, latest="2.1.0", error=None)
    github(monkeypatch, failure)
    status = updates.check(now=NOW)
    assert status["latest"] == "2.1.0" and status["available"]
    assert status["error"] == code and status["checked_at"] == NOW


def test_up_to_date_has_no_notice_and_no_link(monkeypatch):
    github(monkeypatch, FakeResponse({"tag_name": "2.0.0"}))
    status = updates.check(now=NOW)
    assert status["latest"] == "2.0.0"
    assert not status["available"] and not status["notify"] and status["url"] is None


def test_dismissing_hides_that_version_but_not_the_next(isolated_updates):
    save_state(isolated_updates, checked_at=NOW, latest="2.1.0", error=None)
    settings.update({"dismissed_version": "2.1.0"})
    status = updates.status()
    assert status["available"] and not status["notify"] and status["url"] == RELEASE_2_1_0
    save_state(isolated_updates, checked_at=NOW, latest="2.2.0", error=None)
    assert updates.status()["notify"]


def test_once_installed_the_new_version_is_no_longer_offered(monkeypatch, isolated_updates):
    save_state(isolated_updates, checked_at=NOW, latest="2.1.0", error=None)
    monkeypatch.setattr(version, "__version__", "2.1.0")
    status = updates.status()
    assert status["current"] == "2.1.0"
    assert not status["available"] and not status["notify"] and status["url"] is None


@pytest.mark.parametrize("content", [
    "{not json", "[]", '{"checked_at": "yesterday", "latest": "v2.1.0", "error": 5}',
    '{"checked_at": true, "latest": "2.1.0", "error": null}',
])
def test_a_corrupt_state_file_counts_as_never_checked(isolated_updates, content):
    isolated_updates.write_text(content, encoding="utf-8")
    status = updates.status()
    assert status["checked_at"] is None and status["error"] is None
    assert updates.due(now=NOW)


def test_a_check_is_due_daily_and_an_hour_after_a_failure(isolated_updates):
    assert updates.due(now=NOW)  # never checked
    save_state(isolated_updates, checked_at=NOW - 23 * 3600, latest="2.0.0", error=None)
    assert not updates.due(now=NOW)
    save_state(isolated_updates, checked_at=NOW - 25 * 3600, latest="2.0.0", error=None)
    assert updates.due(now=NOW)
    save_state(isolated_updates, checked_at=NOW - 30 * 60, latest=None, error="network-error")
    assert not updates.due(now=NOW)
    save_state(isolated_updates, checked_at=NOW - 2 * 3600, latest=None, error="network-error")
    assert updates.due(now=NOW)


def test_a_check_stamped_in_the_future_is_due_again(isolated_updates):
    save_state(isolated_updates, checked_at=NOW + DAY, latest="2.0.0", error=None)  # the clock went back since
    assert updates.due(now=NOW)


def test_turning_the_check_off_stops_the_daily_check_and_the_notice_but_not_check_now(monkeypatch):
    settings.update({"update_check": False})
    assert not updates.due(now=NOW)
    github(monkeypatch, FakeResponse({"tag_name": "2.1.0"}))
    status = updates.check(now=NOW)
    assert status["available"] and not status["notify"] and status["enabled"] is False


def test_an_unwritable_state_file_still_gives_this_checks_answer(monkeypatch, tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the data dir should be", encoding="utf-8")
    monkeypatch.setattr(updates, "STATE_FILE", blocker / "update.json")
    github(monkeypatch, FakeResponse({"tag_name": "2.1.0"}))
    assert updates.check(now=NOW)["available"]
    assert "couldn't save update.json" in capsys.readouterr().out
