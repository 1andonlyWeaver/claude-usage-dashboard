"""packaging/sign.py: the key pair, the .sig it writes, and that the app accepts it."""
import base64
import importlib.util
from pathlib import Path

import pytest

import updates

REPO = Path(__file__).parent.parent
PASSWORD = b"correct horse battery"


def load_sign():
    spec = importlib.util.spec_from_file_location("sign", REPO / "packaging" / "sign.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sign = load_sign()


@pytest.fixture
def keyed(tmp_path, monkeypatch):
    """A new key in tmp_path, its public half written into a copy of updates.py and set in updates."""
    copy = tmp_path / "updates.py"
    copy.write_text((REPO / "updates.py").read_text(encoding="utf-8"), encoding="utf-8")
    key_file = tmp_path / "keys" / "signing.pem"
    public = sign.new_key(key_file, PASSWORD)
    sign.set_signing_key(public, copy)
    monkeypatch.setattr(updates, "SIGNING_KEY", public)
    return key_file, copy, public


def installer(tmp_path, tag="2.1.0", body=b"MZ setup"):
    path = tmp_path / f"ClaudeUsageDashboard-Setup-{tag}.exe"
    path.write_bytes(body)
    return path


def test_the_repo_ships_one_signing_key_line():
    text = (REPO / "updates.py").read_text(encoding="utf-8")
    assert len(sign._KEY_LINE.findall(text)) == 1


def test_new_key_saves_an_encrypted_private_key_and_writes_the_public_one(keyed):
    key_file, copy, public = keyed
    assert b"ENCRYPTED PRIVATE KEY" in key_file.read_bytes()
    assert f'SIGNING_KEY = "{public}"' in copy.read_text(encoding="utf-8")
    assert len(base64.b64decode(public)) == 32


def test_new_key_never_replaces_an_existing_key(keyed):
    key_file, _, _ = keyed
    before = key_file.read_bytes()
    with pytest.raises(SystemExit, match="already exists"):
        sign.new_key(key_file, PASSWORD)
    assert key_file.read_bytes() == before


def test_a_signed_installer_passes_the_apps_check(keyed, tmp_path):
    key_file, _, _ = keyed
    setup = installer(tmp_path)
    out = sign.sign(setup, sign.load_key(key_file, PASSWORD))
    assert out == tmp_path / "ClaudeUsageDashboard-Setup-2.1.0.exe.sig"
    assert sign.verify(setup)
    assert updates.verify("2.1.0", sign.sha256(setup), out.read_text(encoding="ascii"))


def test_a_changed_or_renamed_installer_fails_the_check(keyed, tmp_path):
    key_file, _, _ = keyed
    setup = installer(tmp_path)
    sig = sign.sign(setup, sign.load_key(key_file, PASSWORD))
    setup.write_bytes(b"MZ something else")
    assert not sign.verify(setup)
    renamed = installer(tmp_path, tag="2.2.0")
    sig.rename(renamed.with_name(renamed.name + ".sig"))
    assert not sign.verify(renamed)


def test_the_wrong_password_is_refused(keyed):
    key_file, _, _ = keyed
    with pytest.raises(SystemExit, match="Wrong password"):
        sign.load_key(key_file, b"not it")


def test_a_key_the_app_doesnt_know_wont_sign(keyed, tmp_path, monkeypatch):
    key_file, _, _ = keyed
    monkeypatch.setattr(updates, "SIGNING_KEY", base64.b64encode(b"\0" * 32).decode())
    with pytest.raises(SystemExit, match="doesn't match"):
        sign.sign(installer(tmp_path), sign.load_key(key_file, PASSWORD))


def test_only_release_installers_are_signed(keyed, tmp_path):
    key_file, _, _ = keyed
    other = tmp_path / "Setup.exe"
    other.write_bytes(b"MZ")
    with pytest.raises(SystemExit, match="isn't named like a release installer"):
        sign.sign(other, sign.load_key(key_file, PASSWORD))


def test_the_sign_command_asks_for_the_password_and_checks_its_work(keyed, tmp_path, monkeypatch, capsys):
    key_file, _, _ = keyed
    setup = installer(tmp_path)
    monkeypatch.setattr(sign.getpass, "getpass", lambda prompt="": PASSWORD.decode())
    assert sign.main(["--key", str(key_file), "sign", str(setup)]) == 0
    assert "gh release upload 2.1.0" in capsys.readouterr().out
    assert sign.main(["verify", str(setup)]) == 0
    setup.write_bytes(b"MZ tampered")
    assert sign.main(["verify", str(setup)]) == 1
