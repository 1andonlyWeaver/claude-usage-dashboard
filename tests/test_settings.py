import json

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
