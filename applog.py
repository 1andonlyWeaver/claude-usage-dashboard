"""
The dashboard's log file, dashboard.log in paths.LOG_DIR.

It's written as UTF-8. The Task Scheduler launcher used to hand the server a cp1252 file,
where printing any character outside cp1252 raised UnicodeEncodeError in whichever thread
was logging. At each start the log moves to dashboard.log.1 once it passes 5 MB, replacing the
previous .1. One long run can still take it past 5 MB.
"""
import os
import sys
from pathlib import Path

import paths

LOG_FILE = paths.LOG_DIR / "dashboard.log"
MAX_BYTES = 5 * 1024 * 1024


def rotate(path: Path = LOG_FILE, max_bytes: int | None = None) -> None:
    """Move path to <name>.1 once it's larger than max_bytes (default MAX_BYTES).

    Best effort: a file another process still holds open stays where it is until a later start.
    """
    limit = MAX_BYTES if max_bytes is None else max_bytes
    try:
        if path.stat().st_size > limit:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass


def open_log(path: Path = LOG_FILE):
    """Rotate, then open the log for appending as line-buffered UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate(path)
    return open(path, "a", encoding="utf-8", errors="backslashreplace", buffering=1)


def utf8_stdio() -> None:
    """Make print() write UTF-8 a line at a time wherever stdout points, and never raise on a character."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
