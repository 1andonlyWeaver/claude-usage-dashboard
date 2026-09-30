# Portability Implementation Plan (Desktop App, Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard runs on someone else's Windows PC without depending on this one. It needs no CDN, finds Claude Code logs in any running WSL distro, writes a log that can't crash on odd characters and can't grow without bound, and can't bill an API key. It also ships the files a release needs: version, pinned requirements, license and README.

**Architecture:** Mostly small, separate changes:
- `version.py` and `applog.py` are new, one job each.
- `ingest.py` gains running-distro detection.
- `person_hours.py` filters the judge's environment.
- `app.py` moves its startup work to a lifespan handler.
- The frontend loads Chart.js and three font families from `static/`.

Three Phase 1 leftovers ride along: the "r" shortcut firing inside the settings dialog, switch labels that read their whole help paragraph as their name, and the test client's `httpx2` warning.

**Tech Stack:** Python 3.12, FastAPI 0.142 / Starlette 1.7, uvicorn 0.54, SQLite, vanilla JS, Chart.js 4.4.4, pytest 9 with `httpx2` for `TestClient`.

**Spec:** `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`. This plan implements Phase 2 from the "Phases" section, plus these bullets elsewhere in the spec:
- "Changes to existing code": the `person_hours.py` and `ingest.py` bullets, the lifespan item under `app.py`, and the vendored assets under Frontend.
- "Packaging and release": the `requirements.txt`, Logging and Docs bullets.
- The spec's version line in the connection panel.

**Where this plan departs from the spec** (each departure is amended in the spec by the task that makes it):
1. **WSL: running distros only.** The spec says to list every installed distro once per process. On 2026-09-30, reading a stopped distro through `\\wsl.localhost` was found to start it: after `wsl --terminate Ubuntu`, the :8080 dashboard's next 90-second ingest pass brought Ubuntu back up for about 40 seconds, even though no WSL transcript had changed since April. So Task 3 asks `wsl.exe --list --running --quiet` on each pass (at most once a minute) and never touches a stopped distro. A distro's transcripts only change while it runs, so the next pass after it starts picks them up.
2. **Log rotation for source runs too.** The spec rotates the log only when frozen. The launcher's log on this PC is 56 MB, so Task 4 applies rotation to the launcher and `pythonw` runs as well.
3. **`requirements.txt` holds today's runtime packages only.** pywebview, pystray and Pillow join it in Phase 3, when code first imports them.
4. **The judge drops four variables, not one:** `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX`. Each of them routes the CLI's billing away from the subscription, and the spec's intent is that the judge "can never bill the API".

**Deliberately left for later phases:**
- pywebview, pystray and Pillow in `requirements.txt`, `desktop.py`, `runtime.json`, and the `/api/app/*` endpoints (Phase 3).
- The settings keys `update_check`, `dismissed_version` and `preferred_port` (Phases 3–4).
- The README's install and SmartScreen sections, the update notice, and the release workflow (Phase 4). The README written here covers running from source.

## Global Constraints

- **Environment:** every `python` / `pytest` / `pip` command runs in the conda env. Each Bash call is a fresh shell, so prefix every command with:
  `eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && `
- **The env was recreated on 2026-09-30:** Python 3.12.14, fastapi 0.142.2, starlette 1.7.0, uvicorn 0.54.0, jinja2 3.1.6, orjson 3.12.0, pytest 9.1.1, httpx 0.28.1 (swapped for httpx2 in Task 1).
- **Working directory:** the worktree root, `C:\Users\weaverjc\Projects\Personal\claude-usage-dashboard\.claude\worktrees\busy-visvesvaraya-0b60fc`, on branch `claude/portability`.
- **Baseline:** `python -m pytest -q` passes 219 tests with 3 warnings before Task 1.
  - Task 1 removes all three warnings.
  - From Task 1 on, every task ends with the full suite green, and the summary line must show no warnings.
- **Writing files:** use the Write and Edit tools, not bash heredocs. In this environment, heredocs have dropped backslashes (seen 2026-09-30), and several files here contain Windows paths.
- **No new runtime Python dependencies.** `httpx2` replaces `httpx` as a test-only dependency. The vendored Chart.js and fonts are static files.
- **Downloads:** Task 6 downloads exactly the 13 files listed there, from the listed jsDelivr URLs, and checks each SHA-256. Nothing else is downloaded.
- **Running from source must keep working.** Both `python app.py --port 8080` and the Task Scheduler launcher must still work.
- **Never press Sign in against the real `claude` CLI** when verifying. On 2026-09-29 it completed a real `claude auth login` unattended and created a session that had to be revoked.
- **Tests stay isolated.** They must never touch `data/`, `~/.claude`, a real WSL distro or the network, and must never start a real process. `tests/conftest.py` enforces this. Task 3 adds a guard for WSL.
- **User-facing text** (README, settings panel) must read as plain human writing. Use the exact strings in this plan; the README text below has already been through the humanize pass.
- **Accessibility:** invoke the `wcag-a11y` skill before changing the Task 7 markup.
- **Commits:**
  - One commit per task, with a plain imperative message.
  - **No `Co-authored-by` or AI-attribution trailers** (user rule).
  - Never stage `.claude/` or other AI tool files.
  - `CLAUDE.md` is tracked in this repo and updated alongside code (precedent `d7d6e88`).
- **Server restarts:** `app.py`, `applog.py`, `auth.py`, `db.py`, `ingest.py`, `paths.py`, `settings.py` and `version.py` load once at startup. Template, JS and CSS edits need no restart.

## Review Focus

These are inputs the spec implies but doesn't spell out. Each has a pinned test in the task named.

1. **A stopped WSL distro stays stopped.** The dashboard must never start a distro to look for logs, and must ask `wsl.exe` without flashing a console window. (Task 3: `test_stopped_distros_are_never_touched`, `test_running_distros_come_from_wsl_exe_without_a_console`.)
2. **A PC where WSL is missing, broken or hung** still ingests its Windows logs. (Task 3: the `test_a_broken_wsl_means_no_distros` rows and `test_without_wsl_exe_nothing_runs`.)
3. **A log line with characters outside cp1252** must be written, not raise in the thread that logged it. Examples: an emoji in a project path, or a Windows error message in another language. (Task 4: `test_utf8_stdio_makes_print_safe_on_a_cp1252_stream`.)
4. **The log file is held open by another process at startup**, for example an orphaned server from an older launcher. The server must still start. (Task 4: `test_rotation_failure_is_not_fatal`.)
5. **A PC with no internet access** renders the whole page, charts and fonts included, and no request leaves 127.0.0.1. (Task 6: `test_page_loads_nothing_from_the_internet`, `test_stylesheets_load_nothing_from_the_internet`, and the manual network check.)

## Test instance (used by Tasks 6 and 7)

The always-on server on :8080 runs from the main checkout, not this worktree. Verify UI work on a second instance with a scratch home. `USERPROFILE` makes `Path.home()` point at the scratch home, so the instance has no credentials and its own transcripts. `CUD_DATA_DIR` keeps its DB out of the worktree. `PERSON_HOURS_WORKER=off` keeps the judge out of the way.

Start it with the Bash tool and `run_in_background: true`. `SP` is this session's scratchpad directory. Every Bash call is a fresh shell, so repeat the `SP=...` line in each call that uses `$SP`:
```bash
SP="C:/Users/weaverjc/AppData/Local/Temp/claude/C--Users-weaverjc-Projects-Personal-claude-usage-dashboard--claude-worktrees-busy-visvesvaraya-0b60fc/98bd79c0-669a-4517-a3f2-aeaa826c399a/scratchpad"
mkdir -p "$SP/h/.claude/projects" "$SP/h/data"
eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && \
  USERPROFILE="$(cygpath -w "$SP/h")" CUD_DATA_DIR="$(cygpath -w "$SP/h/data")" PERSON_HOURS_WORKER=off \
  python app.py --port 8888
```
- **Browse:** open `http://127.0.0.1:8888/` in the built-in browser pane (`mcp__Claude_Browser__preview_start` with `url`). Read state with `get_page_text` / `read_page` / `javascript_tool`, list requests with `read_network_requests`, and take screenshots for visuals.
- **The instance shows the `signed-out` banner** (no credentials in the scratch home). That's expected. Don't press its Sign in button.
- **Stop it:** `powershell -NoProfile -Command 'Get-NetTCPConnection -LocalPort 8888 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }'`
- **Restart it** after any Python change.

---

### Task 1: Lifespan handler and a warning-free test run

**Files:**
- Modify: `app.py:12` (imports), `app.py:46` (app creation), `app.py:531` (the `on_event` decorator)
- Modify: `environment.yml`, `pytest.ini`, `CLAUDE.md` (the "Run the tests" paragraph)
- Test: `tests/test_app_lifespan.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `app._lifespan`, an `asynccontextmanager` that awaits `app.startup()` once.
  - `app.startup` stays an `async def` with no decorator. It is looked up by name at start time, so tests can monkeypatch it.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_app_lifespan.py`:

```python
"""The server's startup work runs through FastAPI's lifespan hook, not the deprecated on_event."""
from fastapi.testclient import TestClient

import app


def test_startup_is_not_registered_as_a_deprecated_event_handler():
    assert not getattr(app.app.router, "on_startup", [])


def test_starting_the_server_runs_startup_once(monkeypatch):
    calls = []

    async def fake_startup():
        calls.append("startup")

    monkeypatch.setattr(app, "startup", fake_startup)
    with TestClient(app.app, base_url="http://127.0.0.1:8080") as client:
        assert calls == ["startup"]
        assert client.get("/api/settings").status_code == 200
    assert calls == ["startup"]
```

- [ ] **Step 2: Run only the registration test and confirm it fails.** Don't run the second test against the old code: under `on_event`, entering the `TestClient` runs the *real* startup. That would start an ingest of the real `~/.claude/projects` in a background thread, which the rest of the session would see.
Run: `python -m pytest tests/test_app_lifespan.py::test_startup_is_not_registered_as_a_deprecated_event_handler -v`
Expected: FAIL. The assertion shows a list holding the `startup` function.

- [ ] **Step 3: Move startup to a lifespan handler.**
  1. `app.py:12`: change `from contextlib import closing` to:
     ```python
     from contextlib import asynccontextmanager, closing
     ```
  2. Replace `app.py:46`, `app = FastAPI(title="Claude Usage Dashboard")`, with:
     ```python
     @asynccontextmanager
     async def _lifespan(_app):
         """Run startup() once as the server starts. Nothing needs undoing at shutdown: the
         background threads are daemons."""
         await startup()  # defined further down, found by name when the server starts
         yield


     app = FastAPI(title="Claude Usage Dashboard", lifespan=_lifespan)
     ```
  3. Delete the line `@app.on_event("startup")` directly above `async def startup():` (line 531 before these edits). Leave the function itself unchanged.

- [ ] **Step 4: Run both lifespan tests.**
Run: `python -m pytest tests/test_app_lifespan.py -v`
Expected: 2 passed.

- [ ] **Step 5: Swap `httpx` for `httpx2`.** Starlette 1.7's `TestClient` prefers `httpx2` and warns when it falls back to `httpx`.
  1. In `environment.yml`, change the pip line `- httpx` to `- httpx2`.
  2. Run: `pip install httpx2 && pip uninstall -y httpx && pip check`
     Expected: `httpx2`, `httpcore2` and `truststore` installed; `httpx` uninstalled; `No broken requirements found.`
     Nothing in the repo imports `httpx` directly (`grep -rn "import httpx" --include=*.py .` finds nothing), so uninstalling it proves the tests don't need it.

- [ ] **Step 6: Remove the dead warning filter.** Starlette 1.7 no longer uses the `anyio.abc.BlockingPortal` alias; with the filter removed, that warning doesn't appear. Replace `pytest.ini` with:

```ini
[pytest]
testpaths = tests
pythonpath = . tests
```

- [ ] **Step 7: Run the full suite.**
Run: `python -m pytest -q`
Expected: `221 passed`, and the summary line has no `warnings`. If any warning appears, stop and report it; don't add a filter.

- [ ] **Step 8: Update `CLAUDE.md`.** In the "Setup & Running" section, replace these two lines:
```
`httpx` is a pip test dependency in `environment.yml` (starlette's `TestClient` needs it). `pytest.ini` filters the anyio `BlockingPortal` deprecation warning that `TestClient` raises.
An existing env created before `httpx` was added needs `pip install httpx` (inside the activated env) to run the tests.
```
with:
```
`httpx2` is a pip test dependency in `environment.yml`. Starlette's `TestClient` needs it; with plain `httpx` it still works but warns. An env created before `httpx2` was added needs `pip install httpx2` (inside the activated env). The suite finishes with no warnings; treat a new one as something to fix, not filter.
```

- [ ] **Step 9: Commit.**
```bash
git add app.py environment.yml pytest.ini CLAUDE.md tests/test_app_lifespan.py
git commit -m "Start background work from a lifespan handler and run the tests warning-free"
```

---

### Task 2: The judge never bills an API account

**Files:**
- Modify: `person_hours.py:10-23` (imports), `person_hours.py` (a new constant and helper directly above `def call_judge`, plus one keyword argument inside it)
- Modify: `CLAUDE.md` (Person-hours section), `docs/superpowers/specs/2026-09-23-person-hours-view-design.md:180`
- Test: `tests/test_person_hours_call.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `person_hours.BILLING_ENV: tuple[str, ...]`.
  - `person_hours.judge_env() -> dict[str, str]`: `os.environ` minus `BILLING_ENV`, compared case-insensitively.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_person_hours_call.py`:

```python
def test_call_judge_never_passes_api_billing_variables(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("CUD_TEST_KEEP_ME", "yes")
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    ph.call_judge("S", "claude", runner=run)
    env = run.calls[0][1]["env"]
    names = {k.upper() for k in env}
    assert not names & {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"}
    assert env["CUD_TEST_KEEP_ME"] == "yes"
    assert "PATH" in names  # everything else is passed through
```

- [ ] **Step 2: Run it and confirm it fails.**
Run: `python -m pytest tests/test_person_hours_call.py::test_call_judge_never_passes_api_billing_variables -v`
Expected: FAIL with `KeyError: 'env'`.

- [ ] **Step 3: Filter the environment.**
  1. Add `import os` to the stdlib imports at the top of `person_hours.py`, between `import json` and `import re`.
  2. Directly above `def call_judge(summary_text: str, cli: str, runner=None) -> dict:`, add:
     ```python
     # Environment variables that point the CLI at an API account or a cloud provider instead
     # of the person's Claude subscription. The judge is opt-in on the understanding that it
     # spends subscription quota, so it never passes these on.
     BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


     def judge_env() -> dict:
         """The server's environment without BILLING_ENV (names compared case-insensitively)."""
         return {k: v for k, v in os.environ.items() if k.upper() not in BILLING_ENV}


     ```
  3. In `call_judge`, add `env=judge_env(),` to the `runner(...)` call, after `cwd=str(workdir),`:
     ```python
             proc = runner(cmd, input=summary_text, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=CALL_TIMEOUT_S, cwd=str(workdir),
                           env=judge_env(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
     ```
  4. In `call_judge`'s docstring, after the sentence ending "so the dashboard never ingests its own judge sessions.", add:
     ```
     It runs without BILLING_ENV, so it always uses the Claude subscription.
     ```

- [ ] **Step 4: Run the test file.**
Run: `python -m pytest tests/test_person_hours_call.py -v`
Expected: all pass.

- [ ] **Step 5: Update the docs.**
  1. `CLAUDE.md`, Person-hours section. Replace:
     ```
     - **Quota**: judge calls use subscription quota, roughly 0.1–0.2% of the weekly quota once the backfill is done. They write no transcript, so `detect_other_pct()` counts an interval with a judge call as local activity.
     ```
     with:
     ```
     - **Quota**: judge calls use subscription quota, roughly 0.1–0.2% of the weekly quota once the backfill is done. They write no transcript, so `detect_other_pct()` counts an interval with a judge call as local activity. The CLI runs without `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX` (`person_hours.BILLING_ENV`), so an API key or cloud setup in the server's environment never takes the bill.
     ```
  2. `CLAUDE.md`, the "Known limits" list: delete the line
     ```
       - If `ANTHROPIC_API_KEY` is set in the server's environment, judge calls bill to the API rather than the subscription.
     ```
  3. `docs/superpowers/specs/2026-09-23-person-hours-view-design.md:180`. Replace:
     ```
     - The judge inherits the server's environment. If `ANTHROPIC_API_KEY` is set there, judge calls bill to the API instead of the subscription.
     ```
     with:
     ```
     - The judge inherits the server's environment minus `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX` (since 2026-09-30), so it always bills the subscription.
     ```

- [ ] **Step 6: Run the full suite.**
Run: `python -m pytest -q`
Expected: `222 passed`, no warnings.

- [ ] **Step 7: Commit.**
```bash
git add person_hours.py tests/test_person_hours_call.py CLAUDE.md docs/superpowers/specs/2026-09-23-person-hours-view-design.md
git commit -m "Keep API-billing variables out of the judge's environment"
```

---

### Task 3: Claude Code logs in every running WSL distro

**Files:**
- Modify: `ingest.py:5-11` (imports), `ingest.py:28-58` (`_get_wsl_projects_dir`, `get_project_dirs`), `ingest.py:239-248` (`_get_wsl_home`)
- Modify: `tests/conftest.py` (a new autouse fixture), `CLAUDE.md`, `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md:71,164`
- Test: `tests/test_ingest_wsl.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `ingest.WSL_UNC_PREFIXES: tuple[str, str]`, which is `(r"\\wsl.localhost", r"\\wsl$")`.
  - `ingest.WSL_CHECK_SECONDS = 60`.
  - `ingest.parse_wsl_list(raw: bytes) -> list[str]`.
  - `ingest.running_wsl_distros() -> list[str]`, cached for `WSL_CHECK_SECONDS`.
  - `ingest.get_project_dirs() -> list[Path]`: same name and return type as today, now covering every `/home` user in every running distro.
  - Internal, patched by tests: `ingest._wsl_exe() -> str | None`, `ingest._clock` (defaults to `time.monotonic`), and the cache globals `ingest._wsl_checked_at` / `ingest._wsl_running`.

- [ ] **Step 1: Guard the tests against real WSL.** Add to `tests/conftest.py`, after the `no_real_cli` fixture:

```python
@pytest.fixture(autouse=True)
def no_wsl(monkeypatch):
    """Tests run as on a PC without WSL: none asks wsl.exe or reads a real distro, which would start it."""
    monkeypatch.setattr(ingest, "_wsl_exe", lambda: None, raising=False)
    monkeypatch.setattr(ingest, "_wsl_checked_at", None, raising=False)
    monkeypatch.setattr(ingest, "_wsl_running", [], raising=False)
