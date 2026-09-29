"""
Where the dashboard reads its bundled files and keeps its data.

Running from source, everything stays in the repo as before: data/ and logs/ beside
the code. A frozen build (PyInstaller) can't write beside its own files, so it keeps
data and logs under %LOCALAPPDATA%\\ClaudeUsageDashboard. CUD_DATA_DIR overrides the
data dir in either case, e.g. for a second test instance.
"""
import os
import sys
from pathlib import Path

APP_NAME = "ClaudeUsageDashboard"
_SOURCE_DIR = Path(__file__).parent


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _app_home() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / APP_NAME


def resource_dir() -> Path:
    """Read-only files shipped with the app: static/ and templates/."""
    if _frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return _SOURCE_DIR


def data_dir() -> Path:
    """usage.db, the quota cache and settings.json."""
    override = os.environ.get("CUD_DATA_DIR")
    if override:
        return Path(override)
    return _app_home() / "data" if _frozen() else _SOURCE_DIR / "data"


def log_dir() -> Path:
    return _app_home() / "logs" if _frozen() else _SOURCE_DIR / "logs"


RESOURCE_DIR = resource_dir()
DATA_DIR = data_dir()
LOG_DIR = log_dir()
