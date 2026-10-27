"""
Whether a newer release is out.

The server asks GitHub's releases/latest at most once a day (app._update_tick); Check now in
Settings and Check for updates in the tray ask at once. Releases are tagged with bare numbers
(2.0.0). The answer is kept in data/update.json, so the restart at every sign-in doesn't ask
again. The request is a plain GET with the app's name and version as its User-Agent, which
GitHub requires; nothing about the PC or its usage goes with it.

The installed app can also install a release itself (install()): it downloads that release's
Setup and its .sig, checks the signature against SIGNING_KEY, starts Setup and quits. Setup
waits for it to exit, upgrades it and starts it again (packaging/installer.iss). The
signature is made on the maintainer's PC (packaging/sign.py), not by the release workflow, so
someone who gets into the GitHub account still can't push code to installed copies.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import paths
import settings
import version

REPO = "1andonlyWeaver/claude-usage-dashboard"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_URL = f"https://github.com/{REPO}/releases/tag/{{}}"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/download/{{tag}}/{{name}}"
STATE_FILE = paths.DATA_DIR / "update.json"
DOWNLOAD_DIR = paths.DATA_DIR / "updates"  # the downloaded Setup, until the next start clears it
SETUP_LOG = paths.LOG_DIR / "update-setup.log"
CHECK_EVERY = 24 * 3600  # seconds between automatic checks
RETRY_AFTER = 3600       # after a failed check, try again in an hour rather than a day
TIMEOUT = 10

DOWNLOAD_TIMEOUT = 30
MAX_SIGNATURE = 1024     # bytes: a .sig is one line of base64

# The Ed25519 public key that release installers must be signed with, base64. Only the
# maintainer's PC holds the private key: packaging/sign.py new-key writes this line, and
# packaging/sign.py sign <installer> makes each release's .sig. Empty: nothing installs from
# the app, and the notice links to the download page instead.
SIGNING_KEY = ""

_NUMBER = re.compile(r"\d+(?:\.\d+){0,3}")
_urlopen = urllib.request.urlopen  # tests replace this: no test may reach GitHub
_popen = subprocess.Popen          # and this: no test may start Setup
_lock = threading.Lock()           # one check at a time
_install_lock = threading.Lock()   # one install at a time
_install = {"state": "idle", "version": None, "error": None}
_install_thread = None


class UpdateError(Exception):
    """An install that can't go ahead. `code` is what the page and the tray show a message for."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def log(message: str) -> None:
    print(f"[updates {datetime.now():%Y-%m-%d %H:%M:%S}] {message}")


def parse(tag) -> tuple[int, int, int, int] | None:
    """'2.10.1' -> (2, 10, 1, 0). None for anything but a bare release number ('v2.1.0', '2.1.0-rc1')."""
    if not isinstance(tag, str) or not _NUMBER.fullmatch(tag):
        return None
    parts = [int(part) for part in tag.split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def is_newer(tag, current: str | None = None) -> bool:
    """Whether `tag` is a later release than `current` (default: the running version)."""
    latest = parse(tag)
    mine = parse(version.__version__ if current is None else current)
    return latest is not None and mine is not None and latest > mine


def installer_name(tag: str) -> str:
    """The release's Setup file, as release.yml names it."""
    return f"{paths.APP_NAME}-Setup-{tag}.exe"


def fetch_latest(timeout: float = TIMEOUT) -> dict:
    """The latest published release: {"tag", "installer_size", "signed"}. OSError or ValueError
    when there isn't a usable one.

    installer_size is the size of its Setup file (None when the release has none), and signed
    says whether a .sig sits beside it. Only these two names count; their download URLs are
    built from the tag, never taken from the answer.
    """
    request = urllib.request.Request(LATEST_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{paths.APP_NAME}/{version.__version__}",
    })
    with _urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read())
    tag = body.get("tag_name") if isinstance(body, dict) else None
    if parse(tag) is None:
        raise ValueError(f"the latest release's tag isn't a release number: {tag!r}")
    assets = body.get("assets")
    sizes = {asset.get("name"): asset.get("size") for asset in assets if isinstance(asset, dict)} \
        if isinstance(assets, list) else {}
    size = sizes.get(installer_name(tag))
    return {"tag": tag, "installer_size": size if type(size) is int and size > 0 else None,
            "signed": installer_name(tag) + ".sig" in sizes}


