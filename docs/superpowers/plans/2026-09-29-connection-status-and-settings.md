# Connection Status and Settings Implementation Plan (Desktop App, Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard tells the user in plain words when their Claude sign-in needs attention and offers a one-click fix. It stops writing the user's Claude credentials unless asked. The person-hours judge becomes opt-in. All three are controlled from a new settings panel.

**Architecture:** Three new modules carry the work:
- `paths.py` decides where data lives.
- `settings.py` stores user choices in JSON beside the DB.
- `auth.py` takes over the OAuth code from `app.py` and adds a pure `connection_status()` function.

`app.py` exposes the status and gets a local-only request guard. The frontend replaces the old red banner with a status-driven banner (Sign in / Renew now) and adds a settings side panel.

**Tech Stack:** Python 3.12, FastAPI/Starlette, SQLite, vanilla JS, pytest (+ httpx for `TestClient`, test-only).

**Spec:** `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`. This plan implements Phase 1: the "New modules" rows for `paths.py`, `settings.py` and `auth.py`, "Connection status", "Local-API guard", and the matching bullets under "Changes to existing code".

**Deliberately left for later phases:**
- The settings keys `update_check`, `dismissed_version` and `preferred_port` (Phases 3–4). Phase 1 adds only `judge_enabled` and `auto_refresh_token`.
- The app version line in the diagnostics (Phase 2 adds `version.py`).
- The tray icon and notifications (Phase 3).
- The lifespan handler, and dropping `ANTHROPIC_API_KEY` from the judge env (Phase 2).

## Global Constraints

- **Environment:** every `python` / `pytest` command runs in the conda env. In the Bash tool, each call is a fresh shell, so prefix every command with:
  `eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && `
- **Working directory:** the worktree root, `C:\Users\weaverjc\Projects\Personal\claude-usage-dashboard\.claude\worktrees\busy-visvesvaraya-0b60fc`.
- **Baseline:** `python -m pytest -q` passes 113 tests before Task 1. Every task ends with the full suite green.
- **No new runtime dependencies.** `httpx` is added in Task 7 as a test-only dependency.
- **Running from source must keep working:** `python app.py --port 8080` serves the same data from the repo's `data/`. Only two defaults change: the person-hours judge is off, and the app doesn't renew the token.
- **Credentials writes:** never write `~/.claude/.credentials.json` except through `auth.refresh_token()`, and call that only when `auto_refresh_token` is on or the user clicks "Renew now".
- **Test isolation:** tests must never touch `data/usage.db`, `data/settings.json` or the real `~/.claude/.credentials.json`, and must never start a real process. `tests/conftest.py` enforces this; keep it that way.
- **User-facing text** (banner, settings panel, error messages) must read as plain human writing: no hedging, no filler, no em-dash chains. Use the exact strings in this plan.
- **Accessibility:** invoke the `wcag-a11y` skill before writing the Task 8 and Task 9 markup. Buttons are real `<button>`s, toggles are labelled checkboxes with `role="switch"`, and the settings panel is a labelled modal dialog that traps and returns focus.
- **Commits:**
  - One commit per task, with a plain imperative message.
  - **No `Co-authored-by` or AI-attribution trailers** (user rule).
  - Never stage `.claude/` or other AI tool files.
  - `CLAUDE.md` is tracked in this repo and updated alongside code (precedent: `d7d6e88`).
- **Server restarts:** `app.py`, `db.py`, `ingest.py`, `auth.py`, `paths.py` and `settings.py` load once at startup. Template, JS and CSS edits need no restart.

## Review Focus

These are inputs the spec implies but doesn't spell out. Each has a pinned test in the task named.

1. **Claude Code renews its token while our usage request is in flight.** The 401 we get back belongs to the old token. The dashboard must not get stuck on "sign-in expired"; the next poll should use the new token. (Task 4: `test_401_after_the_file_changed_mid_request_does_not_stick`.)
2. **A token with no `expiresAt`** must be tried, not reported as expired. A missing expiry means unknown, not past. (Task 4: `test_token_without_expiry_is_tried`; Task 5: the `no-expiry` row of the state table.)
3. **A refusal recovers on its own.** After a read-only 401, Claude Code renewing the token (a file change) brings the dashboard back with no clicks. (Task 4: `test_refused_credentials_need_sign_in_until_the_file_changes`.)
4. **The sign-in console is closed without signing in.** The button must come back and a second click must open a new console. (Task 5: `test_sign_in_can_be_retried_after_the_console_closes`.)
5. **Host header variants:**
   - `localhost:8080`, `[::1]:8080` and bare `127.0.0.1` are served.
   - A look-alike (`127.0.0.1.evil.example`), a missing Host, and `Origin: null` on a POST are refused.

   (Task 7: `test_blocked` parametrized rows.)

## Test instance (used by Tasks 8–10)

The always-on server on :8080 runs from the main checkout, not this worktree. Verify UI work on a second instance with a scratch home. `USERPROFILE` makes `Path.home()` point at the scratch home, so the instance has its own credentials file and transcripts. `CUD_DATA_DIR` (added in Task 1) keeps its DB out of the worktree. `PERSON_HOURS_WORKER=off` keeps the judge out of the way.

Start it with the Bash tool and `run_in_background: true`. `SP` is this session's scratchpad directory. Every Bash call is a fresh shell, so repeat the `SP=...` line in each call that uses `$SP`:
```bash
SP="C:/Users/weaverjc/AppData/Local/Temp/claude/C--Users-weaverjc-Projects-Personal-claude-usage-dashboard--claude-worktrees-busy-visvesvaraya-0b60fc/98bd79c0-669a-4517-a3f2-aeaa826c399a/scratchpad"
mkdir -p "$SP/h/.claude/projects" "$SP/h/data"
eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && \
  USERPROFILE="$(cygpath -w "$SP/h")" CUD_DATA_DIR="$(cygpath -w "$SP/h/data")" PERSON_HOURS_WORKER=off \
  python app.py --port 8888
```
- **Browse:** open `http://127.0.0.1:8888/` in the built-in browser pane (`mcp__Claude_Browser__preview_start` with `url`). Read state with `get_page_text` / `read_page`, and take screenshots for visuals.
- **Change the credentials:** write `"$SP/h/.claude/.credentials.json"` while the instance runs. The page should follow within about 5 s.
- **Stop it:** `powershell -NoProfile -Command 'Get-NetTCPConnection -LocalPort 8888 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }'`
- **Restart it** after any Python change.

---

### Task 1: `paths.py`: one place that decides where files live

**Files:**
- Create: `paths.py`
- Modify: `db.py:9-11`, `ingest.py:60` (plus its import block), `app.py:23-26,35,605-608,867-874`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `paths.APP_NAME: str`
  - `paths.resource_dir() -> Path`, `paths.data_dir() -> Path`, `paths.log_dir() -> Path`
  - Module constants `paths.RESOURCE_DIR`, `paths.DATA_DIR`, `paths.LOG_DIR` (evaluated at import).
  - Environment override `CUD_DATA_DIR`.

- [ ] **Step 1: Write the failing test.** Create `tests/test_paths.py`:

```python
import sys
from pathlib import Path

import paths

REPO = Path(paths.__file__).parent


def test_source_run_keeps_files_in_the_repo(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("CUD_DATA_DIR", raising=False)
    assert paths.resource_dir() == REPO
    assert paths.data_dir() == REPO / "data"
    assert paths.log_dir() == REPO / "logs"


def test_frozen_build_keeps_data_in_local_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.delenv("CUD_DATA_DIR", raising=False)
    assert paths.resource_dir() == tmp_path / "bundle"
    assert paths.data_dir() == tmp_path / "lad" / "ClaudeUsageDashboard" / "data"
    assert paths.log_dir() == tmp_path / "lad" / "ClaudeUsageDashboard" / "logs"


def test_env_var_overrides_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("CUD_DATA_DIR", str(tmp_path / "elsewhere"))
    assert paths.data_dir() == tmp_path / "elsewhere"


def test_app_takes_its_paths_from_paths():
    import app
    assert app.BASE_DIR == paths.RESOURCE_DIR
    assert app.QUOTA_CACHE_FILE == paths.DATA_DIR / "quota_cache.json"
```

- [ ] **Step 2: Run it and confirm it fails.**
Run: `python -m pytest tests/test_paths.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'paths'`.

- [ ] **Step 3: Create `paths.py`.**

```python
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
```

- [ ] **Step 4: Point `db.py`, `ingest.py` and `app.py` at `paths`.**
  1. `db.py`:
     - Delete line 9, `from pathlib import Path`. `DB_PATH` is its only use.
     - Add `import paths` after the stdlib imports (after `from datetime import datetime, timedelta`, separated by a blank line).
     - Replace line 11 with:
       ```python
       DB_PATH = paths.DATA_DIR / "usage.db"
       ```
  2. `ingest.py`:
     - Add `import paths` after its stdlib imports. Keep `from pathlib import Path`; it has other uses.
     - Replace line 60 with:
       ```python
       DB_PATH = paths.DATA_DIR / "usage.db"
       ```
  3. `app.py`:
     - Add `import paths` beside `import db` / `import person_hours`.
     - Replace line 26, `BASE_DIR = Path(__file__).parent`, with:
       ```python
       BASE_DIR = paths.RESOURCE_DIR  # static/ and templates/
       ```
     - Replace line 35 with:
       ```python
       QUOTA_CACHE_FILE = paths.DATA_DIR / "quota_cache.json"
       ```
     - In `startup()` (line 605), make this the first statement of the body:
       ```python
       paths.DATA_DIR.mkdir(parents=True, exist_ok=True)  # the quota cache write assumes it exists
       ```
     - In the `__main__` block, replace the two `log_dir` lines with:
       ```python
       log_dir = paths.LOG_DIR
       log_dir.mkdir(parents=True, exist_ok=True)
       ```
     - Leave `from pathlib import Path` in `app.py` for now; `CREDENTIALS_FILE` still uses it until Task 3.

- [ ] **Step 5: Run the new tests and the full suite.**
Run: `python -m pytest tests/test_paths.py -v && python -m pytest -q`
Expected: 4 passed, then 117 passed.

- [ ] **Step 6: Commit.**
```bash
git add paths.py db.py ingest.py app.py tests/test_paths.py
git commit -m "Add paths.py so data, logs and bundled files have one location rule"
```

---

### Task 2: `settings.py`: persistent user choices

**Files:**
- Create: `settings.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: `paths.DATA_DIR` (Task 1).
- Produces:
  - `settings.SETTINGS_PATH: Path`
  - `settings.DEFAULTS = {"judge_enabled": False, "auto_refresh_token": False}`
  - `settings.load() -> dict`
  - `settings.get(key: str)`
  - `settings.update(changes: dict) -> dict`, which raises `ValueError` on an unknown key or wrong type.
  - conftest autouse fixture `isolated_settings`, which yields the per-test settings path.

- [ ] **Step 1: Isolate settings in tests.** In `tests/conftest.py`, add `import settings` to the imports and add this fixture after `isolated_db`:

```python
@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Settings live in a per-test file, so every test starts from the defaults."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    return path
```

- [ ] **Step 2: Write the failing tests.** Create `tests/test_settings.py`:

```python
import json

import pytest

import settings


def test_defaults_when_there_is_no_file():
    assert settings.load() == {"judge_enabled": False, "auto_refresh_token": False}


def test_update_persists_and_reads_back(isolated_settings):
    assert settings.update({"judge_enabled": True})["judge_enabled"] is True
    assert settings.get("judge_enabled") is True
    assert json.loads(isolated_settings.read_text(encoding="utf-8"))["judge_enabled"] is True


def test_update_keeps_the_other_keys():
    settings.update({"auto_refresh_token": True})
    settings.update({"judge_enabled": True})
    assert settings.load() == {"judge_enabled": True, "auto_refresh_token": True}


@pytest.mark.parametrize("bad", [{"nope": True}, {"judge_enabled": "yes"}, {"judge_enabled": 1}])
def test_update_rejects_unknown_keys_and_wrong_types(bad):
    with pytest.raises(ValueError):
        settings.update(bad)
    assert settings.load()["judge_enabled"] is False


