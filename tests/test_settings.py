import json
import threading

import pytest

import settings


def test_defaults_when_there_is_no_file():
    assert settings.load() == {"judge_enabled": False, "auto_refresh_token": False}


def test_update_persists_and_reads_back(isolated_settings):
    assert settings.update({"judge_enabled": True})["judge_enabled"] is True
    assert settings.get("judge_enabled") is True
    assert json.loads(isolated_settings.read_text(encoding="utf-8"))["judge_enabled"] is True


def test_update_keeps_the_other_keys():
    settings.update({"auto_refresh_token": True})
    settings.update({"judge_enabled": True})
    assert settings.load() == {"judge_enabled": True, "auto_refresh_token": True}


@pytest.mark.parametrize("bad", [{"nope": True}, {"judge_enabled": "yes"}, {"judge_enabled": 1}])
def test_update_rejects_unknown_keys_and_wrong_types(bad):
    with pytest.raises(ValueError):
        settings.update(bad)
    assert settings.load()["judge_enabled"] is False


def test_unreadable_file_or_wrong_types_fall_back_to_defaults(isolated_settings):
    isolated_settings.write_text("{not json", encoding="utf-8")
    assert settings.load()["judge_enabled"] is False
    isolated_settings.write_text(json.dumps({"judge_enabled": "true", "auto_refresh_token": True}),
                                 encoding="utf-8")
    assert settings.load() == {"judge_enabled": False, "auto_refresh_token": True}


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
    reader.join()

    assert exceptions == [], f"Concurrent access raised exceptions: {exceptions}"
    assert settings.load()["judge_enabled"] is True  # Last write was i=99 (odd, so True)
