# Desktop Shell Implementation Plan (Desktop App, Phase 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python desktop.py` runs the dashboard as a Windows desktop app. It gets its own window and a tray icon that keeps it running. There is one copy per session, a Start at login switch, and an amber tray badge plus one notification when the Claude sign-in needs the person.

**Architecture:** Five small modules around the existing FastAPI app. Each has one job:
- `autostart.py`: the HKCU `Run` value.
- `instance.py`: the single-instance mutex, `runtime.json`, and proxy-free calls to the app's own server.
- `app.py`: gains `/api/app/info` and `/api/app/show`, plus a `desktop_show` hook.
- `tray.py`: the pystray icon, its menu, the attention badge and the notification.
- `desktop.py`: wires them together.
  - pywebview owns the main thread, uvicorn serves the `app` object from a thread, and the tray runs in its own thread.
  - `--background` starts in the tray only, and `--smoke` is a quick self-check.

**Tech Stack:** Python 3.12, FastAPI 0.142 / Starlette 1.7, uvicorn 0.54, pywebview 6.2.1 (WinForms + WebView2 through pythonnet), pystray 0.19.5, Pillow 12.3.0, ctypes (kernel32/user32), winreg, pytest 9 with `httpx2`.

**Spec:** `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`. This plan implements Phase 3 from the "Phases" section. It also covers these bullets elsewhere in the spec:
- the whole "Architecture" section;
- the `desktop.py` and `autostart.py` rows of "New modules", and the `preferred_port` key of the `settings.py` row;
- the `/api/app/*` endpoints under "Changes to existing code";
- "Tray alerts" under "Connection status";
- the `requirements.txt` bullet (pywebview, pystray, Pillow) and the `--smoke` bullet under "Packaging and release";
- the Phase 3 manual checks under "Testing".