def test_unreadable_file_or_wrong_types_fall_back_to_defaults(isolated_settings):
    isolated_settings.write_text("{not json", encoding="utf-8")
    assert settings.load()["judge_enabled"] is False
    isolated_settings.write_text(json.dumps({"judge_enabled": "true", "auto_refresh_token": True}),
                                 encoding="utf-8")
    assert settings.load() == {"judge_enabled": False, "auto_refresh_token": True}
```

- [ ] **Step 3: Run them and confirm they fail.**
Run: `python -m pytest tests/test_settings.py -v`
Expected: an error, `ModuleNotFoundError: No module named 'settings'` (raised from conftest's import).

- [ ] **Step 4: Create `settings.py`.**

```python
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
```

- [ ] **Step 5: Run the new tests and the full suite.**
Run: `python -m pytest tests/test_settings.py -v && python -m pytest -q`
Expected: 7 passed, then 124 passed.

- [ ] **Step 6: Commit.**
```bash
git add settings.py tests/conftest.py tests/test_settings.py
git commit -m "Add settings.py for persistent user choices"
```

---

### Task 3: Move the OAuth code into `auth.py` (no behavior change)

**Files:**
- Create: `auth.py`
- Modify:
  - `app.py`: remove lines 27 and 44-50, the state at 72-76, the functions at 177-336 and 563-568. Update the call sites at 347-389, 571-602 and 627.
  - `tests/conftest.py`, `tests/test_app_hours.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (module `auth`):

  | Name | Signature |
  |---|---|
  | `CREDENTIALS_FILE` | `Path` |
  | `read_credentials()` | `-> dict \| None` |
  | `credentials_signature()` | `-> tuple \| None` |
  | `rejected()` | `-> bool`: True while the stored credentials are known bad and the file is unchanged since |
  | `refresh_token()` | `-> bool` |
  | `read_oauth_token()` | `-> tuple[str \| None, str]`. Transitional; Task 4 replaces it. |
  | `token_seconds_left()` | `-> float \| None` |

  Private state stays in the module: `_auth_dead`, `_auth_dead_creds_sig`, `_last_token_refresh_attempt`, `_token_refresh_lock`.
- conftest autouse fixture `isolated_credentials` yields the per-test credentials path. The file is absent until a test writes it.

- [ ] **Step 1: Create `auth.py` by moving code out of `app.py`.** Start the file with this header:

```python
"""
The Claude Code OAuth token the dashboard reads from ~/.claude/.credentials.json.

The usage API needs the access token Claude Code keeps in that file. This module reads
it, renews it when asked, and tracks whether the stored credentials have been refused.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
```

Below the header, move these blocks from `app.py` verbatim, in this order, then apply the renames in the table after them:

1. `app.py:44-50`: the comment block and `OAUTH_REFRESH_URL`, `OAUTH_CLIENT_ID`, `TOKEN_REFRESH_LEEWAY`, `TOKEN_REFRESH_MIN_INTERVAL`.
2. `app.py:72-76`: `_token_refresh_lock`, `_last_token_refresh_attempt`, `_auth_dead`, `_auth_dead_creds_sig`, with their comments. Drop the `# OAuth token refresh state` heading line.
3. `app.py:177-336`: `_read_credentials`, `_write_credentials_atomic`, `_credentials_signature`, `_refresh_oauth_token`, `_read_oauth_token`.
4. `app.py:563-568`: `_token_seconds_left`.

| Old name (app.py) | New name (auth.py) |
|---|---|
| `_read_credentials` | `read_credentials` |
| `_write_credentials_atomic` | `_write_credentials_atomic` (unchanged) |
| `_credentials_signature` | `credentials_signature` |
| `_refresh_oauth_token` | `refresh_token` |
| `_read_oauth_token` | `read_oauth_token` |
| `_token_seconds_left` | `token_seconds_left` |

Rename every call inside the moved code as well: `_read_oauth_token` calls `refresh_token()`, `read_credentials()`, and so on.

Then add this function after `credentials_signature`:

```python
def rejected() -> bool:
    """True while the stored credentials are known bad: refused, and the file unchanged since."""
    return _auth_dead and credentials_signature() == _auth_dead_creds_sig
```

- [ ] **Step 2: Update `app.py` to use `auth`.**
  - **Imports:** add `import auth` beside `import db`. Remove `import urllib.parse`, which is no longer used in `app.py`. Remove `from pathlib import Path` if `grep -n "Path" app.py` shows no remaining uses.
  - **`_fetch_usage_sync`:**
    - Replace `token, auth_status = _read_oauth_token()` with `token, auth_status = auth.read_oauth_token()`.
    - In the 401 branch, replace `_refresh_oauth_token()` with `auth.refresh_token()` and `if _auth_dead:` with `if auth.rejected():`.
  - **`_hours_tick`:**
    - `_token_seconds_left()` becomes `auth.token_seconds_left()` (twice).
    - `_refresh_oauth_token()` becomes `auth.refresh_token()`.
    - The `"auth_dead"` gate becomes `"auth_dead": auth.rejected(),`.
  - **`startup()`:** `threading.Thread(target=_read_oauth_token, daemon=True)` becomes `threading.Thread(target=auth.read_oauth_token, daemon=True)`.
  - **Check:** `grep -n "_auth_dead\|_refresh_oauth_token\|_read_oauth_token\|_credentials_signature\|_token_seconds_left\|_read_credentials\|CREDENTIALS_FILE" app.py` prints nothing.

- [ ] **Step 3: Isolate credentials in tests.** In `tests/conftest.py`, add `import auth`, update the module docstring, and add this fixture:

```python
"""Shared fixtures. Every test gets its own throwaway usage DB, settings file and Claude
credentials path, so none can touch data/ or ~/.claude."""
```

```python
@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    """Point auth at a per-test credentials file (absent until a test writes one) and reset its state."""
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(auth, "CREDENTIALS_FILE", path)
    monkeypatch.setattr(auth, "_auth_dead", False)
    monkeypatch.setattr(auth, "_auth_dead_creds_sig", None)
    monkeypatch.setattr(auth, "_last_token_refresh_attempt", 0.0)
    return path
```

- [ ] **Step 4: Move and adapt the token tests.**
  1. Create `tests/test_auth.py` with the moved `test_token_seconds_left` and a test for `rejected()`:

```python
import json
import time

import auth


def test_token_seconds_left(isolated_credentials):
    assert auth.token_seconds_left() is None
    isolated_credentials.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "t", "expiresAt": (time.time() + 3600) * 1000}}))
    assert 3500 < auth.token_seconds_left() <= 3600


def test_rejected_clears_once_the_credentials_file_changes(monkeypatch):
    monkeypatch.setattr(auth, "_auth_dead", True)
    monkeypatch.setattr(auth, "_auth_dead_creds_sig", ("old",))
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("old",))
    assert auth.rejected()
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("new",))
    assert not auth.rejected()
```

  2. In `tests/test_app_hours.py`:
     - Delete `test_token_seconds_left`.
     - In the `timers` fixture, replace the `_token_seconds_left` patch with `monkeypatch.setattr(app.auth, "token_seconds_left", lambda: 3600.0)`.
     - In `test_hours_tick_refreshes_a_token_that_would_expire_mid_tick`, patch `app.auth.token_seconds_left` and `app.auth.refresh_token` instead of the old `app` names.
     - Replace `test_hours_tick_ignores_auth_dead_after_a_relogin` with:

```python
def test_hours_tick_passes_whether_the_credentials_were_refused(timers, monkeypatch):
    seen = []
    monkeypatch.setattr(app.auth, "rejected", lambda: True)
    monkeypatch.setattr(app, "_cached_five_hour_pct", lambda: 10.0)
    monkeypatch.setattr(app.person_hours, "run_tick", lambda now, gates: seen.append(gates))
    app._hours_tick()
    assert seen[0]["auth_dead"] is True
```

- [ ] **Step 5: Run the full suite.**
Run: `python -m pytest -q`
Expected: 125 passed. That's 124, minus the moved `test_token_seconds_left`, plus the two new `test_auth.py` tests; the replaced hours test keeps its count.

- [ ] **Step 6: Commit.**
```bash
git add auth.py app.py tests/conftest.py tests/test_auth.py tests/test_app_hours.py
git commit -m "Move the OAuth token code from app.py into auth.py"
```

---

### Task 4: Read-only token handling and fast recovery

**Files:**
- Modify:
  - `auth.py`: `refresh_token` gains `force` and uses `mark_rejected`. Add `mark_rejected` and `AUTH_ERRORS`. Replace `read_oauth_token` with `usable_token`.
  - `app.py`:
    - `_usage_cache` gets a `creds_sig` key.
    - New `_retry_now_if_credentials_changed`.
    - `_get_usage_data` changes: the call at the top and the backoff set.
    - `_fetch_usage_sync` is rewritten.
    - `_hours_tick` renews only when allowed.
    - `startup()` pre-renews only when allowed.
  - `tests/helpers.py`
- Test: `tests/test_auth.py`, `tests/test_app_quota.py` (new)

**Interfaces:**
- Consumes: `settings.get("auto_refresh_token")` (Task 2); `auth.credentials_signature`, `auth.rejected`, `auth.read_credentials` (Task 3).
- Produces:
  - `auth.AUTH_ERRORS = ("login-required", "no-credentials", "token-expired")`
  - `auth.usable_token(auto_refresh: bool) -> tuple[str | None, str]`. The status is `"ok"` or one of `AUTH_ERRORS`; the token is `None` unless the status is `"ok"`.
  - `auth.mark_rejected(sig: tuple | None) -> None`
  - `auth.refresh_token(force: bool = False) -> bool`
  - `app._retry_now_if_credentials_changed(cache: dict) -> None`
  - Test helpers in `tests/helpers.py`: `write_credentials(path, token="tok", expires_in=3600, refresh="ref")`, `FakeResponse(payload)`, `http_error(code, body=b"")`, `fake_urlopen(responses) -> (urlopen, calls)`.

- [ ] **Step 1: Add test helpers.** Append to `tests/helpers.py`, and add `import io`, `import time` and `import urllib.error` to its imports:

```python
def write_credentials(path, token="tok", expires_in=3600, refresh="ref"):
    """A Claude Code credentials file whose access token expires `expires_in` seconds from now.

    expires_in=None leaves expiresAt out.
    """
    oauth = {"accessToken": token, "refreshToken": refresh}
    if expires_in is not None:
        oauth["expiresAt"] = int((time.time() + expires_in) * 1000)
    path.write_text(json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")
    return path


class FakeResponse:
    """What urllib.request.urlopen returns, for a JSON body."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://example.invalid", code, "error", {}, io.BytesIO(body))


def fake_urlopen(responses):
    """A urlopen stand-in that returns or raises each item of `responses` in turn."""
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return urlopen, calls
```

- [ ] **Step 2: Write the failing auth tests.** Append to `tests/test_auth.py`, and update its imports to `import json, time, pytest, auth` plus `from helpers import FakeResponse, fake_urlopen, http_error, write_credentials`:

