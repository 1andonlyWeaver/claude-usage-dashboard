"""
Whether a newer release is out.

The server asks GitHub's releases/latest at most once a day (app._update_tick); Check now in
Settings and Check for updates in the tray ask at once. Releases are tagged with bare numbers
(2.0.0). The answer is kept in data/update.json, so the restart at every sign-in doesn't ask
again. The request is a plain GET with the app's name and version as its User-Agent, which
GitHub requires; nothing about the PC or its usage goes with it.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import paths
import settings
import version

REPO = "1andonlyWeaver/claude-usage-dashboard"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_URL = f"https://github.com/{REPO}/releases/tag/{{}}"
STATE_FILE = paths.DATA_DIR / "update.json"
CHECK_EVERY = 24 * 3600  # seconds between automatic checks
RETRY_AFTER = 3600       # after a failed check, try again in an hour rather than a day
TIMEOUT = 10

_NUMBER = re.compile(r"\d+(?:\.\d+){0,3}")
_urlopen = urllib.request.urlopen  # tests replace this: no test may reach GitHub
_lock = threading.Lock()           # one check at a time


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


def fetch_latest(timeout: float = TIMEOUT) -> str:
    """The tag of the latest published release. OSError or ValueError when there isn't a usable one."""
    request = urllib.request.Request(LATEST_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{paths.APP_NAME}/{version.__version__}",
    })
    with _urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read())
    tag = body.get("tag_name") if isinstance(body, dict) else None
    if parse(tag) is None:
        raise ValueError(f"the latest release's tag isn't a release number: {tag!r}")
    return tag


def _load() -> dict:
    """The last check, {"checked_at", "latest", "error"}, each None when missing or malformed."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    checked = state.get("checked_at")
    error = state.get("error")
    return {
        "checked_at": checked if type(checked) in (int, float) else None,
        "latest": state.get("latest") if parse(state.get("latest")) else None,
        "error": error if isinstance(error, str) else None,
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
        try:
            latest, error = fetch_latest(), None
        except urllib.error.HTTPError as ex:  # 404 before the first release, 403 at the hourly limit
            latest, error = previous["latest"], f"http-{ex.code}"
            log(f"check failed: HTTP {ex.code}")
        except OSError as ex:
            latest, error = previous["latest"], "network-error"
            log(f"check failed - {type(ex).__name__}: {ex}")
        except ValueError as ex:
            latest, error = previous["latest"], "bad-answer"
            log(f"check failed - {ex}")
        state = {"checked_at": time.time() if now is None else now, "latest": latest, "error": error}
        _save(state)
    return status(state)


def status(state: dict | None = None) -> dict:
    """What the notice, Settings and the tray show. `state` defaults to the saved last check."""
    state = _load() if state is None else state
    prefs = settings.load()
    current = version.__version__
    latest = state["latest"]
    available = is_newer(latest, current)
    return {
        "current": current,
        "latest": latest,
        "available": available,
        "notify": available and prefs["update_check"] and latest != prefs["dismissed_version"],
        "url": RELEASE_URL.format(latest) if available else None,
        "checked_at": state["checked_at"],
        "error": state["error"],
        "enabled": prefs["update_check"],
    }
