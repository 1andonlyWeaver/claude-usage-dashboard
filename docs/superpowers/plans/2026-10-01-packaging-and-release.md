# Packaging and Release Implementation Plan (Desktop App, Phase 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the desktop app into an installable Windows release. That means:
- `ClaudeUsageDashboard.exe` built with PyInstaller, with its own icon and name;
- a per-user Inno Setup installer;
- an update check with a notice in the page and a tray item;
- a GitHub Actions workflow that builds, tests and drafts each release from a bare-number tag.

**Architecture:**
- `updates.py` asks GitHub's `releases/latest` at most once a day and keeps the answer in `data/update.json`.
  - `app.py` runs the daily check from an hourly tick and serves the answer at `/api/update`; `/api/update/check` asks at once.
  - The page shows a notice; the tray's **Check for updates** asks and opens the release page.
- `packaging/` holds the build:
  - `build_assets.py` draws the exe's icon from `tray.icon_image` and writes its Windows version resource from `version.py`;
  - `ClaudeUsageDashboard.spec` builds the onedir, windowed exe;
  - `installer.iss` packs it per user.
- `.github/workflows/release.yml` runs it all on a clean runner, including a silent install, smoke test and uninstall of the installer itself, then creates a draft release.

**Tech Stack:** Python 3.12, FastAPI 0.142 / uvicorn 0.54, pywebview 6.2.1, pystray 0.19.5, Pillow 12.3.0, PyInstaller 6.22.3 with pyinstaller-hooks-contrib 2026.8, Inno Setup 6.7.3, GitHub Actions (`windows-latest`, `actions/checkout@v7`, `actions/setup-python@v7`), pytest 9 with `httpx2`.

**Spec:** `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`. This plan implements Phase 4 from its "Phases" section. It also covers these bullets elsewhere in the spec:
- the `updates.py` row of "New modules", and the `update_check` and `dismissed_version` keys of the `settings.py` row;
- "Check for updates" in the Architecture section's tray bullet;
- the version/update notice under "Changes to existing code" → Frontend;
- all of "Packaging and release" except what Phases 2 and 3 already built (`requirements.txt`, the log, `LICENSE`, `--smoke` itself);
- the Phase 4 manual checks under "Testing".

**Library and platform facts this plan relies on** (checked on 2026-10-01):
- **pywebview 6.2.1:**
  - `edgechromium.EdgeChrome.on_webview_ready` sets `AreBrowserAcceleratorKeysEnabled = _state['debug']`. Those keys include Ctrl+0, Ctrl+plus and Ctrl+minus, so zoom keys are dead outside debug mode. Its `IsZoomControlEnabled = True` is why Ctrl+wheel still works.
  - `EdgeChrome.__init__` subscribes `on_webview_ready` to `self.webview.CoreWebView2InitializationCompleted`, then calls `EnsureCoreWebView2Async`. The completion arrives later, through the WinForms message loop.
  - `winforms.create_window` builds the `BrowserForm`, fires `before_show` synchronously on the GUI thread, then shows the form. A handler added to `form.browser.webview.CoreWebView2InitializationCompleted` in `before_show` therefore runs after pywebview's own.
  - `BrowserForm` takes its icon from `ExtractIconW(sys.executable)`. A frozen exe with an icon gives the window and its taskbar button that icon with no code change.
  - `import webview.platforms.winforms` loads pythonnet, .NET WinForms and the WebView2 DLLs without opening a window (about 4 s here).
  - pywebview ships its own PyInstaller hook (`webview/__pyinstaller/hook-webview.py`), which collects `webview/lib` and `webview/js`.
- **uvicorn 0.54:** `uvicorn/config.py` imports by name at run time:
  - `uvicorn.protocols.http.auto`, which falls back to `h11_impl` without httptools;
  - `uvicorn.protocols.websockets.auto`, which becomes `None` without websockets or wsproto;
  - `uvicorn.lifespan.on`;
  - `uvicorn.loops.auto`, which falls back to `uvicorn.loops.asyncio`;
  - `uvicorn.logging`.
- **pystray 0.19.5** chooses its backend with importlib, so it needs the hidden import `pystray._win32`.
- **Pillow 12.3:**
  - `image.save(path, format="ICO", sizes=[...], append_images=[...])` stores each appended image at its own size instead of scaling the largest down.
  - `Image.open(ico).ico.getimage((16, 16))` reads one size back.
- **Windows names an app after its exe's version resource.** It takes the name from the resource's `FileDescription`, for the notification header, the taskbar and Task Manager. `python.exe` says "Python", which is what the source runs show.
- **The release machinery:**
  - Inno Setup 6.7.1 is preinstalled on the `windows-latest` image (Windows Server 2025), at `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`. The current Inno Setup 6 release is 6.7.3. Inno Setup 7.1 exists, and this script's directives compile under both.
  - GitHub's `releases/latest` API skips drafts and prereleases. Unauthenticated calls are limited to 60 an hour per IP.
  - The repo's default branch is `main`, and it has no releases or workflows yet.
- **This PC:**
  - Inno Setup isn't installed, and winget is disabled by Group Policy.
  - Windows Sandbox (`Containers-DisposableClientVM`) is present but disabled. The PC is domain-joined. The user chose to skip it; manual check M4 (a DLL check of the bundle) stands in for it.

**Where this departs from the spec** (Task 7 amends the spec for each):
1. **Releases are created as drafts.**
   - The tag's run builds, tests and installs the installer on a clean runner, then runs `gh release create --draft`. Publishing it after a hands-on check is the manual gate.
   - The spec's throwaway test tag isn't needed: a draft is invisible to installed copies, since `releases/latest` skips drafts.
2. **The workflow also runs on pull requests that touch the build** (`packaging/**`, the workflow, `requirements.txt`, `desktop.py`). It runs every step but the tag check and the release, so the build is proven before merge.
3. **The workflow checks the tag first, and tests the installer.**
   - It compares the tag with `version.py` before installing anything.
   - After building the installer, it installs it silently with Start at login, checks the `Run` value, runs `--smoke` on the installed exe, and uninstalls silently.
4. **`--smoke` checks more.**
   - It also fetches a vendored file (`/static/vendor/chart.umd.js`).
   - In a frozen build, it imports pywebview's WinForms backend. A build missing pythonnet or the WebView2 DLLs then fails, instead of quietly opening the browser.
5. **How the update check works.**
   - Who asks: the server asks GitHub from an hourly tick, and only when a check is due: daily, or an hour after a failed check.
   - Endpoints: `/api/update` (GET) reports the answer, and `/api/update/check` (POST) asks at once, whatever `update_check` says.
   - The release-page link is built from the validated tag, not taken from the response.
   - With `update_check` off, the notice stays hidden too.
6. **The app's name comes from the exe's version resource, not an AppUserModelID.** Setting an AUMID would split the taskbar button from a pinned shortcut unless every shortcut carried it too.
7. **The installer's tasks page decides Start at login on every install.**
   - **Superseded during Task 5's review:** the box is offered on a first install only, and an upgrade leaves Start at login as the tray left it. The spec has the current rule.
   - Unticked removes the `Run` value; ticked also clears Task Manager's switch, as the tray does.
   - The uninstaller removes the `Run` and `StartupApproved` values whoever wrote them.
   - It clears `{app}\_internal` before copying, so no stale library from an older build lingers.
   - It installs `LICENSE.txt` beside the exe.
8. **The zoom keys come back.** WebView2's browser shortcut keys are re-enabled after pywebview's setup.
   - F5 reload, Ctrl+F find and Ctrl+P print come back with them.
   - DevTools stay off: `AreDevToolsEnabled` is untouched.

**Carry-overs from Phase 3, all in this plan:**
- The README fixes in Task 7:
  - the "never bills an API key" overclaim;
  - the "first installer will come with release 2.0.0" wording;
  - the renewal wording (the refresh token goes to claude.ai, and automatic renewal involves no click).
- The dead Ctrl+0, Ctrl+plus and Ctrl+minus (Task 3).
- Re-timing a second launch with the frozen exe (manual check M1f).
- The spec's stale status line (Task 7).