```python
def no_refresh(*args, **kwargs):
    pytest.fail("read-only mode must not renew the token")


def test_valid_token_is_used(isolated_credentials):
    write_credentials(isolated_credentials)
    assert auth.usable_token(auto_refresh=False) == ("tok", "ok")


def test_missing_or_blank_token_means_no_credentials(isolated_credentials):
    assert auth.usable_token(auto_refresh=False) == (None, "no-credentials")
    write_credentials(isolated_credentials, token="")
    assert auth.usable_token(auto_refresh=False) == (None, "no-credentials")


def test_expired_token_is_reported_not_renewed(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    monkeypatch.setattr(auth, "refresh_token", no_refresh)
    assert auth.usable_token(auto_refresh=False) == (None, "token-expired")


def test_token_without_expiry_is_tried(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=None)
    monkeypatch.setattr(auth, "refresh_token", no_refresh)
    assert auth.usable_token(auto_refresh=False) == ("tok", "ok")


def test_expired_token_is_renewed_when_allowed(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)

    def renew(force=False):
        write_credentials(isolated_credentials, token="renewed")
        return True
    monkeypatch.setattr(auth, "refresh_token", renew)
    assert auth.usable_token(auto_refresh=True) == ("renewed", "ok")


def test_refused_credentials_need_sign_in_until_the_file_changes(isolated_credentials):
    write_credentials(isolated_credentials)
    auth.mark_rejected(auth.credentials_signature())
    assert auth.usable_token(auto_refresh=False) == (None, "login-required")
    write_credentials(isolated_credentials, token="renewed-by-claude-code")  # a different size
    assert auth.usable_token(auto_refresh=False) == ("renewed-by-claude-code", "ok")


def test_refusal_recorded_against_an_older_file_does_not_stick(isolated_credentials):
    write_credentials(isolated_credentials)
    before = auth.credentials_signature()
    write_credentials(isolated_credentials, token="renewed-meanwhile")
    auth.mark_rejected(before)
    assert not auth.rejected()


def test_refresh_writes_the_new_token(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    urlopen, _ = fake_urlopen([FakeResponse(
        {"access_token": "fresh", "refresh_token": "ref2", "expires_in": 3600})])
    monkeypatch.setattr(auth.urllib.request, "urlopen", urlopen)
    assert auth.refresh_token() is True
    oauth = json.loads(isolated_credentials.read_text())["claudeAiOauth"]
    assert (oauth["accessToken"], oauth["refreshToken"]) == ("fresh", "ref2")


def test_invalid_grant_marks_the_credentials_refused(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    urlopen, _ = fake_urlopen([http_error(400, b'{"error": "invalid_grant"}')])
    monkeypatch.setattr(auth.urllib.request, "urlopen", urlopen)
    assert auth.refresh_token() is False
    assert auth.rejected()


def test_forced_refresh_skips_the_throttle_and_the_refused_flag(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    auth.mark_rejected(auth.credentials_signature())
    urlopen, calls = fake_urlopen([FakeResponse({"access_token": "fresh", "expires_in": 3600})])
    monkeypatch.setattr(auth.urllib.request, "urlopen", urlopen)
    assert auth.refresh_token() is False and calls == []  # background renewal waits for a new file
    assert auth.refresh_token(force=True) is True and len(calls) == 1
    assert not auth.rejected()
```

- [ ] **Step 3: Write the failing quota tests.** Create `tests/test_app_quota.py`:

```python
import pytest

import app
import auth
import settings
from helpers import FakeResponse, fake_urlopen, http_error, write_credentials

USAGE = {"five_hour": {"utilization": 12.0, "resets_at": None},
         "seven_day": {"utilization": 30.0, "resets_at": None}}


def test_expired_token_is_never_sent(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials, expires_in=-60)
    monkeypatch.setattr(app.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("sent an expired token"))
    assert app._fetch_usage_sync() == {"ok": False, "error": "token-expired", "retry_after": None}


def test_read_only_401_asks_for_sign_in(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials)
    urlopen, _ = fake_urlopen([http_error(401)])
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(auth, "refresh_token", lambda force=False: pytest.fail("renewed"))
    assert app._fetch_usage_sync()["error"] == "login-required"
    assert auth.rejected()


def test_401_after_the_file_changed_mid_request_does_not_stick(isolated_credentials, monkeypatch):
    write_credentials(isolated_credentials)

    def urlopen(*args, **kwargs):
        write_credentials(isolated_credentials, token="renewed-meanwhile")
        raise http_error(401)
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    app._fetch_usage_sync()
    assert not auth.rejected()


def test_auto_renewal_retries_once_after_a_401(isolated_credentials, monkeypatch):
    settings.update({"auto_refresh_token": True})
    write_credentials(isolated_credentials)
    urlopen, calls = fake_urlopen([http_error(401), FakeResponse(USAGE)])
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(auth, "refresh_token", lambda force=False: True)
    assert app._fetch_usage_sync()["ok"] is True and len(calls) == 2


def test_credentials_change_lifts_an_auth_backoff(monkeypatch):
    cache = {"error": "token-expired", "retry_after": 1e12, "creds_sig": ("old",)}
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("new",))
    app._retry_now_if_credentials_changed(cache)
    assert cache["retry_after"] == 0.0 and cache["creds_sig"] == ("new",)


def test_credentials_change_leaves_a_rate_limit_backoff_alone(monkeypatch):
    cache = {"error": "rate-limited", "retry_after": 1e12, "creds_sig": ("old",)}
    monkeypatch.setattr(auth, "credentials_signature", lambda: ("new",))
    app._retry_now_if_credentials_changed(cache)
    assert cache["retry_after"] == 1e12
```

- [ ] **Step 4: Run them and confirm they fail.**
Run: `python -m pytest tests/test_auth.py tests/test_app_quota.py -v`
Expected: failures with `AttributeError: module 'auth' has no attribute 'usable_token'` / `'mark_rejected'`, and `app` has no `_retry_now_if_credentials_changed`.

- [ ] **Step 5: Implement the `auth.py` changes.**
  1. After the `TOKEN_REFRESH_*` constants, add:
     ```python
     # usable_token statuses that mean "no token to send", as opposed to a failed request.
     AUTH_ERRORS = ("login-required", "no-credentials", "token-expired")
     ```
  2. Add `mark_rejected` just above `rejected`:
     ```python
     def mark_rejected(sig: tuple | None) -> None:
         """Record that the credentials whose file had signature `sig` were refused.

         Pass the signature taken before the token was read: if Claude Code rewrote the file
         while the request was in flight, the refusal belongs to the old token, and rejected()
         stays False so the next poll tries the new one.
         """
         global _auth_dead, _auth_dead_creds_sig
         _auth_dead = True
         _auth_dead_creds_sig = sig
     ```
  3. Replace the signature, docstring and opening of `refresh_token` down to (but not including) `creds = read_credentials()` with:
     ```python
     def refresh_token(force: bool = False) -> bool:
         """Renew the access token with the stored refreshToken and rewrite the credentials file.

         Background callers are throttled to one attempt per TOKEN_REFRESH_MIN_INTERVAL and
         wait for the file to change once a refresh token has been refused. force (the Renew
         now button) skips both: a person asked for exactly one attempt. Returns True on success.
         """
         global _last_token_refresh_attempt, _auth_dead, _auth_dead_creds_sig
         with _token_refresh_lock:
             if not force:
                 if _auth_dead:
                     if credentials_signature() == _auth_dead_creds_sig:
                         # Refused, and nothing has rewritten the file since. Re-POSTing the same
                         # refresh token can only fail again: wait for a sign-in to replace it.
                         return False
                     # The file changed, so a sign-in may have landed. Retry now rather than
                     # waiting out the throttle left over from the last doomed attempt.
                     _auth_dead = False
                     _auth_dead_creds_sig = None
                     _last_token_refresh_attempt = 0.0
                 if time.monotonic() - _last_token_refresh_attempt < TOKEN_REFRESH_MIN_INTERVAL:
                     return False
             _last_token_refresh_attempt = time.monotonic()

             sig = credentials_signature()  # before reading: see mark_rejected
     ```
     Keep the rest of the body unchanged from `creds = read_credentials()` onward, except for the `invalid_grant` branch. Replace its two assignments (`_auth_dead = True` / `_auth_dead_creds_sig = _credentials_signature()`) with `mark_rejected(sig)`.
  4. Replace `read_oauth_token` entirely with:
     ```python
     def usable_token(auto_refresh: bool) -> tuple[str | None, str]:
         """(access token, status) for a usage-API call. The token is None unless status is 'ok'.

         status is 'ok' or one of AUTH_ERRORS:
           'no-credentials' — no credentials file, or no token in it
           'login-required' — the stored credentials were refused (see rejected())
           'token-expired'  — the token has expired and wasn't renewed

         Read-only unless auto_refresh: an expired token is reported, never renewed or sent.
         The usage API answers an expired token with 429 rather than 401, which used to show
         up as rate limiting. A token with no expiresAt is tried; the API decides.
         """
         oauth = (read_credentials() or {}).get("claudeAiOauth") or {}
         token = oauth.get("accessToken")
         if not token:
             return None, "no-credentials"
         if rejected():
             return None, "login-required"
         expires_at_ms = oauth.get("expiresAt")
         if not expires_at_ms:
             return token, "ok"
         seconds_left = expires_at_ms / 1000 - time.time()
         if seconds_left >= TOKEN_REFRESH_LEEWAY:
             return token, "ok"
         if auto_refresh and refresh_token():
             return (read_credentials() or {}).get("claudeAiOauth", {}).get("accessToken"), "ok"
         if seconds_left > 0:
             return token, "ok"  # close to expiry, but still good for this call
         return None, "login-required" if rejected() else "token-expired"
     ```

- [ ] **Step 6: Implement the `app.py` changes.**
  1. Add `import settings` beside `import auth`.
  2. In `_usage_cache`, add after `"fail_count": 0,`:
     ```python
         "creds_sig": None,   # credentials-file signature seen on the last poll
     ```
  3. Add this function just above `_get_usage_data`:
     ```python
     def _retry_now_if_credentials_changed(cache: dict) -> None:
         """Drop a credentials-related backoff as soon as the credentials file changes.

         Signing in, or Claude Code renewing its token, rewrites the file. Checking it on each
         5-second poll picks the new token up at once instead of after AUTH_RETRY. Backoffs
         for other failures (rate limits, network) are left alone.
         """
         sig = auth.credentials_signature()
         if sig != cache.get("creds_sig"):
             cache["creds_sig"] = sig
             if cache.get("error") in (*auth.AUTH_ERRORS, "http-401"):
                 cache["retry_after"] = 0.0
     ```
  4. In `_get_usage_data`, add `_retry_now_if_credentials_changed(cache)` directly after `cache = _usage_cache`. Further down, replace `if result.get("error") in ("login-required", "no-credentials"):` with:
     ```python
     if result.get("error") in auth.AUTH_ERRORS:
     ```
  5. Replace `_fetch_usage_sync`'s docstring and body before `req = urllib.request.Request(` with:
     ```python
     def _fetch_usage_sync(_already_retried: bool = False) -> dict:
         """Call the Anthropic usage API synchronously. Returns a result dict:
         On success: {"ok": True, "data": {...}, "retry_after": None}
         On rate-limit: {"ok": False, "error": "rate-limited", "retry_after": <seconds>}
         On other failure: {"ok": False, "error": "<message>", "retry_after": None}

         Without a usable token the error is auth.usable_token's status and nothing is sent.
         A 401 means the stored token was refused: with automatic renewal on, renew once and
         retry; otherwise record the refusal so the dashboard asks the person to sign in.
         """
         auto = settings.get("auto_refresh_token")
         sig = auth.credentials_signature()  # the file this token comes from, for mark_rejected
         token, auth_status = auth.usable_token(auto_refresh=auto)
         if not token:
             return {"ok": False, "error": auth_status, "retry_after": None}
     ```
     Then replace the whole `if e.code == 401 and not _already_retried:` block with:
     ```python
             if e.code == 401:
                 if auto and not _already_retried:
                     print(f"[quota {ts}] HTTP 401 - attempting OAuth refresh")
                     if auth.refresh_token():
                         return _fetch_usage_sync(_already_retried=True)
                 elif not auto:
                     auth.mark_rejected(sig)
                 if auth.rejected():
                     return {"ok": False, "error": "login-required", "retry_after": None}
     ```
  6. In `_hours_tick`, change the renewal condition to:
     ```python
     if (settings.get("auto_refresh_token") and left is not None
             and left < person_hours.TOKEN_MIN_SECONDS):
     ```
  7. In `startup()`, replace the pre-refresh comment and thread with:
     ```python
     if settings.get("auto_refresh_token"):
         # Renew a token that expired while the machine was off, before the first poll needs it.
         threading.Thread(target=auth.usable_token, args=(True,), daemon=True).start()
     ```
  8. In `tests/test_app_hours.py`, `test_hours_tick_refreshes_a_token_that_would_expire_mid_tick` now needs renewal allowed. Add `settings.update({"auto_refresh_token": True})` as its first line, and `import settings` at the top of the file.