```
`raising=False` lets the fixture load before Step 4 creates those names.

- [ ] **Step 2: Write the failing tests.** Create `tests/test_ingest_wsl.py`:

```python
"""Finding Claude Code transcripts inside WSL distros without starting any."""
import subprocess

import pytest

import ingest
from helpers import FakeRun

# Captured at import, before the autouse no_wsl fixture swaps in a stub that reports no wsl.exe.
_real_wsl_exe = ingest._wsl_exe
WSL_EXE = r"C:\Windows\System32\wsl.exe"


def utf16(text):
    return text.encode("utf-16-le")


@pytest.mark.parametrize("raw, names", [
    (utf16("Ubuntu\r\ndocker-desktop\r\n"), ["Ubuntu", "docker-desktop"]),
    (b"\xff\xfe" + utf16("Ubuntu-24.04\r\nOracleLinux_9_1\r\n"), ["Ubuntu-24.04", "OracleLinux_9_1"]),
    (utf16("Ubuntu\r\n\r\n\r\n"), ["Ubuntu"]),                      # --running pads with blank lines
    (b"Debian\nkali-linux\n", ["Debian", "kali-linux"]),            # WSL_UTF8=1 output
    (b"", []),
    (utf16("There are no running distributions.\r\n"), []),
    (utf16("Windows Subsystem for Linux has no installed distributions.\r\n"), []),
])
def test_parse_wsl_list(raw, names):
    assert ingest.parse_wsl_list(raw) == names


def with_wsl(monkeypatch, run):
    monkeypatch.setattr(ingest, "_wsl_exe", lambda: WSL_EXE)
    monkeypatch.setattr(ingest.subprocess, "run", run)