**Prerequisites the controller installs** (with the user's approval, before Task 4 and Task 5):
- P1, before Task 4: `pip install -r packaging/requirements-build.txt` inside the conda env. Task 4 creates that file, so the controller runs `pip install pyinstaller==6.22.3 pyinstaller-hooks-contrib==2026.8` before dispatching Task 4. It also pulls altgraph, pefile and pywin32-ctypes.
- P2, before Task 5: Inno Setup 6.7.3, installed for the current user only.
  - It comes from `https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe`, signed by Pyrsys B.V.
  - Install it with `/CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART` to `%LOCALAPPDATA%\Programs\Inno Setup 6`.
  - Afterwards `ISCC.exe` is at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`.

## Global Constraints

- **Environment:** every `python` / `pytest` / `pip` command runs in the conda env. Each Bash call is a fresh shell, so prefix every command with:
  `eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && `
- **The env as of 2026-10-01:** Python 3.12.14, fastapi 0.142.2, uvicorn 0.54.0, jinja2 3.1.6, orjson 3.12.0, pywebview 6.2.1, pystray 0.19.5, Pillow 12.3.0, pytest 9.1.1, httpx2 2.13.1. Install nothing; the controller handles P1 and P2.
- **Working directory:** the worktree root, `C:\Users\weaverjc\Projects\Personal\claude-usage-dashboard\.claude\worktrees\busy-visvesvaraya-0b60fc`, on branch `claude/packaging`.
- **Baseline:** `python -m pytest -q` passes 322 tests with no warnings. Every task ends with the full suite green and no warnings in the summary line.
- **Writing files:** use the Write and Edit tools, not bash heredocs.
  - Heredocs here have dropped backslashes, and this plan is full of them: `Local\ClaudeUsageDashboard`, registry keys, `{localappdata}\Programs\...`, Inno Setup paths.
- **Git Bash rewrites arguments that start with `/`** into Windows paths. Prefix every `ISCC.exe` call made from the Bash tool with `MSYS_NO_PATHCONV=1`; without it, `/DAppVersion=2.0.0` arrives mangled.
- **Tests stay isolated.** `tests/conftest.py` enforces most of this, and Task 1 adds the GitHub guard. Tests must never:
  - touch `data/`, `~/.claude`, a real WSL distro, the real registry or the real `runtime.json`;
  - start a real process;
  - reach the network, GitHub included;
  - open a window or a tray icon;
  - run PyInstaller or Inno Setup.
- **pywebview is imported only inside functions** (`desktop.run_window`, `desktop._window_libraries_load`), so `import desktop` in tests loads no .NET.
- **Names, fixed from here on:**
  - exe `ClaudeUsageDashboard.exe`; build folder `dist/ClaudeUsageDashboard/`;
  - installer `dist/ClaudeUsageDashboard-Setup-<version>.exe`;
  - install dir `{localappdata}\Programs\Claude Usage Dashboard`; data dir `%LOCALAPPDATA%\ClaudeUsageDashboard`;
  - mutex `Local\ClaudeUsageDashboard`; `Run` value name `ClaudeUsageDashboard`, data `"<exe>" --background`;
  - GitHub repo `1andonlyWeaver/claude-usage-dashboard`; tags are bare numbers equal to `version.__version__`;
  - installer AppId `{2E7A6516-90C1-4991-8014-641D5A440BAF}`, which must never change.
- **`packaging/` is not a Python package.** Give it no `__init__.py`: a PyPI library named `packaging` exists, and pip and PyInstaller both import it. Tests load `packaging/build_assets.py` by file path.
- **Build output stays out of git:** `build/` and `dist/` are git-ignored (Task 4).
- **Implementers never:**
  - launch the GUI (`desktop.py` or the exe without `--smoke`);
  - run the built installer on this PC;
  - push, tag or create releases.

  The controller runs the manual checks at the end, with the user's approval where noted.
- **Never press Sign in against the real `claude` CLI**, and never read a stopped WSL distro's share (`\\wsl.localhost\...`).
- **User-facing text:** use the exact strings in this plan. The README in Task 7 has already been through the humanize pass.
- **Commits:**
  - One commit per task, with a plain imperative message.
  - No `Co-authored-by` or AI-attribution trailers (user rule).
  - Never stage `.claude/`, `.superpowers/`, `build/`, `dist/` or `data/`.
  - `CLAUDE.md` is tracked in this repo and updated alongside code (precedent `d7d6e88`).

## Review Focus

These are inputs the spec implies but doesn't spell out. Each has a pinned test in the task named.

1. **GitHub answers with something other than a release.** That covers a 404 before the first release, a 403 when the hourly limit is hit, a timeout, an HTML page, or a tag like `v2.1.0`. The app must not crash or show a false notice. It keeps the last known release and tries again within the hour. (Task 1: `test_a_failed_check_keeps_what_was_known_and_records_why`, `test_a_tag_that_is_not_a_release_number_is_refused`, `test_a_check_is_due_daily_and_an_hour_after_a_failure`.)
2. **Release numbers that sort wrongly as text.** `2.10.0` is newer than `2.9.0`, and `2.0` is the same release as `2.0.0`. (Task 1: `test_versions_compare_as_numbers`.)
3. **A dismissed release, then a newer one, then the person upgrading.**
   - Dismissing hides that release's notice but not the next one's.
   - Once the app runs the offered version, nothing is offered.

   (Task 1: `test_dismissing_hides_that_version_but_not_the_next`, `test_once_installed_the_new_version_is_no_longer_offered`.)
4. **A corrupt `update.json`, or one stamped by a clock that later went back.** Either counts as never checked, so a check is due, and an unwritable file still returns that check's answer. (Task 1: `test_a_corrupt_state_file_counts_as_never_checked`, `test_a_check_stamped_in_the_future_is_due_again`, `test_an_unwritable_state_file_still_gives_this_checks_answer`.)
5. **The installer and the app disagreeing about a name they share.**
   - A different `Run` value format or key would make the tray's Start at login read off.
   - A different mutex name would let Setup overwrite a running app.

   (Task 5: `test_the_installer_writes_the_run_value_the_app_reads_back`, `test_the_installer_waits_for_the_apps_mutex`. The workflow in Task 6 checks the real `Run` value after a silent install.)

---

### Task 1: Settings keys and the update check (`updates.py`)

**Files:**
- Modify: `settings.py:15-19` (`DEFAULTS`)
- Create: `updates.py`
- Modify: `tests/conftest.py` (import `updates`; new autouse fixture)
- Modify: `tests/helpers.py:134-148` (`FakeResponse` takes raw bytes too)
- Modify: `tests/test_settings.py`, `tests/test_app_settings.py` (the defaults now have five keys)
- Create: `tests/test_updates.py`

**Interfaces:**
- Consumes: `settings.load()`, `settings.get()`, `settings.update()`; `version.__version__` (read at call time, so tests can monkeypatch it); `paths.DATA_DIR`, `paths.APP_NAME`.
- Produces:
  - `settings.DEFAULTS` gains `"update_check": True` and `"dismissed_version": ""`.
  - `updates` exposes these names:
    - Constants: `REPO`, `LATEST_URL`, `RELEASE_URL`, `STATE_FILE`, `CHECK_EVERY = 86400`, `RETRY_AFTER = 3600`, `TIMEOUT = 10`.
    - `_urlopen`: the patch point for tests.
    - `parse(tag) -> tuple[int, int, int, int] | None` and `is_newer(tag, current=None) -> bool`.
    - `fetch_latest(timeout=TIMEOUT) -> str`, which raises `OSError` or `ValueError`.
    - `due(now=None) -> bool`.
    - `check(now=None) -> dict`, which never raises for a failed fetch.
    - `status(state=None) -> dict` with exactly the keys `current, latest, available, notify, url, checked_at, error, enabled`.
  - Error codes in `status()["error"]`: `None`, `"http-<code>"`, `"network-error"`, `"bad-answer"`.
  - Autouse fixture `isolated_updates` returns the per-test `update.json` path.

- [ ] **Step 1: Point the tests at a per-test state file and forbid GitHub**

In `tests/conftest.py`, add `import updates` after `import settings` (keep the imports alphabetical: `settings`, then `updates`), and add this fixture at the end of the file:

```python
@pytest.fixture(autouse=True)
def isolated_updates(tmp_path, monkeypatch):
    """No test may ask GitHub, and each keeps its update-check state in its own file."""
    def forbidden(*args, **kwargs):
        pytest.fail("a test tried to reach GitHub")
    path = tmp_path / "update.json"
    monkeypatch.setattr(updates, "_urlopen", forbidden)
    monkeypatch.setattr(updates, "STATE_FILE", path)
    return path
```

In `tests/helpers.py`, let `FakeResponse` carry a body that isn't JSON. Replace its `__init__`:

```python
    def __init__(self, payload=None, raw: bytes | None = None):
        self._body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.headers = {}
```

and its docstring with `"""What urllib.request.urlopen returns: a JSON body, or raw bytes."""`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_updates.py`:

```python
"""The update check: release numbers, the GitHub call, the daily schedule, and what the notice shows."""
import json
import urllib.error

import pytest

import settings
import updates
import version
from helpers import FakeResponse, fake_urlopen, http_error

NOW = 1_800_000_000.0
DAY = 24 * 3600
RELEASE_2_1_0 = "https://github.com/1andonlyWeaver/claude-usage-dashboard/releases/tag/2.1.0"


@pytest.fixture(autouse=True)
def running_2_0_0(monkeypatch):
    monkeypatch.setattr(version, "__version__", "2.0.0")


def github(monkeypatch, *answers):
    """GitHub answers with each item in turn: a FakeResponse, or an exception to raise."""
    urlopen, calls = fake_urlopen(list(answers))
    monkeypatch.setattr(updates, "_urlopen", urlopen)
    return calls


def save_state(path, **state):
    path.write_text(json.dumps(state), encoding="utf-8")


@pytest.mark.parametrize("tag, parsed", [
    ("2.0.0", (2, 0, 0, 0)), ("2.10", (2, 10, 0, 0)), ("3", (3, 0, 0, 0)), ("1.2.3.4", (1, 2, 3, 4)),
    ("v2.0.0", None), ("2.0.0-rc1", None), ("2.0.0 ", None), ("", None), (None, None), (2, None),
    ("1.2.3.4.5", None),
])
def test_only_bare_release_numbers_parse(tag, parsed):
    assert updates.parse(tag) == parsed


def test_versions_compare_as_numbers():
    assert updates.is_newer("2.10.0", "2.9.0")
    assert not updates.is_newer("2.9.0", "2.10.0")
    assert not updates.is_newer("2.0", "2.0.0")
    assert updates.is_newer("2.0.1", "2.0.0")
    assert not updates.is_newer("v9.0.0", "2.0.0")
    assert not updates.is_newer(None, "2.0.0")
    assert updates.is_newer("2.0.1")  # against the running version, 2.0.0 here


def test_fetch_latest_asks_github_for_the_latest_release(monkeypatch):
    calls = github(monkeypatch, FakeResponse({"tag_name": "2.1.0", "name": "2.1.0"}))
    assert updates.fetch_latest() == "2.1.0"
    (request,) = calls
    assert request.full_url == updates.LATEST_URL == \
        "https://api.github.com/repos/1andonlyWeaver/claude-usage-dashboard/releases/latest"
    assert request.get_method() == "GET"
    assert request.get_header("User-agent") == "ClaudeUsageDashboard/2.0.0"
    assert request.get_header("Accept") == "application/vnd.github+json"


def test_a_tag_that_is_not_a_release_number_is_refused(monkeypatch):
    github(monkeypatch, FakeResponse({"tag_name": "v2.1.0"}), FakeResponse(raw=b"<html>busy</html>"),
           FakeResponse(["2.1.0"]), FakeResponse({"name": "no tag"}))
    for _ in range(4):
        with pytest.raises(ValueError):
            updates.fetch_latest()


def test_a_successful_check_is_saved_and_shown(monkeypatch, isolated_updates):
    github(monkeypatch, FakeResponse({"tag_name": "2.1.0"}))
    status = updates.check(now=NOW)
    assert status == {"current": "2.0.0", "latest": "2.1.0", "available": True, "notify": True,
                      "url": RELEASE_2_1_0, "checked_at": NOW, "error": None, "enabled": True}
    assert json.loads(isolated_updates.read_text(encoding="utf-8")) == \
        {"checked_at": NOW, "latest": "2.1.0", "error": None}
    assert updates.status() == status


@pytest.mark.parametrize("failure, code", [
    (http_error(404), "http-404"),  # nothing published yet
    (http_error(403), "http-403"),  # GitHub's hourly limit for unauthenticated calls
    (urllib.error.URLError("offline"), "network-error"),
    (TimeoutError("timed out"), "network-error"),
    (FakeResponse(raw=b"<html>"), "bad-answer"),
])
def test_a_failed_check_keeps_what_was_known_and_records_why(monkeypatch, isolated_updates, failure, code):
    save_state(isolated_updates, checked_at=NOW - 2 * DAY, latest="2.1.0", error=None)
    github(monkeypatch, failure)
    status = updates.check(now=NOW)
    assert status["latest"] == "2.1.0" and status["available"]
    assert status["error"] == code and status["checked_at"] == NOW


def test_up_to_date_has_no_notice_and_no_link(monkeypatch):
    github(monkeypatch, FakeResponse({"tag_name": "2.0.0"}))
    status = updates.check(now=NOW)
    assert status["latest"] == "2.0.0"
    assert not status["available"] and not status["notify"] and status["url"] is None


def test_dismissing_hides_that_version_but_not_the_next(isolated_updates):
    save_state(isolated_updates, checked_at=NOW, latest="2.1.0", error=None)
    settings.update({"dismissed_version": "2.1.0"})
    status = updates.status()
    assert status["available"] and not status["notify"] and status["url"] == RELEASE_2_1_0
    save_state(isolated_updates, checked_at=NOW, latest="2.2.0", error=None)
    assert updates.status()["notify"]


def test_once_installed_the_new_version_is_no_longer_offered(monkeypatch, isolated_updates):
    save_state(isolated_updates, checked_at=NOW, latest="2.1.0", error=None)
    monkeypatch.setattr(version, "__version__", "2.1.0")
    status = updates.status()
    assert status["current"] == "2.1.0"
    assert not status["available"] and not status["notify"] and status["url"] is None


@pytest.mark.parametrize("content", [
    "{not json", "[]", '{"checked_at": "yesterday", "latest": "v2.1.0", "error": 5}',
    '{"checked_at": true, "latest": "2.1.0", "error": null}',
])
def test_a_corrupt_state_file_counts_as_never_checked(isolated_updates, content):
    isolated_updates.write_text(content, encoding="utf-8")
    status = updates.status()
    assert status["checked_at"] is None and status["error"] is None
    assert updates.due(now=NOW)


def test_a_check_is_due_daily_and_an_hour_after_a_failure(isolated_updates):
    assert updates.due(now=NOW)  # never checked
    save_state(isolated_updates, checked_at=NOW - 23 * 3600, latest="2.0.0", error=None)
    assert not updates.due(now=NOW)
    save_state(isolated_updates, checked_at=NOW - 25 * 3600, latest="2.0.0", error=None)
    assert updates.due(now=NOW)
    save_state(isolated_updates, checked_at=NOW - 30 * 60, latest=None, error="network-error")
    assert not updates.due(now=NOW)
    save_state(isolated_updates, checked_at=NOW - 2 * 3600, latest=None, error="network-error")
    assert updates.due(now=NOW)


def test_a_check_stamped_in_the_future_is_due_again(isolated_updates):
    save_state(isolated_updates, checked_at=NOW + DAY, latest="2.0.0", error=None)  # the clock went back since
    assert updates.due(now=NOW)


def test_turning_the_check_off_stops_the_daily_check_and_the_notice_but_not_check_now(monkeypatch):
    settings.update({"update_check": False})
    assert not updates.due(now=NOW)
    github(monkeypatch, FakeResponse({"tag_name": "2.1.0"}))
    status = updates.check(now=NOW)
    assert status["available"] and not status["notify"] and status["enabled"] is False


def test_an_unwritable_state_file_still_gives_this_checks_answer(monkeypatch, tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the data dir should be", encoding="utf-8")
    monkeypatch.setattr(updates, "STATE_FILE", blocker / "update.json")
    github(monkeypatch, FakeResponse({"tag_name": "2.1.0"}))
    assert updates.check(now=NOW)["available"]
    assert "couldn't save update.json" in capsys.readouterr().out
```

In `tests/test_settings.py`:
- Replace the three full-dict assertions so the expected dicts carry all five keys.
  - `test_defaults_when_there_is_no_file`: `{"judge_enabled": False, "auto_refresh_token": False, "preferred_port": 8765, "update_check": True, "dismissed_version": ""}`.
  - `test_update_keeps_the_other_keys`: `{"judge_enabled": True, "auto_refresh_token": True, "preferred_port": 8765, "update_check": True, "dismissed_version": ""}`.
  - `test_unreadable_file_or_wrong_types_fall_back_to_defaults`: `{"judge_enabled": False, "auto_refresh_token": True, "preferred_port": 8765, "update_check": True, "dismissed_version": ""}`.
- Add `{"update_check": "no"}` and `{"dismissed_version": 2}` to the `bad` parametrize list of `test_update_rejects_unknown_keys_and_wrong_types`.
- Add this test after `test_the_desktop_port_can_be_changed`:

```python
def test_the_update_settings_take_a_switch_and_a_release_number():
    assert settings.update({"update_check": False})["update_check"] is False
    assert settings.update({"dismissed_version": "2.1.0"})["dismissed_version"] == "2.1.0"
    assert settings.load()["update_check"] is False
```

In `tests/test_app_settings.py`, change the first assertion of `test_settings_round_trip` to:

```python
    assert app.get_settings() == {"judge_enabled": False, "auto_refresh_token": False,
                                  "preferred_port": 8765, "update_check": True,
                                  "dismissed_version": ""}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_updates.py tests/test_settings.py tests/test_app_settings.py -q`
Expected: collection ERROR in `conftest.py`, `ModuleNotFoundError: No module named 'updates'`.

- [ ] **Step 4: Add the settings keys**

In `settings.py`, replace `DEFAULTS` with:

```python
DEFAULTS = {
    "judge_enabled": False,       # the person-hours judge spends subscription quota: opt-in
    "auto_refresh_token": False,  # renewing rewrites ~/.claude/.credentials.json: opt-in
    "preferred_port": 8765,       # the desktop app's first-choice port (desktop.py); no UI
    "update_check": True,         # ask GitHub once a day whether a newer release is out (updates.py)
    "dismissed_version": "",      # the release whose notice the person dismissed; "" for none
}
```

- [ ] **Step 5: Write `updates.py`**

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_updates.py tests/test_settings.py tests/test_app_settings.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: every test passes (322 + the new ones), no warnings.

- [ ] **Step 7: Commit**

```bash
git add settings.py updates.py tests/conftest.py tests/helpers.py tests/test_updates.py tests/test_settings.py tests/test_app_settings.py
git commit -m "Add the update check: GitHub's latest release, at most daily, kept in update.json"
```

---

### Task 2: The update endpoints, the daily tick, and the notice in the page

**Files:**
- Modify: `app.py` (import; a constant; `_update_tick`; a timer in `startup`; two endpoints)
- Modify: `templates/index.html` (the notice under the auth banner; an Updates section in Settings)
- Modify: `static/dashboard.js` (init; Settings; a new Updates block)
- Modify: `static/style.css` (two rules after the auth-banner rules)
- Create: `tests/test_app_update.py`

**Interfaces:**
- Consumes: `updates.status()`, `updates.check()`, `updates.due()` (Task 1); `settings` keys `update_check`, `dismissed_version`.
- Produces:
  - `GET /api/update` returns `updates.status()`; `POST /api/update/check` returns `updates.check()`. Task 3's tray calls the POST.
  - `app.UPDATE_TICK_SECONDS = 3600` and `app._update_tick()`.
  - Page element ids: `updateBanner`, `updateTitle`, `updateLink`, `setUpdates`, `updateStatus`, `btnCheckUpdate`.
  - JS functions: `loadUpdate`, `renderUpdate`, `showUpdateStatus`, `checkForUpdates`, `dismissUpdate`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app_update.py`:

```python
"""The update endpoints, the hourly tick behind the daily check, and the notice's place in the page."""
import json

import pytest
from fastapi.testclient import TestClient

import app
import updates
import version
from helpers import FakeResponse, fake_urlopen


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(version, "__version__", "2.0.0")
    return TestClient(app.app, base_url="http://127.0.0.1:8080")


def test_get_update_reports_the_last_check_without_asking_github(client, isolated_updates):
    isolated_updates.write_text(json.dumps({"checked_at": 1.0, "latest": "2.1.0", "error": None}),
                                encoding="utf-8")
    body = client.get("/api/update").json()  # the autouse guard fails the test if GitHub is asked
    assert body["current"] == "2.0.0" and body["latest"] == "2.1.0" and body["notify"]


def test_check_now_asks_github_and_returns_the_answer(client, monkeypatch):
    urlopen, calls = fake_urlopen([FakeResponse({"tag_name": "2.1.0"})])
    monkeypatch.setattr(updates, "_urlopen", urlopen)
    body = client.post("/api/update/check").json()
    assert len(calls) == 1
    assert body["available"] and body["url"].endswith("/releases/tag/2.1.0")


def test_check_now_from_another_site_is_refused(client):
    r = client.post("/api/update/check", headers={"Origin": "https://example.com"})
    assert r.status_code == 403  # and the guard means GitHub was never asked


class FakeTimer:
    started = []

    def __init__(self, interval, function):
        self.interval, self.function, self.daemon = interval, function, False

    def start(self):
        FakeTimer.started.append(self)


@pytest.fixture
def timers(monkeypatch):
    FakeTimer.started = []
    monkeypatch.setattr(app.threading, "Timer", FakeTimer)
    return FakeTimer.started


def test_the_tick_checks_only_when_due_and_always_reschedules(timers, monkeypatch):
    checks = []
    monkeypatch.setattr(app.updates, "check", lambda: checks.append("checked"))
    monkeypatch.setattr(app.updates, "due", lambda: False)
    app._update_tick()
    assert checks == []
    monkeypatch.setattr(app.updates, "due", lambda: True)
    app._update_tick()
    assert checks == ["checked"]
    assert [(t.interval, t.function, t.daemon) for t in timers] == \
        [(app.UPDATE_TICK_SECONDS, app._update_tick, True)] * 2


def test_a_failing_tick_still_reschedules(timers, monkeypatch, capsys):
    def broken():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(app.updates, "due", broken)
    app._update_tick()
    assert len(timers) == 1
    assert "tick failed - RuntimeError: disk on fire" in capsys.readouterr().out


def test_the_page_carries_the_update_notice_and_its_settings(client):
    page = client.get("/").text
    for marker in ('id="updateBanner"', 'id="updateTitle"', 'id="updateLink"',
                   'onclick="dismissUpdate()"', 'id="setUpdates"',
                   "saveSetting('update_check', this)", 'id="updateStatus"',
                   'onclick="checkForUpdates()"'):
        assert marker in page, marker
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_app_update.py -q`
Expected: FAIL. The endpoint tests get 404, `app.UPDATE_TICK_SECONDS` and `app._update_tick` are missing, and the page markers are absent.

- [ ] **Step 3: Wire the check into the server**

In `app.py`:

1. Add `import updates` between `import settings` and `import version`.
2. After the line `SEVEN_DAY_WINDOW = 7 * 24 * 3600`, add:

```python
UPDATE_TICK_SECONDS = 3600  # how often _update_tick asks updates.due(); GitHub itself at most daily
```

3. Directly after the `_hours_tick` function, add:

```python
def _update_tick():
    """Run the daily update check if it's due, then reschedule (like _hours_tick, from an outer finally)."""
    try:
        if updates.due():
            updates.check()
    except Exception as ex:
        print(f"[updates {datetime.now():%Y-%m-%d %H:%M:%S}] tick failed - "
              f"{type(ex).__name__}: {ex}")
    finally:
        t = threading.Timer(UPDATE_TICK_SECONDS, _update_tick)
        t.daemon = True
        t.start()
```

4. At the end of `startup()`, after the `if HOURS_WORKER_ENABLED:` block, add:

```python
    u = threading.Timer(60, _update_tick)  # the first check a minute in, off the startup path
    u.daemon = True
    u.start()
```

5. Directly after the `app_show` endpoint, add:

```python
@app.get("/api/update")
def get_update():
    """What the last update check found. _update_tick does the asking, at most daily."""
    return updates.status()


@app.post("/api/update/check")
def check_update():
    """Ask GitHub now: Check now in Settings, and Check for updates in the tray."""
    return updates.check()
```

- [ ] **Step 4: Add the notice and the Updates section to the page**

In `templates/index.html`, insert this block directly after the line `<div id="authLive" class="sr-only" role="status"></div>`:

```html

    <!-- A newer release (hidden until /api/update says there's one the person hasn't dismissed) -->
    <div class="auth-banner update-banner" id="updateBanner" hidden>
      <span class="auth-banner-icon" aria-hidden="true">&#8593;</span>
      <div class="auth-banner-text">
        <strong id="updateTitle"></strong>
        <span>Download the installer and run it over this one. Your data and settings stay.</span>
      </div>
      <div class="auth-banner-actions">
        <a class="btn-refresh" id="updateLink" href="#" target="_blank" rel="noopener">Download</a>
        <button class="btn-refresh" type="button" onclick="dismissUpdate()">Dismiss</button>
      </div>
    </div>
```

In the settings panel, insert this section between the Connection section's closing `</section>` and the line `<p class="setting-help">Claude Usage Dashboard {{ version }}</p>`:

```html

        <section class="settings-section" aria-labelledby="updatesHeading">
          <h3 class="settings-heading" id="updatesHeading">Updates</h3>
          <label class="setting-row">
            <input type="checkbox" role="switch" id="setUpdates" aria-labelledby="setUpdatesName"
                   aria-describedby="setUpdatesHelp" onchange="saveSetting('update_check', this)">
            <span>
              <span class="setting-name" id="setUpdatesName">Check for updates</span>
              <span class="setting-help" id="setUpdatesHelp">Once a day, asks GitHub for the number of the latest release. Nothing about you or your usage goes with it.</span>
            </span>
          </label>
          <p class="setting-help" id="updateStatus" role="status"></p>
          <div><button class="btn-refresh" type="button" id="btnCheckUpdate" onclick="checkForUpdates()">Check now</button></div>
        </section>
```

Leave the `Claude Usage Dashboard {{ version }}` line as it is; `tests/test_release_files.py` checks it.

- [ ] **Step 5: Draw the notice and the status from `/api/update`**

In `static/dashboard.js`:

1. In the `DOMContentLoaded` handler, after `checkIngestStatus();`, add:

```js
  loadUpdate();
  setInterval(loadUpdate, UPDATE_POLL_MS);
```

2. In `setBackgroundInert`, change the selector string to `'.header, .main, #authBanner, #authLive, #updateBanner, #ingestBanner, #sessionPanel'`.

3. In `openSettings`, after `document.getElementById('setRenew').checked = s.auto_refresh_token;`, add:

```js
    document.getElementById('setUpdates').checked = s.update_check;
    loadUpdate();
```

4. Directly before the line `// ─── Cost ────────────────────────────────────────────────────`, add:

```js
// ─── Updates ─────────────────────────────────────────────────
// The server asks GitHub at most once a day (updates.py); the page only shows the answer.
const UPDATE_POLL_MS = 30 * 60 * 1000;
let updateInfo = null;

async function loadUpdate() {
  try {
    renderUpdate(await apiFetch('/api/update'));
  } catch (e) { /* keep showing what we had */ }
}

function renderUpdate(u) {
  updateInfo = u;
  const banner = document.getElementById('updateBanner');
  if (u.notify) {
    document.getElementById('updateTitle').textContent = `Version ${u.latest} is available. You have ${u.current}.`;
    document.getElementById('updateLink').href = u.url;
  } else if (banner.contains(document.activeElement)) {
    document.getElementById('lastUpdated').focus();
  }
  banner.hidden = !u.notify;
  showUpdateStatus(u);
}

// The line under Check now. It's a status region, so rewrite it only when the answer changed.
function showUpdateStatus(u) {
  const el = document.getElementById('updateStatus');
  const key = JSON.stringify([u.available, u.latest, u.error, u.checked_at, u.current]);
  if (el.dataset.key === key) return;
  el.dataset.key = key;
  const when = u.checked_at
    ? new Date(u.checked_at * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
    : '';
  el.textContent = '';
  if (u.available) {
    el.append(`Version ${u.latest} is available. `);
    const link = document.createElement('a');
    link.href = u.url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Download it';
    el.append(link);
  } else if (u.error) {
    el.textContent = when ? `Couldn't reach GitHub at ${when}. You have ${u.current}.`
                          : `Couldn't reach GitHub. You have ${u.current}.`;
  } else if (u.latest) {
    el.textContent = `You have the latest version, ${u.current}. Checked ${when}.`;
  } else {
    el.textContent = `Not checked yet. You have ${u.current}.`;
  }
}

