"""
One running copy of the desktop app per Windows session, and how a second launch reaches it.

The first copy holds a named mutex for as long as it runs and writes its port and pid to
runtime.json. A second launch finds the mutex taken, reads runtime.json and asks the first
copy to show its window.
"""
import ctypes
import http.client
import json
import os
import time
import urllib.request
from ctypes import wintypes

import paths

MUTEX_NAME = r"Local\ClaudeUsageDashboard"  # the installer's AppMutex (Phase 4) names it too
RUNTIME_FILE = paths.DATA_DIR / "runtime.json"
ERROR_ALREADY_EXISTS = 183

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.AllowSetForegroundWindow.argtypes = (wintypes.DWORD,)
_user32.AllowSetForegroundWindow.restype = wintypes.BOOL

# Calls to the app's own server never use a proxy. urllib would otherwise send 127.0.0.1
# through HTTP_PROXY, or through a system proxy whose bypass list doesn't name it.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def acquire(name: str = MUTEX_NAME):
    """This copy's mutex handle, or None when another copy already holds it.

    Keep the handle for the life of the process; Windows releases it at exit.
    """
    ctypes.set_last_error(0)  # so a stale ERROR_ALREADY_EXISTS from an earlier call can't linger
    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


def release(handle) -> None:
    _kernel32.CloseHandle(handle)


def write_runtime(port: int) -> None:
    """Record where this copy's server listens, for a second launch to find."""
    RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUNTIME_FILE.with_name(RUNTIME_FILE.name + ".tmp")
    tmp.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
    tmp.replace(RUNTIME_FILE)


def read_runtime() -> dict | None:
    """{"port", "pid"} from runtime.json, or None when it's missing or garbled."""
    try:
        info = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict):
        return None
    port, pid = info.get("port"), info.get("pid")
    if type(port) is not int or not 0 < port < 65536 or type(pid) is not int:
        return None
    return {"port": port, "pid": pid}


def clear_runtime(pid: int | None = None) -> None:
    """Remove runtime.json. With pid, only when that process wrote it. Best effort."""
    if pid is not None and (read_runtime() or {}).get("pid") != pid:
        return
    try:
        RUNTIME_FILE.unlink()
    except OSError:
        pass


def call(port: int, path: str, method: str = "GET", timeout: float = 5.0) -> bytes:
    """The body of a request to the dashboard server on this PC.

    OSError on any failure, a non-2xx status, or an answer that isn't HTTP (another
    program on a stale port).
    """
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     data=b"" if method == "POST" else None)
    try:
        with _opener.open(request, timeout=timeout) as response:
            return response.read()
    except http.client.HTTPException as ex:
        raise OSError(f"not an HTTP answer: {ex!r}") from ex


def show_running(timeout: float = 10.0, *, sleep=time.sleep, clock=time.monotonic) -> bool:
    """Ask the copy that holds the mutex to show its window. True once it has.

    That copy may still be starting, or runtime.json may still hold its previous run's
    port, so this keeps asking for `timeout` seconds.
    """
    deadline = clock() + timeout
    while True:
        info = read_runtime()
        if info:
            # The person started this process, so Windows lets it hand the foreground on.
            _user32.AllowSetForegroundWindow(info["pid"])
            try:
                call(info["port"], "/api/app/show", method="POST")
                return True
            except OSError:
                pass
        if clock() >= deadline:
            return False
        sleep(0.5)