**Library facts this plan relies on** (read from the pinned versions' source on 2026-09-30):
- pywebview 6.2.1:
  - `webview.start()` must run on the main thread.
  - A window's `initialized` event handler receives the renderer name. Returning `False` from it makes `start()` return at once without opening a window.
  - Without WebView2, the renderer is `mshtml` (the old IE engine, which can't run this dashboard).
  - `PYWEBVIEW_GUI=mshtml` forces that path for testing.
  - `window.native` is the WinForms `Form`, set before the `before_show` event, which runs on the GUI thread.
  - A window created with `hidden=True` is shown and hidden once, so its `shown` event fires and `window.show()` works later.
  - `private_mode=False` with `storage_path` keeps WebView2's profile, and `localStorage` with it.
  - `target="_blank"` links open in the default browser.
  - `text_select` and `zoomable` default to `False`.
- pystray 0.19.5:
  - `Icon.run(setup)` calls `setup(icon)` on a separate thread once the icon exists, and `stop()` joins that thread for up to 5 s.
  - Menu actions may take 0, 1 or 2 arguments; bound methods count without `self`.
  - `MenuItem(checked=callable)` re-reads the callable each time the menu opens.
  - `Menu.SEPARATOR.text` is `'- - - -'`, and `Menu.items` returns the tuple of items.
  - `icon.notify(message, title)` shows a Windows notification.
- uvicorn 0.54: `Server.run(sockets=[sock])` serves a socket bound by the caller. Off the main thread it installs no signal handlers. `server.started` turns `True` after the lifespan startup, and `server.should_exit = True` stops it.

**Where this plan departs from the spec** (Task 6 amends the spec for each):
1. **Close to tray uses the WinForms form, not pywebview's `closing` event.**
   - The spec has the `closing` handler return `False`. That event can't tell the close button from Windows signing out or shutting down, and cancelling those would hold up the sign-out.
   - `desktop.py` instead handles the form's `FormClosing` event. It cancels and hides only when `CloseReason` is `UserClosing` and Quit wasn't chosen.
2. **"Check for updates" leaves the tray menu until Phase 4**, where `updates.py` is built.
3. **The tray reads connection status through its own server's `/api/connection`.**
   - It does this over HTTP on 127.0.0.1 every 30 s, which is in-process as the spec asks.
   - That call runs the normal quota fetch, so the figures, and the judge's 5-hour gate, stay fresh while no window is polling.
4. **Calls to the app's own server bypass HTTP proxies.**
   - `urllib` would otherwise send `127.0.0.1` through `HTTP_PROXY`, or through the Windows system proxy when its bypass list lacks it. That's common on corporate PCs.
5. **The Start at login checkbox respects Task Manager.**
   - Task Manager's Startup tab switches an entry off without deleting it (`Explorer\StartupApproved\Run`). Such an entry reads as off.
   - Turning it on from the tray clears that switch.
6. **`preferred_port`** is a settings key (default 8765) with no UI. It's used when it's an int from 1024 to 65535. When that port is taken or reserved by Windows, the app takes a free port from the OS for that run and doesn't save it.
7. **The notification also fires at startup**, when the first status the tray sees is already an alert state. Otherwise someone who is signed out at every login would never be told.
8. **Without WebView2 at a `--background` start**, no browser tab opens. The tray's Open dashboard opens one.
9. **A second launch that can't reach the first copy** tries for 10 seconds, then takes the mutex itself if the first copy has since quit. This covers a quick Quit-then-relaunch.

**Left for Phase 4** (note these in its plan):
- The installer's `AppMutex` must name the mutex in `instance.MUTEX_NAME`. Inno Setup checks the session namespace, which is what `Local\` means.
- The installer must write the `Run` value in exactly the format `autostart.command()` builds when frozen: `"<exe>" --background`. With any other format, the tray checkbox reads as off.
- The `.ico` for the exe and installer can be drawn by `tray.icon_image`.
- Also left: the "Check for updates" tray item, the window icon, and the README's install and SmartScreen sections.

## Global Constraints

- **Environment:** every `python` / `pytest` / `pip` command runs in the conda env. Each Bash call is a fresh shell, so prefix every command with:
  `eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && `
- **The env as of 2026-09-30:** Python 3.12.14, fastapi 0.142.2, starlette 1.7.0, uvicorn 0.54.0, jinja2 3.1.6, orjson 3.12.0, pytest 9.1.1 and httpx2 2.13.1.
  - Task 4 adds pywebview 6.2.1, pystray 0.19.5 and Pillow 12.3.0 with `pip install -r requirements.txt`. That also pulls their dependencies: pythonnet, clr_loader, cffi, pycparser, proxy_tools, bottle, typing_extensions and six.
  - Install nothing else.
- **Working directory:** the worktree root, `C:\Users\weaverjc\Projects\Personal\claude-usage-dashboard\.claude\worktrees\busy-visvesvaraya-0b60fc`, on branch `claude/desktop-shell`.
- **Baseline:** `python -m pytest -q` passes 258 tests with no warnings. Every task ends with the full suite green and a summary line showing no warnings.
- **Writing files:** use the Write and Edit tools, not bash heredocs.
  - Heredocs in this environment have dropped backslashes (seen 2026-09-30).
  - This plan is full of backslashes: `Local\ClaudeUsageDashboard`, registry key paths, and Windows paths in tests.
- **Tests stay isolated.** `tests/conftest.py` enforces most of this, and Tasks 1 and 2 add the registry and `runtime.json` guards. Tests must never:
  - touch `data/`, `~/.claude`, a real WSL distro or the network;
  - start a real process;
  - read or write the real registry;
  - write the real `runtime.json`;
  - open a real window or tray icon.

  Servers a test starts itself on 127.0.0.1 are fine. So are named mutexes with unique test names.
- **pywebview is imported only inside `desktop.run_window`**, so `import desktop` in tests loads no WinForms or .NET.
- **Windows only.** The new modules call kernel32, user32 and winreg directly.
- **Running from source must keep working.** Both `python app.py --port 8080` and the Task Scheduler launcher must still work. Leave `app.py`'s `__main__` block alone.
- **uvicorn gets the app object** (`app.app`), never the `"app:app"` string, which doesn't resolve in a frozen build.
- **Names:**
  - mutex `Local\ClaudeUsageDashboard`
  - `Run` value `ClaudeUsageDashboard`
  - runtime file `paths.DATA_DIR / "runtime.json"`
  - default port 8765
  - window and notification title `Claude Usage Dashboard`
- **Calls to the app's own server** go through `instance.call()`, which never uses a proxy.
- **Never press Sign in against the real `claude` CLI** when verifying. On 2026-09-29 it completed a real `claude auth login` unattended and created a session that had to be revoked.
- **Never read a stopped WSL distro's share** (`\\wsl.localhost\...`); it boots the distro.
- **User-facing text:** use the exact strings in this plan. The README text in Task 6 has already been through the humanize pass.
- **Commits:**
  - One commit per task, with a plain imperative message.
  - **No `Co-authored-by` or AI-attribution trailers** (user rule).
  - Never stage `.claude/`, `.superpowers/` or other AI tool files.
  - `CLAUDE.md` is tracked in this repo and updated alongside code (precedent `d7d6e88`).
- **Implementers never launch the GUI** (`python desktop.py` without `--smoke`); nobody could see or close it. The controller runs the manual checks at the end.

## Review Focus

These are inputs the spec implies but doesn't spell out. Each has a pinned test in the task named.

1. **Windows signs out or shuts down with the window open.** The close must go through, so the app never holds up a sign-out. Only the person's own close hides the window. (Task 5: `test_only_the_persons_own_close_hides_the_window`.)
2. **Port 8765 is taken by another program or reserved by Windows.** Windows reserves ranges for Hyper-V and WSL, and binding in one fails with `PermissionError`, not "address in use". The app must still start, on a free port. (Task 5: `test_bind_socket_falls_back_when_the_port_is_taken`, `test_bind_socket_falls_back_when_windows_refuses_the_port`.)
3. **A PC with `HTTP_PROXY` or a system proxy set.** The second launch and the tray talk to 127.0.0.1 directly, never through the proxy. (Task 2: `test_call_ignores_a_proxy_in_the_environment`.)
4. **A second launch while the first copy is still starting, or after a crash left a stale `runtime.json`.** The stale port may refuse, or some other program may answer on it with non-HTTP bytes. The second launch keeps trying for 10 s without crashing, and takes over if the first copy has gone. (Task 2: `test_show_running_waits_for_a_copy_that_is_still_starting`, `test_call_raises_oserror_when_something_else_answers`. Task 5: `test_a_second_launch_takes_over_when_the_first_copy_has_gone`.)
5. **Start at login was switched off in Task Manager.** The tray checkbox reads off, and turning it on works again. (Task 1: `test_task_manager_can_switch_it_off_and_enable_switches_it_back_on`.)

## Test instance (used by the manual checks after Task 6)

The always-on server on :8080 runs from the main checkout. Don't touch it. Run the desktop app from this worktree with a scratch home:
- `USERPROFILE` makes `Path.home()` point at the scratch home, so the app has no credentials and shows `signed-out` (or `not-installed`).
- `CUD_DATA_DIR` keeps its DB, settings, `runtime.json` and WebView2 profile out of the worktree.
- `PERSON_HOURS_WORKER=off` keeps the judge out of the way.

Start it with the Bash tool and `run_in_background: true`. `SP` is this session's scratchpad directory. Every Bash call is a fresh shell, so repeat the `SP=...` line in each call that uses `$SP`:
```bash
SP="C:/Users/weaverjc/AppData/Local/Temp/claude/C--Users-weaverjc-Projects-Personal-claude-usage-dashboard--claude-worktrees-busy-visvesvaraya-0b60fc/98bd79c0-669a-4517-a3f2-aeaa826c399a/scratchpad"
mkdir -p "$SP/h/.claude/projects" "$SP/h/data"
eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && \
  USERPROFILE="$(cygpath -w "$SP/h")" CUD_DATA_DIR="$(cygpath -w "$SP/h/data")" PERSON_HOURS_WORKER=off \
  python desktop.py
```
- **Don't press the banner's Sign in button.**
- **Stop it** with the tray's Quit. If that fails: `powershell -NoProfile -Command 'Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }'`

---

### Task 1: Start at login (`autostart.py`)

**Files:**
- Create: `autostart.py`
- Create: `tests/fake_winreg.py`
- Create: `tests/test_autostart.py`
- Modify: `tests/conftest.py` (new autouse fixture `fake_registry`)
- Modify: `CLAUDE.md` (architecture line, test-isolation gotcha)

**Interfaces:**
- Consumes: `paths.APP_NAME` (`"ClaudeUsageDashboard"`).
- Produces:
  - `autostart.command() -> str`
  - `autostart.is_enabled() -> bool`
  - `autostart.enable() -> None` and `autostart.disable() -> None`, both raising `OSError` when the registry refuses
  - the constants `RUN_KEY`, `APPROVED_KEY` and `VALUE_NAME`
  - an autouse fixture `fake_registry` returning a `FakeWinreg` with helpers `value(path, name)` and `set(path, name, value, kind=REG_SZ)`

- [ ] **Step 1: Write the fake registry**

Create `tests/fake_winreg.py`:

```python
"""An in-memory stand-in for the parts of winreg that autostart.py uses."""


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _missing():
    return FileNotFoundError(2, "The system cannot find the file specified")


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1
    REG_BINARY = 3

    def __init__(self):
        self.keys = {}  # (root, path) -> {value name: (data, kind)}

    def OpenKey(self, root, path, reserved=0, access=KEY_READ):
        if (root, path) not in self.keys:
            raise _missing()
        return FakeKey((root, path))

    def CreateKeyEx(self, root, path, reserved=0, access=KEY_READ):
        self.keys.setdefault((root, path), {})
        return FakeKey((root, path))

    def QueryValueEx(self, key, name):
        try:
            return self.keys[key.path][name]
        except KeyError:
            raise _missing() from None

    def SetValueEx(self, key, name, reserved, kind, data):
        self.keys[key.path][name] = (data, kind)

    def DeleteValue(self, key, name):
        try:
            del self.keys[key.path][name]
        except KeyError:
            raise _missing() from None

    # Helpers for tests
    def value(self, path, name):
        return self.keys.get((self.HKEY_CURRENT_USER, path), {}).get(name, (None, None))[0]

    def set(self, path, name, data, kind=REG_SZ):
        self.keys.setdefault((self.HKEY_CURRENT_USER, path), {})[name] = (data, kind)
```

- [ ] **Step 2: Add the registry guard to `tests/conftest.py`**

Add `import autostart` to the module imports (alphabetical, right after `import auth`) and `from fake_winreg import FakeWinreg` after the plain imports. Then append this fixture at the end of the file:

```python
@pytest.fixture(autouse=True)
def fake_registry(monkeypatch):
    """No test reads or writes the real registry: autostart gets an in-memory winreg."""
    registry = FakeWinreg()
    monkeypatch.setattr(autostart, "winreg", registry)
    return registry
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_autostart.py`:

```python
"""Start at login: the HKCU Run value, and Task Manager's switch for it."""
import sys
from pathlib import Path

import autostart

RUN = autostart.RUN_KEY
APPROVED = autostart.APPROVED_KEY
NAME = "ClaudeUsageDashboard"


def test_command_from_source_runs_desktop_py_without_a_console():
    cmd = autostart.command()
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    expected_exe = windowless if windowless.exists() else exe
    desktop_py = Path(autostart.__file__).with_name("desktop.py")
    assert cmd == f'"{expected_exe}" "{desktop_py}" --background'


def test_command_when_frozen_is_the_exe_itself(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\Claude Usage Dashboard\ClaudeUsageDashboard.exe")
    assert autostart.command() == r'"C:\Apps\Claude Usage Dashboard\ClaudeUsageDashboard.exe" --background'


def test_off_until_enabled_then_off_again(fake_registry):
    assert not autostart.is_enabled()
    autostart.enable()
    assert fake_registry.value(RUN, NAME) == autostart.command()
    assert autostart.is_enabled()
    autostart.disable()
    assert fake_registry.value(RUN, NAME) is None
    assert not autostart.is_enabled()


def test_disable_with_nothing_there_is_fine():
    autostart.disable()
    assert not autostart.is_enabled()


def test_a_value_for_another_copy_is_not_this_copy(fake_registry):
    fake_registry.set(RUN, NAME, r'"D:\Old\ClaudeUsageDashboard.exe" --background')
    assert not autostart.is_enabled()


def test_the_comparison_ignores_case_and_stray_spaces(fake_registry):
    fake_registry.set(RUN, NAME, autostart.command().upper() + " ")
    assert autostart.is_enabled()


def test_task_manager_can_switch_it_off_and_enable_switches_it_back_on(fake_registry):
    autostart.enable()
    # Task Manager's Startup tab writes 12 bytes; an odd first byte means "Disabled".
    fake_registry.set(APPROVED, NAME, bytes([3]) + bytes(11), fake_registry.REG_BINARY)
    assert not autostart.is_enabled()
    autostart.enable()
    assert fake_registry.value(APPROVED, NAME) is None
    assert autostart.is_enabled()


def test_a_task_manager_entry_marked_enabled_counts_as_on(fake_registry):
    autostart.enable()
    fake_registry.set(APPROVED, NAME, bytes([2]) + bytes(11), fake_registry.REG_BINARY)
    assert autostart.is_enabled()


def test_an_unreadable_registry_reads_as_off(fake_registry, monkeypatch):
    autostart.enable()

    def denied(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(fake_registry, "OpenKey", denied)
    assert not autostart.is_enabled()
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `python -m pytest tests/test_autostart.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'autostart'` (conftest imports it too, so every test file errors until Step 5).

- [ ] **Step 5: Write `autostart.py`**

```python
"""
Start at login: the HKCU Run value that starts the desktop app in the tray.

Windows runs each value under the Run key when the person signs in. Task Manager's Startup
tab can switch an entry off without deleting it: it records that under
Explorer\\StartupApproved\\Run. So is_enabled() reads both, and enable() clears Task
Manager's switch.
"""
import os
import sys
import winreg
from pathlib import Path

import paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = paths.APP_NAME  # the installer (Phase 4) writes the same value


def command() -> str:
    """What Windows runs at sign-in: this copy of the app, started hidden in the tray."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")  # no console window at sign-in
    if windowless.exists():
        exe = windowless
    return f'"{exe}" "{Path(__file__).with_name("desktop.py")}" --background'


def _query(key_path: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)[0]
    except OSError:
        return None


def is_enabled() -> bool:
    """True when Windows will start this copy of the app at sign-in."""
    value = _query(RUN_KEY)
    if not isinstance(value, str):
        return False
    if os.path.normcase(value.strip()) != os.path.normcase(command()):
        return False
    approved = _query(APPROVED_KEY)
    switched_off = isinstance(approved, bytes) and len(approved) > 0 and bool(approved[0] & 1)
    return not switched_off


def enable() -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
    _delete(APPROVED_KEY)  # undo a Task Manager "Disable"; no entry there counts as enabled


def disable() -> None:
    _delete(RUN_KEY)


def _delete(key_path: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_autostart.py -q`, then `python -m pytest -q`.
Expected: 9 passed in the new file, then the full suite green (267 passed), no warnings.

- [ ] **Step 7: Update `CLAUDE.md`**

In the Architecture code block, after the line starting `settings.py  User settings`, add:
```
autostart.py  Start at login for the desktop app: the HKCU Run value, and Task Manager's StartupApproved switch for it
```
In Gotchas, the bullet starting `- **Tests never touch `data/usage.db`.**` ends with "forbids `subprocess.run` / `subprocess.Popen`." Append to that bullet: ` Every test also gets an in-memory `winreg` in `autostart` (`tests/fake_winreg.py`), so none can touch the real registry.`

- [ ] **Step 8: Commit**

```bash
git add autostart.py tests/fake_winreg.py tests/test_autostart.py tests/conftest.py CLAUDE.md
git commit -m "Add the Start at login switch for the desktop app"
```

---

### Task 2: One copy per session (`instance.py`)

**Files:**
- Create: `instance.py`
- Create: `tests/test_instance.py`
- Modify: `tests/conftest.py` (new autouse fixture `isolated_runtime`)
- Modify: `CLAUDE.md` (architecture line, test-isolation gotcha)

**Interfaces:**
- Consumes: `paths.DATA_DIR`.
- Produces:
  - `instance.MUTEX_NAME = r"Local\ClaudeUsageDashboard"`
  - `instance.RUNTIME_FILE: Path`
  - `instance.acquire(name: str = MUTEX_NAME) -> int | None`: the handle, or `None` when another copy holds the mutex
  - `instance.release(handle) -> None`
  - `instance.write_runtime(port: int) -> None`
  - `instance.read_runtime() -> dict | None`: `{"port": int, "pid": int}`
  - `instance.clear_runtime(pid: int | None = None) -> None`
  - `instance.call(port: int, path: str, method: str = "GET", timeout: float = 5.0) -> bytes`: raises `OSError` on any failure or a non-2xx status
  - `instance.show_running(timeout: float = 10.0, *, sleep=time.sleep, clock=time.monotonic) -> bool`
  - an autouse fixture `isolated_runtime` returning the per-test runtime path

- [ ] **Step 1: Add the runtime-file guard to `tests/conftest.py`**

Add `import instance` to the module imports (alphabetical, after `import ingest`), and append:

```python
@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """runtime.json goes to a per-test file, so no test can point a real second launch at a test server."""
    path = tmp_path / "runtime.json"
    monkeypatch.setattr(instance, "RUNTIME_FILE", path)
    return path
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_instance.py`:

```python
"""One copy per session, runtime.json, and how a second launch reaches the first."""
import http.server
import json
import os
import socket
import threading
import uuid

import pytest

import instance


def unique_name():
    return rf"Local\ClaudeUsageDashboardTest-{uuid.uuid4().hex}"


def test_the_first_copy_gets_the_mutex_and_a_second_does_not():
    name = unique_name()
    first = instance.acquire(name)
    assert first
    try:
        assert instance.acquire(name) is None
    finally:
        instance.release(first)
    again = instance.acquire(name)  # Windows drops the mutex with its last handle
    assert again
    instance.release(again)


def test_runtime_round_trip(isolated_runtime):
    instance.write_runtime(8765)
    assert instance.read_runtime() == {"port": 8765, "pid": os.getpid()}
    assert json.loads(isolated_runtime.read_text(encoding="utf-8")) == {"port": 8765, "pid": os.getpid()}


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"port": "8765", "pid": 1}',
                                     '{"port": 0, "pid": 1}', '{"port": 70000, "pid": 1}',
                                     '{"port": 8765}', '{"port": true, "pid": 1}'])
def test_a_missing_or_garbled_runtime_file_reads_as_none(isolated_runtime, content):
    assert instance.read_runtime() is None
    isolated_runtime.write_text(content, encoding="utf-8")
    assert instance.read_runtime() is None


def test_clear_runtime_only_removes_its_own_file(isolated_runtime):
    instance.write_runtime(8765)
    instance.clear_runtime(pid=os.getpid() + 1)
    assert isolated_runtime.exists()
    instance.clear_runtime(pid=os.getpid())
    assert not isolated_runtime.exists()
    instance.write_runtime(8765)
    instance.clear_runtime()  # no pid: whatever is there goes
    assert not isolated_runtime.exists()
    instance.clear_runtime()  # nothing there: fine


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        found = self.path == "/api/app/show"
        body = b'{"shown": true}' if found else b'{"detail": "Not Found"}'
        self.send_response(200 if found else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port
    server.shutdown()
    server.server_close()


def test_call_ignores_a_proxy_in_the_environment(monkeypatch, local_server):
    # urllib's default opener would send this through the dead proxy and fail.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    assert instance.call(local_server, "/api/app/show", method="POST") == b'{"shown": true}'


def test_call_raises_oserror_for_an_error_status(local_server):
    with pytest.raises(OSError):
        instance.call(local_server, "/api/nope", method="POST")


def test_call_raises_oserror_when_something_else_answers():
    """A stale runtime.json can name a port that some other program now answers on."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def answer_garbage():
        conn, _ = listener.accept()
        conn.recv(1024)
        conn.sendall(b"hello\r\n\r\n")
        conn.close()

    threading.Thread(target=answer_garbage, daemon=True).start()
    try:
        with pytest.raises(OSError):
            instance.call(listener.getsockname()[1], "/api/app/show", method="POST")
    finally:
        listener.close()


class Clock:
    """Fake time for show_running: sleeping advances it instantly."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_show_running_asks_the_port_in_runtime_json(monkeypatch):
    instance.write_runtime(8765)
    calls = []

    def fake_call(port, path, method="GET", timeout=5.0):
        calls.append((port, path, method))
        return b'{"shown": true}'

    monkeypatch.setattr(instance, "call", fake_call)
    clock = Clock()
    assert instance.show_running(sleep=clock.sleep, clock=clock.time) is True
    assert calls == [(8765, "/api/app/show", "POST")]
    assert clock.sleeps == []


def test_show_running_waits_for_a_copy_that_is_still_starting(monkeypatch):
    """No runtime.json yet, then the previous run's port (refused), then the real one."""
    clock = Clock()

    def fake_sleep(seconds):
        clock.sleep(seconds)
        if len(clock.sleeps) == 2:
            instance.write_runtime(9999)
        if len(clock.sleeps) == 4:
            instance.write_runtime(8765)

    def fake_call(port, path, method="GET", timeout=5.0):
        if port != 8765:
            raise ConnectionRefusedError(10061, "No connection could be made")
        return b'{"shown": true}'

    monkeypatch.setattr(instance, "call", fake_call)
    assert instance.show_running(sleep=fake_sleep, clock=clock.time) is True
    assert len(clock.sleeps) == 4


def test_show_running_gives_up_after_the_timeout(monkeypatch):
    instance.write_runtime(8765)

    def refused(*args, **kwargs):
        raise ConnectionRefusedError(10061, "No connection could be made")

    monkeypatch.setattr(instance, "call", refused)
    clock = Clock()
    assert instance.show_running(timeout=10, sleep=clock.sleep, clock=clock.time) is False
    assert clock.now == 10.0 and len(clock.sleeps) == 20
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_instance.py -q`
Expected: collection errors, `ModuleNotFoundError: No module named 'instance'`.

- [ ] **Step 4: Write `instance.py`**

```python
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
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_instance.py -q`, then `python -m pytest -q`.
Expected: 17 passed in the new file (the parametrized garble test counts 8), then the full suite green, no warnings.

- [ ] **Step 6: Update `CLAUDE.md`**

In the Architecture code block, after the `autostart.py` line, add:
```
instance.py  One desktop app per Windows session: the Local\ClaudeUsageDashboard mutex, data/runtime.json (port, pid), proxy-free calls to the app's own server
```
In Gotchas, append to the `**Tests never touch `data/usage.db`.**` bullet: ` `instance.RUNTIME_FILE` points at a per-test file too.`

- [ ] **Step 7: Commit**

```bash
git add instance.py tests/test_instance.py tests/conftest.py CLAUDE.md
git commit -m "Keep the desktop app to one copy and let a second launch find the first"
```

---

### Task 3: `/api/app/info` and `/api/app/show`

**Files:**
- Modify: `app.py` (a `desktop_show` hook and two endpoints, placed after the `/api/settings` handlers)
- Create: `tests/test_app_shell.py`
- Modify: `CLAUDE.md` (endpoint count, API list)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `app.desktop_show`: a module-level callable or `None`. `desktop.py` sets it to its window's show function (Task 5).
  - `GET /api/app/info` returns `{"name", "version", "desktop", "pid", "data_dir", "log_dir"}`.
  - `POST /api/app/show` returns `{"shown": true}`, or 409 when `desktop_show` is `None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app_shell.py`:

```python
"""The endpoints the desktop shell uses: which program is on this port, and bring its window forward."""
import os

from fastapi.testclient import TestClient

import app
import paths
import version


def client():
    return TestClient(app.app, base_url="http://127.0.0.1:8765")


def test_info_names_the_app_and_says_whether_a_window_is_attached(monkeypatch):
    info = client().get("/api/app/info").json()
    assert info == {"name": "ClaudeUsageDashboard", "version": version.__version__, "desktop": False,
                    "pid": os.getpid(), "data_dir": str(paths.DATA_DIR), "log_dir": str(paths.LOG_DIR)}
    monkeypatch.setattr(app, "desktop_show", lambda: None)
    assert client().get("/api/app/info").json()["desktop"] is True


def test_show_brings_the_window_forward(monkeypatch):
    shown = []
    monkeypatch.setattr(app, "desktop_show", lambda: shown.append(True))
    response = client().post("/api/app/show")
    assert response.status_code == 200
    assert response.json() == {"shown": True}
    assert shown == [True]


def test_show_without_a_desktop_window_is_a_conflict():
    response = client().post("/api/app/show")
    assert response.status_code == 409
    assert "on its own" in response.json()["detail"]


def test_a_web_page_cannot_raise_the_window(monkeypatch):
    shown = []
    monkeypatch.setattr(app, "desktop_show", lambda: shown.append(True))
    response = client().post("/api/app/show", headers={"Origin": "https://example.com"})
    assert response.status_code == 403
    assert shown == []
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_app_shell.py -q`
Expected: 4 failed (404 for both routes, `AttributeError` for `desktop_show` in the monkeypatching tests).

- [ ] **Step 3: Add the hook and the endpoints to `app.py`**

Add this after the `_hours_lock = threading.Lock()` line:

```python

# desktop.py sets this to its window's show(); it stays None when app.py runs on its own.
desktop_show = None
```

Add these handlers directly after the `post_settings` function (before `@app.get("/api/ingest-status")`):

```python
@app.get("/api/app/info")
def app_info():
    """Which program answers on this port, and where it keeps its files."""
    return {"name": paths.APP_NAME, "version": version.__version__,
            "desktop": desktop_show is not None, "pid": os.getpid(),
            "data_dir": str(paths.DATA_DIR), "log_dir": str(paths.LOG_DIR)}


@app.post("/api/app/show")
def app_show():
    """Bring the desktop window forward. A second launch of the app asks this of the first."""
    if desktop_show is None:
        raise HTTPException(409, "No desktop window: this server was started on its own")
    desktop_show()
    return {"shown": True}
```

(`os`, `paths`, `version` and `HTTPException` are already imported. Both handlers are plain `def`, so `desktop_show()` runs in the thread pool and never blocks the event loop.)

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_app_shell.py -q`, then `python -m pytest -q`.
Expected: 4 passed, then the full suite green, no warnings.

- [ ] **Step 5: Update `CLAUDE.md`**

- In the Architecture block, change `app.py       FastAPI server — 20 API endpoints,` to `app.py       FastAPI server — 22 API endpoints,`.
- In "API Endpoints", append `, `/api/app/info`, `/api/app/show` (POST)` to the end of the first line (the endpoint list).
- Add this paragraph after the `/api/connection` paragraph:

```
`/api/app/info` names the program on the port (`name`, `version`, `desktop`, `pid`, `data_dir`, `log_dir`). `/api/app/show` brings the desktop window forward; a second launch of the desktop app calls it. It answers 409 when `app.py` runs on its own, with no window attached.
```

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app_shell.py CLAUDE.md
git commit -m "Add the endpoints a second launch uses to reach the desktop window"
```

---

### Task 4: The tray icon (`tray.py`) and the desktop packages

**Files:**
- Modify: `requirements.txt` (three pins)
- Modify: `tests/test_release_files.py` (`test_runtime_requirements_are_pinned`)
- Create: `tray.py`
- Create: `tests/test_tray.py`
- Modify: `CLAUDE.md` (architecture line, env note)

**Interfaces:**
- Consumes:
  - `autostart.is_enabled()`, `autostart.enable()` and `autostart.disable()` (Task 1)
  - `instance.call(port, path, method="GET", timeout=5.0) -> bytes` (Task 2)
  - `paths.APP_NAME`
- Produces:
  - `tray.TITLE = "Claude Usage Dashboard"`, `tray.POLL_SECONDS = 30`, `tray.ALERT_STATES`, `tray.RING`, `tray.BADGE`
  - `tray.icon_image(attention: bool, size: int = 64) -> PIL.Image.Image`
  - `tray.Alerts` with `.state`, `.attention: bool` and `.observe(status: dict) -> str | None`
  - `tray.Tray(shell, url: str, port: int, icon=None)`, where `shell` has `show()` and `quit()`. Its methods: `.menu() -> pystray.Menu`, `.start()`, `.stop()`, `.poll_once()`, `.toggle_autostart()`, and attributes `.icon` and `.alerts`.

- [ ] **Step 1: Pin and install the packages**

Append to `requirements.txt` (after `orjson==3.12.0`):
```
pywebview==6.2.1
pystray==0.19.5
pillow==12.3.0
```
Run: `pip install -r requirements.txt` and then `pip check`.
Expected: pywebview, pystray, pillow, pythonnet, clr_loader, cffi, pycparser, proxy_tools, bottle, typing_extensions and six installed; `pip check` says "No broken requirements found."
Then check that the GUI stack loads: `python -c "import webview, pystray, PIL, clr; print(webview.__name__, pystray.Icon.HAS_NOTIFICATION)"`.
Expected: `webview True`.

- [ ] **Step 2: Update the pinned-requirements test**

In `tests/test_release_files.py::test_runtime_requirements_are_pinned`, change the expected set to:
```python
    assert {r.split("==")[0].lower() for r in reqs} == {"fastapi", "uvicorn", "jinja2", "orjson",
                                                          "pywebview", "pystray", "pillow"}
```
Run: `python -m pytest tests/test_release_files.py -q`
Expected: 5 passed.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_tray.py`:

```python
"""The tray icon: its image, its menu, and when it badges or notifies."""
import json

import pystray

import autostart
import tray

URL = "http://127.0.0.1:8765/"
CONNECTED = {"state": "connected", "title": "Connected to Claude.", "detail": ""}
EXPIRED = {"state": "token-expired", "title": "Your sign-in token expired at 3:12 AM.",
           "detail": "It renews the next time you use Claude Code. Quota figures are paused until then."}
UNAVAILABLE = {"state": "unavailable", "title": "Couldn't reach Anthropic for quota figures.",
               "detail": "Retrying automatically."}
SIGNED_OUT = {"state": "signed-out", "title": "Not signed in to Claude Code.",
              "detail": "Sign in to Claude Code to see your quota."}
LOGIN = {"state": "login-required", "title": "Your Claude sign-in has expired.",
         "detail": "Sign in again to bring back the quota gauges."}


class FakeIcon:
    def __init__(self):
        self.icon = None
        self.title = tray.TITLE
        self.visible = False
        self.notes = []
        self.menu_updates = 0
        self.stopped = False

    def notify(self, message, title=None):
        self.notes.append((message, title))

    def update_menu(self):
        self.menu_updates += 1

    def stop(self):
        self.stopped = True


class FakeShell:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def quit(self):
        self.calls.append("quit")


def make_tray(monkeypatch, answers):
    """A tray whose /api/connection answers come from `answers`: a status dict, raw bytes,
    or an exception to raise."""
    answers = iter(answers)

    def fake_call(port, path, method="GET", timeout=5.0):
        assert (port, path, method) == (8765, "/api/connection", "GET")
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()

    monkeypatch.setattr(tray.instance, "call", fake_call)
    return tray.Tray(FakeShell(), URL, 8765, icon=FakeIcon())


def test_the_badge_is_the_only_difference_between_the_two_images():
    plain, badged = tray.icon_image(False), tray.icon_image(True)
    assert plain.size == badged.size == (64, 64)
    assert plain.mode == badged.mode == "RGBA"
    assert tray.RING in plain.getdata()
    assert tray.BADGE not in plain.getdata()
    assert tray.BADGE in badged.getdata()


def test_one_notification_per_problem_and_the_badge_follows_the_state(monkeypatch):
    t = make_tray(monkeypatch, [CONNECTED, SIGNED_OUT, SIGNED_OUT, LOGIN, CONNECTED])
    t.poll_once()
    assert t.icon.notes == [] and t.icon.title == tray.TITLE
    t.poll_once()
    assert t.icon.notes == [("Not signed in to Claude Code. Sign in to Claude Code to see your quota.",
                             tray.TITLE)]
    assert t.icon.title == "Claude Usage Dashboard: Not signed in to Claude Code."
    assert tray.BADGE in t.icon.icon.getdata()
    t.poll_once()
    assert len(t.icon.notes) == 1
    t.poll_once()  # a different problem gets its own notification
    assert len(t.icon.notes) == 2
    assert t.icon.notes[-1][0] == "Your Claude sign-in has expired. Sign in again to bring back the quota gauges."
    t.poll_once()
    assert len(t.icon.notes) == 2
    assert t.icon.title == tray.TITLE
    assert tray.BADGE not in t.icon.icon.getdata()


def test_starting_in_an_alert_state_notifies_once(monkeypatch):
    t = make_tray(monkeypatch, [SIGNED_OUT, SIGNED_OUT])
    t.poll_once()
    t.poll_once()
    assert len(t.icon.notes) == 1


def test_an_expired_token_or_an_outage_needs_nobody(monkeypatch):
    t = make_tray(monkeypatch, [EXPIRED, UNAVAILABLE])
    t.poll_once()
    t.poll_once()
    assert t.icon.notes == []
    assert t.icon.title == tray.TITLE
    assert not t.alerts.attention


def test_an_unreachable_or_garbled_server_changes_nothing(monkeypatch):
    t = make_tray(monkeypatch, [SIGNED_OUT, ConnectionRefusedError(10061, "refused"), b"<html>", b"[]",
                                SIGNED_OUT])
    for _ in range(5):
        t.poll_once()
    assert len(t.icon.notes) == 1
    assert t.alerts.state == "signed-out"


def test_menu_items_and_their_actions(monkeypatch):
    t = make_tray(monkeypatch, [])
    menu = t.menu()
    assert [item.text for item in menu.items] == [
        "Open dashboard", "Open in browser", "Start at login", pystray.Menu.SEPARATOR.text, "Quit"]
    assert menu.items[0].default
    items = {item.text: item for item in menu.items}
    opened = []
    monkeypatch.setattr(tray.webbrowser, "open", opened.append)
    items["Open dashboard"](t.icon)
    items["Open in browser"](t.icon)
    items["Quit"](t.icon)
    assert t.shell.calls == ["show", "quit"]
    assert opened == [URL]


def test_start_at_login_toggles_and_reads_back(monkeypatch):
    t = make_tray(monkeypatch, [])
    item = {i.text: i for i in t.menu().items}["Start at login"]
    assert item.checked is False
    item(t.icon)
    assert autostart.is_enabled() and item.checked is True
    item(t.icon)
    assert not autostart.is_enabled() and item.checked is False
    assert t.icon.menu_updates == 2


def test_a_registry_refusal_is_reported_not_raised(monkeypatch):
    t = make_tray(monkeypatch, [])

    def denied():
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(tray.autostart, "enable", denied)
    t.toggle_autostart()
    assert t.icon.notes == [("Couldn't change Start at login: Access is denied", tray.TITLE)]
    assert t.icon.menu_updates == 1


def test_stop_ends_the_polls_and_removes_the_icon(monkeypatch):
    t = make_tray(monkeypatch, [])
    t.stop()
    assert t.icon.stopped
    t._run_polls(t.icon)  # returns at once instead of polling every 30 s
    assert t.icon.visible
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `python -m pytest tests/test_tray.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'tray'`.

- [ ] **Step 5: Write `tray.py`**

```python
"""
The desktop app's tray icon: its menu, its attention badge, and one Windows notification
when the Claude sign-in needs the person.

Every 30 s it reads /api/connection from the app's own server. That call also runs the
usual quota fetch, so the figures stay fresh while no window is polling.
"""
import json
import threading
import webbrowser
from datetime import datetime

import pystray
from PIL import Image, ImageDraw

import autostart
import instance
import paths

TITLE = "Claude Usage Dashboard"
POLL_SECONDS = 30
# The states the person has to act on. token-expired isn't one: without automatic renewal
# it happens most nights and clears the next time they use Claude Code.
ALERT_STATES = frozenset({"not-installed", "signed-out", "login-required"})

RING = (224, 122, 95, 255)        # #E07A5F, the favicon's ring
BADGE = (242, 177, 52, 255)       # amber: something needs the person
BADGE_EDGE = (17, 16, 16, 255)    # the page background, #111010, to set the dot off the ring


def icon_image(attention: bool, size: int = 64) -> Image.Image:
    """The favicon's open ring, plus an amber dot when the sign-in needs attention."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    width = max(2, size // 8)
    inset = width // 2 + 1
    # The favicon's dash covers 60 of the circle's 88 units from the top: about 245 degrees.
    draw.arc((inset, inset, size - 1 - inset, size - 1 - inset), start=-90, end=155,
             fill=RING, width=width)
    if attention:
        radius = size * 0.22
        centre = size - 1 - radius
        draw.ellipse((centre - radius, centre - radius, centre + radius, centre + radius),
                     fill=BADGE, outline=BADGE_EDGE, width=max(1, size // 32))
    return image


class Alerts:
    """Turns successive connection states into the badge and at most one notification per problem."""

    def __init__(self):
        self.state = None

    @property
    def attention(self) -> bool:
        return self.state in ALERT_STATES

    def observe(self, status: dict) -> str | None:
        """Record a /api/connection result. Returns the notification text when it enters an
        alert state, the first result included."""
        state = status.get("state")
        entered = state in ALERT_STATES and state != self.state
        self.state = state
        if not entered:
            return None
        return " ".join(part for part in (status.get("title"), status.get("detail")) if part)


class Tray:
    """The icon and its menu. `shell` is desktop.Shell: show() and quit()."""

    def __init__(self, shell, url: str, port: int, icon=None):
        self.shell = shell
        self.url = url
        self.port = port
        self.alerts = Alerts()
        self._badge_shown = False
        self._stop = threading.Event()
        self.icon = icon or pystray.Icon(paths.APP_NAME, icon_image(False), TITLE, menu=self.menu())

    def menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Open dashboard", self.shell.show, default=True),
            pystray.MenuItem("Open in browser", lambda: webbrowser.open(self.url)),
            pystray.MenuItem("Start at login", self.toggle_autostart,
                             checked=lambda item: autostart.is_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.shell.quit),
        )

    def start(self) -> None:
        """Show the icon from its own thread. Polling starts once the icon is up."""
        threading.Thread(target=self.icon.run, kwargs={"setup": self._run_polls},
                         name="tray", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.icon.stop()

    def _run_polls(self, icon) -> None:
        # pystray calls this on its own thread once the icon exists, so notify() works.
        icon.visible = True
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as ex:  # keep polling: one bad round mustn't freeze the badge
                print(f"[tray {datetime.now():%Y-%m-%d %H:%M:%S}] poll failed - "
                      f"{type(ex).__name__}: {ex}")
            self._stop.wait(POLL_SECONDS)

    def poll_once(self) -> None:
        try:
            status = json.loads(instance.call(self.port, "/api/connection", timeout=20))
        except (OSError, ValueError):
            return  # the server is starting or stopping; the next poll tries again
        if not isinstance(status, dict):
            return
        message = self.alerts.observe(status)
        if self.alerts.attention != self._badge_shown:
            self._badge_shown = self.alerts.attention
            self.icon.icon = icon_image(self._badge_shown)
        title = f"{TITLE}: {status.get('title')}" if self._badge_shown else TITLE
        if self.icon.title != title[:127]:
            self.icon.title = title[:127]  # Windows cuts tray tooltips at 127 characters
        if message:
            self.icon.notify(message[:255], TITLE)  # Windows' limit for notification text

    def toggle_autostart(self) -> None:
        try:
            if autostart.is_enabled():
                autostart.disable()
            else:
                autostart.enable()
        except OSError as ex:
            self.icon.notify(f"Couldn't change Start at login: {ex.strerror or ex}", TITLE)
        self.icon.update_menu()
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_tray.py -q`, then `python -m pytest -q`.
Expected: 9 passed, then the full suite green, no warnings.

- [ ] **Step 7: Update `CLAUDE.md`**

- In the Architecture block, after the `instance.py` line, add:
```
tray.py      The desktop app's tray icon (pystray): menu, amber attention dot, one notification when the sign-in needs the person; polls /api/connection every 30 s
```
- In "Setup & Running", after the paragraph that starts "`environment.yml` installs `requirements.txt`", add this sentence to the end of that paragraph: `An env created before the desktop packages (pywebview, pystray, Pillow) were pinned needs `pip install -r requirements.txt` inside the activated env.`

- [ ] **Step 8: Commit**

```bash
git add requirements.txt tests/test_release_files.py tray.py tests/test_tray.py CLAUDE.md
git commit -m "Add the tray icon with its attention badge and sign-in notification"
```

---

### Task 5: The desktop entry point (`desktop.py`) and `preferred_port`

**Files:**
- Modify: `settings.py` (`DEFAULTS`)
- Modify: `tests/test_settings.py`, `tests/test_app_settings.py` (the new key in exact-dict assertions)
- Create: `desktop.py`
- Create: `tests/test_desktop.py`
- Modify: `CLAUDE.md` (architecture lines)

**Interfaces:**
- Consumes:
  - `app.app`, `app.desktop_show` (Task 3), `app.startup` (monkeypatched in tests)
  - `instance.acquire`, `release`, `clear_runtime`, `write_runtime`, `call` and `show_running` (Task 2)
  - `tray.Tray(shell, url, port)` with `.start()` and `.stop()` (Task 4)
  - `settings.get("preferred_port")`, `applog.open_log()`, `applog.utf8_stdio()` and `paths.DATA_DIR`
- Produces:
  - `desktop.main(argv=None) -> int`
  - `desktop.smoke() -> int`
  - `desktop.bind_socket(preferred: int) -> socket.socket`
  - `desktop.start_server(sock) -> tuple[uvicorn.Server, threading.Thread]`
  - `desktop.wait_started(server, thread, timeout=60.0) -> bool`
  - `desktop.Shell(url, server, open_browser=webbrowser.open, restore=_restore_if_minimized)`, with `show()`, `hides_on_close(user_closing) -> bool`, `use_browser(background)` and `quit()`, and attributes `window`, `hwnd`, `tray`, `quitting` and `stopped`
  - `desktop.run_window(shell, background)`
  - `settings.DEFAULTS["preferred_port"] == 8765`

- [ ] **Step 1: Add `preferred_port` to settings, test first**

In `tests/test_settings.py`:
- `test_defaults_when_there_is_no_file`: expect `{"judge_enabled": False, "auto_refresh_token": False, "preferred_port": 8765}`.
- `test_update_keeps_the_other_keys`: expect `{"judge_enabled": True, "auto_refresh_token": True, "preferred_port": 8765}`.
- `test_unreadable_file_or_wrong_types_fall_back_to_defaults`: the last assertion expects `{"judge_enabled": False, "auto_refresh_token": True, "preferred_port": 8765}`.
- Add three rows to the `bad` parametrize list of `test_update_rejects_unknown_keys_and_wrong_types`: `{"preferred_port": "8765"}`, `{"preferred_port": True}`, `{"preferred_port": 8765.0}`.
- Add:

```python
def test_the_desktop_port_can_be_changed():
    assert settings.update({"preferred_port": 9123})["preferred_port"] == 9123
    assert settings.get("preferred_port") == 9123
```

In `tests/test_app_settings.py`, the assertion `assert app.get_settings() == {"judge_enabled": False, "auto_refresh_token": False}` becomes `== {"judge_enabled": False, "auto_refresh_token": False, "preferred_port": 8765}`.

Run: `python -m pytest tests/test_settings.py tests/test_app_settings.py -q`
Expected: the changed and new tests fail (`preferred_port` missing, or "unknown setting").

In `settings.py`, `DEFAULTS` becomes:
```python
DEFAULTS = {
    "judge_enabled": False,       # the person-hours judge spends subscription quota: opt-in
    "auto_refresh_token": False,  # renewing rewrites ~/.claude/.credentials.json: opt-in
    "preferred_port": 8765,       # the desktop app's first-choice port (desktop.py); no UI
}
```
Run the two files again. Expected: all pass.

- [ ] **Step 2: Write the failing desktop tests**

Create `tests/test_desktop.py`:

```python
"""The desktop entry point: port choice, the server thread, the window's rules, and --smoke."""
import socket

import pytest

import app
import desktop

URL = "http://127.0.0.1:8765/"


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_bind_socket_takes_the_preferred_port_when_it_is_free():
    port = free_port()
    sock = desktop.bind_socket(port)
    try:
        assert sock.getsockname() == ("127.0.0.1", port)
    finally:
        sock.close()


def test_bind_socket_falls_back_when_the_port_is_taken():
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    try:
        sock = desktop.bind_socket(taken.getsockname()[1])
        try:
            assert sock.getsockname()[1] not in (0, taken.getsockname()[1])
        finally:
            sock.close()
    finally:
        taken.close()


def test_bind_socket_falls_back_when_windows_refuses_the_port(monkeypatch):
    """Ports in a range Windows reserves for Hyper-V or WSL fail with WinError 10013."""
    real_socket = socket.socket

    class Refusing(real_socket):
        def bind(self, address):
            if address[1] != 0:
                raise PermissionError(10013, "An attempt was made to access a socket in a way "
                                             "forbidden by its access permissions")
            return super().bind(address)

    monkeypatch.setattr(desktop.socket, "socket", Refusing)
    sock = desktop.bind_socket(8765)
    try:
        assert sock.getsockname()[1] not in (0, 8765)
    finally:
        sock.close()


@pytest.mark.parametrize("unusable", [0, 80, 70000, -1])
def test_an_unusable_preferred_port_goes_straight_to_the_os(unusable):
    sock = desktop.bind_socket(unusable)
    try:
        assert sock.getsockname()[1] >= 1024
    finally:
        sock.close()


class FakeWindow:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def destroy(self):
        self.calls.append("destroy")


class FakeServer:
    should_exit = False


class FakeTray:
    stopped = False

    def stop(self):
        self.stopped = True


def make_shell():
    opened, restored = [], []
    shell = desktop.Shell(URL, FakeServer(), open_browser=opened.append, restore=restored.append)
    return shell, opened, restored


def test_show_restores_a_minimized_window_then_brings_it_forward():
    shell, opened, restored = make_shell()
    shell.window, shell.hwnd = FakeWindow(), 4242
    shell.show()
    assert restored == [4242]
    assert shell.window.calls == ["show"]
    assert opened == []


def test_without_a_window_show_opens_the_browser():
    shell, opened, _ = make_shell()
    shell.show()
    assert opened == [URL]


def test_the_browser_stands_in_for_the_window_but_not_at_a_background_start():
    shell, opened, _ = make_shell()
    shell.window = FakeWindow()
    shell.use_browser(background=True)
    assert shell.window is None and opened == []
    shell.use_browser(background=False)
    assert opened == [URL]


def test_only_the_persons_own_close_hides_the_window():
    shell, _, _ = make_shell()
    assert shell.hides_on_close(user_closing=True)
    assert not shell.hides_on_close(user_closing=False)  # Windows signing out, Task Manager
    shell.quitting = True
    assert not shell.hides_on_close(user_closing=True)  # the close that Quit makes


def test_quit_stops_the_server_the_tray_and_the_window_once():
    shell, _, _ = make_shell()
    shell.window, shell.tray = FakeWindow(), FakeTray()
    shell.quit()
    shell.quit()
    assert shell.server.should_exit
    assert shell.tray.stopped
    assert shell.window.calls == ["destroy"]
    assert shell.stopped.is_set()


def test_quit_still_finishes_when_the_window_is_already_gone():
    shell, _, _ = make_shell()

    class GoneWindow(FakeWindow):
        def destroy(self):
            raise RuntimeError("window already closed")

    shell.window = GoneWindow()
    shell.quit()
    assert shell.server.should_exit and shell.stopped.is_set()


class Stop(Exception):
    """Raised by a stub to end main() once it has shown which way it went."""


@pytest.fixture
def quiet_main(monkeypatch):
    monkeypatch.setattr(desktop, "setup_logging", lambda: None)


def test_a_second_launch_asks_the_first_to_show_and_exits(monkeypatch, quiet_main):
    monkeypatch.setattr(desktop.instance, "acquire", lambda: None)
    asked = []
    monkeypatch.setattr(desktop.instance, "show_running", lambda: asked.append(True) or True)

    def must_not_serve(preferred):
        raise AssertionError("a second copy must not start a server")

    monkeypatch.setattr(desktop, "bind_socket", must_not_serve)
    assert desktop.main([]) == 0
    assert asked == [True]


def test_a_second_launch_takes_over_when_the_first_copy_has_gone(monkeypatch, quiet_main):
    handles = iter([None, 1234])
    monkeypatch.setattr(desktop.instance, "acquire", lambda: next(handles))
    monkeypatch.setattr(desktop.instance, "show_running", lambda: False)

    def proceed(preferred):
        raise Stop

    monkeypatch.setattr(desktop, "bind_socket", proceed)
    with pytest.raises(Stop):
        desktop.main([])


def test_a_second_launch_that_reaches_nobody_fails_quietly(monkeypatch, quiet_main):
    monkeypatch.setattr(desktop.instance, "acquire", lambda: None)
    monkeypatch.setattr(desktop.instance, "show_running", lambda: False)
    assert desktop.main([]) == 1


def test_smoke_skips_the_mutex(monkeypatch, quiet_main):
    def no_mutex():
        raise AssertionError("--smoke must not take the mutex")

    monkeypatch.setattr(desktop.instance, "acquire", no_mutex)
    monkeypatch.setattr(desktop, "smoke", lambda: 0)
    assert desktop.main(["--smoke"]) == 0


def test_smoke_passes_against_a_working_server(monkeypatch):
    async def no_startup():
        pass

    monkeypatch.setattr(app, "startup", no_startup)
    assert desktop.smoke() == 0


def test_smoke_fails_when_the_server_cannot_start(monkeypatch):
    async def broken_startup():
        raise RuntimeError("startup broke")

    monkeypatch.setattr(app, "startup", broken_startup)
    assert desktop.smoke() == 1
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_desktop.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'desktop'`.

- [ ] **Step 4: Write `desktop.py`**

```python
"""
The desktop app: the dashboard in its own window, and a tray icon that keeps it running.

`python desktop.py` (or the frozen ClaudeUsageDashboard.exe) runs everything in one process:
- the main thread runs the pywebview window (WebView2); closing it hides it to the tray;
- a thread runs the uvicorn server on 127.0.0.1, on settings' preferred_port when it's free;
- tray.py's icon polls the connection and offers Open, Start at login and Quit.
One copy runs per Windows session; launching another brings the first one's window forward.
Without WebView2 the dashboard opens in the default browser and the tray works as usual.

--background starts in the tray only (what Start at login runs). --smoke starts the server
on a free port, checks / and /api/connection, and exits 0 or 1.
"""
import argparse
import ctypes
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from ctypes import wintypes
from datetime import datetime

import uvicorn

import app
import applog
import instance
import paths
import settings
import tray

TITLE = "Claude Usage Dashboard"
SW_RESTORE = 9

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.IsIconic.argtypes = (wintypes.HWND,)
_user32.IsIconic.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.ShowWindow.restype = wintypes.BOOL


def log(message: str) -> None:
    print(f"[desktop {datetime.now():%Y-%m-%d %H:%M:%S}] {message}")


def bind_socket(preferred: int) -> socket.socket:
    """A socket bound to 127.0.0.1: on `preferred` when that's free, else on a port the OS picks.

    Another program can hold 8765, or it can sit in a range Windows reserves (Hyper-V, WSL),
    which fails with PermissionError rather than "address in use". Either way, fall back.
    """
    if 1024 <= preferred <= 65535:
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", preferred))
            return sock
        except OSError:
            sock.close()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock


def start_server(sock: socket.socket) -> tuple[uvicorn.Server, threading.Thread]:
    """Serve the dashboard on `sock` from a daemon thread.

    uvicorn gets the app object: the "app:app" import string doesn't resolve in a frozen
    build. No access log: the page's 5-second polls would fill dashboard.log.
    """
    config = uvicorn.Config(app.app, lifespan="on", access_log=False, timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="server",
                              daemon=True)
    thread.start()
    return server, thread


def wait_started(server: uvicorn.Server, thread: threading.Thread, timeout: float = 60.0) -> bool:
    """True once the server is serving; False if it stopped, or took longer than `timeout`."""
    deadline = time.monotonic() + timeout
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _restore_if_minimized(hwnd) -> None:
    if hwnd and _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)  # back to its earlier size, maximized or not