async function checkForUpdates() {
  const btn = document.getElementById('btnCheckUpdate');
  if (btn.getAttribute('aria-disabled') === 'true') return;
  btn.setAttribute('aria-disabled', 'true');
  btn.textContent = 'Checking…';
  try {
    renderUpdate(await apiFetch('/api/update/check', { method: 'POST' }));
  } catch (e) {
    const el = document.getElementById('updateStatus');
    el.dataset.key = '';
    el.textContent = "Couldn't check for updates.";
  } finally {
    btn.removeAttribute('aria-disabled');
    btn.textContent = 'Check now';
  }
}

async function dismissUpdate() {
  if (!updateInfo || !updateInfo.latest) return;
  document.getElementById('lastUpdated').focus();
  document.getElementById('updateBanner').hidden = true;
  try {
    await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dismissed_version: updateInfo.latest }),
    });
  } catch (e) { /* hidden for now; it comes back at the next poll */ }
}

```

- [ ] **Step 6: Style the notice**

In `static/style.css`, directly after the rule `.auth-banner-actions a { text-decoration: none; }`, add:

```css
/* A newer release: the auth banner's layout, in the connected dot's green, since nothing is wrong */
.auth-banner.update-banner {
  background: rgba(123,200,123,0.07);
  border-color: rgba(123,200,123,0.3);
}
.update-banner .auth-banner-icon { color: #7BC87B; }
#updateStatus a { color: var(--claude-orange); }
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_app_update.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: every test passes, no warnings.

- [ ] **Step 8: Commit**

```bash
git add app.py templates/index.html static/dashboard.js static/style.css tests/test_app_update.py
git commit -m "Serve the update check, run it daily, and show a notice and an Updates section in the page"
```

---

### Task 3: Check for updates in the tray, the zoom keys, and a stricter `--smoke`

**Files:**
- Modify: `tray.py` (menu item; `check_for_updates`, `_report_update_check`)
- Modify: `desktop.py` (`_allow_zoom_keys`, `_prepare_window`, the `before_show` hookup in `run_window`, `SMOKE_FILE`, `_window_libraries_load`, `smoke`)
- Modify: `tests/test_tray.py` (`import pytest`; the menu test; new tests)
- Modify: `tests/test_desktop.py` (new tests)

**Interfaces:**
- Consumes: `POST /api/update/check` (Task 2), which answers `updates.status()` keys; `instance.call(port, path, method, timeout)`.
- Produces:
  - Tray menu order: Open dashboard, Open in browser, Start at login, Check for updates, separator, Quit.
  - `Tray.check_for_updates()` and `Tray._report_update_check()`.
  - In `desktop`: `_allow_zoom_keys(window)`, `_prepare_window(shell, window)`, `SMOKE_FILE`, `_window_libraries_load() -> bool`.

- [ ] **Step 1: Write the failing tray tests**

In `tests/test_tray.py`, add `import pytest` after `import json`, and add `RELEASE = "https://github.com/1andonlyWeaver/claude-usage-dashboard/releases/tag/2.1.0"` after the `LOGIN = ...` constant.

In `test_menu_items_and_their_actions`, change the expected list to:

```python
    assert [item.text for item in menu.items] == [
        "Open dashboard", "Open in browser", "Start at login", "Check for updates",
        pystray.Menu.SEPARATOR.text, "Quit"]
```

Append these tests to the end of the file:

```python
def updating_tray(monkeypatch, answer):
    """A tray whose POST /api/update/check answers with `answer`: a dict, raw bytes, or an
    exception to raise. Returns the tray and the list of pages it opened."""
    def fake_call(port, path, method="GET", timeout=5.0):
        assert (port, path, method) == (8765, "/api/update/check", "POST")
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()

    monkeypatch.setattr(tray.instance, "call", fake_call)
    opened = []
    monkeypatch.setattr(tray.webbrowser, "open", opened.append)
    return tray.Tray(FakeShell(), URL, 8765, icon=FakeIcon()), opened


def test_check_for_updates_opens_the_download_page_when_a_newer_version_is_out(monkeypatch):
    t, opened = updating_tray(monkeypatch, {"available": True, "latest": "2.1.0", "current": "2.0.0",
                                            "url": RELEASE, "error": None})
    t._report_update_check()
    assert t.icon.notes == [("Version 2.1.0 is available. Opening the download page.", tray.TITLE)]
    assert opened == [RELEASE]


@pytest.mark.parametrize("answer, note", [
    ({"available": False, "latest": "2.0.0", "current": "2.0.0", "url": None, "error": None},
     "You have the latest version, 2.0.0."),
    ({"available": False, "latest": None, "current": "2.0.0", "url": None, "error": "network-error"},
     "Couldn't reach GitHub to check for updates. Try again later."),
    (OSError("connection refused"), "Couldn't check for updates. Try again in a minute."),
    (b"not json", "Couldn't check for updates. Try again in a minute."),
    (b"[]", "Couldn't check for updates. Try again in a minute."),
])
def test_check_for_updates_reports_without_opening_anything(monkeypatch, answer, note):
    t, opened = updating_tray(monkeypatch, answer)
    t._report_update_check()
    assert t.icon.notes == [(note, tray.TITLE)]
    assert opened == []


def test_check_for_updates_runs_off_the_menu_thread(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            started.append((target, daemon))

        def start(self):
            started.append("started")

    t = make_tray(monkeypatch, [])
    monkeypatch.setattr(tray.threading, "Thread", FakeThread)
    {item.text: item for item in t.menu().items}["Check for updates"](t.icon)
    assert started == [(t._report_update_check, True), "started"]
```

- [ ] **Step 2: Write the failing desktop tests**

In `tests/test_desktop.py`, add `import sys` and `import types` to the imports (alphabetical: `platform`, `socket`, `sys`, `types`), then append:

```python
class FakeEvent:
    """A .NET event as pythonnet shows it: handlers are added with +=."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


def webview2_window():
    """A pywebview window whose native form holds a WebView2 control, plus that control's settings."""
    control = types.SimpleNamespace(CoreWebView2InitializationCompleted=FakeEvent())
    form = types.SimpleNamespace(browser=types.SimpleNamespace(webview=control))
    browser_settings = types.SimpleNamespace(AreBrowserAcceleratorKeysEnabled=False,
                                             AreDevToolsEnabled=False)
    return types.SimpleNamespace(native=form), control, browser_settings


def test_the_zoom_keys_come_back_once_webview2_is_ready():
    window, control, browser_settings = webview2_window()
    desktop._allow_zoom_keys(window)
    (on_ready,) = control.CoreWebView2InitializationCompleted.handlers
    sender = types.SimpleNamespace(CoreWebView2=types.SimpleNamespace(Settings=browser_settings))
    on_ready(sender, types.SimpleNamespace(IsSuccess=False))
    assert browser_settings.AreBrowserAcceleratorKeysEnabled is False
    on_ready(sender, types.SimpleNamespace(IsSuccess=True))
    assert browser_settings.AreBrowserAcceleratorKeysEnabled is True
    assert browser_settings.AreDevToolsEnabled is False


def test_the_zoom_keys_leave_a_window_without_webview2_alone():
    desktop._allow_zoom_keys(types.SimpleNamespace(native=types.SimpleNamespace(browser=None)))


def test_before_show_sets_up_close_to_tray_and_the_zoom_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "_close_to_tray", lambda shell, window: calls.append(("tray", shell, window)))
    monkeypatch.setattr(desktop, "_allow_zoom_keys", lambda window: calls.append(("zoom", window)))
    desktop._prepare_window("shell", "window")
    assert calls == [("tray", "shell", "window"), ("zoom", "window")]


def test_smoke_fails_when_a_page_file_is_missing(monkeypatch):
    async def no_startup():
        pass

    monkeypatch.setattr(app, "startup", no_startup)
    real_call = desktop.instance.call

    def call(port, path, method="GET", timeout=5.0):
        if path == desktop.SMOKE_FILE:
            raise OSError("HTTP Error 404: Not Found")
        return real_call(port, path, method, timeout)

    monkeypatch.setattr(desktop.instance, "call", call)
    assert desktop.smoke() == 1


def test_a_frozen_smoke_also_loads_the_window_libraries(monkeypatch):
    async def no_startup():
        pass

    monkeypatch.setattr(app, "startup", no_startup)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop, "_window_libraries_load", lambda: False)
    assert desktop.smoke() == 1
    monkeypatch.setattr(desktop, "_window_libraries_load", lambda: True)
    assert desktop.smoke() == 0


def test_from_source_smoke_leaves_the_window_libraries_alone(monkeypatch):
    async def no_startup():
        pass

    def must_not_load():
        raise AssertionError("loading .NET into the test process")

    monkeypatch.setattr(app, "startup", no_startup)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(desktop, "_window_libraries_load", must_not_load)
    assert desktop.smoke() == 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_tray.py tests/test_desktop.py -q`
Expected: FAIL. The menu has no "Check for updates", `_report_update_check`, `_allow_zoom_keys`, `_prepare_window` and `SMOKE_FILE` are missing, and the frozen smoke test returns 0 where it expects 1.

- [ ] **Step 4: Add Check for updates to the tray**

In `tray.py`, in `menu()`, add the item between "Start at login" and the separator:

```python
            pystray.MenuItem("Start at login", self.toggle_autostart,
                             checked=lambda item: autostart.is_enabled()),
            pystray.MenuItem("Check for updates", self.check_for_updates),
            pystray.Menu.SEPARATOR,
```

Add these two methods after `toggle_autostart`:

```python
    def check_for_updates(self) -> None:
        """Ask the server to check GitHub now. On a thread of its own: the check can take
        seconds, and menu actions run on the icon's thread."""
        threading.Thread(target=self._report_update_check, name="update-check", daemon=True).start()

    def _report_update_check(self) -> None:
        try:
            status = json.loads(instance.call(self.port, "/api/update/check", method="POST", timeout=30))
        except (OSError, ValueError):
            status = None
        if not isinstance(status, dict):
            self.icon.notify("Couldn't check for updates. Try again in a minute.", TITLE)
        elif status.get("available"):
            self.icon.notify(f"Version {status.get('latest')} is available. Opening the download page.", TITLE)
            webbrowser.open(status["url"])
        elif status.get("error"):
            self.icon.notify("Couldn't reach GitHub to check for updates. Try again later.", TITLE)
        else:
            self.icon.notify(f"You have the latest version, {status.get('current')}.", TITLE)
```

Update the module docstring's first paragraph to: `The desktop app's tray icon: its menu (Open, Start at login, Check for updates, Quit), its attention badge, and one Windows notification when the Claude sign-in needs the person.`

- [ ] **Step 5: Bring back the zoom keys**

In `desktop.py`, directly after the `_close_to_tray` function, add:

```python
def _allow_zoom_keys(window) -> None:
    """Let Ctrl+0, Ctrl+plus and Ctrl+minus zoom the page, as Ctrl+wheel already does.

    pywebview turns WebView2's browser shortcut keys off outside debug mode, and the zoom keys
    are among them. Its own setup handler subscribed when the control was created, so this
    one runs after it and has the last word. The other browser keys come back too: F5
    reloads, Ctrl+F finds, Ctrl+P prints. DevTools stay off; AreDevToolsEnabled is untouched.
    """
    browser = getattr(window.native, "browser", None)
    control = getattr(browser, "webview", None)
    if control is None:
        return  # no WebView2 control (MSHTML), and that window never opens anyway

    def on_ready(sender, args):
        if args.IsSuccess:
            sender.CoreWebView2.Settings.AreBrowserAcceleratorKeysEnabled = True

    control.CoreWebView2InitializationCompleted += on_ready


def _prepare_window(shell: Shell, window) -> None:
    """Runs on the GUI thread before the window first shows."""
    _close_to_tray(shell, window)
    _allow_zoom_keys(window)
```

In `run_window`, change the `before_show` line to:

```python
        window.events.before_show += lambda: _prepare_window(shell, window)
```

- [ ] **Step 6: Make `--smoke` check the bundle**

In `desktop.py`, add this constant after `SW_RESTORE = 9`:

```python
SMOKE_FILE = "/static/vendor/chart.umd.js"  # a vendored file: proves a build bundled static/
```

Directly before `def smoke()`, add:

```python
def _window_libraries_load() -> bool:
    """Whether pywebview's WinForms backend loads: pythonnet, .NET WinForms and the WebView2 DLLs.

    A frozen build that left one out would open the browser instead of the window, with only
    a log line to say why. Importing the backend opens no window.
    """
    try:
        import webview.platforms.winforms  # noqa: F401
    except Exception as ex:
        log(f"smoke: the window's libraries didn't load ({type(ex).__name__}: {ex})")
        return False
    return True
```

Replace `smoke()` with:

```python
def smoke() -> int:
    """Start the server on a free port, fetch /, /api/connection and a vendored file, stop.
    0 when all three answer, and, in a frozen build, the window's libraries load."""
    sock = bind_socket(0)
    port = sock.getsockname()[1]
    server, thread = start_server(sock)
    try:
        if not wait_started(server, thread):
            log("smoke: the server didn't start; see the lines above")
            return 1
        page = instance.call(port, "/", timeout=30)
        status = json.loads(instance.call(port, "/api/connection", timeout=30))
        script = instance.call(port, SMOKE_FILE, timeout=30)
        if (TITLE.encode() not in page or not isinstance(status, dict) or "state" not in status
                or not script):
            log("smoke: unexpected answer")
            return 1
        if getattr(sys, "frozen", False) and not _window_libraries_load():
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
```

In the module docstring, change the last sentence to: `--smoke starts the server on a free port, checks /, /api/connection and a vendored file (and, when frozen, that the window's libraries load), and exits 0 or 1.`

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_tray.py tests/test_desktop.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: every test passes, no warnings.

- [ ] **Step 8: Commit**

```bash
git add tray.py desktop.py tests/test_tray.py tests/test_desktop.py
git commit -m "Add Check for updates to the tray, bring back the zoom keys, make --smoke check the bundle"
```

---

### Task 4: The PyInstaller build (`packaging/`)

**Prerequisite:** the controller has installed PyInstaller (P1). Check with `python -m PyInstaller --version`; it should print `6.22.3`.

**Files:**
- Modify: `.gitignore`
- Create: `packaging/requirements-build.txt`
- Create: `packaging/build_assets.py`
- Create: `packaging/ClaudeUsageDashboard.spec`
- Create: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `tray.icon_image(attention, size)`, `version.__version__`.
- Produces:
  - `build_assets` exposes `APP_NAME = "Claude Usage Dashboard"`, `EXE_NAME = "ClaudeUsageDashboard.exe"`, `AUTHOR`, `ICON_SIZES`.
  - Its functions: `file_version(number) -> tuple[int, int, int, int]`, `write_icon(path) -> Path`, `version_info(number) -> str`, and `write(out_dir) -> tuple[str, str]`, which returns `(icon_path, version_path)`.
  - The build: `dist/ClaudeUsageDashboard/ClaudeUsageDashboard.exe` (onedir, windowed), plus `build/assets/icon.ico`, which Task 5's installer uses.

- [ ] **Step 1: Keep build output out of git**

Append to `.gitignore`:

```
# Release builds (PyInstaller and Inno Setup output)
build/
dist/
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_packaging.py`:

```python
"""Release build inputs: the icon and version resource drawn from the app, and the PyInstaller spec."""
import importlib.util
from pathlib import Path

from PIL import Image

import tray
import version

REPO = Path(__file__).parent.parent


def load(name):
    """Import a module from packaging/. It isn't a package: `packaging` is also a PyPI library's name."""
    spec = importlib.util.spec_from_file_location(name, REPO / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_assets = load("build_assets")


def test_file_version_pads_the_release_number_to_four_parts():
    assert build_assets.file_version("2.0.0") == (2, 0, 0, 0)
    assert build_assets.file_version("2.10") == (2, 10, 0, 0)


def test_the_icon_holds_the_tray_ring_drawn_at_every_size(tmp_path):
    path = build_assets.write_icon(tmp_path / "icon.ico")
    with Image.open(path) as ico:
        assert sorted(ico.info["sizes"]) == [(s, s) for s in build_assets.ICON_SIZES]
        for size in (16, 32, 256):  # each drawn at its own size, not scaled from 256
            stored = ico.ico.getimage((size, size)).convert("RGBA")
            assert stored.tobytes() == tray.icon_image(False, size).tobytes()


def test_the_version_resource_names_the_app_for_windows():
    calls = {}

    def recorder(kind):
        def build(*args, **kwargs):
            calls.setdefault(kind, []).append((args, kwargs))
            return kind
        return build

    kinds = ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct",
             "VarFileInfo", "VarStruct")
    eval(build_assets.version_info("2.0.0"), {kind: recorder(kind) for kind in kinds})  # as PyInstaller reads it
    strings = dict(args for args, _ in calls["StringStruct"])
    assert strings["FileDescription"] == strings["ProductName"] == "Claude Usage Dashboard"
    assert strings["FileVersion"] == strings["ProductVersion"] == "2.0.0"
    assert strings["OriginalFilename"] == "ClaudeUsageDashboard.exe"
    ((_, fixed),) = calls["FixedFileInfo"]
    assert fixed["filevers"] == fixed["prodvers"] == (2, 0, 0, 0)


def test_write_puts_both_files_in_the_build_folder(tmp_path):
    icon, info = build_assets.write(tmp_path / "assets")
    assert Path(icon) == tmp_path / "assets" / "icon.ico" and Path(icon).is_file()
    assert Path(info).read_text(encoding="utf-8") == build_assets.version_info(version.__version__)


def test_the_spec_builds_a_windowed_exe_with_the_page_files():
    spec = (REPO / "packaging" / "ClaudeUsageDashboard.spec").read_text(encoding="utf-8")
    assert "desktop.py" in spec and "console=False" in spec
    assert '"static"' in spec and '"templates"' in spec
    for module in ("uvicorn.lifespan.on", "uvicorn.protocols.http.h11_impl",
                   "uvicorn.protocols.websockets.auto", "uvicorn.loops.asyncio", "pystray._win32"):
        assert f'"{module}"' in spec, module
    assert spec.count(f'name="{build_assets.EXE_NAME.removesuffix(".exe")}"') == 2  # EXE and COLLECT


def test_build_tools_are_pinned_apart_from_the_runtime():
    text = (REPO / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")
    reqs = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert reqs == ["pyinstaller==6.22.3", "pyinstaller-hooks-contrib==2026.8"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_packaging.py -q`
Expected: collection ERROR, `FileNotFoundError` for `packaging/build_assets.py`.

- [ ] **Step 4: Pin the build tools**

Create `packaging/requirements-build.txt`:

```
# Build tools for the release (packaging/ClaudeUsageDashboard.spec). Running or testing the app
# doesn't need them. Install with: pip install -r packaging/requirements-build.txt
pyinstaller==6.22.3
pyinstaller-hooks-contrib==2026.8
```

- [ ] **Step 5: Write `packaging/build_assets.py`**

```python
"""
Build inputs drawn from the app itself, so the exe can't drift from it:
- icon.ico: tray.py's ring, drawn at each size Windows asks for. The exe, its window, the
  taskbar and the installer all use it.
- version_info.txt: the exe's Windows version resource. Its FileDescription is the name
  Windows puts on the app's notifications, its taskbar button and Task Manager (from source
  they say "Python", python.exe's own description).
ClaudeUsageDashboard.spec writes both into build/assets before PyInstaller runs.
"""
from pathlib import Path

import tray
import version

APP_NAME = "Claude Usage Dashboard"
EXE_NAME = "ClaudeUsageDashboard.exe"
AUTHOR = "Jonathan Weaver"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def file_version(number: str) -> tuple[int, int, int, int]:
    """'2.0.0' -> (2, 0, 0, 0): the four numbers a version resource holds."""
    parts = [int(part) for part in number.split(".")]
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"not a release number: {number!r}")
    return tuple(parts + [0] * (4 - len(parts)))


def write_icon(path: Path) -> Path:
    """Each size drawn on its own rather than scaled down from 256, so the ring stays crisp at 16 px."""
    images = [tray.icon_image(False, size) for size in ICON_SIZES]
    images[-1].save(path, format="ICO", sizes=[(s, s) for s in ICON_SIZES], append_images=images[:-1])
    return path


def version_info(number: str) -> str:
    """The version resource, in the text form PyInstaller's EXE(version=...) reads (it evals it)."""
    numbers = file_version(number)
    strings = {
        "CompanyName": AUTHOR,
        "FileDescription": APP_NAME,
        "FileVersion": number,
        "InternalName": EXE_NAME.removesuffix(".exe"),
        "LegalCopyright": f"Copyright (c) 2026 {AUTHOR}. MIT License.",
        "OriginalFilename": EXE_NAME,
        "ProductName": APP_NAME,
        "ProductVersion": number,
    }
    table = ",\n".join(f"        StringStruct({key!r}, {value!r})" for key, value in strings.items())
    return (
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0,\n"
        "                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('040904B0', [\n"
        f"{table}\n"
        "      ])\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )


def write(out_dir) -> tuple[str, str]:
    """Write icon.ico and version_info.txt into out_dir. Returns their paths, for the spec."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    icon = write_icon(out / "icon.ico")
    info = out / "version_info.txt"
    info.write_text(version_info(version.__version__), encoding="utf-8")
    return str(icon), str(info)
```

- [ ] **Step 6: Write `packaging/ClaudeUsageDashboard.spec`**

```python
# PyInstaller spec for ClaudeUsageDashboard.exe: one folder, no console window.
#
# Build from the repo root, in an env with requirements.txt and packaging/requirements-build.txt:
#     python -m PyInstaller --noconfirm --clean packaging/ClaudeUsageDashboard.spec
# Output: dist/ClaudeUsageDashboard/ (the exe and its _internal folder), which
# packaging/installer.iss packs into the installer.
import os
import sys

ROOT = os.path.dirname(SPECPATH)
sys.path[:0] = [SPECPATH, ROOT]  # build_assets, and the app modules it draws from
import build_assets  # noqa: E402

icon_file, version_file = build_assets.write(os.path.join(ROOT, "build", "assets"))

a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    datas=[
        # paths.RESOURCE_DIR is sys._MEIPASS (the _internal folder) in a frozen build
        (os.path.join(ROOT, "static"), "static"),
        (os.path.join(ROOT, "templates"), "templates"),
    ],
    hiddenimports=[
        # uvicorn/config.py imports these by name at run time, out of the analysis' sight
        "uvicorn.lifespan.on",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.logging",
        # pystray chooses its backend with importlib
        "pystray._win32",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClaudeUsageDashboard",
    console=False,  # windowed: desktop.setup_logging sends output to the log file
    icon=icon_file,
    version=version_file,
    upx=False,  # UPX-packed exes trip more antivirus heuristics
)
coll = COLLECT(exe, a.binaries, a.datas, name="ClaudeUsageDashboard", upx=False)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_packaging.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: every test passes, no warnings.

- [ ] **Step 8: Build the exe and smoke-test it**

Run (about 2–4 minutes): `python -m PyInstaller --noconfirm --clean packaging/ClaudeUsageDashboard.spec 2>&1 | tail -15`
Expected: ends with `Build complete!`, and `dist/ClaudeUsageDashboard/ClaudeUsageDashboard.exe` exists.

Check that the bundle holds the page files, the WebView2 DLLs and pythonnet:
```bash
ls dist/ClaudeUsageDashboard/_internal/static/vendor/chart.umd.js dist/ClaudeUsageDashboard/_internal/templates/index.html
ls dist/ClaudeUsageDashboard/_internal/webview/lib | head
ls dist/ClaudeUsageDashboard/_internal/pythonnet/runtime | head
```
Expected: the first two paths are listed, and `webview/lib` lists `Microsoft.Web.WebView2.Core.dll` and `Microsoft.Web.WebView2.WinForms.dll`.
- `pythonnet/runtime` should list `Python.Runtime.dll`. If the hook put it somewhere else, find it with `find dist -name Python.Runtime.dll` and report where.
- The smoke test below is the real proof: in a frozen build it imports the WinForms backend.

Read `build/ClaudeUsageDashboard/warn-ClaudeUsageDashboard.txt` and report every "missing module" line that names `uvicorn`, `webview`, `pystray`, `clr`, `clr_loader`, `pythonnet`, `PIL`, `fastapi`, `starlette` or `jinja2`. Lines about other optional imports, such as `uvloop`, `httptools`, `websockets`, POSIX-only modules or `pydantic` plugins, are expected.

Smoke-test the frozen exe against a throwaway home. `LOCALAPPDATA` moves the frozen app's data and log out of the real profile:
```bash
WT="$(pwd)"; mkdir -p data/frozen/home data/frozen/local
USERPROFILE="$(cygpath -w "$WT/data/frozen/home")" LOCALAPPDATA="$(cygpath -w "$WT/data/frozen/local")" PERSON_HOURS_WORKER=off ./dist/ClaudeUsageDashboard/ClaudeUsageDashboard.exe --smoke; echo "exit $?"
tail -5 data/frozen/local/ClaudeUsageDashboard/logs/dashboard.log
```
Expected:
- `exit 0`;
- the log's last lines include `smoke: ok on port <n>; connection signed-out`. It says `not-installed` if no `claude` CLI is on `PATH`.

Check the version resource:
```bash
powershell.exe -NoProfile -Command "(Get-Item dist\ClaudeUsageDashboard\ClaudeUsageDashboard.exe).VersionInfo | Format-List FileDescription,ProductName,FileVersion,ProductVersion"
```
Expected: `FileDescription : Claude Usage Dashboard`, `ProductName : Claude Usage Dashboard`, and `2.0.0` for both versions.

If the build or the smoke test fails, fix the spec, not the app. Report anything a fix would need outside `packaging/`.

- [ ] **Step 9: Commit**

`git status` must show no `build/`, `dist/` or `data/` entries.

```bash
git add .gitignore packaging/requirements-build.txt packaging/build_assets.py packaging/ClaudeUsageDashboard.spec tests/test_packaging.py
git commit -m "Build ClaudeUsageDashboard.exe with PyInstaller: its own icon and name, static and templates bundled"
```

---

### Task 5: The per-user installer (`packaging/installer.iss`)

**Prerequisite:** the controller has installed Inno Setup (P2), and Task 4's build is in `dist/ClaudeUsageDashboard/` with `build/assets/icon.ico`. Check with `ls "$LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe"`.

**Files:**
- Create: `packaging/installer.iss`
- Create: `tests/test_installer.py`

**Interfaces:**
- Consumes:
  - `instance.MUTEX_NAME`;
  - `autostart.RUN_KEY`, `autostart.APPROVED_KEY`, `autostart.VALUE_NAME` and `autostart.command()`, which returns `"<sys.executable>" --background` when frozen;
  - `paths.APP_NAME` and `paths._app_home()`;
  - Task 4's `dist/ClaudeUsageDashboard/` and `build/assets/icon.ico`.
- Produces:
  - `dist/ClaudeUsageDashboard-Setup-<version>.exe`, built by `ISCC /DAppVersion=<version> packaging\installer.iss`.
  - Task names `startatlogin` (checked by default) and `desktopicon` (unchecked). Task 6's workflow passes `/TASKS=startatlogin`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_installer.py`:

```python
"""The installer agrees with the app on every name they share (packaging/installer.iss)."""
import re
import sys
from pathlib import Path

import autostart
import instance
import paths

REPO = Path(__file__).parent.parent
ISS = (REPO / "packaging" / "installer.iss").read_text(encoding="utf-8")


def setup_value(key):
    match = re.search(rf"^{key}=(.*)$", ISS, re.MULTILINE)
    assert match, key
    return match.group(1).strip()


def define(name):
    match = re.search(rf'^#define {name} "(.*)"$', ISS, re.MULTILINE)
    assert match, name
    return match.group(1)


def iss_string(value):
    """An Inno Setup field: outer quotes dropped and doubled quotes undone when quoted."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('""', '"')
    return value


def run_entry():
    line = next(l for l in ISS.splitlines() if l.startswith("Root: HKCU"))
    return {key: iss_string(value) for key, value in re.findall(r'(\w+): ("(?:[^"]|"")*"|[^;]*)', line)}


def test_it_installs_per_user_without_admin_rights():
    assert setup_value("PrivilegesRequired") == "lowest"
    assert setup_value("DefaultDirName") == r"{localappdata}\Programs\{#AppName}"
    assert define("AppName") == "Claude Usage Dashboard"


def test_the_installer_waits_for_the_apps_mutex():
    assert setup_value("AppMutex") == instance.MUTEX_NAME


def test_the_installer_writes_the_run_value_the_app_reads_back(monkeypatch):
    entry = run_entry()
    assert entry["Subkey"] == autostart.RUN_KEY
    assert entry["ValueName"] == autostart.VALUE_NAME
    assert entry["Tasks"] == "startatlogin"
    app_dir = r"C:\Users\someone\AppData\Local\Programs\Claude Usage Dashboard"
    written = entry["ValueData"].replace("{app}", app_dir).replace("{#AppExe}", define("AppExe"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", app_dir + "\\" + define("AppExe"))
    assert written == autostart.command()


def test_the_tasks_page_decides_start_at_login_and_the_uninstaller_clears_it():
    code = ISS.split("[Code]", 1)[1]
    assert f"RunKey = '{autostart.RUN_KEY}';" in code
    assert f"ApprovedKey = '{autostart.APPROVED_KEY}';" in code
    assert f"RunValue = '{autostart.VALUE_NAME}';" in code
    install, uninstall = code.split("procedure CurUninstallStepChanged", 1)
    assert "WizardIsTaskSelected('startatlogin')" in install
    assert "RegDeleteValue(HKEY_CURRENT_USER, ApprovedKey, RunValue)" in install
    assert "RegDeleteValue(HKEY_CURRENT_USER, RunKey, RunValue)" in install
    assert "RegDeleteValue(HKEY_CURRENT_USER, RunKey, RunValue)" in uninstall
    assert "RegDeleteValue(HKEY_CURRENT_USER, ApprovedKey, RunValue)" in uninstall


def test_the_uninstaller_asks_before_deleting_the_apps_data(monkeypatch):
    assert "ExpandConstant('{localappdata}\\" + paths.APP_NAME + "')" in ISS
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Local")
    assert str(paths._app_home()) == "C:\\Local\\" + paths.APP_NAME  # the folder the app writes to
    assert "MB_YESNO or MB_DEFBUTTON2" in ISS  # No is the default answer
    assert "not UninstallSilent" in ISS


def test_the_installer_packs_the_build_the_spec_makes():
    assert define("AppExe") == "ClaudeUsageDashboard.exe"
    assert r'Source: "..\dist\ClaudeUsageDashboard\*"' in ISS
    assert setup_value("OutputDir") == r"..\dist"
    assert setup_value("OutputBaseFilename") == "ClaudeUsageDashboard-Setup-{#AppVersion}"
    assert setup_value("SetupIconFile") == r"..\build\assets\icon.ico"
    assert setup_value("AppId") == "{{2E7A6516-90C1-4991-8014-641D5A440BAF}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_installer.py -q`
Expected: collection ERROR, `FileNotFoundError` for `packaging/installer.iss`.

- [ ] **Step 3: Write `packaging/installer.iss`**

Write it with the Write tool, since it is full of backslashes:

```
; Inno Setup script for Claude Usage Dashboard: a per-user install that needs no admin rights.
;
; Build the app first (packaging/ClaudeUsageDashboard.spec), then from the repo root:
;     ISCC /DAppVersion=2.0.0 packaging\installer.iss
; Output: dist\ClaudeUsageDashboard-Setup-2.0.0.exe
;
; Names shared with the app (tests/test_installer.py checks them):
; - AppMutex is instance.MUTEX_NAME, so Setup asks the person to quit a running copy first.
; - The Run value is what autostart.command() writes when frozen: "<exe>" --background.
;   With any other text the tray's Start at login checkbox would read off.
; - AppId must never change: it's how Setup finds an earlier install to upgrade.

#ifndef AppVersion
  #error Pass the release number: ISCC /DAppVersion=2.0.0 packaging\installer.iss
#endif
#define AppName "Claude Usage Dashboard"
#define AppExe "ClaudeUsageDashboard.exe"

[Setup]
AppId={{2E7A6516-90C1-4991-8014-641D5A440BAF}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Jonathan Weaver
AppPublisherURL=https://github.com/1andonlyWeaver/claude-usage-dashboard
AppSupportURL=https://github.com/1andonlyWeaver/claude-usage-dashboard
AppUpdatesURL=https://github.com/1andonlyWeaver/claude-usage-dashboard/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
AppMutex=Local\ClaudeUsageDashboard
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=ClaudeUsageDashboard-Setup-{#AppVersion}
SetupIconFile=..\build\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: "startatlogin"; Description: "Start {#AppName} in the tray when I sign in to Windows"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; The previous build's libraries: a stale one left beside the new ones can break the app
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\ClaudeUsageDashboard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Start at login, written exactly as autostart.command() writes it when frozen
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ClaudeUsageDashboard"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: startatlogin; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  ApprovedKey = 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run';
  RunValue = 'ClaudeUsageDashboard';

{ The Start at login box on the tasks page decides, on every install and upgrade. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('startatlogin') then
      { As when the tray turns it on: clear an "off" left by Task Manager's Startup tab }
      RegDeleteValue(HKEY_CURRENT_USER, ApprovedKey, RunValue)
    else
      RegDeleteValue(HKEY_CURRENT_USER, RunKey, RunValue);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { The tray writes these too, so remove them whoever wrote them }
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, RunValue);
    RegDeleteValue(HKEY_CURRENT_USER, ApprovedKey, RunValue);
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\ClaudeUsageDashboard');
    if DirExists(DataDir) and not UninstallSilent and
       (MsgBox('Also delete your dashboard data?' + #13#10#13#10 +
               'That''s the usage database, person-hours estimates, settings and logs in ' + DataDir + '. ' +
               'Keep them and a later install carries on where this one left off. ' +
               'Claude Code''s own logs and sign-in aren''t touched either way.',
               mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
      DelTree(DataDir, True, True, True);
  end;
end;
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_installer.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: every test passes, no warnings.

- [ ] **Step 5: Compile the installer**

```bash
ISCC="$LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe"
VERSION="$(python -c 'import version; print(version.__version__)')"
MSYS_NO_PATHCONV=1 "$ISCC" "/DAppVersion=$VERSION" packaging/installer.iss 2>&1 | tail -8; ls -la dist/*.exe
```
Expected:
- ISCC ends with `Successful compile`, with no warnings beyond its usual summary;
- `dist/ClaudeUsageDashboard-Setup-2.0.0.exe` exists, roughly 25–45 MB.

Also run ISCC once without `/DAppVersion`:
```bash
MSYS_NO_PATHCONV=1 "$ISCC" packaging/installer.iss 2>&1 | tail -3
```
Expected: it fails with the `#error` text `Pass the release number: ...`.

Do not run the built installer: installing on this PC is a controller check, with the user's approval.

- [ ] **Step 6: Commit**

```bash
git add packaging/installer.iss tests/test_installer.py
git commit -m "Add the per-user Inno Setup installer: Start at login task, AppMutex, data kept unless asked"
```

---

### Task 6: The build-and-release workflow

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `tests/test_release_workflow.py`

**Interfaces:**
- Consumes:
  - `packaging/requirements-build.txt`, `packaging/ClaudeUsageDashboard.spec` and `packaging/installer.iss` (Tasks 4 and 5), and their output names;
  - `desktop.py --smoke`;
  - `version.__version__`.
- Produces: the workflow "Build and release".
  - On a pushed bare-number tag, it ends with a draft release carrying `ClaudeUsageDashboard-Setup-<tag>.exe`.
  - On a pull request touching the build, it runs everything but the tag check and the release.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_release_workflow.py`:

```python
"""The release workflow builds what packaging/ describes, and releases only from a matching tag."""
import re
from pathlib import Path

import version

REPO = Path(__file__).parent.parent
WORKFLOW = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def step(name):
    """The text of one step, from its name to the next step."""
    return WORKFLOW.split(f"- name: {name}\n", 1)[1].split("- name:", 1)[0]


def test_a_bare_number_tag_triggers_it_and_version_py_fits_that_pattern():
    assert "      - '[0-9]+.[0-9]+.[0-9]+'" in WORKFLOW
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version.__version__)


def test_the_steps_run_in_release_order():
    assert re.findall(r"^      - name: (.+)$", WORKFLOW, re.MULTILINE) == [
        "Read the version",
        "Check the tag matches version.py",
        "Install the packages",
        "Run the tests",
        "Build the app",
        "Smoke-test the build",
        "Build the installer",
        "Install, smoke-test and uninstall",
        "Create a draft release",
    ]


def test_only_a_tag_push_checks_the_tag_and_releases():
    for name in ("Check the tag matches version.py", "Create a draft release"):
        assert "if: github.event_name == 'push'" in step(name), name
    release = step("Create a draft release")
    assert "gh release create" in release and "--draft" in release


def test_it_builds_with_the_files_in_packaging():
    for needle in ("packaging/requirements-build.txt", "packaging/ClaudeUsageDashboard.spec",
                   r"packaging\installer.iss", r"dist\ClaudeUsageDashboard\ClaudeUsageDashboard.exe",
                   "ClaudeUsageDashboard-Setup-", "--smoke", "/TASKS=startatlogin"):
        assert needle in WORKFLOW, needle
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_release_workflow.py -q`
Expected: collection ERROR, `FileNotFoundError` for `.github/workflows/release.yml`.

- [ ] **Step 3: Write `.github/workflows/release.yml`**

```yaml
# Builds ClaudeUsageDashboard.exe and its installer on a clean Windows runner, then installs,
# smoke-tests and uninstalls that installer.
# - A pushed release tag (a bare number such as 2.0.0, equal to version.py) also creates a
#   draft GitHub release with the installer attached. Try it, then publish the draft by hand:
#   installed copies don't see a release until it's published.
# - A pull request that touches the build runs everything except the tag check and the release.
name: Build and release

on:
  push:
    tags:
      - '[0-9]+.[0-9]+.[0-9]+'
  pull_request:
    paths:
      - '.github/workflows/release.yml'
      - 'packaging/**'
      - 'requirements.txt'
      - 'desktop.py'

permissions:
  contents: write

jobs:
  build:
    runs-on: windows-latest
    env:
      TAG: ${{ github.ref_name }}
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: '3.12'

      - name: Read the version
        id: version
        run: |
          $number = python -c "import version; print(version.__version__)"
          "number=$number" >> $env:GITHUB_OUTPUT

      - name: Check the tag matches version.py
        if: github.event_name == 'push'
        env:
          VERSION: ${{ steps.version.outputs.number }}
        run: |
          if ($env:TAG -ne $env:VERSION) {
            Write-Error "Tag $env:TAG doesn't match version.py ($env:VERSION). Fix one, then tag again."
            exit 1
          }

      - name: Install the packages
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt -r packaging/requirements-build.txt pytest==9.1.1 httpx2==2.13.1

      - name: Run the tests
        run: python -m pytest -q

      - name: Build the app
        run: python -m PyInstaller --noconfirm --clean packaging/ClaudeUsageDashboard.spec

      - name: Smoke-test the build
        run: |
          $p = Start-Process -FilePath dist\ClaudeUsageDashboard\ClaudeUsageDashboard.exe -ArgumentList '--smoke' -Wait -PassThru
          $log = Join-Path $env:LOCALAPPDATA 'ClaudeUsageDashboard\logs\dashboard.log'
          if (Test-Path $log) { Get-Content $log -Tail 30 }
          if ($p.ExitCode -ne 0) { Write-Error "--smoke exited with $($p.ExitCode)"; exit 1 }

      - name: Build the installer
        env:
          VERSION: ${{ steps.version.outputs.number }}
        run: |
          $iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
          if (-not (Test-Path $iscc)) {
            choco install innosetup --version 6.7.3 -y --no-progress
          }
          & $iscc "/DAppVersion=$env:VERSION" packaging\installer.iss
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Install, smoke-test and uninstall
        env:
          VERSION: ${{ steps.version.outputs.number }}
        run: |
          $setup = "dist\ClaudeUsageDashboard-Setup-$env:VERSION.exe"
          $p = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=startatlogin' -Wait -PassThru
          if ($p.ExitCode -ne 0) { Write-Error "Setup exited with $($p.ExitCode)"; exit 1 }
          $app = Join-Path $env:LOCALAPPDATA 'Programs\Claude Usage Dashboard'
          $exe = Join-Path $app 'ClaudeUsageDashboard.exe'
          $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
          $run = (Get-ItemProperty $runKey -ErrorAction SilentlyContinue).ClaudeUsageDashboard
          if ($run -ne "`"$exe`" --background") { Write-Error "The Run value is [$run]"; exit 1 }
          $p = Start-Process $exe -ArgumentList '--smoke' -Wait -PassThru
          if ($p.ExitCode -ne 0) { Write-Error "The installed app's --smoke exited with $($p.ExitCode)"; exit 1 }
          Start-Process (Join-Path $app 'unins000.exe') -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
          for ($i = 0; $i -lt 30 -and (Test-Path $exe); $i++) { Start-Sleep -Seconds 1 }
          if (Test-Path $exe) { Write-Error 'The uninstaller left the app behind'; exit 1 }
          if ((Get-ItemProperty $runKey -ErrorAction SilentlyContinue).ClaudeUsageDashboard) {
            Write-Error 'The uninstaller left the Run value behind'; exit 1
          }

      - name: Create a draft release
        if: github.event_name == 'push'
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh release create $env:TAG "dist\ClaudeUsageDashboard-Setup-$env:TAG.exe" --draft --title $env:TAG --generate-notes
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_release_workflow.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: every test passes, no warnings.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml tests/test_release_workflow.py
git commit -m "Add the build-and-release workflow: test, build, smoke, install round trip, draft release"
```

---

### Task 7: Docs: README, CLAUDE.md and the spec

**Files:**
- Modify: `README.md` (replace the whole file)
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`
- Modify: `tests/test_release_files.py` (one new test)

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_release_files.py`:

```python
def test_the_readme_covers_installing_past_smartscreen_updating_and_uninstalling():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for text in ("releases/latest", "**More info**", "**Run anyway**", "## Updating",
                 "## Uninstalling", "apiKeyHelper", "update.json"):
        assert text in readme, text
    assert "never bills an API key" not in readme
    assert "first installer will come" not in readme
```

Run: `python -m pytest tests/test_release_files.py -q`
Expected: the new test FAILS on `releases/latest`.

- [ ] **Step 2: Replace `README.md`**

Write this text exactly. It has been through the humanize pass. The inner code fences are written with `~~~` here so this plan's own fence survives; write them as triple backticks in `README.md`.

```markdown
# Claude Usage Dashboard

A dashboard for your Claude Code usage on Windows. It shows how much of your plan's 5-hour and weekly limits you've used, where your tokens went by day, project and model, what the same usage would cost at API prices, and which sessions were the heavy ones.

It runs on your own PC, in its own window, with a tray icon that keeps it going after you close the window.

## What it reads, and what leaves your PC

It reads Claude Code's session logs in `%USERPROFILE%\.claude\projects`, the same folder inside any WSL distro that's running, and Claude Desktop's agent-mode logs in `%APPDATA%\Claude\local-agent-mode-sessions`. From those it copies token counts, model names, timestamps, project names and git branches into a local database. It doesn't keep the text of your conversations.

It also reads your Claude Code sign-in from `%USERPROFILE%\.claude\.credentials.json`. The access token goes to `api.anthropic.com` to read your quota percentages. The refresh token goes to `claude.ai`, and only when the dashboard renews your sign-in: when you click **Renew now**, or by itself if you turn on automatic renewal. A renewal is the only thing that changes that file.

Once a day it asks GitHub for the number of the latest release, so it can tell you about new versions. You can turn that off in Settings.

Nothing else leaves your PC, and there's no analytics or telemetry. The optional person-hours estimates described below also send Claude an extract of each day's sessions, through your own Claude Code: your prompts and Claude's last replies, both clipped, plus the paths of files it changed and the descriptions of shell commands it ran.

## What you need

- Windows 10 or 11, 64-bit.
- Claude Code, signed in with your Claude account rather than an API key. The usage charts work from the logs alone, but the quota gauges need the account sign-in and stay empty without it.
- Microsoft's WebView2 runtime, for the window. Windows 11 has it, and so do most Windows 10 PCs with a current Edge. Without it, the dashboard opens in your browser and everything else works the same.

## Installing

Download `ClaudeUsageDashboard-Setup-<version>.exe` from the [latest release](https://github.com/1andonlyWeaver/claude-usage-dashboard/releases/latest) and run it. It installs for your Windows account only, in `%LOCALAPPDATA%\Programs\Claude Usage Dashboard`, so it doesn't need admin rights.

The installer isn't signed, so Windows will probably stop it with "Windows protected your PC". Click **More info**, check that the app is `ClaudeUsageDashboard-Setup-<version>.exe`, then click **Run anyway**. Antivirus programs are sometimes wary of apps packaged the way this one is (with PyInstaller). If yours quarantines it, that's the likely reason.

The installer can start the dashboard each time you sign in to Windows. Leave that ticked and it waits in the tray with its window closed. The first start reads every log you have, so give it a minute or two if you've used Claude Code a lot.

## Using it

Closing the window only hides it. The tray icon brings it back; it may be hiding under the ^ arrow next to the clock. Its menu also opens the dashboard in your browser, turns **Start at login** on or off, checks for updates, and quits. Starting the app again while it runs just brings its window forward.

Ctrl+plus and Ctrl+minus zoom the window, and Ctrl+0 puts it back.

## Updating

When there's a newer release, a notice at the top of the dashboard links to it. Download the new installer and run it over the old one. Your data and settings stay. If the installer says the dashboard is running, choose **Quit** from the tray icon's menu, then click **OK**.

**Check for updates** in the tray menu, or **Check now** in Settings, asks GitHub straight away.

## Uninstalling

Uninstall it from **Installed apps** in Windows Settings. The uninstaller turns off Start at login and asks whether to delete your data too. Say no and a later install picks up where you left off.

## When your sign-in needs attention

If the dashboard can't read your quota, a banner at the top says why and offers a fix.

- Not signed in, or the sign-in expired: click **Sign in**. A console window opens with `claude auth login`. Once you finish there, the dashboard picks up the new sign-in within a few seconds.
- The token expired: this is normal after a night away. It renews the next time you use Claude Code, or you can click **Renew now**.

The tray icon also gets an amber dot, and Windows shows one notification, when you need to sign in or install Claude Code.

Settings (the gear icon) can renew the token automatically. That's off by default: if the dashboard and Claude Code renew at the same moment, one of them loses, and you'd have to sign in again.

## Person-hours estimates (off by default)

The cost card can show roughly how long your work would have taken a person without AI. Turn on **Estimate person-hours** in Settings to get them. The dashboard then has Claude (Sonnet, through your Claude Code sign-in) read that extract of each day's sessions and estimate the hours.

That costs quota from your plan: about 1–2% of a week's quota to catch up on the last 90 days, then about 0.1–0.2% a week. The dashboard runs Claude Code without `ANTHROPIC_API_KEY` and the other variables that would send the bill to an API account or a cloud provider. It can't see a key that Claude Code gets from its own settings, though: an `apiKeyHelper`, or an `env` block in `%USERPROFILE%\.claude\settings.json`. If you've set one of those up, the estimates are billed to that key. Claude's short summary of each day's work is stored with its estimate.

## Where your data lives

Everything the dashboard keeps is in `%LOCALAPPDATA%\ClaudeUsageDashboard`. Its `data` folder holds the database, a quota cache, your settings, the last update check (`update.json`), the port of the running copy (`runtime.json`) and the window's own browser storage (`webview`). Its `logs` folder holds `dashboard.log`.

Think twice before deleting `data\usage.db`. The dashboard rebuilds it from your logs on the next start, but Claude Code deletes logs older than 30 days by default, so anything older is gone for good, person-hours estimates included.

## If something's wrong

Open Settings and click **Copy diagnostics**, then send the text to whoever gave you the dashboard. It says what state the connection is in, when your token expires, when the quota was last read, and where the dashboard found Claude Code. It never includes the token. The log, `%LOCALAPPDATA%\ClaudeUsageDashboard\logs\dashboard.log`, helps too.

## Running from source

This is for working on the dashboard itself. You need Python 3.12:

~~~
conda env create -f environment.yml
conda activate claude-usage-dashboard
python desktop.py
~~~

`python app.py --port 8080` runs the server on its own, for your browser at http://127.0.0.1:8080/. Without conda, `pip install -r requirements.txt` works too.

From source, the data stays in the repo's `data` folder. The server prints its log to the console you started it from, or to `logs\dashboard.log` when started without one (with `pythonw`). Quit the installed app before running `desktop.py`: only one copy runs per Windows session. And don't run two copies with automatic renewal on, installed or not, or they'll race to renew your token.

To build the installer yourself, install the build tools with `pip install -r packaging/requirements-build.txt` and get [Inno Setup 6](https://jrsoftware.org/isdl.php). Then, from the repo folder, with the number from `version.py`:

~~~
python -m PyInstaller --noconfirm --clean packaging/ClaudeUsageDashboard.spec
ISCC /DAppVersion=2.0.0 packaging\installer.iss
~~~

The installer lands in `dist`. Releases themselves are built by GitHub Actions from a version tag.

## License

MIT, see `LICENSE`. The dashboard ships with Chart.js 4.4.4 (MIT) and the Sora, DM Sans and DM Mono fonts (SIL Open Font License 1.1). Their licenses sit next to them in `static/vendor` and `static/fonts`.
```

- [ ] **Step 3: Update `CLAUDE.md`**

Make these edits:

1. **Setup & Running.** After the paragraph that starts `Don't run it from the main checkout while the scheduled :8080 server runs`, add:

   ````markdown
   Build the exe and the installer. This needs `pip install -r packaging/requirements-build.txt` and Inno Setup 6; on this PC it's installed per user in `%LOCALAPPDATA%\Programs\Inno Setup 6`:
   ```bash
   conda activate claude-usage-dashboard && python -m PyInstaller --noconfirm --clean packaging/ClaudeUsageDashboard.spec   # -> dist/ClaudeUsageDashboard/
   MSYS_NO_PATHCONV=1 "$LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe" /DAppVersion=2.0.0 packaging/installer.iss              # -> dist/ClaudeUsageDashboard-Setup-2.0.0.exe
   ```
   `MSYS_NO_PATHCONV=1` stops Git Bash from turning `/DAppVersion=...` into a path. To smoke-test the frozen exe without touching the real profile, set `USERPROFILE` and `LOCALAPPDATA` to scratch folders and run `ClaudeUsageDashboard.exe --smoke`.

   The installed app and a source `desktop.py` share the `Local\ClaudeUsageDashboard` mutex but not `runtime.json`, since their data dirs differ. A source copy started while the installed one runs therefore waits 10 s, then exits 1. Quit one first.
   ````

2. **Architecture block.**
   - Change `app.py       FastAPI server — 22 API endpoints` to `app.py       FastAPI server — 24 API endpoints`.
   - Change the `settings.py` line's description to `User settings in data/settings.json: judge opt-in, automatic token renewal, the desktop app's preferred_port (8765), the daily update check and the dismissed update notice`.
   - Change the `tray.py` line's description to `The desktop app's tray icon (pystray): menu (with Check for updates), amber attention dot, one notification when the sign-in needs the person; polls /api/connection every 30 s`.
   - Change the `desktop.py` line's description to `Desktop entry point: pywebview window (close hides to tray, Ctrl+0/plus/minus zoom), uvicorn thread on preferred_port or a free one, tray, second-launch handoff, --background, --smoke`.
   - Add after the `desktop.py` line:
     ```
     updates.py   Update check: GitHub's latest release at most once a day (an hour after a failure), kept in data/update.json and compared with version.py
     ```
   - Add after the `scripts/` lines:
     ```
     packaging/   ClaudeUsageDashboard.spec (PyInstaller: onedir, windowed), build_assets.py (the exe's icon from tray.icon_image, its version resource from version.py),
                  installer.iss (Inno Setup, per user), requirements-build.txt (PyInstaller pins). Not a Python package: a PyPI library is called `packaging`
     .github/workflows/release.yml  On a bare-number tag: tag check, tests, build, --smoke, installer, silent install/--smoke/uninstall, draft release. PRs touching the build run all but the tag check and release
     ```

3. **Key Paths table.** Add these rows after the `data/webview/` row:
   ```
   | `data/update.json` | The last update check: when, the latest release number, and any error |
   | `%LOCALAPPDATA%\Programs\Claude Usage Dashboard` | The installed app (Inno Setup, per user). Its data and log are in `%LOCALAPPDATA%\ClaudeUsageDashboard\data` and `\logs` |
   ```

4. **Server Restart.** In the first paragraph's file list, insert `updates.py` between `settings.py` and `version.py`.

5. **Gotchas, first bullet ("Tests never touch `data/usage.db`").** Append: `` `updates._urlopen` fails any test that would reach GitHub, and `updates.STATE_FILE` points at a per-test file. ``

6. **API Endpoints.**
   - Add `` `/api/update`, `/api/update/check` (POST) `` to the end of the endpoint list line.
   - In the paragraph about `/api/settings`, change `` reads or changes `judge_enabled`, `auto_refresh_token` and `preferred_port` `` to `` reads or changes `judge_enabled`, `auto_refresh_token`, `preferred_port`, `update_check` and `dismissed_version` ``.
   - After the `/api/app/info` paragraph, add:

   ```markdown
   `/api/update` returns the last update check: `current`, `latest`, `available`, `notify` (available, checks on, and not the dismissed release), `url` (the release page, built from the tag), `checked_at`, `error` (`http-<code>`, `network-error`, `bad-answer`) and `enabled`. `/api/update/check` asks GitHub at once, even with `update_check` off. `app._update_tick` runs hourly and asks only when `updates.due()`.
   ```

7. **New section** directly before `## Person-hours`:

   ```markdown
   ## Releasing

   Release tags are bare numbers equal to `version.__version__` (annotated, no `v`).
   1. Bump `version.py` on develop. Settings, `/api/app/info`, the exe's version resource and the installer all read it.
   2. Fast-forward main to develop and push it, then tag main: `git tag -a 2.1.0 -m 2.1.0 && git push origin 2.1.0`.
   3. `.github/workflows/release.yml` checks the tag against `version.py`, runs the tests, builds, smoke-tests the exe, builds the installer, installs it silently with Start at login (checking the `Run` value), smoke-tests the installed exe and uninstalls it. Then it creates a **draft** release with `ClaudeUsageDashboard-Setup-<version>.exe`.
   4. Download the installer from the draft, try it, then publish the draft. `releases/latest` skips drafts, so installed copies see the release only once it's published.

   A tag that doesn't match `version.py` fails before anything is built: delete the tag, fix it, tag again.
   ```

- [ ] **Step 4: Amend the spec**

In `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`:

1. Replace the `Status:` line with: `Status: Phases 1–4 are built (PRs #7, #8 and #9, then the packaging PR). Phase 5, moving Jonathan's own setup over, is manual.`
2. In the Architecture section's "Main thread" bullet, append: `WebView2's browser shortcut keys are switched back on after pywebview's setup, so Ctrl+0, Ctrl+plus and Ctrl+minus zoom (F5, Ctrl+F and Ctrl+P come with them; DevTools stay off).`
3. In the "Tray thread" bullet, replace `Menu: Open dashboard (default action), Open in browser, Start at login (checkbox), Quit. Check for updates joins it in Phase 4, with `updates.py`.` with `Menu: Open dashboard (default action), Open in browser, Start at login (checkbox), Check for updates, Quit. Check for updates asks the app's own server to check GitHub now, opens the release page when there's a newer version, and otherwise says in a notification that it's up to date or couldn't reach GitHub.`
4. Replace the `updates.py` row's purpose with: `Asks GitHub's `releases/latest` at most once a day (an hour after a failed check), from an hourly tick in `app.py`, and keeps the answer in `<data>/update.json`. Compares its bare-number tag with `__version__`; any other tag is ignored. `/api/update` reports the answer, and `/api/update/check` (POST) asks at once. The release-page link is built from the validated tag.`
5. In "Changes to existing code" → Frontend, replace `A settings panel, opened from a gear icon, holds the judge toggle, the automatic refresh toggle, connection diagnostics, and the version/update notice.` with `A settings panel, opened from a gear icon, holds the judge toggle, the automatic refresh toggle, connection diagnostics, and an Updates section (the daily check's switch, its last answer, Check now). A notice under the header offers a newer release with Download and Dismiss; Dismiss stores `dismissed_version`, and the notice stays hidden while the check is off.`
6. In "Packaging and release":
   - Append to the spec-file bullet: `` `packaging/build_assets.py` draws the icon from `tray.icon_image` at each size and writes the exe's version resource from `version.py`. Its FileDescription is what Windows shows on notifications, the taskbar and Task Manager instead of "Python". No AppUserModelID is set; it would split a pinned shortcut from the running window unless every shortcut carried it. ``
   - Replace `A "Start at login" task, checked by default, writes the `Run` value with `--background`.` with `A "Start at login" task, checked by default, writes the `Run` value as `"<exe>" --background`, exactly as `autostart.command()` does. The tasks page decides on every install: unticked removes the value, and ticked also clears Task Manager's switch. Setup clears `{app}\_internal` before copying, and installs `LICENSE.txt`.`
   - Replace `The uninstaller removes the `Run` value and asks before deleting `%LOCALAPPDATA%\ClaudeUsageDashboard`. The default is to keep it.` with `The uninstaller removes the `Run` and `StartupApproved` values, whoever wrote them, and asks before deleting `%LOCALAPPDATA%\ClaudeUsageDashboard`. The default is to keep it; a silent uninstall keeps it.`
   - Replace the workflow's numbered list (steps 1–7) and its lead-in with:

     ```markdown
     - **`.github/workflows/release.yml`**, running on `windows-latest`. A pushed bare-number tag (`2.0.0`) runs every step; a pull request touching the build runs all but the first and last:
       1. Fail if the tag doesn't match `version.__version__`.
       2. Install deps, and run pytest.
       3. Build with PyInstaller, and smoke-test the frozen exe with `--smoke`.
       4. Compile the installer with Inno Setup (preinstalled on the runner; choco 6.7.3 if it's missing).
       5. Install it silently with Start at login, check the `Run` value, run `--smoke` on the installed exe, and uninstall silently.
       6. `gh release create --draft` with `ClaudeUsageDashboard-Setup-<ver>.exe` attached. Publishing the draft, after trying the installer, is the manual step; installed copies can't see a draft.
     ```
   - Replace the `--smoke` bullet's text with: `**`--smoke`** starts the server on a free port, GETs `/`, `/api/connection` and `/static/vendor/chart.umd.js`, and in a frozen build imports pywebview's WinForms backend, so a build missing pythonnet or the WebView2 DLLs fails instead of quietly opening the browser. It exits 0 or 1.`
7. In "Testing" → Phase 4 manual checks, replace the first bullet with `The workflow passes on the pull request. The release tag's run leaves a draft: download its installer, try it, then publish.`. Replace the second bullet (the one starting `Install the build in Windows Sandbox`) with: `Install the build in Windows Sandbox where it can be turned on: a clean machine without Claude Code. It should show `not-installed`, render with no network access to the CDN or fonts, and uninstall cleanly. Where Sandbox is off (as on Jonathan's PC), check the bundle for DLLs that a stock Windows lacks, and give the first release to one person before the rest.`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_release_files.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: every test passes, no warnings.

Run: `grep -n "Check for updates joins it\|never bills an API key\|first installer will come\|not yet implemented" README.md CLAUDE.md docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md tests/test_release_files.py
git commit -m "Document installing, updating and releasing, and amend the spec where Phase 4 departs from it"
```

---

## Manual checks (controller, after Task 7)

Implementers don't run these. Steps marked **(approval)** need the user's yes first. Steps marked **(screen)** drive the mouse and keyboard, so they wait until the user says they're away, and they use `scratchpad/ui.py` (SendInput + ImageGrab), not Windows-MCP.

**Test instance for the frozen exe.** Use a short scratch tree inside the worktree's git-ignored `data/`. WebView2 drops `localStorage` past 260-character paths.
- Set `USERPROFILE=<worktree>\data\frozen\home` and `LOCALAPPDATA=<worktree>\data\frozen\local`, so the data, log and WebView2 profile land under `data\frozen\local\ClaudeUsageDashboard`.
- Set `PERSON_HOURS_WORKER=off`.
- Start it with `run_in_background: true`.

**M1. The frozen exe on this PC** (screen):
- a. The title bar and taskbar button show the ring icon. Hovering the taskbar button, and Task Manager's Processes tab, say "Claude Usage Dashboard".
- b. The scratch home has no credentials, so the start shows the sign-in notification. Its header names "Claude Usage Dashboard", not "Python".
  - If it still says Python, record it. Adding an AppUserModelID to both `desktop.py` and the installer's shortcut is the fallback, and that is a follow-up, not a fix inside this PR.
- c. In the window, Ctrl+plus and Ctrl+minus zoom, Ctrl+0 resets, and F12 opens nothing.
- d. Tray menu → Check for updates. With no release published yet, GitHub answers 404, so the notification says "Couldn't reach GitHub to check for updates. Try again later."
- e. Quit. Write `data\frozen\local\ClaudeUsageDashboard\data\update.json` containing `{"checked_at": <now>, "latest": "9.9.9", "error": null}`, then start the exe again.
  - The notice reads "Version 9.9.9 is available. You have 2.0.0."
  - Download opens the browser at `.../releases/tag/9.9.9`; a 404 page is fine.
  - Dismiss hides the notice, and it stays hidden after a reload (F5).
  - Settings → Updates shows "Version 9.9.9 is available. Download it", and the switch turns off and on.
- f. Second launch timing: with the first copy running, time `ClaudeUsageDashboard.exe` from start to exit (it exits 0 once the window is forward). Record the time; the source run took about 2 s.
  - Over 3 s is worth a follow-up: moving `desktop.py`'s heavy imports below the mutex check.
- g. `--background` starts in the tray only, and Quit frees the port and removes the icon.

**M2. The installer on this PC** (approval, screen). It writes to the real HKCU and Start menu, so uninstall it in the same session.
- Run `dist\ClaudeUsageDashboard-Setup-2.0.0.exe` and leave Start at login ticked. Then check:
  - the Start menu entry;
  - `HKCU\...\Run\ClaudeUsageDashboard` = `"C:\Users\weaverjc\AppData\Local\Programs\Claude Usage Dashboard\ClaudeUsageDashboard.exe" --background`;
  - `LICENSE.txt` beside the exe.
- Launch the installed exe with the scratch `USERPROFILE` and `LOCALAPPDATA`. The tray's Start at login reads on.
- Run Setup again while the app runs. It must ask for the app to be closed, through AppMutex.
- Uninstall from Installed apps:
  - the `Run` value is gone, along with the install folder;
  - the data prompt appears only when the real `%LOCALAPPDATA%\ClaudeUsageDashboard` exists, and No is the default.

**M3. CI on the pull request** (approval to push and open the PR): the "Build and release" workflow runs on the PR, because it touches `packaging/**`, and must pass every step. Read it with `gh run view`.

**M4. DLL check, in place of Windows Sandbox.** The user chose on 2026-10-01 to skip Sandbox: it's disabled on this PC and needs admin rights and a reboot. This check covers the failure Sandbox would catch: a DLL that this PC and the CI runner both have, but a stock Windows 10/11 PC lacks.
- **How:** a scratchpad script uses `pefile` (installed with PyInstaller) to list every DLL that each `.exe`, `.dll` and `.pyd` under `dist/ClaudeUsageDashboard/` imports.
- **What counts as fine:**
  - a DLL that is in the bundle itself, matched by name and ignoring case;
  - an API set (`api-ms-win-*`, `ext-ms-*`);
  - a DLL that is in `C:\Windows\System32` and isn't a redistributable: not `msvcp*`, `vcruntime*`, `concrt*`, `vcomp*`, `msvcr*`, `mfc*`, `vcamp*`, and not a debug build (`*d.dll` variants such as `ucrtbased.dll`).
- **What to report:** anything else, with the bundled file that imports it.
  - Managed .NET assemblies import only `mscoree.dll`, which ships with Windows.
  - A flagged C++ runtime DLL gets bundled through the spec's `binaries`. That is a fix in this PR.
- **The release itself** goes to one friend first, before the rest.

**After merging (the release itself; a separate approval):**
1. Fast-forward main to develop and push.
2. `git tag -a 2.0.0 -m 2.0.0` on main, and push the tag.
3. Watch the run; it ends with a draft.
4. Download the installer from the draft (`gh release download 2.0.0 --pattern "*.exe"`). That file carries the mark of the web, so SmartScreen's "More info → Run anyway" can be checked for real.
5. Publish the draft.

Phase 5, moving the user's own setup over, comes after that.
