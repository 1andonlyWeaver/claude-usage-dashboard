import json
import time

import auth


def test_token_seconds_left(isolated_credentials):
    assert auth.token_seconds_left() is None
    isolated_credentials.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "t", "expiresAt": (time.time() + 3600) * 1000}}))
    assert 3500 < auth.token_seconds_left() <= 3600


def test_rejected_clears_once_the_credentials_file_changes(monkeypatch):
    monkeypatch.setattr(auth, "_auth_dead", True)
    monkeypatch.setattr(auth, "_auth_dead_creds_sig", ("old",))
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("old",))
    assert auth.rejected()
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("new",))
    assert not auth.rejected()