class Shell:
    """What Open dashboard, a second launch, Quit and the window's close button do.

    `window` is the pywebview window, or None when there's no WebView2 and the default
    browser stands in for it.
    """

    def __init__(self, url: str, server, open_browser=webbrowser.open, restore=_restore_if_minimized):
        self.url = url
        self.server = server
        self.window = None
        self.hwnd = None  # the window's handle, once it exists
        self.tray = None
        self.quitting = False
        self.stopped = threading.Event()
        self._open_browser = open_browser
        self._restore = restore

    def show(self) -> None:
        if self.window is None:
            self._open_browser(self.url)
            return
        self._restore(self.hwnd)
        self.window.show()

    def hides_on_close(self, user_closing: bool) -> bool:
        """Whether closing the window should hide it to the tray instead.

        Only the person's own close does. Quit, Windows signing out or shutting down, and
        Task Manager all get a real close, so the app never holds up a sign-out.
        """
        return user_closing and not self.quitting

    def use_browser(self, background: bool) -> None:
        """No WebView2: the browser stands in for the window, except at a --background start."""
        self.window = None
        if not background:
            self.show()

    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        self.server.should_exit = True
        if self.tray is not None:
            self.tray.stop()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception as ex:  # already gone; the rest of Quit must still happen
                log(f"closing the window failed ({type(ex).__name__}: {ex})")
        self.stopped.set()