def _load() -> dict:
    """The last check, {"checked_at", "latest", "error", "installer_size", "signed"}, each None
    (signed: False) when missing or malformed."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    checked = state.get("checked_at")
    error = state.get("error")
    size = state.get("installer_size")
    return {
        "checked_at": checked if type(checked) in (int, float) else None,
        "latest": state.get("latest") if parse(state.get("latest")) else None,
        "error": error if isinstance(error, str) else None,
        "installer_size": size if type(size) is int and size > 0 else None,
        "signed": state.get("signed") is True,
    }


def _save(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as ex:
        log(f"couldn't save {STATE_FILE.name} ({ex}); the next hourly tick asks GitHub again")


def due(now: float | None = None) -> bool:
    """Whether the automatic check should ask GitHub now: daily, an hour after a failure, never when off."""
    if not settings.get("update_check"):
        return False
    now = time.time() if now is None else now
    state = _load()
    checked = state["checked_at"]
    if checked is None or checked > now:  # never checked, or the clock has gone back since
        return True
    return now - checked >= (RETRY_AFTER if state["error"] else CHECK_EVERY)


def check(now: float | None = None) -> dict:
    """Ask GitHub now, whatever the setting says, and return status(). A failure is recorded, not raised."""
    with _lock:
        previous = _load()
        release = {key: previous[key] for key in ("installer_size", "signed")}
        try:
            found = fetch_latest()
            latest, error = found["tag"], None
            release = {key: found[key] for key in ("installer_size", "signed")}
        except urllib.error.HTTPError as ex:  # 404 before the first release, 403 at the hourly limit
            latest, error = previous["latest"], f"http-{ex.code}"
            log(f"check failed: HTTP {ex.code}")
        except (OSError, http.client.HTTPException) as ex:
            latest, error = previous["latest"], "network-error"
            log(f"check failed - {type(ex).__name__}: {ex}")
        except ValueError as ex:
            latest, error = previous["latest"], "bad-answer"
            log(f"check failed - {ex}")
        state = {"checked_at": time.time() if now is None else now, "latest": latest, "error": error,
                 **release}
        _save(state)
    return status(state)


def installable(state: dict) -> bool:
    """Whether the release in `state` can be installed from the app: newer, with a Setup and a .sig
    on the release, and a key here to check the .sig against. app.py adds whether this copy is
    the installed desktop app."""
    return (is_newer(state["latest"]) and state["installer_size"] is not None and state["signed"]
            and bool(SIGNING_KEY))


def status(state: dict | None = None) -> dict:
    """What the notice, Settings and the tray show. `state` defaults to the saved last check."""
    state = _load() if state is None else state
    prefs = settings.load()
    current = version.__version__
    latest = state["latest"]
    available = is_newer(latest, current)
    with _install_lock:
        progress = dict(_install)
    return {
        "current": current,
        "latest": latest,
        "available": available,
        "notify": available and prefs["update_check"] and latest != prefs["dismissed_version"],
        "url": RELEASE_URL.format(latest) if available else None,
        "checked_at": state["checked_at"],
        "error": state["error"],
        "enabled": prefs["update_check"],
        "installable": available and installable(state),
        "install": progress,
    }


def signed_message(tag: str, sha256: str) -> bytes:
    """What a release's .sig signs: the app, the release number and the Setup file's SHA-256.

    The number is in it so an older release's signed Setup can't pass for a newer one.
    """
    return f"{paths.APP_NAME}-Setup {tag} sha256:{sha256}".encode("ascii")


def verify(tag: str, sha256: str, signature: str, key: str | None = None) -> bool:
    """Whether `signature` (base64, from the .sig) signs this Setup with `key` (default SIGNING_KEY)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = SIGNING_KEY if key is None else key
    try:
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True))
        public.verify(base64.b64decode(signature.strip(), validate=True), signed_message(tag, sha256))
    except (InvalidSignature, ValueError):  # a bad signature, or a key or .sig that isn't base64
        return False
    return True


