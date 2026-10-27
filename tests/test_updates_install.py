"""Installing a release from the app: what the check records, the signature, the download, and
handing the checked Setup over."""
import base64
import hashlib
import json
import subprocess
import urllib.error

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import updates
import version
from helpers import FakeResponse, fake_urlopen, http_error

NOW = 1_800_000_000.0
SETUP = b"MZ pretend installer " * 1000
NAME = "ClaudeUsageDashboard-Setup-2.1.0.exe"
DOWNLOADS = "https://github.com/1andonlyWeaver/claude-usage-dashboard/releases/download/2.1.0/"


@pytest.fixture(autouse=True)
def running_2_0_0(monkeypatch):
    monkeypatch.setattr(version, "__version__", "2.0.0")


@pytest.fixture
def key(monkeypatch):
    """A signing key whose public half updates.SIGNING_KEY holds."""
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(updates, "SIGNING_KEY", base64.b64encode(raw).decode())
    return private


def sig_for(private, tag="2.1.0", body=SETUP) -> bytes:
    signature = private.sign(updates.signed_message(tag, hashlib.sha256(body).hexdigest()))
    return base64.b64encode(signature) + b"\n"


def save_state(path, **state):
    state = {"checked_at": NOW, "latest": "2.1.0", "error": None, "installer_size": len(SETUP),
             "signed": True, **state}
    path.write_text(json.dumps(state), encoding="utf-8")


def github(monkeypatch, *answers):
    urlopen, calls = fake_urlopen(list(answers))
    monkeypatch.setattr(updates, "_urlopen", urlopen)
    return calls


def release(tag="2.1.0", **extra):
    return {"tag_name": tag, "assets": [
        {"name": f"ClaudeUsageDashboard-Setup-{tag}.exe", "size": len(SETUP),
         "browser_download_url": "https://evil.example/setup.exe"},
        {"name": f"ClaudeUsageDashboard-Setup-{tag}.exe.sig", "size": 89},
        {"name": "notes.txt", "size": 4},
    ], **extra}


# --- what the check records ---

def test_the_check_records_the_setup_size_and_whether_it_is_signed(monkeypatch, isolated_updates, key):
    github(monkeypatch, FakeResponse(release()))
    status = updates.check(now=NOW)
    assert status["installable"]
    saved = json.loads(isolated_updates.read_text(encoding="utf-8"))
    assert saved["installer_size"] == len(SETUP) and saved["signed"] is True
    assert "evil.example" not in isolated_updates.read_text(encoding="utf-8")  # URLs are built, not stored


@pytest.mark.parametrize("assets", [
    None, "nope", [], [{"name": NAME, "size": 0}], [{"name": NAME, "size": "big"}],
    [{"name": NAME + ".sig", "size": 89}], ["junk"],
])
def test_odd_or_missing_assets_mean_not_installable(monkeypatch, key, assets):
    github(monkeypatch, FakeResponse({"tag_name": "2.1.0", "assets": assets}))
    status = updates.check(now=NOW)
    assert status["available"] and not status["installable"]


def test_a_failed_check_keeps_what_it_knew_about_the_release(monkeypatch, isolated_updates, key):
    save_state(isolated_updates)
    github(monkeypatch, urllib.error.URLError("offline"))
    assert updates.check(now=NOW)["installable"]


@pytest.mark.parametrize("change", [
    {"signed": False}, {"installer_size": None}, {"latest": "2.0.0"}, {"latest": "1.9.0"},
])
def test_installable_needs_a_newer_signed_setup(isolated_updates, key, change):
    save_state(isolated_updates, **change)
    assert not updates.status()["installable"]


def test_without_a_signing_key_nothing_is_installable(isolated_updates, monkeypatch):
    monkeypatch.setattr(updates, "SIGNING_KEY", "")
    save_state(isolated_updates)
    assert not updates.status()["installable"]


# --- the signature ---

def test_a_good_signature_verifies(key):
    assert updates.verify("2.1.0", hashlib.sha256(SETUP).hexdigest(), sig_for(key).decode())


@pytest.mark.parametrize("tag, body", [("2.2.0", SETUP), ("2.1.0", SETUP + b"!")])
def test_a_signature_is_for_one_release_and_one_file(key, tag, body):
    # An older release's signed Setup can't pass for a newer one, nor can a changed file
    assert not updates.verify(tag, hashlib.sha256(body).hexdigest(), sig_for(key).decode())


@pytest.mark.parametrize("signature", ["", "not base64!", base64.b64encode(b"short").decode()])
def test_a_garbled_signature_is_refused(key, signature):
    assert not updates.verify("2.1.0", hashlib.sha256(SETUP).hexdigest(), signature)


def test_another_key_signature_is_refused(key):
    other = Ed25519PrivateKey.generate()
    assert not updates.verify("2.1.0", hashlib.sha256(SETUP).hexdigest(), sig_for(other).decode())


def test_a_garbled_key_refuses_everything(key, monkeypatch):
    monkeypatch.setattr(updates, "SIGNING_KEY", "!!")
    assert not updates.verify("2.1.0", hashlib.sha256(SETUP).hexdigest(), sig_for(key).decode())


# --- the download ---

def test_download_fetches_both_files_from_the_release_and_keeps_a_checked_setup(monkeypatch, key):
    calls = github(monkeypatch, FakeResponse(raw=sig_for(key)), FakeResponse(raw=SETUP))
    path = updates.download("2.1.0", len(SETUP))
    assert [c.full_url for c in calls] == [DOWNLOADS + NAME + ".sig", DOWNLOADS + NAME]
    assert calls[0].get_header("User-agent") == "ClaudeUsageDashboard/2.0.0"
    assert path == updates.DOWNLOAD_DIR / NAME and path.read_bytes() == SETUP
    assert [p.name for p in updates.DOWNLOAD_DIR.iterdir()] == [NAME]