def _close_to_tray(shell: Shell, window) -> None:
    """Make the close button hide the window. Runs on the GUI thread before the window first shows.

    pywebview's closing event can't tell the close button from Windows signing out, so this
    handles the WinForms form's own FormClosing, whose CloseReason can.
    """
    from System.Windows.Forms import CloseReason  # pythonnet; pywebview has loaded WinForms

    form = window.native
    shell.hwnd = form.Handle.ToInt64()

    def on_form_closing(sender, args):
        if shell.hides_on_close(args.CloseReason == CloseReason.UserClosing):
            args.Cancel = True
            sender.Hide()

    form.FormClosing += on_form_closing


def run_window(shell: Shell, background: bool) -> None:
    """Run the window until Quit, or fall back to the browser. Blocks the main thread either way."""
    renderer = None
    try:
        import webview  # here, not at the top: tests import this module without WinForms

        window = webview.create_window(TITLE, shell.url, width=1280, height=860, min_size=(900, 600),
                                       hidden=background, background_color="#111010",
                                       text_select=True, zoomable=True)

        def on_initialized(name):
            nonlocal renderer
            renderer = name
            return name == "edgechromium"  # False stops here: MSHTML can't run the dashboard

        window.events.initialized += on_initialized
        window.events.before_show += lambda: _close_to_tray(shell, window)
        shell.window = window
        app.desktop_show = shell.show
        webview.start(private_mode=False, storage_path=str(paths.DATA_DIR / "webview"))
    except Exception as ex:  # pythonnet, .NET or WebView2 failed to load
        log(f"couldn't open the window ({type(ex).__name__}: {ex})")
        renderer = None
    if renderer == "edgechromium":
        return  # the window ran until Quit, or until Windows closed it
    log(f"no WebView2 (renderer: {renderer}); the default browser stands in for the window")
    shell.use_browser(background)
    app.desktop_show = shell.show
    shell.stopped.wait()