def _get(url: str, limit: int, sink) -> int:
    """Download `url` into sink(chunk), at most `limit` bytes. The number of bytes read."""
    request = urllib.request.Request(url, headers={"User-Agent": f"{paths.APP_NAME}/{version.__version__}"})
    total = 0
    with _urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError(f"{url} is bigger than the {limit} bytes expected")
            sink(chunk)
    return total


def clear_downloads() -> None:
    """Delete downloaded installers. Best effort: Setup may still hold the one it ran from."""
    try:
        files = list(DOWNLOAD_DIR.iterdir())
    except OSError:
        return
    for path in files:
        try:
            path.unlink()
        except OSError:
            pass


def download(tag: str, size: int) -> Path:
    """Download release `tag`'s Setup (`size` bytes) and its .sig, and check the signature.

    The path of the checked Setup. UpdateError("download-failed") or ("bad-signature").
    """
    name = installer_name(tag)
    clear_downloads()
    part = DOWNLOAD_DIR / f"{name}.part"
    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        sig = bytearray()
        _get(DOWNLOAD_URL.format(tag=tag, name=f"{name}.sig"), MAX_SIGNATURE, sig.extend)
        signature = sig.decode("ascii")
        digest = hashlib.sha256()
        with open(part, "wb") as out:
            def write(chunk):
                digest.update(chunk)
                out.write(chunk)
            got = _get(DOWNLOAD_URL.format(tag=tag, name=name), size, write)
        if got != size:
            raise ValueError(f"got {got} bytes of {name}, expected {size}")
    except (OSError, http.client.HTTPException, ValueError) as ex:  # UnicodeDecodeError is a ValueError
        part.unlink(missing_ok=True)
        raise UpdateError("download-failed", f"{type(ex).__name__}: {ex}") from ex
    if not verify(tag, digest.hexdigest(), signature):
        part.unlink(missing_ok=True)
        raise UpdateError("bad-signature", f"{name} doesn't match its .sig")
    final = DOWNLOAD_DIR / name
    part.replace(final)
    return final


def launch(installer: Path, relaunch: str) -> None:
    """Start the downloaded Setup as an update of this process, which must then quit.

    /UPDATE tells Setup to wait for this pid to exit, then start the app again, with its window
    (relaunch "window") or in the tray ("background"). /SILENT shows only its progress.
    OSError when Windows won't start it.
    """
    SETUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    _popen([str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/UPDATE={relaunch}",
            f"/PID={os.getpid()}", f"/LOG={SETUP_LOG}"],
           creationflags=flags, close_fds=True,
           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install_idle() -> bool:
    """Whether no install is under way, so a downloaded Setup isn't in use."""
    with _install_lock:
        return _install["state"] not in ("downloading", "starting")


def _set_install(**changes) -> None:
    with _install_lock:
        _install.update(changes)


def _run_install(tag: str, size: int, hand_over) -> None:
    try:
        installer = download(tag, size)
    except UpdateError as ex:
        log(f"install of {tag} failed - {ex}")
        _set_install(state="failed", error=ex.code)
        return
    except Exception as ex:
        log(f"install of {tag} failed - {type(ex).__name__}: {ex}")
        _set_install(state="failed", error="download-failed")
        return
    log(f"downloaded {installer.name} and checked its signature; starting it")
    _set_install(state="starting")
    try:
        hand_over(installer)
    except Exception as ex:  # OSError from Popen, mostly
        log(f"couldn't start {installer.name} - {type(ex).__name__}: {ex}")
        _set_install(state="failed", error="launch-failed")


def install(hand_over) -> dict:
    """Install the latest release: download and check it on a thread, then hand_over(path), which
    starts Setup and quits the app. Returns status() at once. UpdateError("not-installable")
    when there's nothing to install."""
    global _install_thread
    state = _load()
    if not installable(state):
        raise UpdateError("not-installable")
    with _install_lock:
        if _install["state"] not in ("downloading", "starting"):  # else a second click changes nothing
            _install.update(state="downloading", version=state["latest"], error=None)
            _install_thread = threading.Thread(target=_run_install, name="update-install", daemon=True,
                                               args=(state["latest"], state["installer_size"], hand_over))
            _install_thread.start()
    return status(state)
