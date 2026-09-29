"""
User settings, kept as JSON in the data dir.

Every read goes to disk, so a change made in the settings panel applies on the next
quota poll or judge tick without a restart. Reads are a few a minute; no cache needed.
"""
import json
import threading

import paths

SETTINGS_PATH = paths.DATA_DIR / "settings.json"

DEFAULTS = {
    "judge_enabled": False,       # the person-hours judge spends subscription quota: opt-in
    "auto_refresh_token": False,  # renewing rewrites ~/.claude/.credentials.json: opt-in
}

_write_lock = threading.Lock()


def load() -> dict:
    """Every setting: stored values of the right type over DEFAULTS.

    A missing or unreadable file, or a stored value of the wrong type, gives the default.
    """
    try:
        stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return {key: stored[key] if type(stored.get(key)) is type(default) else default
            for key, default in DEFAULTS.items()}


def get(key: str):
    return load()[key]


def update(changes: dict) -> dict:
    """Store `changes` and return every setting. ValueError for an unknown key or wrong type."""
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown setting: {key}")
        if type(value) is not type(DEFAULTS[key]):
            raise ValueError(f"{key} must be {type(DEFAULTS[key]).__name__}")
    with _write_lock:
        merged = {**load(), **changes}
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".tmp")
        tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
    return merged