def smoke() -> int:
    """Start the server on a free port, fetch / and /api/connection, stop. 0 when both answer."""
    sock = bind_socket(0)
    port = sock.getsockname()[1]
    server, thread = start_server(sock)
    try:
        if not wait_started(server, thread):
            log("smoke: the server didn't start; see the lines above")
            return 1
        page = instance.call(port, "/", timeout=30)
        status = json.loads(instance.call(port, "/api/connection", timeout=30))
        if TITLE.encode() not in page or not isinstance(status, dict) or "state" not in status:
            log("smoke: unexpected answer")
            return 1
        log(f"smoke: ok on port {port}; connection {status['state']}")
        return 0
    except (OSError, ValueError) as ex:
        log(f"smoke: failed ({type(ex).__name__}: {ex})")
        return 1
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()  # uvicorn closes it on a clean stop; not when startup failed


def setup_logging() -> None:
    if sys.stdout is None or sys.stderr is None:
        # pythonw.exe and the windowed exe start without stdout/stderr: log to the file
        sys.stdout = sys.stderr = applog.open_log()
    else:
        applog.utf8_stdio()


def parse_args(argv):
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--background", action="store_true",
                        help="start in the tray with the window hidden (Start at login uses this)")
    parser.add_argument("--smoke", action="store_true",
                        help="start the server on a free port, check that it answers, exit 0 or 1")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    setup_logging()
    args = parse_args(argv)
    if args.smoke:
        return smoke()
    lock = instance.acquire()
    if lock is None:
        if instance.show_running():
            log("already running; brought its window forward")
            return 0
        lock = instance.acquire()  # the other copy may have quit meanwhile
        if lock is None:
            log("already running, but it didn't answer")
            return 1
    instance.clear_runtime()  # this copy holds the mutex, so any runtime.json is from an earlier run
    sock = bind_socket(settings.get("preferred_port"))
    port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    server, thread = start_server(sock)
    if not wait_started(server, thread):
        log(f"the server didn't start on port {port}; see the lines above")
        server.should_exit = True
        instance.release(lock)
        return 1
    instance.write_runtime(port)
    log(f"serving {url}")
    shell = Shell(url, server)
    shell.tray = tray.Tray(shell, url, port)
    shell.tray.start()
    try:
        run_window(shell, background=args.background)
    finally:
        shell.quit()  # also after Windows closed the window at sign-out
        thread.join(timeout=10)
        instance.clear_runtime(os.getpid())
        instance.release(lock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_desktop.py -q`, then `python -m pytest -q`.
Expected: 19 passed in the new file (the parametrized port test counts 4), then the full suite green, no warnings.

- [ ] **Step 6: Run the smoke check for real**

Run it against a scratch home and data dir, so it reads no real credentials and builds no DB in the worktree (write the command with the Write tool into a `.sh` file if your shell mangles it; don't use a heredoc):
```bash
SP="C:/Users/weaverjc/AppData/Local/Temp/claude/C--Users-weaverjc-Projects-Personal-claude-usage-dashboard--claude-worktrees-busy-visvesvaraya-0b60fc/98bd79c0-669a-4517-a3f2-aeaa826c399a/scratchpad"
mkdir -p "$SP/h/.claude/projects" "$SP/h/data"
USERPROFILE="$(cygpath -w "$SP/h")" CUD_DATA_DIR="$(cygpath -w "$SP/h/data")" PERSON_HOURS_WORKER=off python desktop.py --smoke; echo "exit $?"
```
(with the conda prefix in front, as always).
Expected: uvicorn's startup lines, then `[desktop …] smoke: ok on port <n>; connection signed-out` (or `not-installed` when no `claude` is on PATH), and `exit 0`. Nothing opens on screen.
Don't run `python desktop.py` without `--smoke`: the controller checks the window and tray by hand after Task 6.

- [ ] **Step 7: Update `CLAUDE.md`**

In the Architecture block:
- change `settings.py  User settings in data/settings.json: judge opt-in, automatic token renewal` to `settings.py  User settings in data/settings.json: judge opt-in, automatic token renewal, the desktop app's preferred_port (8765)`;
- after the `tray.py` line, add:
```
desktop.py   Desktop entry point: pywebview window (close hides to tray), uvicorn thread on preferred_port or a free one, tray, second-launch handoff, --background, --smoke
```

- [ ] **Step 8: Commit**

```bash
git add settings.py tests/test_settings.py tests/test_app_settings.py desktop.py tests/test_desktop.py CLAUDE.md
git commit -m "Add the desktop app: window, tray, one copy per session, --background and --smoke"
```

---

### Task 6: README, CLAUDE.md and the spec

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: README**

In "## Running it", after the paragraph starting "Then open http://127.0.0.1:8080/.", add:

```markdown
### As a desktop app

`python desktop.py` runs the same dashboard in its own window, with an icon in the tray. Closing the window only hides it. The tray icon's menu brings it back, opens the dashboard in your browser instead, turns **Start at login** on or off, and quits. It listens on port 8765, or on any free port if something else has 8765. Starting it a second time just brings the first window forward.

The window needs Microsoft's WebView2 runtime. Windows 11 has it, and so do most Windows 10 PCs with a current Edge. Without it, the dashboard opens in your browser and the tray icon works as usual.

Don't run `desktop.py` and `app.py` from the same folder at the same time. They'd share one database, and with automatic renewal on they'd race to renew your token.
```

In "## When your sign-in needs attention", add this paragraph after the bullet list (before the "Settings (the gear icon)" paragraph):

```markdown
The desktop app also puts an amber dot on its tray icon and shows one Windows notification when you need to sign in or install Claude Code.
```

In "## Where your data lives", after the sentence ending "it writes to `logs\dashboard.log` instead.", add:

```markdown
`desktop.py` logs the same way. The desktop app also keeps `data\runtime.json`, which tells a second launch which port the first one is on, and `data\webview`, the window's own browser storage.
```

- [ ] **Step 2: CLAUDE.md**

1. In "Setup & Running", after the "Run the server" code block (the one ending `# Serves at http://127.0.0.1:8080/`), add:

````markdown
Run the desktop app (window + tray, one copy per Windows session):
```bash
conda activate claude-usage-dashboard && python desktop.py               # window + tray on port 8765, or a free port
conda activate claude-usage-dashboard && python desktop.py --background  # tray only: what Start at login runs
conda activate claude-usage-dashboard && python desktop.py --smoke       # free port, checks / and /api/connection, exits 0/1
```
Don't run it from the main checkout while the scheduled :8080 server runs: both would use `data/usage.db`, and with automatic renewal on they'd race on the refresh token. Try it from a worktree with `USERPROFILE` and `CUD_DATA_DIR` pointed at a scratch home.
````

2. In "Key Paths", add three rows after the `data/settings.json` row:
```
| `data/runtime.json` | The running desktop app's port and pid, for a second launch; removed at Quit |
| `data/webview/` | The desktop window's WebView2 profile, so `localStorage` survives restarts |
| `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\ClaudeUsageDashboard` | Start at login (desktop app, tray menu). Task Manager's switch is under `...\Explorer\StartupApproved\Run` |
```

3. In "Server Restart", after the first paragraph (the one ending "offer to restart the server."), add:
```
The desktop app (`python desktop.py`) loads the same modules plus `desktop.py`, `tray.py`, `instance.py` and `autostart.py`. After changing any of them, Quit it from the tray and start it again.
```

4. In "Gotchas", add a bullet after the "Local-only guard" bullet:
```
- **Desktop app threads.** pywebview owns the main thread. uvicorn runs in a thread, so it installs no signal handlers. pystray runs its icon in another thread, and its setup callback is the 30-second connection poll. The close button hides the window only when WinForms reports `CloseReason.UserClosing` and Quit wasn't chosen, so Windows sign-out is never held up. Calls to the app's own server go through `instance.call()`, which ignores proxies.
```

- [ ] **Step 3: Spec amendments**

In `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`, "## Architecture":

1. In the **Main thread** bullet, replace `The window's `closing` handler hides it and returns `False` to cancel the close.` with:
   `Closing the window hides it. A handler on the WinForms form cancels the close and hides the form when `CloseReason` is `UserClosing` and the person hasn't chosen Quit. pywebview's own `closing` event can't tell the close button from Windows signing out, and cancelling a sign-out would hold it up.`
2. In the **Server thread** bullet, replace `It tries port 8765 first and falls back to a free port from the OS.` with:
   `It tries `preferred_port` (a setting, default 8765, used when it's between 1024 and 65535) and falls back to a free port from the OS for that run. A port Windows reserves fails with `PermissionError` and falls back the same way.`
3. Replace the **Tray thread** bullet's text after its bold label with:
   `Menu: Open dashboard (default action), Open in browser, Start at login (checkbox), Quit. Check for updates joins it in Phase 4, with `updates.py`. The icon has a "needs attention" variant, driven by connection status. Every 30 s the tray reads its own server's `/api/connection` over 127.0.0.1, which also keeps the quota figures fresh while no window is polling. Calls to the app's own server bypass any HTTP proxy.`
4. In the **Single instance** bullet, after `POSTs `/api/app/show` to the running instance, and exits.`, insert:
   ``runtime.json` holds `{port, pid}`. The first copy deletes a stale one at start and its own at Quit. A second launch keeps trying for 10 seconds, in case the first copy is still starting, and hands it the foreground with `AllowSetForegroundWindow`. If the first copy has quit by then, the second launch takes over.`
5. Replace the **No WebView2** bullet with:
   `- **No WebView2:** pywebview would fall back to the old MSHTML engine, which can't run the dashboard. The app stops there and opens the dashboard in the default browser instead (not at a `--background` start), and the tray works as usual.`

In "### New modules", the `autostart.py` row's purpose becomes:
`Reads, writes and removes the HKCU `Run` value through `winreg`. Task Manager's Startup switch (`Explorer\StartupApproved\Run`) counts: an entry switched off there reads as off, and turning it on from the tray clears the switch.`

Add a row after the `autostart.py` row:
`| `instance.py` | The single-instance mutex, `runtime.json`, and calls to the app's own server that never go through a proxy. |`
and one after it:
`| `tray.py` | The pystray icon, its menu, the attention variant and the sign-in notification. |`

In "## Connection status", the **Tray alerts** bullet: replace `One Windows notification fires on entering `signed-out`, `login-required` or `not-installed`.` with:
`One Windows notification fires on entering `signed-out`, `login-required` or `not-installed`, including when the app starts in one of them.`

In "### Changes to existing code", the `app.py` endpoints line: after `/api/app/info`, `/api/app/show` (POST).` append ` `/api/app/show` answers 409 when no desktop window is attached (`app.py` run on its own).`

In "## Known limitations", add:
`- **Another Windows user signed in at the same time** (fast user switching) can open the first user's dashboard at `127.0.0.1`: the local-only guard checks the host, not the user. The second user's own copy falls back to another port.`

- [ ] **Step 4: Check the docs**

Run: `grep -n "Check for updates" docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md` — every hit must be in Phase 4 context (the Tray bullet's "joins it in Phase 4" sentence, or the Phase 4 list).
Run: `python -m pytest -q`. Expected: green, no warnings (the README test still finds the title line).

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md
git commit -m "Document the desktop app and amend the spec where Phase 3 departs from it"
```

---

## Manual checks (controller, after Task 6, before the final review)

Use the test instance above. Drive the window with screenshots, and the tray through the notification area (Windows-MCP or computer-use). Record each result in the ledger.

1. **Start.** The window titled "Claude Usage Dashboard" shows the dashboard with the signed-out (or not-installed) banner. The tray icon has the amber dot, one Windows notification appears, and the tray tooltip names the problem. `Get-NetTCPConnection -LocalPort 8765 -State Listen` shows the listener. `$SP/h/data/runtime.json` holds port 8765 and the app's pid.
2. **Close to tray.** The window's X hides it; the listener stays.
3. **Open dashboard** from the tray brings it back.
4. **Second launch.** Minimize the window, then run the same `python desktop.py` command again. It exits within a few seconds with code 0, and the window comes back restored and in front.
5. **Start at login.** Ask the person first, because this writes their real registry.
   - Tray → Start at login. `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v ClaudeUsageDashboard` shows `"<env>\pythonw.exe" "<worktree>\desktop.py" --background`, and the menu shows the check.
   - Click it again: the value is gone.
6. **Quit.** Tray → Quit. The icon, window and process are gone, port 8765 is free, and `runtime.json` is gone.
7. **`--background`.** No window appears; the tray icon does. Open dashboard shows the window. Then Quit.
8. **No WebView2.** `PYWEBVIEW_GUI=mshtml` plus the same command opens the dashboard in the default browser, and the tray works. Quit ends it. With `--background` added, no browser tab opens.
9. **The window's page.**
   - Settings → Copy diagnostics puts text on the clipboard.
   - Text in the diagnostics box can be selected.
   - Ctrl+wheel zooms.
   - The install link (if the state is `not-installed`) opens in the default browser, not in the window.
10. **Storage survives.** Switch the cost card to `h`, Quit, start again: it's still `h`.
11. **`python desktop.py --smoke`** prints `smoke: ok` and exits 0, even while the app from check 1 runs.

Not checked by hand: a Windows sign-out with the window open. `test_only_the_persons_own_close_hides_the_window` pins the rule, and the PR says so.