def test_running_distros_come_from_wsl_exe_without_a_console(monkeypatch):
    run = FakeRun(stdout=utf16("Ubuntu\r\n"))
    with_wsl(monkeypatch, run)
    assert ingest.running_wsl_distros() == ["Ubuntu"]
    cmd, kw = run.calls[0]
    assert cmd == [WSL_EXE, "--list", "--running", "--quiet"]
    assert kw["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert kw["timeout"] == 10


def test_running_distros_are_reused_for_a_minute(monkeypatch):
    run = FakeRun(stdout=utf16("Ubuntu\r\n"))
    with_wsl(monkeypatch, run)
    clock = [1000.0]
    monkeypatch.setattr(ingest, "_clock", lambda: clock[0])
    ingest.running_wsl_distros()
    clock[0] += ingest.WSL_CHECK_SECONDS - 1
    ingest.running_wsl_distros()
    assert len(run.calls) == 1
    clock[0] += 1
    ingest.running_wsl_distros()
    assert len(run.calls) == 2


@pytest.mark.parametrize("run", [
    FakeRun(stdout=utf16("Windows Subsystem for Linux is not installed.\r\n"), returncode=1),
    FakeRun(raises=subprocess.TimeoutExpired("wsl.exe", 10)),
    FakeRun(raises=OSError("blocked by policy")),
], ids=["not-installed", "hung", "blocked"])
def test_a_broken_wsl_means_no_distros(monkeypatch, run):
    with_wsl(monkeypatch, run)
    assert ingest.running_wsl_distros() == []


def test_without_wsl_exe_nothing_runs():
    # no_wsl reports no wsl.exe, and no_real_cli fails the test if anything tries to run.
    assert ingest.running_wsl_distros() == []


def test_wsl_exe_is_looked_up_on_path_then_in_system32(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None)
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    assert _real_wsl_exe() is None
    exe = tmp_path / "System32" / "wsl.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    assert _real_wsl_exe() == str(exe)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: r"C:\bin\wsl.exe")
    assert _real_wsl_exe() == r"C:\bin\wsl.exe"


def fake_shares(tmp_path, monkeypatch, running):
    """Stand-ins for \\\\wsl.localhost and \\\\wsl$, and the list of running distros."""
    new, old = tmp_path / "wsl.localhost", tmp_path / "wsl$"
    monkeypatch.setattr(ingest, "WSL_UNC_PREFIXES", (str(new), str(old)))
    monkeypatch.setattr(ingest, "running_wsl_distros", lambda: list(running))
    return new, old


def claude_projects(share, distro, user):
    path = share / distro / "home" / user / ".claude" / "projects"
    path.mkdir(parents=True)
    return path


def test_every_user_in_every_running_distro_is_scanned(tmp_path, monkeypatch):
    new, old = fake_shares(tmp_path, monkeypatch, running=["Ubuntu", "Debian", "Arch"])
    alice = claude_projects(new, "Ubuntu", "alice")
    bob = claude_projects(new, "Ubuntu", "bob")
    (new / "Ubuntu" / "home" / "carol").mkdir()   # a user without Claude Code
    dan = claude_projects(old, "Debian", "dan")   # reachable only under the older \\wsl$ name
    # Arch is running but has nothing under /home on either share.
    assert ingest.get_project_dirs() == [ingest.PROJECTS_DIR, alice, bob, dan]


def test_stopped_distros_are_never_touched(tmp_path, monkeypatch):
    new, _ = fake_shares(tmp_path, monkeypatch, running=[])
    claude_projects(new, "Ubuntu", "alice")
    assert ingest.get_project_dirs() == [ingest.PROJECTS_DIR]


def test_a_distro_under_both_names_is_scanned_once(tmp_path, monkeypatch):
    new, old = fake_shares(tmp_path, monkeypatch, running=["Ubuntu"])
    alice = claude_projects(new, "Ubuntu", "alice")
    claude_projects(old, "Ubuntu", "alice")
    assert ingest.get_project_dirs() == [ingest.PROJECTS_DIR, alice]


def test_wsl_project_names_resolve_in_the_distro_that_has_the_user(tmp_path, monkeypatch):
    new, _ = fake_shares(tmp_path, monkeypatch, running=["Ubuntu", "Debian"])
    (new / "Ubuntu" / "home" / "alice").mkdir(parents=True)
    (new / "Debian" / "home" / "bob" / "Projects" / "my-tool").mkdir(parents=True)
    assert ingest.extract_project_name("-home-bob-Projects-my-tool") == "Projects / my-tool"
```

- [ ] **Step 3: Run them and confirm they fail.**
Run: `python -m pytest tests/test_ingest_wsl.py -v`
Expected: collection error, `AttributeError: module 'ingest' has no attribute '_wsl_exe'` (from the module-level capture).

- [ ] **Step 4: Rewrite the WSL lookup in `ingest.py`.**
  1. Imports: add `import shutil`, `import subprocess`, `import threading` and `import time` to the stdlib block, keeping it alphabetical after `import sqlite3`. The block becomes:
     ```python
     import os
     import re
     import shutil
     import sqlite3
     import subprocess
     import threading
     import time
     from contextlib import contextmanager
     from datetime import datetime, timezone
     from itertools import chain
     from pathlib import Path
     ```
  2. Replace everything from `def _get_wsl_projects_dir():` through the end of `get_project_dirs()` (lines 28-58, ending with `return dirs`) with:
     ```python
     # Windows shows each running WSL distro's files under both names; \\wsl$ is the older one.
     WSL_UNC_PREFIXES = (r"\\wsl.localhost", r"\\wsl$")
     WSL_CHECK_SECONDS = 60  # reuse the running-distro list this long; one ingest pass asks several times
     _DISTRO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
     _clock = time.monotonic
     _wsl_lock = threading.Lock()
     _wsl_checked_at = None  # _clock() of the last wsl.exe query
     _wsl_running = []


     def _wsl_exe():
         """Path to wsl.exe, or None on a PC without it."""
         found = shutil.which("wsl.exe")
         if found:
             return found
         candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe"
         return str(candidate) if candidate.exists() else None


     def parse_wsl_list(raw: bytes) -> list:
         """Distro names from `wsl.exe --list --quiet` output.

         wsl.exe writes UTF-16LE, or UTF-8 when WSL_UTF8=1 is set. Lines that can't be a distro
         name, such as "There are no running distributions.", are dropped.
         """
         text = raw.decode("utf-16-le" if b"\x00" in raw else "utf-8", errors="ignore")
         names = []
         for line in text.replace("\ufeff", "").splitlines():
             name = line.strip()
             if _DISTRO_NAME.fullmatch(name) and name not in names:
                 names.append(name)
         return names


     def _query_running_distros() -> list:
         exe = _wsl_exe()
         if exe is None:
             return []
         try:
             proc = subprocess.run([exe, "--list", "--running", "--quiet"], capture_output=True,
                                   timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
         except (OSError, subprocess.SubprocessError):
             return []
         return parse_wsl_list(proc.stdout) if proc.returncode == 0 else []


     def running_wsl_distros() -> list:
         """Names of the WSL distros running now, asked of wsl.exe at most once a minute.

         Stopped distros are left alone on purpose: reading one through \\\\wsl.localhost starts
         it, and ingest runs every 90 s. A distro's transcripts only change while it runs, so
         the first pass after it starts picks up anything new.
         """
         global _wsl_checked_at, _wsl_running
         with _wsl_lock:
             now = _clock()
             if _wsl_checked_at is None or now - _wsl_checked_at >= WSL_CHECK_SECONDS:
                 _wsl_running = _query_running_distros()
                 _wsl_checked_at = now
             return list(_wsl_running)


     def _wsl_distro_root(distro: str):
         """The share a running distro's files are reachable under, or None."""
         for prefix in WSL_UNC_PREFIXES:
             root = Path(prefix, distro)
             try:
                 if (root / "home").is_dir():
                     return root
             except OSError:
                 pass
         return None


     def _get_wsl_projects_dirs() -> list:
         """~/.claude/projects of every user under /home in every running WSL distro."""
         found = []
         for distro in running_wsl_distros():
             root = _wsl_distro_root(distro)
             if root is None:
                 continue
             try:
                 users = sorted((root / "home").iterdir())
             except OSError:
                 continue
             for user_dir in users:
                 projects = user_dir / ".claude" / "projects"
                 try:
                     if projects.is_dir():
                         found.append(projects)
                 except OSError:
                     pass
         return found


     def get_project_dirs() -> list:
         """Every ~/.claude/projects root to ingest: Windows's, plus each user's in each running WSL distro."""
         return [PROJECTS_DIR, *_get_wsl_projects_dirs()]
     ```
  3. Replace `_get_wsl_home` (the whole function, lines 239-248 before these edits) with:
     ```python
     def _get_wsl_home(username: str):
         """username's home in the first running WSL distro that has one, else None."""
         for distro in running_wsl_distros():
             root = _wsl_distro_root(distro)
             if root is None:
                 continue
             home = root / "home" / username
             try:
                 if home.exists():
                     return home
             except OSError:
                 pass
         return None
     ```

- [ ] **Step 5: Run the WSL tests.**
Run: `python -m pytest tests/test_ingest_wsl.py -v`
Expected: all 18 pass (7 parse rows, 3 broken-WSL rows, 8 others).

- [ ] **Step 6: Check it against this PC without starting Ubuntu.** Ubuntu is installed here and is usually stopped. First see its state:
Run: `wsl.exe -l -v | tr -d '\000'`
Then run the scan:
Run: `python -c "import ingest; print(ingest.running_wsl_distros()); print(ingest.get_project_dirs())"`
Then run `wsl.exe -l -v | tr -d '\000'` again.
Expected:
- If Ubuntu was Stopped: `[]`, then only the Windows projects dir, and Ubuntu is still Stopped afterwards.
- If it was Running: `['Ubuntu']` and its `\\wsl.localhost\Ubuntu\home\weaverjc\.claude\projects` dir.

If the ingest pass on :8080 happens to start Ubuntu while you check, repeat.

- [ ] **Step 7: Run the full suite.**
Run: `python -m pytest -q`
Expected: `240 passed`, no warnings.

- [ ] **Step 8: Update the docs.**
  1. `CLAUDE.md`, the "Files ingested" line. Replace:
     ```
     **Files ingested** per `~/.claude/projects/<project>/` dir (Windows root plus the WSL root from `get_project_dirs()`):
     ```
     with:
     ```
     **Files ingested** per `~/.claude/projects/<project>/` dir (the Windows root plus, from `get_project_dirs()`, the root of every `/home` user in every running WSL distro):
     ```
  2. `CLAUDE.md`, the Gotchas list. Add after the "Project name resolution" bullet:
     ```
     - **WSL distros are scanned only while running.** `ingest.running_wsl_distros()` asks `wsl.exe --list --running --quiet`, at most once a minute. Reading a stopped distro through `\\wsl.localhost` starts it; until 2026-09-30 the scan did that to Ubuntu on every 90-second pass. A stopped distro's transcripts are picked up on the first pass after it starts. One cost: a WSL session-day the judge queued while its distro ran, but reaches after the distro stopped, is recorded as having no transcript and stays provisional.
     ```
  3. `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md:71`. Replace:
     ```
     - **`ingest.py`**: WSL distros are found once per process with `wsl.exe -l -q` (the output is UTF-16), cached, and skipped when `wsl.exe` is missing. The `/home/*/.claude/projects` scan runs for every distro, not just `Ubuntu`.
     ```
     with:
     ```
     - **`ingest.py`**: the `/home/*/.claude/projects` scan runs for every *running* WSL distro, not just `Ubuntu`. The list comes from `wsl.exe --list --running --quiet` (the output is UTF-16), asked at most once a minute and skipped when `wsl.exe` is missing. Stopped distros are left alone: reading one through `\\wsl.localhost` starts it, and on 2026-09-30 the old scan was found starting Ubuntu on every 90-second ingest pass. (This replaces the first design, which listed all installed distros once per process.)
     ```
  4. Same file, line 164. Replace `- Parsing \`wsl -l -q\` UTF-16 output.` with `- Parsing \`wsl.exe --list --quiet\` UTF-16 output.`

- [ ] **Step 9: Commit.**
```bash
git add ingest.py tests/conftest.py tests/test_ingest_wsl.py CLAUDE.md docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md
git commit -m "Scan every running WSL distro for Claude Code logs, and never start a stopped one"
```

---

### Task 4: A UTF-8 log that rotates at 5 MB

**Files:**
- Create: `applog.py`
- Modify: `app.py:192-193` (a comment), `app.py:520-521` (a docstring), `app.py:839-855` (`__main__`)
- Modify: `scripts/launcher.py`, `CLAUDE.md`, `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md:134`
- Test: `tests/test_applog.py` (new)

**Interfaces:**
- Consumes: `paths.LOG_DIR`.
- Produces (Phase 3's `desktop.py` will use `open_log`):
  - `applog.LOG_FILE: Path`, which is `paths.LOG_DIR / "dashboard.log"`.
  - `applog.MAX_BYTES = 5 * 1024 * 1024`.
  - `applog.rotate(path: Path = LOG_FILE, max_bytes: int | None = None) -> None`. `None` means `MAX_BYTES`, read at call time.
  - `applog.open_log(path: Path = LOG_FILE) -> TextIO`: rotates, then opens for appending as line-buffered UTF-8 with `errors="backslashreplace"`.
  - `applog.utf8_stdio() -> None`: reconfigures `sys.stdout` and `sys.stderr` to UTF-8 with `errors="backslashreplace"`, skipping streams that are `None` or can't be reconfigured.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_applog.py`:

```python
"""The dashboard's log file: UTF-8, rotated to .1 at startup once it passes 5 MB."""
import io
import sys

import applog


def test_a_small_log_stays_put(tmp_path):
    log = tmp_path / "dashboard.log"
    log.write_bytes(b"x" * 100)
    applog.rotate(log, max_bytes=100)
    assert log.read_bytes() == b"x" * 100
    assert not (tmp_path / "dashboard.log.1").exists()


def test_a_big_log_moves_to_dot_one_replacing_the_old_one(tmp_path):
    log = tmp_path / "dashboard.log"
    older = tmp_path / "dashboard.log.1"
    older.write_text("the run before last")
    log.write_bytes(b"x" * 101)
    applog.rotate(log, max_bytes=100)
    assert not log.exists()
    assert older.read_bytes() == b"x" * 101


def test_a_missing_log_is_fine(tmp_path):
    applog.rotate(tmp_path / "dashboard.log", max_bytes=1)


def test_rotation_failure_is_not_fatal(tmp_path, monkeypatch):
    log = tmp_path / "dashboard.log"
    log.write_bytes(b"x" * 101)

    def held_open(src, dst):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(applog.os, "replace", held_open)
    applog.rotate(log, max_bytes=100)
    assert log.read_bytes() == b"x" * 101


def test_open_log_rotates_first_and_writes_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(applog, "MAX_BYTES", 10)
    log = tmp_path / "logs" / "dashboard.log"
    log.parent.mkdir()
    log.write_bytes(b"x" * 11)
    f = applog.open_log(log)
    try:
        print("résumé ✓ ☃ 🚀", file=f)
    finally:
        f.close()
    assert (tmp_path / "logs" / "dashboard.log.1").read_bytes() == b"x" * 11
    assert log.read_text(encoding="utf-8").strip() == "résumé ✓ ☃ 🚀"


def test_open_log_creates_the_folder(tmp_path):
    f = applog.open_log(tmp_path / "new" / "dashboard.log")
    f.close()
    assert (tmp_path / "new" / "dashboard.log").exists()


def test_utf8_stdio_makes_print_safe_on_a_cp1252_stream(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", None)  # pythonw.exe has none
    applog.utf8_stdio()
    print("☃ 🚀")
    stream.flush()
    assert raw.getvalue().decode("utf-8").strip() == "☃ 🚀"
```

- [ ] **Step 2: Run them and confirm they fail.**
Run: `python -m pytest tests/test_applog.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'applog'`.

- [ ] **Step 3: Create `applog.py`.**

```python
"""
The dashboard's log file, dashboard.log in paths.LOG_DIR.

It's written as UTF-8. The Task Scheduler launcher used to hand the server a cp1252 file,
where printing any character outside cp1252 raised UnicodeEncodeError in whichever thread
was logging. At startup the log moves to dashboard.log.1 once it passes 5 MB, replacing the
previous .1, so it can't grow without bound.
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
    """Make print() write UTF-8 wherever stdout points, and never raise on a character."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")
```

- [ ] **Step 4: Run the applog tests.**
Run: `python -m pytest tests/test_applog.py -v`
Expected: 7 passed.

- [ ] **Step 5: Use it in `app.py`.**
  1. Replace the whole `if __name__ == "__main__":` block at the end of the file with:
     ```python
     if __name__ == "__main__":
         import argparse
         import sys

         import uvicorn

         import applog

         if sys.stdout is None or sys.stderr is None:
             # pythonw.exe starts with no stdout/stderr: log to the file instead
             sys.stdout = sys.stderr = applog.open_log()
         else:
             # A console is fine as it is, but a file handed over by the launcher opens as cp1252
             applog.utf8_stdio()

         parser = argparse.ArgumentParser()
         parser.add_argument("--port", type=int, default=8080)
         args = parser.parse_args()
         uvicorn.run("app:app", host="127.0.0.1", port=args.port, reload=False)
     ```
  2. In `_periodic_ingest`, replace the two comment lines
     ```python
             # Reschedule no matter what, even if logging itself fails (under the launcher,
             # stdout is a strict cp1252 file), or periodic ingest stops until a restart.
     ```
     with:
     ```python
             # Reschedule no matter what, even if logging itself fails, or periodic ingest
             # stops until a restart.
     ```
  3. In `_hours_tick`'s docstring, replace
     ```
         Like _periodic_ingest, it reschedules in an outer finally: under the launcher, stdout is
         a strict cp1252 file, so even logging a failure can raise.
     ```
     with:
     ```
         Like _periodic_ingest, it reschedules in an outer finally, so not even a failure while
         logging a failure can stop the worker.
     ```

- [ ] **Step 6: Use it in `scripts/launcher.py`.**
  1. In the module docstring, change `Checks if port 8080 is already in use before starting, logs to logs/dashboard.log.` to:
     ```
     Checks if port 8080 is already in use before starting, logs to logs/dashboard.log
     (UTF-8; moved to dashboard.log.1 at startup once it passes 5 MB).
     ```
  2. Replace
     ```python
     REPO_DIR = Path(__file__).parent.parent
     LOG_FILE = REPO_DIR / "logs" / "dashboard.log"
     PORT = 8080
     ```
     with:
     ```python
     REPO_DIR = Path(__file__).parent.parent
     sys.path.insert(0, str(REPO_DIR))  # Task Scheduler runs this file directly; the repo isn't on sys.path
     import applog  # noqa: E402

     LOG_FILE = applog.LOG_FILE
     PORT = 8080
     ```
  3. In `log()`, change `with open(LOG_FILE, "a") as f:` to:
     ```python
         with open(LOG_FILE, "a", encoding="utf-8", errors="backslashreplace") as f:
     ```
  4. Directly above `if port_in_use(PORT):`, add:
     ```python
     applog.rotate()  # before this run's first line, so the whole run lands in one file
     ```
  5. In the `subprocess.Popen(...)` call, change `stdout=open(LOG_FILE, "a"),` to `stdout=applog.open_log(),`.

- [ ] **Step 7: Check the launcher by hand.** :8080 is in use (the scheduled server), so the launcher logs one line and exits without starting anything. It logs to this worktree's `logs/`, which git ignores.
Run: `python scripts/launcher.py; tail -c 120 logs/dashboard.log | od -c | tail -5`
Expected: the last line is `[<timestamp>] Port 8080 already in use — skipping startup.` The em dash must show as the UTF-8 bytes `342 200 224` in `od -c`'s octal, not the single cp1252 byte `227`.

- [ ] **Step 8: Run the full suite.**
Run: `python -m pytest -q`
Expected: `247 passed`, no warnings.

- [ ] **Step 9: Update the docs.**
  1. `CLAUDE.md`, the Architecture block.
     - Add a line after the `settings.py` line:
       ```
       applog.py    The log file: opens logs/dashboard.log as UTF-8, rotates it at 5 MB, makes print() safe on any stdout
       ```
     - Replace the `logs/` line:
       ```
       logs/        dashboard.log — server output when run via Task Scheduler; not committed
       ```
       with:
       ```
       logs/        dashboard.log — server output under Task Scheduler or pythonw, UTF-8; moved to dashboard.log.1 at startup once past 5 MB; not committed
       ```
  2. `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md:134`. Replace:
     ```
     - **Logging when frozen**: UTF-8 `LOG_DIR/dashboard.log`, rotated to `.1` at startup once it passes 5 MB. Opening the log as UTF-8 avoids the cp1252 encoding failures the launcher has today.
     ```
     with:
     ```
     - **Logging**: UTF-8 `LOG_DIR/dashboard.log`, rotated to `.1` at startup once it passes 5 MB (`applog.py`). This applies to the frozen app and to source runs under the launcher or `pythonw`. Opening the log as UTF-8 avoids the cp1252 encoding failures the launcher used to have.
     ```

- [ ] **Step 10: Commit.**
```bash
git add applog.py app.py scripts/launcher.py tests/test_applog.py CLAUDE.md docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md
git commit -m "Write the log as UTF-8 and rotate it at 5 MB"
```

---

### Task 5: Version number and pinned requirements

**Files:**
- Create: `version.py`, `requirements.txt`
- Modify: `environment.yml`, `auth.py` (imports and `diagnostics`), `app.py` (the `FastAPI(...)` call and the `index` route)
- Modify: `templates/index.html` (the settings panel, after the Connection section's `</section>`), `CLAUDE.md`
- Test: `tests/test_release_files.py` (new), `tests/test_auth.py`

**Interfaces:**
- Consumes: `app._lifespan` (Task 1).
- Produces:
  - `version.__version__: str`, which is `"2.0.0"`.
  - The diagnostics text's second line reads `App version: <version>`.
  - The index template receives `version`.
  - `app.app.version == version.__version__`.

- [ ] **Step 1: Write the failing tests.**
  1. Create `tests/test_release_files.py`:
     ```python
     """Files a release build depends on, and where the version shows."""
     import re
     from pathlib import Path

     from fastapi.testclient import TestClient

     import app
     import version

     REPO = Path(__file__).parent.parent


     def test_version_is_a_bare_release_number():
         assert re.fullmatch(r"\d+\.\d+\.\d+", version.__version__)


     def test_runtime_requirements_are_pinned():
         lines = (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
         reqs = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
         assert {r.split("==")[0].lower() for r in reqs} == {"fastapi", "uvicorn", "jinja2", "orjson"}
         for req in reqs:
             assert re.fullmatch(r"[A-Za-z0-9_.\-]+==\d[\w.]*", req), req


     def test_the_conda_env_installs_the_runtime_requirements():
         assert "- -r requirements.txt" in (REPO / "environment.yml").read_text(encoding="utf-8")


     def test_the_settings_panel_and_the_api_show_the_version():
         client = TestClient(app.app, base_url="http://127.0.0.1:8080")
         assert f"Claude Usage Dashboard {version.__version__}" in client.get("/").text
         assert app.app.version == version.__version__
     ```
  2. Append to `tests/test_auth.py`, and add `import version` to its imports:
     ```python
     def test_diagnostics_name_the_app_version():
         status = auth.connection_status(**BASE)
         text = auth.diagnostics(status, cli_path=None, auto_refresh=False, login_running=False)
         assert text.splitlines()[:2] == ["Claude Usage Dashboard diagnostics",
                                          f"App version: {version.__version__}"]
     ```

- [ ] **Step 2: Run them and confirm they fail.**
Run: `python -m pytest tests/test_release_files.py tests/test_auth.py -v`
Expected: collection errors, `ModuleNotFoundError: No module named 'version'`.

- [ ] **Step 3: Create `version.py`.**

```python
"""The app's version. Release tags are the same bare number (2.0.0, never v2.0.0)."""
__version__ = "2.0.0"
```

- [ ] **Step 4: Create `requirements.txt`.**

```
# Runtime packages, pinned for the release build. environment.yml installs this file too,
# so a source run and the build use the same versions.
fastapi==0.142.2
uvicorn==0.54.0
jinja2==3.1.6
orjson==3.12.0
```

- [ ] **Step 5: Point `environment.yml` at it.** Replace the file with:

```yaml
name: claude-usage-dashboard
channels:
  - conda-forge
  - defaults
dependencies:
  - python=3.12
  - pip
  - pytest
  - pip:
    - -r requirements.txt
    - httpx2  # tests only: starlette's TestClient
```
This drops `uvicorn[standard]` for plain `uvicorn`, as the spec asks: the extras (httptools, websockets, watchfiles and others) aren't used. Existing envs keep them installed, which is harmless.
Run: `pip install --dry-run -r requirements.txt`
Expected: every line reads `Requirement already satisfied`, and there's no `Would install` line.

- [ ] **Step 6: Show the version.**
  1. `auth.py`: add `import version` after `from pathlib import Path`, separated by a blank line. In `diagnostics`, insert this line right after `"Claude Usage Dashboard diagnostics",`:
     ```python
             f"App version: {version.__version__}",
     ```
  2. `app.py`: add `import version` to the local imports, after `import settings`. Change the app line from Task 1 to:
     ```python
     app = FastAPI(title="Claude Usage Dashboard", version=version.__version__, lifespan=_lifespan)
     ```
     In `index`, pass the version to the template:
     ```python
         return templates.TemplateResponse(request, "index.html",
                                           {"asset_url": _asset_url, "version": version.__version__})
     ```
  3. `templates/index.html`: after the Connection section's closing `</section>` (the one right after `<p class="setting-help" id="copyResult" aria-hidden="true"></p>`), add:
     ```html
             <p class="setting-help">Claude Usage Dashboard {{ version }}</p>
     ```

- [ ] **Step 7: Run the tests.**
Run: `python -m pytest -q`
Expected: `252 passed`, no warnings.

- [ ] **Step 8: Update `CLAUDE.md`.**
  1. Architecture block: add these lines after the `applog.py` line (Task 4):
     ```
     version.py   __version__, a bare release number (2.0.0) shown in Settings and the diagnostics
     requirements.txt  Pinned runtime packages for the release build; environment.yml installs it too
     ```
  2. "Setup & Running": after the `conda env create -f environment.yml` code block's closing fence, add:
     ```
     `environment.yml` installs `requirements.txt` (the pinned runtime packages) plus the test tools. When you upgrade a runtime package, change its pin in `requirements.txt`.
     ```

- [ ] **Step 9: Commit.**
```bash
git add version.py requirements.txt environment.yml auth.py app.py templates/index.html tests/test_release_files.py tests/test_auth.py CLAUDE.md
git commit -m "Add a version number and pinned runtime requirements"
```

---

### Task 6: Chart.js and the fonts served from `static/`

**Files:**
- Create: `static/vendor/chart.umd.js`, `static/vendor/LICENSE-chart.js.md`
- Create: 8 font files in `static/fonts/` (listed in Step 1), `static/fonts/LICENSE-Sora.txt`, `static/fonts/LICENSE-DM-Sans.txt`, `static/fonts/LICENSE-DM-Mono.txt`, `static/fonts/fonts.css`
- Modify: `templates/index.html:7-9` (Google Fonts links), `templates/index.html:369` (Chart.js script tag), `.gitattributes`, `CLAUDE.md`
- Test: `tests/test_static_assets.py` (new)

**Interfaces:**
- Consumes: `app._asset_url(rel_path)`, which already handles subpaths (`fonts/fonts.css` → `/static/fonts/fonts.css?v=<mtime>-<size>`).
- Produces: the families `'Sora'`, `'DM Sans'` and `'DM Mono'`, with the same names the CSS variables and `Chart.defaults.font.family` already use. No CSS or JS change is needed.

- [ ] **Step 1: Download the files and check their hashes.** These are the only downloads in the plan. They come from jsDelivr's npm mirror, 13 files and about 374 KB in total. Versions:
  - Chart.js 4.4.4, the version the page loads from the CDN today. The npm package ships `dist/chart.umd.js`, already minified. The `.min.js` name the page uses today is minified on the fly by jsDelivr, so vendor the real file.
  - Fontsource 5.3.0: `@fontsource-variable/sora`, `@fontsource-variable/dm-sans` and `@fontsource/dm-mono`.

  The files are the Latin and Latin Extended subsets, the same two Google Fonts serves for this page. Sora and DM Sans are variable fonts, one file per subset covering every weight. DM Mono has one file per weight; the page uses 400 and 500, as the Google Fonts link does today.
```bash
mkdir -p static/vendor static/fonts
J=https://cdn.jsdelivr.net/npm
C=$J/chart.js@4.4.4
S=$J/@fontsource-variable/sora@5.3.0
D=$J/@fontsource-variable/dm-sans@5.3.0
M=$J/@fontsource/dm-mono@5.3.0
curl -fsSL -o static/vendor/chart.umd.js                     "$C/dist/chart.umd.js"
curl -fsSL -o static/vendor/LICENSE-chart.js.md              "$C/LICENSE.md"
curl -fsSL -o static/fonts/sora-latin-wght-normal.woff2      "$S/files/sora-latin-wght-normal.woff2"
curl -fsSL -o static/fonts/sora-latin-ext-wght-normal.woff2  "$S/files/sora-latin-ext-wght-normal.woff2"
curl -fsSL -o static/fonts/LICENSE-Sora.txt                  "$S/LICENSE"
curl -fsSL -o static/fonts/dm-sans-latin-wght-normal.woff2     "$D/files/dm-sans-latin-wght-normal.woff2"
curl -fsSL -o static/fonts/dm-sans-latin-ext-wght-normal.woff2 "$D/files/dm-sans-latin-ext-wght-normal.woff2"
curl -fsSL -o static/fonts/LICENSE-DM-Sans.txt                 "$D/LICENSE"
curl -fsSL -o static/fonts/dm-mono-latin-400-normal.woff2      "$M/files/dm-mono-latin-400-normal.woff2"
curl -fsSL -o static/fonts/dm-mono-latin-500-normal.woff2      "$M/files/dm-mono-latin-500-normal.woff2"
curl -fsSL -o static/fonts/dm-mono-latin-ext-400-normal.woff2  "$M/files/dm-mono-latin-ext-400-normal.woff2"
curl -fsSL -o static/fonts/dm-mono-latin-ext-500-normal.woff2  "$M/files/dm-mono-latin-ext-500-normal.woff2"
curl -fsSL -o static/fonts/LICENSE-DM-Mono.txt                 "$M/LICENSE"
```
Then use the Write tool to save this checklist as `vendored.sha256` in the scratchpad directory (`$SP` in "Test instance"). The hashes are the SHA-256s jsDelivr publishes for these package versions.
```
fed6a739f8d0f0687174de6cd14745fc0fc7809144ab113d22908a26bf0d7fea  static/vendor/chart.umd.js
41a84aa2caba645f966a18d9c2056b73e6d3a81d80bc0046bc0011a2634d4cce  static/vendor/LICENSE-chart.js.md
fa26406eeda9a3c6ec3d9ea8813c3045d6dc755e30c716d5c094e8ef43be5a7f  static/fonts/sora-latin-wght-normal.woff2
c163c536f68befd99a83d4f17e8b88030e7f54229688a45c44f70c7149db7385  static/fonts/sora-latin-ext-wght-normal.woff2
1ec9623d38c445eb4dfe5fcc783e0f0ee728cc97ca157ce1f8cb2b3bf9d9b0d9  static/fonts/LICENSE-Sora.txt
9fea608a947e67020c33cad9a6fe3d60c54119dfb8cff87768a8117a15ed7543  static/fonts/dm-sans-latin-wght-normal.woff2
a5d38fe99f930275684999b462c7123faa063d9e44e73b4b241723d884aa0f49  static/fonts/dm-sans-latin-ext-wght-normal.woff2
6fbd040a29c2037a765dfb9f2561e9965b5c95c6dcb5dce516089d63d5f17af7  static/fonts/LICENSE-DM-Sans.txt
e1896b13b2b1bb112fac2f9571bd6c40e118746e77a4511edbf43fbb41bf3e1e  static/fonts/dm-mono-latin-400-normal.woff2
9964608a849396bd00c4bfd7034afe03486469dcf20b4f6b8cbdfdd310369951  static/fonts/dm-mono-latin-500-normal.woff2
a52e19ebe0398c9c0f8fa28a0c5e9a6bc324f35d9f0881c9aa407d10af447175  static/fonts/dm-mono-latin-ext-400-normal.woff2
8711f938c3f04f91e35ae64ebc7f2eecd800d2ae1c3542f6c837c758a50422f5  static/fonts/dm-mono-latin-ext-500-normal.woff2
d2766c396a8534cba53340f8af2037749b6a0a1dd3587348c917a70e779d2e32  static/fonts/LICENSE-DM-Mono.txt
```
Run from the worktree root, with the `SP=...` line from "Test instance" first: `sha256sum -c "$SP/vendored.sha256"`
Expected: 13 lines ending `OK`. If any line fails, stop and report it; don't commit a file whose hash doesn't match.

- [ ] **Step 2: Keep git from rewriting them.** `.gitattributes` sets `* text=auto eol=lf`, and git's binary detection could someday misjudge a font. Add at the end of the "Binary files" group in `.gitattributes`:
```
*.woff2 binary
```

- [ ] **Step 3: Write the failing tests.** Create `tests/test_static_assets.py`:

```python
"""The page loads nothing from the internet: Chart.js and the fonts are served from static/."""
import re

from fastapi.testclient import TestClient

import app

STATIC = app.BASE_DIR / "static"


def client():
    return TestClient(app.app, base_url="http://127.0.0.1:8080")


def test_page_loads_nothing_from_the_internet():
    html = client().get("/").text
    refs = re.findall(r'<(?:script|link)\b[^>]*?\b(?:src|href)="([^"]+)"', html)
    assert any("chart.umd.js" in r for r in refs) and any("fonts.css" in r for r in refs)
    assert [r for r in refs if r.startswith(("http:", "https:", "//"))] == []


def test_stylesheets_load_nothing_from_the_internet():
    for css in STATIC.rglob("*.css"):
        text = css.read_text(encoding="utf-8")
        assert "@import" not in text, css
        assert not re.search(r"url\(\s*['\"]?(https?:)?//", text), css


def test_every_font_the_stylesheet_names_is_served():
    css = (STATIC / "fonts" / "fonts.css").read_text(encoding="utf-8")
    assert set(re.findall(r"font-family:\s*'([^']+)'", css)) == {"Sora", "DM Sans", "DM Mono"}
    files = re.findall(r"url\('([^']+)'\)", css)
    assert len(files) == 8
    c = client()
    for name in files:
        r = c.get(f"/static/fonts/{name}")
        assert r.status_code == 200, name
        assert r.content[:4] == b"wOF2", name


def test_chart_js_is_served_locally():
    r = client().get("/static/vendor/chart.umd.js")
    assert r.status_code == 200
    assert b"Chart.js v4.4.4" in r.content[:300]


def test_vendored_files_carry_their_licenses():
    for name in ("vendor/LICENSE-chart.js.md", "fonts/LICENSE-Sora.txt",
                 "fonts/LICENSE-DM-Sans.txt", "fonts/LICENSE-DM-Mono.txt"):
        assert (STATIC / name).stat().st_size > 500, name
```

- [ ] **Step 4: Run them and confirm the right ones fail.**
Run: `python -m pytest tests/test_static_assets.py -v`
Expected:
- `test_page_loads_nothing_from_the_internet` fails: the CDN script and the Google Fonts links are still in the page.
- `test_every_font_the_stylesheet_names_is_served` fails: `fonts.css` doesn't exist yet.
- The other three pass (the files are already downloaded).

- [ ] **Step 5: Create `static/fonts/fonts.css`.**

```css
/* Fonts served with the app, so the dashboard looks the same with no internet connection.
   Sora and DM Sans are variable fonts, one file per subset covering every weight. DM Mono has
   one file per weight. From Fontsource 5.3.0 (@fontsource-variable/sora,
   @fontsource-variable/dm-sans, @fontsource/dm-mono), Latin and Latin Extended subsets.
   SIL Open Font License 1.1: see the LICENSE-*.txt files beside this one. */

@font-face {
  font-family: 'Sora';
  font-style: normal;
  font-display: swap;
  font-weight: 100 800;
  src: url('sora-latin-ext-wght-normal.woff2') format('woff2');
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}

@font-face {
  font-family: 'Sora';
  font-style: normal;
  font-display: swap;
  font-weight: 100 800;
  src: url('sora-latin-wght-normal.woff2') format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}

@font-face {
  font-family: 'DM Sans';
  font-style: normal;
  font-display: swap;
  font-weight: 100 1000;
  src: url('dm-sans-latin-ext-wght-normal.woff2') format('woff2');
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}

@font-face {
  font-family: 'DM Sans';
  font-style: normal;
  font-display: swap;
  font-weight: 100 1000;
  src: url('dm-sans-latin-wght-normal.woff2') format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}

@font-face {
  font-family: 'DM Mono';
  font-style: normal;
  font-display: swap;
  font-weight: 400;
  src: url('dm-mono-latin-ext-400-normal.woff2') format('woff2');
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}

@font-face {
  font-family: 'DM Mono';
  font-style: normal;
  font-display: swap;
  font-weight: 400;
  src: url('dm-mono-latin-400-normal.woff2') format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}

@font-face {
  font-family: 'DM Mono';
  font-style: normal;
  font-display: swap;
  font-weight: 500;
  src: url('dm-mono-latin-ext-500-normal.woff2') format('woff2');
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}

@font-face {
  font-family: 'DM Mono';
  font-style: normal;
  font-display: swap;
  font-weight: 500;
  src: url('dm-mono-latin-500-normal.woff2') format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}
```

- [ ] **Step 6: Point the page at them.** In `templates/index.html`:
  1. Replace the three lines 7-9 (two `preconnect` links and the `fonts.googleapis.com` stylesheet) with:
     ```html
       <link rel="stylesheet" href="{{ asset_url('fonts/fonts.css') }}">
     ```
  2. Replace `<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>` with:
     ```html
       <script src="{{ asset_url('vendor/chart.umd.js') }}"></script>
     ```

- [ ] **Step 7: Run the tests.**
Run: `python -m pytest -q`
Expected: `257 passed`, no warnings.

- [ ] **Step 8: Check it in the browser.** Start the test instance (see "Test instance") and open `http://127.0.0.1:8888/` in the built-in browser.
  1. Use `read_network_requests` to list every request. All of them must go to `127.0.0.1:8888`, and among them must be `/static/vendor/chart.umd.js`, `/static/fonts/fonts.css` and at least one `.woff2`.
  2. Run with `javascript_tool`:
     `await document.fonts.ready; [typeof Chart, document.fonts.check("600 16px Sora"), document.fonts.check("16px 'DM Sans'"), document.fonts.check("500 16px 'DM Mono'")]`
     Expected: `["function", true, true, true]`.
  3. Take a screenshot. The headings should be in Sora, the numbers in DM Mono, and the window charts should draw. Compare with `http://127.0.0.1:8080/`, which still loads from the CDN: the type should look the same.
  4. `read_console_messages` with `onlyErrors: true` should list nothing new. A DevTools notice about the missing `chart.umd.js.map` is fine: the file ends with a `sourceMappingURL` comment and the map isn't vendored. The `signed-out` banner is expected (scratch home); don't press Sign in.
  Stop the test instance.

- [ ] **Step 9: Update `CLAUDE.md`.** In the Architecture block, replace the `static/` line:
```
static/      dashboard.js (Chart.js, quota polling), style.css (glassmorphism dark theme)
```
with:
```
static/      dashboard.js (Chart.js, quota polling), style.css (glassmorphism dark theme)
             vendor/chart.umd.js (Chart.js 4.4.4) and fonts/ (Sora, DM Sans, DM Mono, fonts.css): nothing loads from a CDN, so the page works offline
```

- [ ] **Step 10: Commit.**
```bash
git add static/vendor static/fonts templates/index.html .gitattributes tests/test_static_assets.py CLAUDE.md
git commit -m "Serve Chart.js and the fonts from static/ instead of CDNs"
```

---

### Task 7: The settings dialog's keyboard and screen-reader fixes

**Files:**
- Modify: `static/dashboard.js:53-56` (the global `keydown` handler)
- Modify: `templates/index.html:312-325` (the two switches)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - Element ids `setJudgeName`, `setJudgeHelp`, `setRenewName`, `setRenewHelp`.
  - The switches `#setJudge` and `#setRenew` keep their ids and their `onchange` handlers.

There's no JS test harness in this repo. This task is checked in the browser (Step 4).

- [ ] **Step 1: Invoke the `wcag-a11y` skill** and read its guidance on switch labelling and descriptions before editing the markup.

- [ ] **Step 2: Stop "r" from firing inside dialogs and with modifier keys.** In `static/dashboard.js`, replace:
```js
  document.addEventListener('keydown', e => {
    if (e.key === 'r' || e.key === 'R') { if (!e.target.matches('input,textarea')) triggerRefresh(); }
    if (e.key === 'Escape') closePanel();
  });
```
with:
```js
  document.addEventListener('keydown', e => {
    // "r" re-parses the logs, but not while typing, inside a dialog (Settings), or as part
    // of a shortcut such as Ctrl+R, which already reloads the page.
    if ((e.key === 'r' || e.key === 'R') && !e.ctrlKey && !e.metaKey && !e.altKey
        && !e.target.closest('input, textarea, select, [role="dialog"]')) triggerRefresh();
    if (e.key === 'Escape') closePanel();
  });
```

- [ ] **Step 3: Give each switch a short name and a separate description.** Clicking anywhere in the row should still toggle the switch, so keep the wrapping `<label>`. Name the input with `aria-labelledby` (which overrides the label's full text) and point `aria-describedby` at the help text. In `templates/index.html`, replace the two `<label class="setting-row">` blocks with:
```html
          <label class="setting-row">
            <input type="checkbox" role="switch" id="setJudge" aria-labelledby="setJudgeName"
                   aria-describedby="setJudgeHelp" onchange="saveSetting('judge_enabled', this)">
            <span>
              <span class="setting-name" id="setJudgeName">Estimate person-hours</span>
              <span class="setting-help" id="setJudgeHelp">Claude reads a summary of each day's sessions and estimates how long the work would take someone without AI. It runs through your Claude Code sign-in, so it uses your plan's quota: about 1–2% of a week's quota to catch up on the last 90 days, then about 0.1–0.2% a week.</span>
            </span>
          </label>
          <label class="setting-row">
            <input type="checkbox" role="switch" id="setRenew" aria-labelledby="setRenewName"
                   aria-describedby="setRenewHelp" onchange="saveSetting('auto_refresh_token', this)">
            <span>
              <span class="setting-name" id="setRenewName">Renew my sign-in token automatically</span>
              <span class="setting-help" id="setRenewHelp">Keeps the quota gauges live when you haven't used Claude Code for a while. The dashboard writes the new token into Claude Code's credentials file. If Claude Code renews at the same moment, one of them can lose, and you'd have to sign in again.</span>
            </span>
          </label>
```
The visible text is unchanged, character for character.

- [ ] **Step 4: Check it in the browser.** Start the test instance and open `http://127.0.0.1:8888/`. Don't press Sign in.
  1. **Switch names:** open Settings with the gear button, then `read_page`. The switches must read as `switch "Estimate person-hours"` and `switch "Renew my sign-in token automatically"`, and nothing longer. Then run with `javascript_tool`:
     `[...document.querySelectorAll('[role=switch]')].map(s => [s.getAttribute('aria-labelledby'), s.getAttribute('aria-describedby')])`
     Expected: `[["setJudgeName","setJudgeHelp"],["setRenewName","setRenewHelp"]]`.
  2. **Clicking the row still toggles:** click the help text of "Estimate person-hours". The switch turns on, and `GET /api/settings` shows `judge_enabled: true`. Click again to turn it off.
  3. **"r" inside the dialog does nothing:** with Settings open and focus on a switch, press `r`. `read_network_requests` with `urlPattern: "/api/refresh"` shows no request.
  4. **"r" outside the dialog still works:** close Settings with Escape, click an empty part of the page, and press `r`. Exactly one `POST /api/refresh` appears. It re-parses the scratch home's empty logs, which is harmless.
  5. **Ctrl+R doesn't re-parse:** a real Ctrl+R reloads the page and clears the request log, so send the key event from script instead. Run with `javascript_tool`:
     `document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'r', ctrlKey: true, bubbles: true}))`
     Then `read_network_requests` with `urlPattern: "/api/refresh"` still shows only the one request from item 4.
  Stop the test instance.

- [ ] **Step 5: Run the full suite** (nothing in Python changed, but check anyway).
Run: `python -m pytest -q`
Expected: `257 passed`, no warnings.

- [ ] **Step 6: Commit.**
```bash
git add static/dashboard.js templates/index.html
git commit -m "Keep the r shortcut out of the settings dialog and give its switches short names"
```

---

### Task 8: License, README and the remaining docs

**Files:**
- Create: `LICENSE`, `README.md`
- Modify: `CLAUDE.md` (Architecture block, "Server Restart", the settings-file gotcha)
- Test: `tests/test_release_files.py`

**Interfaces:**
- Consumes: `version.__version__` (Task 5), `applog.py` (Task 4), `static/vendor` and `static/fonts` (Task 6).
- Produces: nothing code depends on.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_release_files.py`:

```python
def test_the_repo_carries_an_mit_license_and_a_readme():
    license_text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License")
    assert "Copyright (c) 2026 Jonathan Weaver" in license_text
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Claude Usage Dashboard")
```

- [ ] **Step 2: Run it and confirm it fails.**
Run: `python -m pytest tests/test_release_files.py -v`
Expected: `test_the_repo_carries_an_mit_license_and_a_readme` fails with `FileNotFoundError` for `LICENSE`.

- [ ] **Step 3: Create `LICENSE`** with exactly this text:

```
MIT License

Copyright (c) 2026 Jonathan Weaver

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 4: Create `README.md`** with exactly this text. It has been through the humanize pass; don't reword it.

````markdown
# Claude Usage Dashboard

A dashboard for your Claude Code usage on Windows. It shows how much of your plan's 5-hour and weekly limits you've used, where your tokens went by day, project and model, what the same usage would cost at API prices, and which sessions were the heavy ones.

It runs on your own PC and serves the page from 127.0.0.1.

## What it reads, and what leaves your PC

It reads Claude Code's session logs in `%USERPROFILE%\.claude\projects`, the same folder inside any WSL distro that's running, and Claude Desktop's agent-mode logs in `%APPDATA%\Claude\local-agent-mode-sessions`. From those it copies token counts, model names, timestamps, project names and git branches into a local database. It doesn't keep the text of your conversations.

It also reads your Claude Code sign-in token from `%USERPROFILE%\.claude\.credentials.json`. The token only goes to Anthropic: to `api.anthropic.com` to read your quota percentages, and to `claude.ai` when you ask for it to be renewed. Unless you ask for a renewal, the dashboard never changes that file.

Nothing else leaves your PC, and there's no analytics or telemetry. The optional person-hours estimates described below also send summaries to Claude, through your own Claude Code.

## What you need

- Windows 10 or 11.
- Claude Code, signed in with your Claude account rather than an API key. The usage charts work from the logs alone, but the quota gauges need the account sign-in and stay empty without it.

## Running it

The first installer will come with release 2.0.0. Until then, run it from source with Python 3.12:

```
conda env create -f environment.yml
conda activate claude-usage-dashboard
python app.py --port 8080
```

Without conda, `pip install -r requirements.txt` followed by `python app.py --port 8080` works too.

Then open http://127.0.0.1:8080/. The first start reads every log you have, so give it a minute or two if you've used Claude Code a lot.

## When your sign-in needs attention

If the dashboard can't read your quota, a banner at the top says why and offers a fix.

- Not signed in, or the sign-in expired: click **Sign in**. A console window opens with `claude auth login`. Once you finish there, the dashboard picks up the new sign-in within a few seconds.
- The token expired: this is normal after a night away. It renews the next time you use Claude Code, or you can click **Renew now**.

Settings (the gear icon) can renew the token automatically. That's off by default: if the dashboard and Claude Code renew at the same moment, one of them loses, and you'd have to sign in again.

## Person-hours estimates (off by default)

The cost card can show roughly how long your work would have taken a person without AI. Turn on **Estimate person-hours** in Settings to get them. The dashboard then has Claude (Sonnet, through your Claude Code sign-in) read a summary of each day's sessions and estimate the hours.

That costs quota from your plan: about 1–2% of a week's quota to catch up on the last 90 days, then about 0.1–0.2% a week. It never bills an API key, even if `ANTHROPIC_API_KEY` is set on your PC. Claude's short summary of each day's work is stored with its estimate.

## Where your data lives

Running from source, it all stays in the repo folder. `data\` holds the database, a quota cache and your settings, and `logs\dashboard.log` is the server's log. The installed app will keep the same files under `%LOCALAPPDATA%\ClaudeUsageDashboard`.

Think twice before deleting `data\usage.db`. The dashboard rebuilds it from your logs on the next start, but Claude Code deletes logs older than 30 days by default, so anything older is gone for good, person-hours estimates included.

## If something's wrong

Open Settings and click **Copy diagnostics**, then send the text to whoever gave you the dashboard. It says what state the connection is in, when your token expires, when the quota was last read, and where the dashboard found Claude Code. It never includes the token.

## License

MIT, see `LICENSE`. The dashboard ships with Chart.js 4.4.4 (MIT) and the Sora, DM Sans and DM Mono fonts (SIL Open Font License 1.1). Their licenses sit next to them in `static/vendor` and `static/fonts`.
````

- [ ] **Step 5: Check the README's claims against the code.** Each of these must hold. If one doesn't, fix the README sentence to match the code, and say so in your report.
  - `ingest.py` reads `DESKTOP_SESSIONS_DIR` = `%APPDATA%\Claude\local-agent-mode-sessions`, and the `messages` schema has no message-text column.
  - `auth.py`'s refresh URL is on `claude.ai`, and `app.py`'s `USAGE_API_URL` is on `api.anthropic.com`.
  - `settings.DEFAULTS` has `judge_enabled` and `auto_refresh_token` both `False`.
  - `person_hours.JUDGE_MODEL` is `"sonnet"`.
  - `paths.APP_NAME` is `"ClaudeUsageDashboard"`.

- [ ] **Step 6: Run the tests.**
Run: `python -m pytest -q`
Expected: `258 passed`, no warnings.

- [ ] **Step 7: Update `CLAUDE.md`.**
  1. Architecture block: add after the `requirements.txt` line (Task 5):
     ```
     README.md    For the people who run the dashboard: what it reads and sends, sign-in, the judge's cost, where data lives. LICENSE is MIT
     ```
  2. "Server Restart": replace
     ```
     Changes to `app.py`, `auth.py`, `db.py`, `ingest.py`, `paths.py` or `settings.py` require a server restart to take effect
     ```
     with:
     ```
     Changes to `app.py`, `applog.py`, `auth.py`, `db.py`, `ingest.py`, `paths.py`, `settings.py` or `version.py` require a server restart to take effect
     ```
  3. Gotchas, "Settings file": the retry count there is out of date (`settings._REPLACE_ATTEMPTS` is 10, with delays growing to 0.2 s). Replace
     ```
     `update()` retries the file replace up to 5 times on a Windows `PermissionError` (antivirus or an indexer holding the file).
     ```
     with:
     ```
     `update()` tries the file replace up to 10 times over about a second on a Windows `PermissionError` (antivirus or an indexer holding the file).
     ```

- [ ] **Step 8: Commit.**
```bash
git add LICENSE README.md CLAUDE.md tests/test_release_files.py
git commit -m "Add the MIT license and a README for people running the dashboard"
```
