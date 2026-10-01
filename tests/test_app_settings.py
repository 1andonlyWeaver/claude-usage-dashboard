import pytest

import app


class FakeThread:
    started = []

    def __init__(self, target=None, daemon=None, **kwargs):
        self.target = target

    def start(self):
        FakeThread.started.append(self.target)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(app, "_usage_cache", dict(app._usage_cache))


@pytest.fixture
def threads(monkeypatch):
    FakeThread.started = []
    monkeypatch.setattr(app.threading, "Thread", FakeThread)
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", True)
    return FakeThread.started


def test_settings_round_trip(threads):
    assert app.get_settings() == {"judge_enabled": False, "auto_refresh_token": False,
                                  "preferred_port": 8765}
    assert app.post_settings({"auto_refresh_token": True})["auto_refresh_token"] is True
    assert app.get_settings()["auto_refresh_token"] is True


@pytest.mark.parametrize("bad", [{"judge_enabled": "yes"}, {"surprise": True}])
def test_settings_reject_bad_input(threads, bad):
    with pytest.raises(app.HTTPException) as err:
        app.post_settings(bad)
    assert err.value.status_code == 400


def test_turning_the_judge_on_starts_a_pass_right_away(threads):
    app.post_settings({"judge_enabled": True})
    app.post_settings({"judge_enabled": True})  # already on: no second pass
    assert threads == [app._hours_pass]


def test_no_pass_when_the_worker_is_off(threads, monkeypatch):
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", False)
    app.post_settings({"judge_enabled": True})
    assert threads == []


def test_changing_token_renewal_rechecks_the_token(threads):
    app._usage_cache["retry_after"] = 1e12
    app.post_settings({"auto_refresh_token": True})
    assert app._usage_cache["retry_after"] == 0.0