def test_a_bad_signature_leaves_nothing_to_run(monkeypatch, key):
    github(monkeypatch, FakeResponse(raw=sig_for(key, tag="2.0.5")), FakeResponse(raw=SETUP))
    with pytest.raises(updates.UpdateError) as caught:
        updates.download("2.1.0", len(SETUP))
    assert caught.value.code == "bad-signature"
    assert list(updates.DOWNLOAD_DIR.iterdir()) == []


@pytest.mark.parametrize("answers", [
    [http_error(404)],                                                # no .sig on the release
    [FakeResponse(raw=b"x" * (updates.MAX_SIGNATURE + 1))],           # not a .sig
    [FakeResponse(raw="sigé".encode())],                          # not ASCII
    ["SIG", FakeResponse(raw=SETUP + b"more")],                       # bigger than the release said
    ["SIG", FakeResponse(raw=SETUP[:-1])],                            # cut short
    ["SIG", TimeoutError("timed out")],
])
def test_a_failed_download_is_a_download_failure(monkeypatch, key, answers):
    answers = [FakeResponse(raw=sig_for(key)) if a == "SIG" else a for a in answers]
    github(monkeypatch, *answers)
    with pytest.raises(updates.UpdateError) as caught:
        updates.download("2.1.0", len(SETUP))
    assert caught.value.code == "download-failed"
    assert list(updates.DOWNLOAD_DIR.iterdir()) == []


def test_download_clears_earlier_downloads_and_so_does_clear_downloads(monkeypatch, key):
    updates.DOWNLOAD_DIR.mkdir(parents=True)
    (updates.DOWNLOAD_DIR / "ClaudeUsageDashboard-Setup-2.0.9.exe").write_bytes(b"old")
    github(monkeypatch, FakeResponse(raw=sig_for(key)), FakeResponse(raw=SETUP))
    updates.download("2.1.0", len(SETUP))
    assert [p.name for p in updates.DOWNLOAD_DIR.iterdir()] == [NAME]
    updates.clear_downloads()
    assert list(updates.DOWNLOAD_DIR.iterdir()) == []


def test_clear_downloads_without_the_folder_is_fine():
    updates.clear_downloads()


# --- starting Setup ---

def test_launch_starts_setup_as_an_update_of_this_process(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(updates, "_popen", lambda args, **kw: started.append((args, kw)))
    monkeypatch.setattr(updates.os, "getpid", lambda: 4242)
    updates.launch(tmp_path / NAME, "background")
    ((args, kw),) = started
    assert args == [str(tmp_path / NAME), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    "/UPDATE=background", "/PID=4242", f"/LOG={updates.SETUP_LOG}"]
    assert kw["close_fds"] and kw["stdin"] == subprocess.DEVNULL
    assert updates.SETUP_LOG.parent.is_dir()


# --- the whole install ---

def run_install(hand_over):
    status = updates.install(hand_over)
    updates._install_thread.join(timeout=5)
    return status


def test_install_downloads_checks_and_hands_the_setup_over(monkeypatch, isolated_updates, key):
    save_state(isolated_updates)
    github(monkeypatch, FakeResponse(raw=sig_for(key)), FakeResponse(raw=SETUP))
    handed = []
    status = run_install(handed.append)
    assert status["install"] == {"state": "downloading", "version": "2.1.0", "error": None}
    assert handed == [updates.DOWNLOAD_DIR / NAME]
    assert updates.status()["install"]["state"] == "starting"
    assert not updates.install_idle()


def test_a_bad_signature_never_reaches_setup(monkeypatch, isolated_updates, key):
    save_state(isolated_updates)
    github(monkeypatch, FakeResponse(raw=sig_for(Ed25519PrivateKey.generate())), FakeResponse(raw=SETUP))
    handed = []
    run_install(handed.append)
    assert handed == []
    assert updates.status()["install"] == {"state": "failed", "version": "2.1.0", "error": "bad-signature"}
    assert updates.install_idle()


def test_a_setup_windows_wont_start_is_a_launch_failure(monkeypatch, isolated_updates, key):
    save_state(isolated_updates)
    github(monkeypatch, FakeResponse(raw=sig_for(key)), FakeResponse(raw=SETUP))

    def refuse(path):
        raise OSError(1260, "blocked by group policy")

    run_install(refuse)
    assert updates.status()["install"]["error"] == "launch-failed"


def test_a_second_click_while_downloading_starts_nothing_new(monkeypatch, isolated_updates, key):
    save_state(isolated_updates)
    monkeypatch.setattr(updates, "_install", {"state": "downloading", "version": "2.1.0", "error": None})
    status = updates.install(lambda path: pytest.fail("handed over twice"))
    assert status["install"]["state"] == "downloading" and updates._install_thread is None


def test_a_failed_install_can_be_tried_again(monkeypatch, isolated_updates, key):
    save_state(isolated_updates)
    github(monkeypatch, TimeoutError("slow"), FakeResponse(raw=sig_for(key)), FakeResponse(raw=SETUP))
    handed = []
    run_install(handed.append)
    assert updates.status()["install"]["error"] == "download-failed"
    run_install(handed.append)
    assert len(handed) == 1 and updates.status()["install"]["state"] == "starting"


def test_nothing_to_install_is_refused(isolated_updates, key):
    save_state(isolated_updates, signed=False)
    with pytest.raises(updates.UpdateError) as caught:
        updates.install(lambda path: None)
    assert caught.value.code == "not-installable"