- [ ] **Step 7: Run the tests.**
Run: `python -m pytest tests/test_auth.py tests/test_app_quota.py -v && python -m pytest -q`
Expected: all pass; 141 in total.

- [ ] **Step 8: Commit.**
```bash
git add auth.py app.py tests/helpers.py tests/test_auth.py tests/test_app_quota.py tests/test_app_hours.py
git commit -m "Stop renewing or sending expired tokens unless automatic renewal is on"
```

---

### Task 5: Connection status, Sign in and Renew now

**Files:**
- Modify:
  - `auth.py`: add `INSTALL_DOCS_URL`, `_clock`, `connection_status`, `diagnostics`, `launch_login`, `login_running`, `_login_proc`, and `import subprocess`.
  - `app.py`:
    - `_usage_cache` gets `last_ok_at`, filled from the disk-cache seed and on each successful fetch.
    - New `_connection()`.
    - `/api/quota` returns the connection.
    - New endpoints `/api/connection`, `/api/connection/login`, `/api/connection/renew`.
  - `tests/conftest.py`, `tests/helpers.py`
- Test: `tests/test_auth.py`, `tests/test_app_connection.py` (new)

**Interfaces:**
- Consumes: `auth.read_credentials`, `auth.rejected`, `auth.refresh_token(force=True)`, `auth.AUTH_ERRORS` (Tasks 3–4); `person_hours.find_claude_cli()` (existing, `person_hours.py:364`); `settings.get` (Task 2).
- Produces:
  - `auth.connection_status(*, creds, creds_exists, cli_path, rejected, last_error, last_ok_at, now) -> dict` with keys `state, title, detail, actions, token_expires_at, last_ok_at, last_error`.
    - `state` is one of `not-installed | signed-out | login-required | token-expired | unavailable | connected`.
    - `actions` is a list drawn from `install | sign-in | renew`.
  - `auth.diagnostics(status, *, cli_path, auto_refresh, login_running) -> str`
  - `auth.launch_login(cli: str) -> bool` and `auth.login_running() -> bool`
  - `app._connection() -> dict`: the status plus `login_running`, `install_url`, `diagnostics`.
  - `GET /api/connection` returns `_connection()`.
  - `GET /api/quota` returns the usage data plus a `connection` key.
  - `POST /api/connection/login` returns `_connection()`, or 409 without the CLI.
  - `POST /api/connection/renew` returns `{"renewed": bool, **_connection()}`.
  - Test helper `FakePopen`.

- [ ] **Step 1: Guard `Popen` and reset login state in tests.** In `tests/conftest.py`:
  - In `isolated_credentials`, add `monkeypatch.setattr(auth, "_login_proc", None)`.
  - In `no_real_cli`, add `monkeypatch.setattr(person_hours.subprocess, "Popen", forbidden)`.
  - Update `no_real_cli`'s docstring to: `"""No test may start a real process: the claude CLI would spend quota or open a sign-in window."""`
  - Append to `tests/helpers.py`:

```python
class FakePopen:
    """subprocess.Popen stand-in. Set .returncode to simulate the process exiting."""
    launched = []

    def __init__(self, args, **kwargs):
        FakePopen.launched.append(args)
        self.returncode = None

    def poll(self):
        return self.returncode
```

- [ ] **Step 2: Write the failing status tests.** Append to `tests/test_auth.py`, and add `from datetime import datetime` plus `FakePopen` to the helpers import:

```python
NOW = datetime(2026, 9, 29, 9, 0).timestamp()


def creds(token="tok", expires_in=3600, expiry=True):
    oauth = {"accessToken": token}
    if expiry:
        oauth["expiresAt"] = (NOW + expires_in) * 1000
    return {"claudeAiOauth": oauth}


BASE = dict(creds=creds(), creds_exists=True, cli_path="C:/claude.exe", rejected=False,
            last_error=None, last_ok_at=NOW - 60, now=NOW)


@pytest.mark.parametrize("change, state, actions", [
    ({}, "connected", []),
    ({"creds": creds(expiry=False)}, "connected", []),  # no-expiry
    ({"creds": None, "creds_exists": False, "cli_path": None}, "not-installed", ["install"]),
    ({"creds": None, "creds_exists": False}, "signed-out", ["sign-in"]),
    ({"creds": creds(token="")}, "signed-out", ["sign-in"]),
    ({"creds": None}, "signed-out", ["sign-in"]),
    ({"creds": creds(token=""), "cli_path": None}, "signed-out", ["install"]),
    ({"rejected": True}, "login-required", ["sign-in"]),
    ({"last_error": "login-required"}, "login-required", ["sign-in"]),
    ({"creds": creds(expires_in=-60)}, "token-expired", ["renew", "sign-in"]),
    ({"creds": creds(expires_in=-60), "cli_path": None}, "token-expired", ["renew"]),
    ({"creds": creds(expires_in=-60), "rejected": True}, "login-required", ["sign-in"]),
    ({"last_error": "rate-limited"}, "unavailable", []),
    ({"last_error": "http-503"}, "unavailable", []),
])
def test_connection_states(change, state, actions):
    out = auth.connection_status(**{**BASE, **change})
    assert (out["state"], out["actions"]) == (state, actions)
    assert out["title"]


def test_signed_out_detail_explains_a_blank_or_unreadable_file():
    blank = auth.connection_status(**{**BASE, "creds": creds(token="")})
    assert "desktop app" in blank["detail"]
    unreadable = auth.connection_status(**{**BASE, "creds": None})
    assert "couldn't be read" in unreadable["detail"]


def test_unavailable_says_how_old_the_figures_are():
    out = auth.connection_status(**{**BASE, "last_error": "rate-limited",
                                    "last_ok_at": datetime(2026, 9, 29, 8, 42).timestamp()})
    assert "8:42 AM" in out["detail"]


def test_clock_adds_the_date_for_another_day():
    ts = datetime(2026, 9, 29, 3, 12).timestamp()
    assert auth._clock(ts, NOW) == "3:12 AM"
    assert auth._clock(ts, datetime(2026, 9, 30, 9, 0).timestamp()) == "Sep 29, 3:12 AM"


def test_diagnostics_never_include_tokens(isolated_credentials):
    write_credentials(isolated_credentials, token="sk-ant-oat-SECRET", refresh="sk-ant-ort-SECRET")
    status = auth.connection_status(**BASE)
    text = auth.diagnostics(status, cli_path="C:/claude.exe", auto_refresh=False, login_running=False)
    assert "SECRET" not in text
    assert "State: connected" in text and str(isolated_credentials) in text


def test_sign_in_opens_one_console_at_a_time(monkeypatch):
    FakePopen.launched = []
    monkeypatch.setattr(auth.subprocess, "Popen", FakePopen)
    assert auth.launch_login("C:/claude.exe") is True
    assert auth.launch_login("C:/claude.exe") is False
    assert FakePopen.launched == [["C:/claude.exe", "auth", "login", "--claudeai"]]
    assert auth.login_running()


def test_sign_in_can_be_retried_after_the_console_closes(monkeypatch):
    FakePopen.launched = []
    monkeypatch.setattr(auth.subprocess, "Popen", FakePopen)
    auth.launch_login("C:/claude.exe")
    auth._login_proc.returncode = 1  # closed without signing in
    assert not auth.login_running()
    assert auth.launch_login("C:/claude.exe") is True
    assert len(FakePopen.launched) == 2
```

- [ ] **Step 3: Write the failing endpoint tests.** Create `tests/test_app_connection.py`:

```python
import asyncio
import json

import pytest

import app
import auth
from helpers import FakePopen, write_credentials


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(app, "_usage_cache", dict(app._usage_cache))


@pytest.fixture
def no_fetch(monkeypatch):
    async def fake():
        return {}
    monkeypatch.setattr(app, "_get_usage_data", fake)


def test_quota_response_carries_the_connection(no_fetch, monkeypatch):
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: None)
    out = asyncio.run(app.get_quota())
    assert out["connection"]["state"] == "not-installed"
    assert out["connection"]["install_url"] == "https://code.claude.com/docs/en/setup"


def test_connection_never_leaks_tokens(isolated_credentials, no_fetch, monkeypatch):
    write_credentials(isolated_credentials, token="sk-ant-oat-SECRET", refresh="sk-ant-ort-SECRET")
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: "C:/claude.exe")
    out = asyncio.run(app.connection())
    assert out["state"] == "connected"
    assert "SECRET" not in json.dumps(out)


def test_sign_in_endpoint_needs_the_cli(monkeypatch):
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: None)
    with pytest.raises(app.HTTPException) as err:
        app.connection_login()
    assert err.value.status_code == 409


def test_sign_in_endpoint_reports_the_open_console(monkeypatch):
    FakePopen.launched = []
    monkeypatch.setattr(app.person_hours, "find_claude_cli", lambda: "C:/claude.exe")
    monkeypatch.setattr(auth.subprocess, "Popen", FakePopen)
    assert app.connection_login()["login_running"] is True
    assert len(FakePopen.launched) == 1


def test_renew_endpoint_forces_one_attempt_and_refetches(no_fetch, monkeypatch):
    monkeypatch.setattr(auth, "refresh_token", lambda force=False: force)
    app._usage_cache["retry_after"] = 1e12
    out = asyncio.run(app.connection_renew())
    assert out["renewed"] is True and app._usage_cache["retry_after"] == 0.0


def test_successful_fetch_records_when(monkeypatch):
    monkeypatch.setattr(app, "_fetch_usage_sync", lambda: {
        "ok": True, "retry_after": None,
        "data": {"five_hour": {"utilization": 1.0}, "seven_day": {"utilization": 2.0}}})
    monkeypatch.setattr(app, "_maybe_write_snapshot", lambda *a: None)
    monkeypatch.setattr(app, "QUOTA_CACHE_FILE", app.paths.DATA_DIR / "__unused__" / "q.json")
    app._usage_cache.update(data=None, retry_after=0.0, last_ok_at=None)
    asyncio.run(app._get_usage_data())
    assert app._usage_cache["last_ok_at"] is not None
```
In the last test, `QUOTA_CACHE_FILE` points into a directory that doesn't exist, so the cache write fails silently (`except: pass`) and never touches the real cache.

- [ ] **Step 4: Run them and confirm they fail.**
Run: `python -m pytest tests/test_auth.py tests/test_app_connection.py -v`
Expected: `AttributeError` for `connection_status`, `_clock`, `diagnostics`, `launch_login`, `app.connection`, and so on.

- [ ] **Step 5: Implement the `auth.py` additions.** Add `import subprocess` to the imports and `INSTALL_DOCS_URL = "https://code.claude.com/docs/en/setup"` below `CREDENTIALS_FILE`. Then append:

```python
# ─── Connection status ───────────────────────────────────────
def _clock(ts: float, now: float) -> str:
    """'3:12 AM' for today, 'Sep 28, 3:12 AM' for another day, in local time."""
    dt = datetime.fromtimestamp(ts)
    t = dt.strftime("%I:%M %p").lstrip("0")
    return t if dt.date() == datetime.fromtimestamp(now).date() else f"{dt:%b} {dt.day}, {t}"


def connection_status(*, creds, creds_exists, cli_path, rejected, last_error, last_ok_at,
                      now) -> dict:
    """Plain-language state of the dashboard's link to the person's Claude account.

    creds: parsed credentials JSON, or None when the file is missing or unreadable.
    creds_exists: whether the credentials file exists at all.
    cli_path: the claude CLI, or None. Signing in needs it.
    rejected: auth.rejected(). last_error: the usage cache's error, None after a good fetch.
    last_ok_at, now: epoch seconds.
    The first matching state wins; see the spec's "Connection status" table.
    """
    oauth = (creds or {}).get("claudeAiOauth") or {}
    expires_at = oauth["expiresAt"] / 1000 if oauth.get("expiresAt") else None
    sign_in = ["sign-in"] if cli_path else ["install"]

    def result(state, title, detail="", actions=()):
        return {"state": state, "title": title, "detail": detail, "actions": list(actions),
                "token_expires_at": expires_at, "last_ok_at": last_ok_at, "last_error": last_error}

    if not oauth.get("accessToken"):
        if not creds_exists and not cli_path:
            return result("not-installed", "Claude Code isn't set up on this PC.",
                          "Install Claude Code and sign in. The dashboard connects on its own after that.",
                          ["install"])
        if creds_exists and creds is None:
            detail = "The Claude credentials file couldn't be read."
        elif creds_exists:
            detail = ("The Claude credentials file holds no sign-in. The Claude desktop app "
                      "can keep its sign-in to itself and leave this file empty.")
        else:
            detail = "Sign in to Claude Code to see your quota."
        return result("signed-out", "Not signed in to Claude Code.", detail, sign_in)
    if rejected or last_error == "login-required":
        return result("login-required", "Your Claude sign-in has expired.",
                      "Sign in again to bring back the quota gauges.", sign_in)
    if expires_at is not None and expires_at <= now:
        return result("token-expired", f"Your sign-in token expired at {_clock(expires_at, now)}.",
                      "It renews the next time you use Claude Code. Quota figures are paused until then.",
                      ["renew", "sign-in"] if cli_path else ["renew"])
    if last_error and last_error not in AUTH_ERRORS:
        since = f"Showing figures from {_clock(last_ok_at, now)}. " if last_ok_at else ""
        return result("unavailable", "Couldn't reach Anthropic for quota figures.",
                      f"{since}Retrying automatically.")
    return result("connected", "Connected to Claude.")


def diagnostics(status: dict, *, cli_path, auto_refresh: bool, login_running: bool) -> str:
    """A plain-text summary a person can paste into a message. Never includes a token."""
    def when(ts):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "never"
    try:
        creds_line = f"{CREDENTIALS_FILE} (modified {when(CREDENTIALS_FILE.stat().st_mtime)})"
    except OSError:
        creds_line = f"{CREDENTIALS_FILE} (missing)"
    return "\n".join([
        "Claude Usage Dashboard diagnostics",
        f"State: {status['state']}",
        f"Token expires: {when(status['token_expires_at']) if status['token_expires_at'] else 'unknown'}",
        f"Last successful quota fetch: {when(status['last_ok_at'])}",
        f"Last error: {status['last_error'] or 'none'}",
        f"Claude CLI: {cli_path or 'not found'}",
        f"Credentials file: {creds_line}",
        f"Automatic token renewal: {'on' if auto_refresh else 'off'}",
        f"Sign-in window open: {'yes' if login_running else 'no'}",
    ])


# ─── Signing in ──────────────────────────────────────────────
_login_proc = None  # the `claude auth login` console, while one is open


def login_running() -> bool:
    return _login_proc is not None and _login_proc.poll() is None


def launch_login(cli: str) -> bool:
    """Open `claude auth login` in its own console window. False if one is still open.

    The dashboard needs nothing back from the process: a successful sign-in rewrites the
    credentials file, and the next quota poll notices.
    """
    global _login_proc
    if login_running():
        return False
    _login_proc = subprocess.Popen([cli, "auth", "login", "--claudeai"],
                                   creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return True
```

- [ ] **Step 6: Implement the `app.py` additions.**
  1. In `_usage_cache`, add `"last_ok_at": None,   # wall-clock time of the last successful fetch`.
  2. In the disk-cache seed block at module level, directly after `_saved = json.loads(...)`, add:
     ```python
     _usage_cache["last_ok_at"] = _saved.get("time")
     ```
  3. In `_get_usage_data`'s success branch, after `cache["fail_count"] = 0`, add:
     ```python
     cache["last_ok_at"] = time.time()
     ```
  4. Add below `_cached_five_hour_pct`:
     ```python
     def _connection() -> dict:
         """The connection status for the banner, the settings panel and (later) the tray."""
         cli = person_hours.find_claude_cli()
         status = auth.connection_status(
             creds=auth.read_credentials(), creds_exists=auth.CREDENTIALS_FILE.exists(),
             cli_path=cli, rejected=auth.rejected(), last_error=_usage_cache["error"],
             last_ok_at=_usage_cache["last_ok_at"], now=time.time())
         running = auth.login_running()
         return {**status, "login_running": running, "install_url": auth.INSTALL_DOCS_URL,
                 "diagnostics": auth.diagnostics(status, cli_path=cli, login_running=running,
                                                 auto_refresh=settings.get("auto_refresh_token"))}
     ```
  5. Replace the `/api/quota` handler, and add the new endpoints after it:
     ```python
     @app.get("/api/quota")
     async def get_quota():
         """Live quota data from the Anthropic usage API, plus the connection status."""
         data = await _get_usage_data()
         return {**data, "connection": _connection()}


     @app.get("/api/connection")
     async def connection():
         await _get_usage_data()  # so the state reflects a fetch, not just the file
         return _connection()


     @app.post("/api/connection/login")
     def connection_login():
         """Open `claude auth login` in a console window for the person to finish."""
         cli = person_hours.find_claude_cli()
         if not cli:
             raise HTTPException(409, "Claude Code isn't installed")
         auth.launch_login(cli)
         return _connection()


     @app.post("/api/connection/renew")
     async def connection_renew():
         """One token renewal the person asked for, then a fresh fetch with the result."""
         renewed = await asyncio.get_running_loop().run_in_executor(
             None, lambda: auth.refresh_token(force=True))
         if renewed:
             _usage_cache["retry_after"] = 0.0
             _usage_cache["fetched_at"] = 0.0
         await _get_usage_data()
         return {"renewed": renewed, **_connection()}
     ```

- [ ] **Step 7: Run the tests.**
Run: `python -m pytest tests/test_auth.py tests/test_app_connection.py -v && python -m pytest -q`
Expected: all pass; about 167 in total (141 + 20 in `test_auth.py` + 6 in `test_app_connection.py`).

- [ ] **Step 8: Commit.**
```bash
git add auth.py app.py tests/conftest.py tests/helpers.py tests/test_auth.py tests/test_app_connection.py
git commit -m "Report the Claude connection as named states with Sign in and Renew now"
```

---

### Task 6: Settings API and person-hours opt-in

**Files:**
- Modify:
  - `person_hours.py:711-727`: `skip_reason` gains `enabled`. Also the `run_tick` docstring (`person_hours.py:749-758`).
  - `app.py`:
    - `from fastapi import` line gains `Body`.
    - `HOURS_WORKER_ENABLED` comment.
    - New `_judge_enabled`.
    - `_hours_tick` splits into `_hours_pass` + `_hours_tick`.
    - `/api/hours` override.
    - New `/api/settings` GET/POST.
  - `tests/test_person_hours_worker.py`, `tests/test_app_hours.py`
- Test: `tests/test_app_settings.py` (new)

**Interfaces:**
- Consumes: `settings.load/get/update` (Task 2); `auth.token_seconds_left`, `auth.refresh_token`, `auth.rejected` (Tasks 3–4).
- Produces:
  - `person_hours.skip_reason(..., enabled=True)`: returns `"disabled"` first when `enabled` is False.
  - `run_tick` gates may carry `"enabled"`.
  - `app._judge_enabled() -> bool`, `app._hours_pass() -> None`.
  - `GET /api/settings` returns `settings.load()`.
  - `POST /api/settings` takes a JSON object and returns every setting; 400 on bad input.

- [ ] **Step 1: Write the failing worker tests.** In `tests/test_person_hours_worker.py`, add this row to the `test_skip_reason` parametrize list:

```python
    ({"enabled": False, "cli_found": False, "auth_dead": True}, "disabled"),
```

and add this test after `test_run_tick_still_queues_when_paused`:

```python
def test_run_tick_queues_but_does_not_judge_when_disabled(conn, tmp_path, monkeypatch):
    add_message(conn, "s1", "2026-09-20T09:00:00")
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: {"s1": tmp_path / "s1.jsonl"})
    assert ph.run_tick(NOW, {**GATES, "enabled": False}) == {"skipped": "disabled"}
    assert ph.queue_counts(conn)["pending"] == 1
```

- [ ] **Step 2: Write the failing app tests.**
  1. Create `tests/test_app_settings.py`:

```python
import pytest

import app


class FakeThread:
    started = []

    def __init__(self, target=None, daemon=None, **kwargs):
        self.target = target

    def start(self):
        FakeThread.started.append(self.target)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(app, "_usage_cache", dict(app._usage_cache))


@pytest.fixture
def threads(monkeypatch):
    FakeThread.started = []
    monkeypatch.setattr(app.threading, "Thread", FakeThread)
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", True)
    return FakeThread.started


def test_settings_round_trip(threads):
    assert app.get_settings() == {"judge_enabled": False, "auto_refresh_token": False}
    assert app.post_settings({"auto_refresh_token": True})["auto_refresh_token"] is True
    assert app.get_settings()["auto_refresh_token"] is True


@pytest.mark.parametrize("bad", [{"judge_enabled": "yes"}, {"surprise": True}])
def test_settings_reject_bad_input(threads, bad):
    with pytest.raises(app.HTTPException) as err:
        app.post_settings(bad)
    assert err.value.status_code == 400


def test_turning_the_judge_on_starts_a_pass_right_away(threads):
    app.post_settings({"judge_enabled": True})
    app.post_settings({"judge_enabled": True})  # already on: no second pass
    assert threads == [app._hours_pass]


def test_no_pass_when_the_worker_is_off(threads, monkeypatch):
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", False)
    app.post_settings({"judge_enabled": True})
    assert threads == []


def test_changing_token_renewal_rechecks_the_token(threads):
    app._usage_cache["retry_after"] = 1e12
    app.post_settings({"auto_refresh_token": True})
    assert app._usage_cache["retry_after"] == 0.0
```

  2. In `tests/test_app_hours.py`:
     - Rename `test_hours_tick_refreshes_a_token_that_would_expire_mid_tick` to `test_hours_pass_renews_a_token_that_would_expire_mid_tick_when_allowed`; its body is unchanged from Task 4.
     - Add these tests:

```python
def test_hours_pass_leaves_the_token_alone_in_read_only_mode(timers, monkeypatch):
    seen = []
    monkeypatch.setattr(app.auth, "token_seconds_left", lambda: 100.0)
    monkeypatch.setattr(app.auth, "refresh_token", lambda force=False: pytest.fail("renewed"))
    monkeypatch.setattr(app, "_cached_five_hour_pct", lambda: 10.0)
    monkeypatch.setattr(app.person_hours, "run_tick", lambda now, gates: seen.append(gates))
    app._hours_tick()
    assert seen[0]["token_seconds_left"] == 100.0


def test_hours_pass_passes_the_judge_setting(timers, monkeypatch):
    seen = []
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", True)
    monkeypatch.setattr(app, "_cached_five_hour_pct", lambda: 10.0)
    monkeypatch.setattr(app.person_hours, "run_tick", lambda now, gates: seen.append(gates))
    app._hours_pass()
    settings.update({"judge_enabled": True})
    app._hours_pass()
    assert [g["enabled"] for g in seen] == [False, True]


def test_hours_endpoint_reports_disabled_until_opted_in(conn, monkeypatch):
    monkeypatch.setattr(app, "HOURS_WORKER_ENABLED", True)
    monkeypatch.setitem(ph._worker, "reason", None)
    assert app.hours(30)["worker"]["reason"] == "disabled"
    settings.update({"judge_enabled": True})
    assert app.hours(30)["worker"]["state"] == "idle"
```

- [ ] **Step 3: Run them and confirm they fail.**
Run: `python -m pytest tests/test_person_hours_worker.py tests/test_app_settings.py tests/test_app_hours.py -v`
Expected: `skip_reason() got an unexpected keyword argument 'enabled'`, `app` has no `get_settings` / `_hours_pass`, and similar.

- [ ] **Step 4: Implement the `person_hours.py` change.** Replace the start of `skip_reason` with the code below; the rest of the function is unchanged:

```python
def skip_reason(*, cli_found, auth_dead, ingest_running, five_hour_pct, token_seconds_left,
                calls_last_hour, enabled=True):
    """Why this tick shouldn't call the judge, or None to go ahead. First match wins."""
    if not enabled:  # the person hasn't opted in: judging spends their quota
        return "disabled"
    if not cli_found:
        return "unavailable"
```

In `run_tick`'s docstring, change "`gates` carries the server's state: auth_dead, ingest_running, five_hour_pct and token_seconds_left." to:

"`gates` carries the server's state: enabled (the person opted in), auth_dead, ingest_running, five_hour_pct and token_seconds_left."

- [ ] **Step 5: Implement the `app.py` changes.**
  1. Change the FastAPI import to `from fastapi import Body, FastAPI, HTTPException, Query`.
  2. Replace the comment above `HOURS_WORKER_ENABLED` with:
     ```python
     # PERSON_HOURS_WORKER=off stops the judge worker entirely (no queueing either), e.g. for a
     # test server. Otherwise it runs, and judges only once the person opts in (settings.py).
     ```
  3. Replace `_hours_tick` entirely with:
     ```python
     def _judge_enabled() -> bool:
         """Judge calls happen only when the person opted in and PERSON_HOURS_WORKER allows it."""
         return HOURS_WORKER_ENABLED and settings.get("judge_enabled")


     def _hours_pass():
         """One judge-worker pass. Skipped if another pass is still running."""
         if not _hours_lock.acquire(blocking=False):
             return
         try:
             left = auth.token_seconds_left()
             if (settings.get("auto_refresh_token") and left is not None
                     and left < person_hours.TOKEN_MIN_SECONDS):
                 # Renew here, in the process that already owns token renewal, so the CLI
                 # never starts a call on a token it would have to renew itself.
                 auth.refresh_token()
                 left = auth.token_seconds_left()
             person_hours.run_tick(datetime.now(), {
                 "enabled": _judge_enabled(),
                 "auth_dead": auth.rejected(),
                 "ingest_running": bool(_ingest_status.get("running")),
                 "five_hour_pct": _cached_five_hour_pct(),
                 "token_seconds_left": left,
             })
         except Exception as ex:
             print(f"[hours {datetime.now():%Y-%m-%d %H:%M:%S}] tick failed - "
                   f"{type(ex).__name__}: {ex}")
         finally:
             _hours_lock.release()


     def _hours_tick():
         """Run a judge-worker pass, then reschedule.

         Like _periodic_ingest, it reschedules in an outer finally: under the launcher, stdout is
         a strict cp1252 file, so even logging a failure can raise.
         """
         try:
             _hours_pass()
         finally:
             t = threading.Timer(person_hours.TICK_SECONDS, _hours_tick)
             t.daemon = True
             t.start()
     ```
  4. In `/api/hours`, replace `if not HOURS_WORKER_ENABLED:` with `if not _judge_enabled():`.
  5. Add after the `/api/connection/renew` endpoint:
     ```python
     @app.get("/api/settings")
     def get_settings():
         return settings.load()


     @app.post("/api/settings")
     def post_settings(changes: dict = Body(...)):
         """Change settings. Takes effect at once: both are read live."""
         before = settings.load()
         try:
             after = settings.update(changes)
         except ValueError as ex:
             raise HTTPException(400, str(ex))
         if after["judge_enabled"] and not before["judge_enabled"] and HOURS_WORKER_ENABLED:
             threading.Thread(target=_hours_pass, daemon=True).start()  # don't wait 5 minutes
         if after["auto_refresh_token"] != before["auto_refresh_token"]:
             _usage_cache["retry_after"] = 0.0  # re-check the token under the new rule
         return after
     ```

- [ ] **Step 6: Run the tests.**
Run: `python -m pytest tests/test_person_hours_worker.py tests/test_app_settings.py tests/test_app_hours.py -v && python -m pytest -q`
Expected: all pass; about 178 in total.

- [ ] **Step 7: Commit.**
```bash
git add person_hours.py app.py tests/test_person_hours_worker.py tests/test_app_settings.py tests/test_app_hours.py
git commit -m "Make the person-hours judge opt-in and add the settings API"
```

---

### Task 7: Local-only request guard

**Files:**
- Modify:
  - `app.py`: add `_blocked` and the `_local_only` middleware, right after `app.mount(...)` / `templates = ...`.
  - `environment.yml`: add `httpx`.
  - `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`: the "Local-API guard" section and the frontend bullet.
- Test: `tests/test_app_guard.py` (new)

**Interfaces:**
- Consumes: `/api/settings` (Task 6), used as the test target.
- Produces: `app._blocked(method: str, host: str | None, origin: str | None) -> str | None`. Middleware returns 403 `{"detail": reason}` when blocked.

- [ ] **Step 1: Add httpx for `TestClient`.** In `environment.yml`, add `  - httpx` on the line after `  - pytest`. Then install it:
Run: `conda install -n claude-usage-dashboard -c conda-forge httpx -y` (after the conda hook `eval`).
Expected: `python -c "import httpx"` succeeds in the env.

- [ ] **Step 2: Write the failing tests.** Create `tests/test_app_guard.py`:

```python
import pytest
from fastapi.testclient import TestClient

import app
import settings


@pytest.fixture
def client():
    return TestClient(app.app, base_url="http://127.0.0.1:8080")


@pytest.mark.parametrize("method, host, origin, blocked", [
    ("GET", "127.0.0.1:8080", None, False),
    ("GET", "localhost:8080", None, False),
    ("GET", "[::1]:8080", None, False),
    ("GET", "127.0.0.1", None, False),
    ("GET", "evil.example:8080", None, True),
    ("GET", "127.0.0.1.evil.example:8080", None, True),
    ("GET", None, None, True),
    ("POST", "127.0.0.1:8080", None, False),                        # curl, scripts
    ("POST", "127.0.0.1:8080", "http://127.0.0.1:8080", False),     # the dashboard itself
    ("POST", "localhost:8080", "http://localhost:8080", False),
    ("POST", "127.0.0.1:8080", "https://evil.example", True),
    ("POST", "127.0.0.1:8080", "null", True),
    ("POST", "127.0.0.1:8080", "http://127.0.0.1:9999", True),
    ("GET", "127.0.0.1:8080", "https://evil.example", False),       # reads are safe: no CORS
])
def test_blocked(method, host, origin, blocked):
    assert (app._blocked(method, host, origin) is not None) is blocked


def test_foreign_host_gets_403(client):
    assert client.get("/api/settings", headers={"host": "evil.example:8080"}).status_code == 403


def test_cross_origin_post_is_refused_and_changes_nothing(client):
    r = client.post("/api/settings", json={"judge_enabled": True},
                    headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert settings.load()["judge_enabled"] is False


def test_same_origin_and_originless_posts_are_served(client):
    r = client.post("/api/settings", json={"auto_refresh_token": True},
                    headers={"origin": "http://127.0.0.1:8080"})
    assert r.status_code == 200 and r.json()["auto_refresh_token"] is True
    assert client.post("/api/settings", json={"auto_refresh_token": False}).status_code == 200
```

`TestClient` without a `with` block doesn't run the startup handler, so no ingest or judge threads start.

- [ ] **Step 3: Run them and confirm they fail.**
Run: `python -m pytest tests/test_app_guard.py -v`
Expected: `AttributeError: module 'app' has no attribute '_blocked'`, and the 403 tests fail with 200.

- [ ] **Step 4: Implement the guard.** Add `from urllib.parse import urlsplit` to `app.py`'s imports. Then add directly after `templates = Jinja2Templates(...)`:

```python
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _blocked(method: str, host: str | None, origin: str | None) -> str | None:
    """Why the local server should refuse a request, or None to serve it.

    The Host must name this machine: a DNS-rebinding page reaches 127.0.0.1 under its own
    hostname, so this keeps other sites from reading the API. A state-changing request
    that carries an Origin must come from the dashboard's own origin. Browsers attach
    Origin to every cross-site POST, so a web page can't press Sign in or change settings;
    curl and scripts send no Origin and keep working.
    """
    try:
        hostname = urlsplit("//" + (host or "")).hostname
    except ValueError:
        hostname = None
    if hostname not in LOCAL_HOSTNAMES:
        return "host not allowed"
    if method not in SAFE_METHODS and origin is not None and origin != f"http://{host}":
        return "cross-origin request refused"
    return None


@app.middleware("http")
async def _local_only(request: Request, call_next):
    reason = _blocked(request.method, request.headers.get("host"), request.headers.get("origin"))
    if reason:
        return JSONResponse({"detail": reason}, status_code=403)
    return await call_next(request)
```

- [ ] **Step 5: Bring the spec in line.** In `docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md`:
  1. Replace the two bullets under "### Local-API guard" with:
     ```markdown
     - Every request must carry a `Host` of `127.0.0.1`, `localhost` or `[::1]` (any port). This blocks DNS rebinding.
     - Every state-changing request (not GET, HEAD or OPTIONS) that carries an `Origin` must come from the dashboard's own origin (`http://<Host>`). Browsers attach `Origin` to every cross-site POST, so a web page can't trigger Sign in or change settings. curl and scripts send no `Origin` and keep working, so `curl -X POST http://127.0.0.1:8080/api/refresh` still does.

     This replaces the per-launch `X-App-Token` header first planned, which would have broken the curl recovery steps for no extra protection.
     ```
  2. Under "Changes to existing code" → "Frontend", delete the bullet "Mutating requests send the per-launch token, read from a `<meta>` tag."
  3. Under "Testing", change "The guard returns 403 for a foreign `Host` and for a POST without the token." to "The guard returns 403 for a foreign `Host` and for a cross-origin POST, and serves an Origin-less POST."

- [ ] **Step 6: Run the tests.**
Run: `python -m pytest tests/test_app_guard.py -v && python -m pytest -q`
Expected: all pass; about 195 in total.

- [ ] **Step 7: Commit.**
```bash
git add app.py environment.yml tests/test_app_guard.py docs/superpowers/specs/2026-09-29-windows-desktop-app-design.md
git commit -m "Refuse requests from other hosts and cross-origin POSTs"
```

---

### Task 8: Connection banner in the dashboard

**Files:**
- Modify: `templates/index.html:33-55`, `static/dashboard.js:98-160`, `static/style.css:175-192`

**Interfaces:**
- Consumes:
  - `GET /api/quota` → `connection` (Task 5)
  - `POST /api/connection/login` → connection (Task 5)
  - `POST /api/connection/renew` → `{renewed, ...connection}` (Task 5)
- Produces: JS functions `renderConnection(c)`, `signIn()`, `renewToken(ev)`, and the global `connectionState`. Task 9 reuses `renderConnection` and `showDiagnostics`.

- [ ] **Step 1: Invoke the `wcag-a11y` skill** before touching markup.

- [ ] **Step 2: Replace the banner markup.** In `templates/index.html`:
  1. Replace the `<!-- Quota auth failure ... -->` block (the `div#authBanner` and its two spans) with:

```html
    <!-- Claude sign-in problems (hidden while connected or merely unreachable) -->
    <div class="auth-banner" id="authBanner" role="status" hidden>
      <span class="auth-banner-icon" aria-hidden="true">&#9888;</span>
      <div class="auth-banner-text">
        <strong id="authTitle"></strong>
        <span id="authDetail"></span>
      </div>
      <div class="auth-banner-actions" id="authActions"></div>
    </div>
```

  2. In the header, insert a status dot directly before `<div class="last-updated" id="lastUpdated">`:

```html
        <span class="conn-dot" id="connDot" aria-hidden="true"></span>
```

- [ ] **Step 3: Replace the banner logic in `static/dashboard.js`.**
  1. Delete `setAuthBanner` and the comment above it (lines 98–117).
  2. Add this section in its place:

```js
// ─── Claude connection ───────────────────────────────────────
// Without live quota the session charts silently switch their y-axis to raw tokens, which
// looks like a rendering quirk rather than a sign-in problem. Say what's wrong and offer
// the fix. The server decides the state (auth.connection_status); this only draws it.
const CONNECTION_ATTENTION = new Set(['not-installed', 'signed-out', 'login-required']);
const CONNECTION_QUIET = new Set(['connected', 'unavailable']);  // no banner for these
let connectionState = null;
let _connectionKey = '';

function renderConnection(c) {
  connectionState = c;
  const dot = document.getElementById('connDot');
  dot.className = 'conn-dot ' + (c.state === 'connected' ? 'ok'
    : CONNECTION_ATTENTION.has(c.state) ? 'bad' : 'warn');
  dot.title = c.title;
  if (typeof showDiagnostics === 'function' && document.getElementById('settingsPanel')?.classList.contains('open')) {
    showDiagnostics(c);
  }
  // The banner is a live region: rewrite it only when something changed.
  const key = [c.state, c.title, c.detail, c.actions.join(','), c.login_running].join('|');
  if (key === _connectionKey) return;
  _connectionKey = key;
  const banner = document.getElementById('authBanner');
  if (CONNECTION_QUIET.has(c.state)) {
    banner.hidden = true;
    return;
  }
  banner.classList.toggle('attention', CONNECTION_ATTENTION.has(c.state));
  document.getElementById('authTitle').textContent = c.title;
  document.getElementById('authDetail').textContent = c.detail;
  const actions = document.getElementById('authActions');
  actions.textContent = '';
  for (const a of c.actions) actions.append(connectionAction(a, c));
  banner.hidden = false;
}

function connectionAction(action, c) {
  if (action === 'install') {
    const link = document.createElement('a');
    link.className = 'btn-refresh';
    link.href = c.install_url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'How to install Claude Code';
    return link;
  }
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn-refresh';
  if (action === 'sign-in') {
    btn.textContent = c.login_running ? 'Waiting for sign-in…' : 'Sign in';
    btn.disabled = c.login_running;
    btn.addEventListener('click', signIn);
  } else {
    btn.textContent = 'Renew now';
    btn.addEventListener('click', renewToken);
  }
  return btn;
}

async function signIn() {
  try {
    renderConnection(await apiFetch('/api/connection/login', { method: 'POST' }));
  } catch (e) {
    document.getElementById('authDetail').textContent = "Couldn't open the sign-in window.";
  }
}

async function renewToken(ev) {
  const btn = ev.currentTarget;
  btn.disabled = true;
  btn.textContent = 'Renewing…';
  let renewed = false;
  try {
    renewed = (await apiFetch('/api/connection/renew', { method: 'POST' })).renewed;
  } catch (e) { /* reported below */ }
  _connectionKey = '';  // redraw the banner, which restores the button
  await fetchQuota();
  if (!renewed) {
    document.getElementById('authDetail').textContent = "Couldn't renew the token. Use Sign in instead.";
  }
}
```

  3. In `fetchQuota`, replace the lines from `const hasError = !!data.error;` through the closing brace of the `if (hasError) { ... } else { ... }` block with:

```js
    const hasError = !!data.error;
    const conn = data.connection;
    if (conn) renderConnection(conn);
    document.getElementById('gauge5h').classList.toggle('stale', hasError);
    document.getElementById('gauge7d').classList.toggle('stale', hasError);

    const hasData = data.five_hour_resets_at != null;
    document.getElementById('windowInfo').classList.toggle('stale', hasError);
    if (hasError) {
      // A sign-in problem is fixed by a file change the server spots on each poll, so keep
      // polling fast; slow down only for real outages and rate limits.
      const signInProblem = conn && !CONNECTION_QUIET.has(conn.state);
      document.getElementById('lastUpdated').textContent = signInProblem
        ? 'Quota paused'
        : 'Quota unavailable, retrying';
      _setQuotaPollRate(signInProblem ? QUOTA_POLL_NORMAL : QUOTA_POLL_ERROR);
      if (!hasData) return;
    } else {
      _setQuotaPollRate(QUOTA_POLL_NORMAL);
    }
```

- [ ] **Step 4: Replace the banner styles.** In `static/style.css`, replace the `/* ─── Quota auth banner ─── */` section (the `.auth-banner` and `.auth-banner code` rules) with:

```css
/* ─── Claude connection banner ─── */
.auth-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  background: rgba(201,169,110,0.08);
  border: 1px solid rgba(201,169,110,0.35);
  border-radius: var(--radius-sm);
  margin-bottom: 20px;
  font-size: 0.8rem;
  color: var(--text-primary);
}
.auth-banner[hidden] { display: none; }
.auth-banner.attention {
  background: rgba(217,95,95,0.1);
  border-color: rgba(217,95,95,0.4);
}
.auth-banner-icon { color: var(--claude-amber); font-size: 1rem; }
.auth-banner.attention .auth-banner-icon { color: var(--claude-red); }
.auth-banner-text { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 12rem; }
.auth-banner-text span { color: var(--text-secondary); }
.auth-banner-actions { display: flex; gap: 8px; flex-shrink: 0; }
.auth-banner-actions a { text-decoration: none; }
.btn-refresh:disabled { opacity: 0.6; cursor: default; }
.btn-refresh:focus-visible { outline: 2px solid var(--claude-orange); outline-offset: 2px; }

.conn-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-muted);
  flex-shrink: 0;
}
.conn-dot.ok   { background: #7BC87B; }
.conn-dot.warn { background: var(--claude-amber); }
.conn-dot.bad  { background: var(--claude-red); }
```

- [ ] **Step 5: Syntax check.**
Run: `node --check static/dashboard.js`
Expected: no output (exit 0).

