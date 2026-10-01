import json
import pathlib
import threading
import types

import pytest

import settings


def test_defaults_when_there_is_no_file():
    assert settings.load() == {"judge_enabled": False, "auto_refresh_token": False,
                               "preferred_port": 8765, "update_check": True,
                               "dismissed_version": ""}


def test_update_persists_and_reads_back(isolated_settings):
    assert settings.update({"judge_enabled": True})["judge_enabled"] is True
    assert settings.get("judge_enabled") is True
    assert json.loads(isolated_settings.read_text(encoding="utf-8"))["judge_enabled"] is True


def test_update_keeps_the_other_keys():
    settings.update({"auto_refresh_token": True})
    settings.update({"judge_enabled": True})
    assert settings.load() == {"judge_enabled": True, "auto_refresh_token": True,
                               "preferred_port": 8765, "update_check": True,
                               "dismissed_version": ""}


@pytest.mark.parametrize("bad", [{"nope": True}, {"judge_enabled": "yes"}, {"judge_enabled": 1},
                                 {"preferred_port": "8765"}, {"preferred_port": True},
                                 {"preferred_port": 8765.0}, {"update_check": "no"},
                                 {"dismissed_version": 2}])
def test_update_rejects_unknown_keys_and_wrong_types(bad):
    with pytest.raises(ValueError):
        settings.update(bad)
    assert settings.load()["judge_enabled"] is False


def test_unreadable_file_or_wrong_types_fall_back_to_defaults(isolated_settings):
    isolated_settings.write_text("{not json", encoding="utf-8")
    assert settings.load()["judge_enabled"] is False
    isolated_settings.write_text(json.dumps({"judge_enabled": "true", "auto_refresh_token": True}),
                                 encoding="utf-8")
    assert settings.load() == {"judge_enabled": False, "auto_refresh_token": True,
                               "preferred_port": 8765, "update_check": True,
                               "dismissed_version": ""}


def test_the_desktop_port_can_be_changed():
    assert settings.update({"preferred_port": 9123})["preferred_port"] == 9123
    assert settings.get("preferred_port") == 9123


def test_the_update_settings_take_a_switch_and_a_release_number():
    assert settings.update({"update_check": False})["update_check"] is False
    assert settings.update({"dismissed_version": "2.1.0"})["dismissed_version"] == "2.1.0"
    assert settings.load()["update_check"] is False


def test_concurrent_read_write_stress():
    """Stress test: tight-loop readers + frequent writers must not crash with PermissionError."""
    stop_event = threading.Event()
    exceptions = []

    def reader_loop():
        try:
            while not stop_event.is_set():
                settings.load()
        except Exception as e:
            exceptions.append(("reader", e))

    reader = threading.Thread(target=reader_loop, daemon=True)
    reader.start()

    # Main thread: update ~100 times, alternating True/False
    for i in range(100):
        try:
            value = bool(i % 2)
            settings.update({"judge_enabled": value})
        except Exception as e:
            exceptions.append(("writer", e))

    stop_event.set()
    reader.join(timeout=10)

    assert not reader.is_alive(), "reader thread did not stop"
    assert exceptions == [], f"Concurrent access raised exceptions: {exceptions}"
    assert settings.load()["judge_enabled"] is True  # Last write was i=99 (odd, so True)


def refuse_replace(monkeypatch, failures):
    """Make replacing settings.json.tmp raise PermissionError `failures` times (None: always).

    Returns a list that collects one entry per attempt. Other replaces are untouched.
    """
    real_replace = pathlib.Path.replace
    attempts = []

    def replace(self, target):
        if self.name != "settings.json.tmp":
            return real_replace(self, target)
        attempts.append(target)
        if failures is None or len(attempts) <= failures:
            raise PermissionError(13, "The process cannot access the file")
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", replace)
    return attempts


def no_sleep(monkeypatch):
    """Skip the retry waits and return the list of them. Only settings' own `time` is swapped."""
    naps = []
    monkeypatch.setattr(settings, "time", types.SimpleNamespace(sleep=naps.append))
    return naps


def test_update_rides_out_a_file_held_open_for_a_while(isolated_settings, monkeypatch):
    """A scanner can hold settings.json for a good part of a second: retry, don't 500."""
    no_sleep(monkeypatch)
    failures = 8  # more than the old 5-attempt budget, fewer than the attempts allowed now
    attempts = refuse_replace(monkeypatch, failures)

    assert settings.update({"judge_enabled": True})["judge_enabled"] is True
    assert len(attempts) == failures + 1
    assert failures < settings._REPLACE_ATTEMPTS
    assert json.loads(isolated_settings.read_text(encoding="utf-8"))["judge_enabled"] is True
    assert not isolated_settings.with_name("settings.json.tmp").exists()


def test_update_gives_up_cleanly_when_the_file_stays_locked(isolated_settings, monkeypatch):
    naps = no_sleep(monkeypatch)
    settings.update({"judge_enabled": True})
    attempts = refuse_replace(monkeypatch, None)

    with pytest.raises(PermissionError):
        settings.update({"judge_enabled": False})

    assert not isolated_settings.with_name("settings.json.tmp").exists()
    assert settings.load()["judge_enabled"] is True  # the old value survives
    assert len(attempts) == settings._REPLACE_ATTEMPTS
    # Waits grow, stay capped, and add up to about a second.
    assert naps == sorted(naps) and max(naps) <= settings._REPLACE_MAX_DELAY
    assert 0.8 <= sum(naps) <= 1.5