- [ ] **Step 6: Verify in the browser.**
  1. Start the test instance (see "Test instance" above; restart it if it's already running, since Tasks 1–7 changed Python).
  2. With no credentials file, the banner reads "Not signed in to Claude Code." with a **Sign in** button (or "How to install Claude Code" if the CLI isn't on `PATH`). The header shows "Quota paused" and a red dot.
  3. Write each credentials file below in turn, in bash, with `C="$SP/h/.claude/.credentials.json"`. Confirm the banner follows within about 5 s, with no R key press:
     - `printf '{"claudeAiOauth":{"accessToken":"","refreshToken":"","expiresAt":0}}' > "$C"` → signed-out, and the detail mentions the desktop app.
     - `printf '{not json' > "$C"` → signed-out, "couldn't be read".
     - `printf '{"claudeAiOauth":{"accessToken":"x","refreshToken":"y","expiresAt":1000}}' > "$C"` → amber banner, "Your sign-in token expired at …", buttons **Renew now** and **Sign in**, amber dot.
     - A future-dated fake token: `python -c "import json,time;print(json.dumps({'claudeAiOauth':{'accessToken':'bogus','refreshToken':'y','expiresAt':int((time.time()+3600)*1000)}}))" > "$C"` → the server sends the bogus token to the usage API. Expect "Your Claude sign-in has expired." (a 401, recorded as refused). If the API answers 429 instead, the state is "unavailable" and no banner shows. Record which one happened in the task report, because it tells us how the API treats bad tokens.
  4. Click **Sign in**. A console window opens running `claude auth login --claudeai`, and the button reads "Waiting for sign-in…" and is disabled. The CLI may also open a claude.ai sign-in page in the default browser. Don't sign in: close that page and the console. Within about 5 s the button is back to "Sign in".
  5. Screenshot the amber and red banners at desktop width and at 375 px wide (`resize_window` preset `mobile`, then back to `desktop`). Check that nothing overflows.
  6. Stop the test instance.

- [ ] **Step 7: Commit.**
```bash
git add templates/index.html static/dashboard.js static/style.css
git commit -m "Show the Claude connection state with Sign in and Renew now in the dashboard"
```

---

### Task 9: Settings panel

**Files:**
- Modify: `templates/index.html` (header button, panel markup before `#panelOverlay`), `static/dashboard.js` (keydown handler, `closePanel`, `HOURS_PAUSE_TEXT`, new settings section), `static/style.css` (settings styles)

**Interfaces:**
- Consumes:
  - `GET/POST /api/settings` (Task 6)
  - `GET /api/connection` (Task 5)
  - `renderConnection` / `connectionState` (Task 8)
  - `loadHours`, `costUnit`, `fetchQuota` (existing)
- Produces: JS `openSettings()`, `closeSettings()`, `showDiagnostics(c)`, `saveSetting(key, input)`, `copyDiagnostics(btn)`.

- [ ] **Step 1: Invoke the `wcag-a11y` skill** if it isn't already loaded this session.

- [ ] **Step 2: Add the markup.** In `templates/index.html`:
  1. Insert a settings button in `.header-right`, directly before the Re-parse button:

```html
        <button class="btn-icon" id="btnSettings" type="button" onclick="openSettings()"
                aria-label="Settings" aria-haspopup="dialog" aria-controls="settingsPanel">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="3"/>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
          </svg>
        </button>
```

  2. Insert the panel directly before `<div class="panel-overlay" id="panelOverlay" ...>`:

```html
    <!-- Settings panel -->
    <div class="session-panel settings-panel" id="settingsPanel" role="dialog" aria-modal="true"
         aria-labelledby="settingsTitle" inert>
      <div class="session-panel-inner">
        <div class="session-panel-header">
          <h2 class="session-panel-title" id="settingsTitle">Settings</h2>
          <button class="panel-close" type="button" onclick="closeSettings()" aria-label="Close settings">✕</button>
        </div>

        <section class="settings-section" aria-label="Options">
          <label class="setting-row">
            <input type="checkbox" role="switch" id="setJudge" onchange="saveSetting('judge_enabled', this)">
            <span>
              <span class="setting-name">Estimate person-hours</span>
              <span class="setting-help">Claude reads a summary of each day's sessions and estimates how long the work would take someone without AI. It runs through your Claude Code sign-in, so it uses your plan's quota: about 1–2% of a week's quota to catch up on the last 90 days, then about 0.1–0.2% a week.</span>
            </span>
          </label>
          <label class="setting-row">
            <input type="checkbox" role="switch" id="setRenew" onchange="saveSetting('auto_refresh_token', this)">
            <span>
              <span class="setting-name">Renew my sign-in token automatically</span>
              <span class="setting-help">Keeps the quota gauges live when you haven't used Claude Code for a while. The dashboard writes the new token into Claude Code's credentials file. If Claude Code renews at the same moment, one of them can lose, and you'd have to sign in again.</span>
            </span>
          </label>
          <p class="setting-error" id="settingsError" role="alert"></p>
        </section>

        <section class="settings-section" aria-labelledby="connHeading">
          <h3 class="settings-heading" id="connHeading">Connection</h3>
          <p class="setting-name" id="connSummary"></p>
          <pre class="diagnostics" id="connDiagnostics"></pre>
          <div><button class="btn-refresh" type="button" onclick="copyDiagnostics(this)">Copy diagnostics</button></div>
        </section>
      </div>
    </div>
```

- [ ] **Step 3: Add the panel logic in `static/dashboard.js`.**
  1. In `HOURS_PAUSE_TEXT`, change these two entries:

```js
  token: 'Paused until Claude Code renews its sign-in token',
  disabled: 'Estimates are off. Turn them on in Settings.',
```

  2. Make `closePanel()` also close settings, so the shared overlay and Escape close whichever panel is open. Add as its last line:

```js
  closeSettings();
```

  3. Add a new section after the "Session detail panel" section:

```js
// ─── Settings panel ──────────────────────────────────────────
let _settingsReturnFocus = null;

// While the modal is open, keep keyboard focus out of the page behind it.
function setBackgroundInert(on) {
  for (const el of document.querySelectorAll('.header, .main, #authBanner, #ingestBanner, #sessionPanel')) {
    el.inert = on;
  }
}

async function openSettings() {
  _settingsReturnFocus = document.activeElement;
  const panel = document.getElementById('settingsPanel');
  panel.inert = false;
  panel.classList.add('open');
  document.getElementById('panelOverlay').classList.add('open');
  setBackgroundInert(true);
  document.getElementById('settingsError').textContent = '';
  panel.querySelector('.panel-close').focus();
  try {
    const [s, c] = await Promise.all([apiFetch('/api/settings'), apiFetch('/api/connection')]);
    document.getElementById('setJudge').checked = s.judge_enabled;
    document.getElementById('setRenew').checked = s.auto_refresh_token;
    showDiagnostics(c);
  } catch (e) {
    document.getElementById('settingsError').textContent = "Couldn't load settings.";
  }
}

function closeSettings() {
  const panel = document.getElementById('settingsPanel');
  if (!panel.classList.contains('open')) return;
  panel.classList.remove('open');
  panel.inert = true;
  setBackgroundInert(false);
  document.getElementById('panelOverlay').classList.remove('open');
  if (_settingsReturnFocus) _settingsReturnFocus.focus();
}

function showDiagnostics(c) {
  document.getElementById('connSummary').textContent = c.title;
  document.getElementById('connDiagnostics').textContent = c.diagnostics;
}

async function saveSetting(key, input) {
  const err = document.getElementById('settingsError');
  err.textContent = '';
  input.disabled = true;
  try {
    const s = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: input.checked }),
    });
    input.checked = s[key];
    if (key === 'judge_enabled' && costUnit === 'hours') loadHours();
    if (key === 'auto_refresh_token') fetchQuota();
  } catch (e) {
    input.checked = !input.checked;
    err.textContent = "Couldn't save that setting.";
  } finally {
    input.disabled = false;
    input.focus();
  }
}

async function copyDiagnostics(btn) {
  const pre = document.getElementById('connDiagnostics');
  try {
    await navigator.clipboard.writeText(pre.textContent);
    btn.textContent = 'Copied';
  } catch (e) {
    // Clipboard blocked: select the text so Ctrl+C works.
    const range = document.createRange();
    range.selectNodeContents(pre);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    btn.textContent = 'Press Ctrl+C to copy';
  }
  setTimeout(() => { btn.textContent = 'Copy diagnostics'; }, 2000);
}
```

- [ ] **Step 4: Add the styles.** Append to `static/style.css`:

```css
/* ─── Settings panel ─── */
.btn-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255,245,235,0.05);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  padding: 7px;
  cursor: pointer;
  transition: background 0.2s, color 0.2s;
}
.btn-icon:hover { background: rgba(255,245,235,0.1); color: var(--text-primary); }
.btn-icon:focus-visible,
.panel-close:focus-visible,
.setting-row input:focus-visible { outline: 2px solid var(--claude-orange); outline-offset: 2px; }
.settings-section { display: flex; flex-direction: column; gap: 14px; margin-bottom: 28px; }
.settings-heading { font-family: var(--font-display); font-size: 0.85rem; font-weight: 600; }
.setting-row { display: flex; gap: 12px; align-items: flex-start; cursor: pointer; }
.setting-row input {
  margin-top: 3px;
  width: 16px;
  height: 16px;
  accent-color: var(--claude-orange);
  flex-shrink: 0;
}
.setting-name { display: block; font-size: 0.85rem; color: var(--text-primary); }
.setting-help {
  display: block;
  font-size: 0.75rem;
  color: var(--text-secondary);
  line-height: 1.5;
  margin-top: 2px;
}
.setting-error { font-size: 0.75rem; color: var(--claude-red); }
.setting-error:empty { display: none; }
.diagnostics {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: var(--text-secondary);
  background: rgba(255,245,235,0.04);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
```

- [ ] **Step 5: Syntax check.**
Run: `node --check static/dashboard.js`
Expected: exit 0.

- [ ] **Step 6: Verify in the browser.** Start the test instance with a valid-looking expired credentials file in place (the `expiresAt:1000` one from Task 8), then:
  1. Click the gear. The panel slides in and focus lands on the close button. Tab cycles only through the panel's controls, since the background is inert.
  2. Escape closes the panel and focus returns to the gear. Clicking the overlay also closes it.
  3. Toggle "Estimate person-hours" on:
     - `$SP/h/data/settings.json` shows `"judge_enabled": true`.
     - After a page reload, the switch is still on.
     - With `PERSON_HOURS_WORKER=off` the hours card still says the estimates are off. That's expected for this test server; note it and move on.
  4. Toggle "Renew my sign-in token automatically" on and then off again. Do not leave it on: with the fake refresh token in the scratch file, the next poll would POST `y` to claude.ai. That's harmless, but it's pointless traffic.
  5. The Connection section shows the title and diagnostics text. The diagnostics contain no token value; search the page text for `"x"` next to "accessToken", which should be absent.
  6. Click **Copy diagnostics**. The button reads "Copied" or "Press Ctrl+C to copy" and reverts after 2 s.
  7. Check at 375 px width that the panel fills the screen width, with no horizontal scroll.
  8. Stop the test instance.

- [ ] **Step 7: Commit.**
```bash
git add templates/index.html static/dashboard.js static/style.css
git commit -m "Add a settings panel for the judge, token renewal and connection diagnostics"
```

---

### Task 10: Docs, end-to-end check with real credentials, memory

**Files:**
- Modify: `CLAUDE.md`
- Memory (outside the repo; not committed): `C:\Users\weaverjc\.claude\projects\C--Users-weaverjc-Projects-Personal-claude-usage-dashboard\memory\quota-auth-failure-mode.md`

**Interfaces:**
- Consumes: everything above.
- Produces: accurate docs; a verified read-only run against the real account.

- [ ] **Step 1: Update `CLAUDE.md`.** Make these edits:
  1. **Architecture block:**
     - Change the `app.py` line to: `app.py       FastAPI server — 20 API endpoints, local-only request guard, background ingest and person-hours threads, serves templates/`
     - Add these lines after `person_hours.py`:
       ```
       auth.py      Claude Code OAuth token — reads ~/.claude/.credentials.json, renews it on request, connection status, sign-in launcher
       paths.py     Where bundled files, data and logs live (repo when run from source, %LOCALAPPDATA% when frozen; CUD_DATA_DIR overrides data)
       settings.py  User settings in data/settings.json: judge opt-in, automatic token renewal
       ```
  2. **"Quota source" paragraph:** append:
     "The dashboard only *reads* the token by default. An expired token is reported as `token-expired` and never sent (the usage API answers expired tokens with 429, which used to look like rate limiting). The token is written back only when the user clicks **Renew now** or turns on automatic renewal in Settings (`auth.refresh_token`). `auth.connection_status()` turns the credentials file, the CLI lookup and the last fetch into one of six states: `not-installed`, `signed-out`, `login-required`, `token-expired`, `unavailable`, `connected`. `/api/quota` responses carry it as `connection`, and the dashboard draws the banner from it. Each quota poll stats the credentials file, and a change lifts any sign-in backoff at once, so a re-login shows up within about 5 s."
  3. **Key Paths table:**
     - Change the `~/.claude/.credentials.json` row to: `OAuth token for the quota API. Read-only unless automatic renewal is on or Renew now is clicked`.
     - Add a row: `` `data/settings.json` | User settings (auto-created on first change) ``.
  4. **Person-hours section:**
     - In the **Worker** bullet, add: "It judges only after the user turns on **Estimate person-hours** in Settings (off by default). While it's off, ticks still queue session-days, so scheduled runs are recognized, but make no calls."
     - In the token bullet, change "It first asks `_refresh_oauth_token()` to refresh early, so the CLI never has to." to "With automatic renewal on, it first asks `auth.refresh_token()` to renew early, so the CLI never has to; otherwise it waits for Claude Code to renew the token."
     - In the **Settings** bullet, change "`PERSON_HOURS_WORKER=off` disables the worker" to "`PERSON_HOURS_WORKER=off` stops the worker entirely (no queueing either)".
  5. **Gotchas:** add:
     - "**Local-only guard.** Requests whose Host isn't `127.0.0.1`, `localhost` or `[::1]` get 403, and so do state-changing requests whose `Origin` isn't the dashboard's own. curl sends no `Origin`, so `curl -X POST http://127.0.0.1:8080/api/refresh` still works."
     - Extend the "Tests never touch `data/usage.db`" bullet with: "`tests/conftest.py` also redirects `settings.SETTINGS_PATH` and `auth.CREDENTIALS_FILE` to per-test temp files and forbids `subprocess.run` / `subprocess.Popen`."
  6. **API Endpoints:**
     - Add `/api/connection`, `/api/connection/login` (POST), `/api/connection/renew` (POST), `/api/settings` (GET/POST) to the list.
     - Add: "`/api/connection` returns the connection state with `title`, `detail`, `actions` (`install` / `sign-in` / `renew`), `login_running` and a token-free `diagnostics` text. `/api/connection/login` opens `claude auth login --claudeai` in a console window (409 without the CLI). `/api/connection/renew` makes one forced token renewal. `/api/settings` reads or changes `judge_enabled` and `auto_refresh_token` (400 on unknown keys or non-boolean values)."

- [ ] **Step 2: End-to-end check against the real account (read-only).**
  1. Record the real credentials file's modification time:
     `stat -c '%y' ~/.claude/.credentials.json`
  2. Start a second instance with the **real** home but a scratch data dir and the judge off (Bash, `run_in_background: true`):
     ```bash
     SP="C:/Users/weaverjc/AppData/Local/Temp/claude/C--Users-weaverjc-Projects-Personal-claude-usage-dashboard--claude-worktrees-busy-visvesvaraya-0b60fc/98bd79c0-669a-4517-a3f2-aeaa826c399a/scratchpad"
     eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && \
       CUD_DATA_DIR="$(cygpath -w "$SP/real-data")" PERSON_HOURS_WORKER=off python app.py --port 8888
     ```
     The scratch data dir starts empty, so this instance ingests every transcript on its first start. The dashboard fills in after a minute or two; the connection state doesn't depend on it.
  3. Open `http://127.0.0.1:8888/`. The banner is hidden, the dot is green, and the gauges show percentages matching `:8080`. If the real token happens to be expired, you'll see the amber `token-expired` banner instead; that's also a pass. Don't click **Renew now** on the real account, and don't turn on automatic renewal here.
  4. Open Settings. The diagnostics show the real CLI path and credentials path, with no token text.
  5. After at least 2 minutes, run `stat -c '%y' ~/.claude/.credentials.json` again. The time is unchanged unless Claude Code itself renewed the token in the meantime. If it did change, check `logs/` and the instance output for `[oauth` lines: there must be none.
  6. Check the guard from the shell:
     - `curl -s -o /dev/null -w "%{http_code}\n" -H "Origin: https://evil.example" -X POST http://127.0.0.1:8888/api/refresh` → `403`
     - `curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8888/api/refresh` → `200`
  7. Stop the instance.

- [ ] **Step 3: Run the full suite one last time.**
Run: `python -m pytest -q`
Expected: all pass, no new warnings compared with the baseline's 2.

- [ ] **Step 4: Commit the docs.**
```bash
git add CLAUDE.md
git commit -m "Document connection status, settings, the local-only guard and read-only tokens"
```

- [ ] **Step 5: Update the memory file** (not in the repo). Append a dated paragraph to `quota-auth-failure-mode.md`:

  "**As of 2026-09-29 (desktop-app Phase 1, branch `claude/windows-app-standalone-afe11b`)**
  - The dashboard no longer renews the token unless 'Renew my sign-in token automatically' is on in Settings.
  - An expired token shows an amber 'token expired' banner with Renew now / Sign in, not frozen gauges.
  - A refused token shows a red 'sign-in expired' banner with a Sign in button that opens `claude auth login --claudeai`.
  - Recovery is picked up within about 5 s of the credentials file changing.
  - The diagnostics text in Settings replaces most of the manual steps above. Once this is live on :8080, the judge is off until toggled on in Settings."

- [ ] **Step 6: Flag the restart for the live server.** Tell the user:
  - `:8080` keeps running the old code until this branch is merged and the scheduled task restarts: `Stop-ScheduledTask -TaskName ClaudeUsageDashboard; Start-ScheduledTask -TaskName ClaudeUsageDashboard`.
  - After that restart, person-hours judging stays off until they turn on **Estimate person-hours** in Settings.
  - Automatic token renewal is off unless they turn it on.
