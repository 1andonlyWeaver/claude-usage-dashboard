# Windows Desktop App — Design Spec

Date: 2026-09-29
Status: approved design, not yet implemented. Built in four phases (see "Phases"), one PR each.

## Goal

Package the dashboard so a few colleagues and friends can install it on a Windows PC and run it like any other app: an installer, a Start menu entry, its own window, and a tray icon. Nobody should need conda, a repo checkout, or Task Scheduler.

A second goal came out of brainstorming. When the Claude sign-in stops working, the app should say so in plain words and offer a fix. Today, a dead or expired token shows up as "the percent usage stopped working", and the fix is a terminal command.

## Decisions

| Question | Decision |
|---|---|
| Audience | A few colleagues and friends. An unsigned installer with a SmartScreen warning is acceptable. |
| Shell | Tray icon keeps the server running; the app opens its own window on demand. Closing the window hides it to the tray. |
| Stack | All Python: PyInstaller (onedir, windowed), pywebview (WebView2), pystray, Inno Setup per-user installer (no admin). |
| Person-hours judge | Off by default. Users opt in from a settings panel. |
| Token refresh | Read-only by default: the app never writes `~/.claude/.credentials.json` on its own. A "Renew now" button refreshes once on request; automatic refresh is an opt-in advanced setting. |
| Autostart | On by default, tray only (window hidden), through the HKCU `Run` key. Toggled from the tray menu. |
| Updates | The app checks GitHub Releases and shows a notice. Users run the new installer over the old one. |
| License | MIT |

Rejected alternatives: a Tauri shell with the Python server as a sidecar (a second language and a two-process lifecycle to maintain), and Edge `--app` mode with embedded Python (lighter, but the window is really Edge and close-to-tray is awkward).

## What in the current code blocks distribution

- **Data paths are relative to the source files.** `db.py:11`, `ingest.py:60` and `app.py:35` all build paths from `__file__`, which puts the DB and quota cache inside the repo. An installed app can't write to its install folder.
- **CDN assets.** Chart.js comes from jsDelivr and the fonts from Google Fonts (`templates/index.html:7-9,318`). The dashboard is blank offline.
- **The app rewrites the user's Claude credentials.** `_refresh_oauth_token` (`app.py:211`) refreshes with Claude Code's OAuth client ID and rewrites `~/.claude/.credentials.json`. If that races with the CLI's own refresh, a rotated refresh token can sign the user out of Claude Code. Acceptable for one person who knows the risk; not for colleagues.
- **The judge spends quota by default.** `HOURS_WORKER_ENABLED` is on unless an env var says otherwise (`app.py:79`), and the first run starts a 90-day backfill.
- **No guard on the local API.** Nothing stops a web page in the user's browser from POSTing to `127.0.0.1`. That matters once endpoints can launch processes.
- **WSL detection assumes a distro named `Ubuntu`** (`ingest.py:33,239`).
- **No version, README, LICENSE or requirements.txt.** The repo is public; its one tag is `1.0.0` and it has no GitHub releases.

## Architecture

One process, `ClaudeUsageDashboard.exe`, built from a new `desktop.py`:

- **Main thread: pywebview's GUI loop.** It owns one window, created hidden when launched with `--background` (autostart). Closing the window hides it. A handler on the WinForms form cancels the close and hides the form when `CloseReason` is `UserClosing` and the person hasn't chosen Quit. pywebview's own `closing` event can't tell the close button from Windows signing out, and cancelling a sign-out would hold it up. The window uses `private_mode=False` and `storage_path=<data>/webview`; pywebview's default private mode would wipe the `localStorage` that `dashboard.js` uses.
- **Server thread.** A `uvicorn.Server` running the `app` object on `127.0.0.1`. Passing the object avoids the `"app:app"` import string, which breaks when frozen. It tries `preferred_port` (a setting, default 8765, used when it's between 1024 and 65535) and falls back to a free port from the OS for that run. A port Windows reserves fails with `PermissionError` and falls back the same way. The port in use goes into `<data>/runtime.json`.
- **Tray thread (pystray).** Menu: Open dashboard (default action), Open in browser, Start at login (checkbox), Quit. Check for updates joins it in Phase 4, with `updates.py`. The icon has a "needs attention" variant, driven by connection status. Every 30 s the tray reads its own server's `/api/connection` over 127.0.0.1, which also keeps the quota figures fresh while no window is polling. Calls to the app's own server bypass any HTTP proxy.
- **Single instance.** A named mutex, `Local\ClaudeUsageDashboard`, created through ctypes. A second launch reads `runtime.json`, POSTs `/api/app/show` to the running instance, and exits. `runtime.json` holds `{port, pid}`. The first copy deletes a stale one at start and its own at Quit. A second launch keeps trying for 10 seconds, in case the first copy is still starting, and hands it the foreground with `AllowSetForegroundWindow`. If the first copy has quit by then, the second launch takes over. Inno Setup's `AppMutex` uses the same name, so the installer asks the user to close a running copy.
- **Quit** sets `server.should_exit`, destroys the window and stops the tray. The existing ingest and judge threads are daemons and die with the process.
- **No WebView2:** pywebview would fall back to the old MSHTML engine, which can't run the dashboard. The app stops there and opens the dashboard in the default browser instead (not at a `--background` start), and the tray works as usual.

Running from source doesn't change: `python app.py --port 8080` and the Task Scheduler setup keep working for development. `scripts/launcher.py` and its Job Object stay for that setup; the desktop app is a single process, so it doesn't need them.

### New modules

| Module | Purpose |
|---|---|
| `paths.py` | The one place paths are decided. `DATA_DIR` is `$CUD_DATA_DIR`, else `%LOCALAPPDATA%\ClaudeUsageDashboard\data` when frozen, else the repo's `data/`. Also `LOG_DIR`, and `RESOURCE_DIR` (`sys._MEIPASS` when frozen, else the repo). Creates the data dir at startup. |
| `settings.py` | JSON settings in `DATA_DIR/settings.json`. Defaults, thread-safe load/save, read live so changes need no restart. Keys: `judge_enabled` (false), `auto_refresh_token` (false), `update_check` (true), `dismissed_version`, `preferred_port`. `PERSON_HOURS_WORKER=off` still forces the judge off. |
| `auth.py` | The OAuth code moved out of `app.py` (`_read_credentials`, `_write_credentials_atomic`, `_credentials_signature`, `_refresh_oauth_token`, `_read_oauth_token`, `_token_seconds_left`, now at `app.py:177-336` and `563-568`), plus the new `connection_status()`. |
| `desktop.py` | The desktop entry point described above. Flags: `--background`, `--smoke`. |
| `autostart.py` | Reads, writes and removes the HKCU `Run` value through `winreg`. Task Manager's Startup switch (`Explorer\StartupApproved\Run`) counts: an entry switched off there reads as off, and turning it on from the tray clears the switch. |
| `instance.py` | The single-instance mutex, `runtime.json`, and calls to the app's own server that never go through a proxy. |
| `tray.py` | The pystray icon, its menu, the attention variant and the sign-in notification. |
| `updates.py` | Fetches `releases/latest` from the GitHub API at most once a day and compares its tag with `__version__`. |
| `version.py` | `__version__` |

### Changes to existing code

- **`app.py`**
  - New endpoints: `/api/connection`, `/api/connection/login` (POST), `/api/connection/renew` (POST), `/api/settings` (GET, POST), `/api/app/info`, `/api/app/show` (POST). `/api/app/show` answers 409 when no desktop window is attached (`app.py` run on its own).
  - Local-API guard middleware (below).
  - `_hours_tick` is always scheduled and checks `settings.judge_enabled` on each tick. It pre-refreshes the token only when `auto_refresh_token` is on.
  - The deprecated `@app.on_event("startup")` becomes a lifespan handler.
  - `db.DB_PATH`, `ingest.DB_PATH`, `QUOTA_CACHE_FILE` and the static/templates dirs come from `paths`. Both `DB_PATH` constants stay, because `tests/conftest.py` patches both. Creating the data dir at startup also fixes the quota-cache write at `app.py:516`, which fails silently when `data/` is missing.
- **`person_hours.py`**: the judge subprocess env drops `ANTHROPIC_API_KEY`, so turning the judge on can never bill the API. With automatic refresh off, the existing `token` pause reason covers a token close to expiry. The CLI refreshes the token itself whenever it's used for real work.
- **`ingest.py`**: the `/home/*/.claude/projects` scan runs for every *running* WSL distro, not just `Ubuntu`. The list comes from `wsl.exe --list --running --quiet` (the output is UTF-16), asked at most every 10 seconds and skipped when `wsl.exe` is missing. Stopped distros are left alone: reading one through `\\wsl.localhost` starts it, and on 2026-09-30 the old scan was found starting Ubuntu on every 90-second ingest pass. (This replaces the first design, which listed all installed distros once per process.)
- **Frontend**
  - Chart.js is vendored into `static/vendor/` (MIT) and the fonts into `static/fonts/` (OFL).
  - The connection banner is rebuilt from `/api/connection`.
  - A settings panel, opened from a gear icon, holds the judge toggle, the automatic refresh toggle, connection diagnostics, and the version/update notice.

## Connection status

`auth.connection_status()` is a pure function of:

- the credentials file (missing, unparseable, blank tokens, or present with `expiresAt`),
- whether the `claude` CLI was found (reusing the lookup at `person_hours.py:364-377`),
- the known-dead refresh flag and the credentials-file signature,
- the last usage-API result.

It returns `{state, message, actions, token_expires_at, last_ok_at, detail}`. The first matching row wins:

| State | Condition | Message | Actions |
|---|---|---|---|
| `not-installed` | No credentials file and no CLI | Claude Code isn't set up on this PC. | Link to the install docs |
| `signed-out` | File missing or tokens blank, CLI present | Not signed in to Claude Code. If the file exists but its tokens are blank, the message adds that Claude Desktop can hold the sign-in and leave this file empty. | Sign in |
| `login-required` | The refresh token was rejected and the credentials file hasn't changed since, or the API returned 401 for a token that hasn't expired | Your Claude sign-in has expired. | Sign in |
| `token-expired` | `expiresAt` has passed and the refresh token isn't known to be dead | Sign-in token expired at 3:12 AM. It renews the next time you use Claude Code. | Renew now, Sign in |
| `unavailable` | Token is valid but the fetch failed (network, 5xx, genuine 429) | Couldn't reach Anthropic. Showing figures from 10:42; retrying. | none |
| `connected` | Last fetch succeeded | Green dot in the header | none |

Behavior that fixes today's confusing symptoms:

- **Don't call the usage API with an expired token.** Today a stale token often gets a 429 instead of a 401, and the UI reports it as rate limiting.
- **Recover in seconds.** The frontend polls `/api/quota` every 5 s. On each poll, stat the credentials file. If its `(mtime, size)` signature changed, clear the backoff and fetch right away. No 30-second wait, no pressing R.
- **Sign in** runs `claude auth login` in its own console window (`CREATE_NEW_CONSOLE`). The button stays disabled while that process runs. Success is detected from the credentials file changing.
- **Renew now** calls the existing `_refresh_oauth_token` once. On `invalid_grant` the state becomes `login-required`.
- **Tray alerts.** The icon switches to its attention variant. One Windows notification fires on entering `signed-out`, `login-required` or `not-installed`, including when the app starts in one of them. `token-expired` gets no notification: with automatic refresh off it happens most nights and needs no action.
- **Connection panel** shows the state, token expiry, last successful fetch, last error, CLI path, credentials path and modified time, and the app version. "Copy diagnostics" puts the same information on the clipboard for a user to send along. It never includes a token.

### Local-API guard

The app can now launch processes. Without a guard, a web page open in the user's browser could trigger them by POSTing to `127.0.0.1`. The guard has two checks:

- Every request must carry a `Host` of `127.0.0.1`, `localhost` or `[::1]` (any port). This blocks DNS rebinding.
- Every state-changing request (not GET, HEAD or OPTIONS) that carries an `Origin` must come from the dashboard's own origin (`http://<Host>`). Browsers attach `Origin` to every cross-site POST, so a web page can't trigger Sign in or change settings. curl and scripts send no `Origin` and keep working, so `curl -X POST http://127.0.0.1:8080/api/refresh` still does.

This replaces the per-launch `X-App-Token` header first planned, which would have broken the curl recovery steps for no extra protection.

## Packaging and release

- **`requirements.txt`**, pinned, used for the build: fastapi, `uvicorn` (plain, without `[standard]`, for a smaller bundle with fewer native wheels), jinja2, orjson, pywebview, pystray, Pillow. The new packages also go into `environment.yml`.
- **`packaging/ClaudeUsageDashboard.spec`** (PyInstaller): onedir, windowed, `.ico` icon, bundles `static/` and `templates/`, uvicorn hidden imports. pywebview ships its own PyInstaller hooks.
- **`packaging/installer.iss`** (Inno Setup)
  - Installs per user with `PrivilegesRequired=lowest`, into `{localappdata}\Programs\Claude Usage Dashboard`.
  - Adds a Start menu shortcut and an optional desktop icon.
  - A "Start at login" task, checked by default, writes the `Run` value with `--background`.
  - Uses `AppMutex`, and offers "Launch now" on the finish page.
  - The uninstaller removes the `Run` value and asks before deleting `%LOCALAPPDATA%\ClaudeUsageDashboard`. The default is to keep it.
- **`.github/workflows/release.yml`**, triggered by pushing a bare-number tag (`2.0.0`) and running on `windows-latest`:
  1. Install deps.
  2. Run pytest.
  3. Fail if the tag doesn't match `version.__version__`.
  4. Build with PyInstaller.
  5. Compile the installer with Inno Setup, installing it with choco if it's missing.
  6. Smoke-test the frozen exe with `--smoke`.
  7. `gh release create` with `ClaudeUsageDashboard-Setup-<ver>.exe` attached.
- **`--smoke`** starts the server on a free port, GETs `/` and `/api/connection`, and exits 0 or 1.
- **Logging**: UTF-8 `LOG_DIR/dashboard.log`, rotated to `.1` at startup once it passes 5 MB (`applog.py`). This applies to the frozen app and to source runs under the launcher or `pythonw`. Opening the log as UTF-8 avoids the cp1252 encoding failures the launcher used to have.
- **Docs**
  - `LICENSE` (MIT).
  - `README.md` for colleagues: what the app is, requirements (Windows 10/11, Claude Code installed and signed in), how to install, getting past SmartScreen ("More info", then "Run anyway"), what the judge costs, and where data lives.
  - `CLAUDE.md` updated with the new modules, paths and endpoints.
- **First installer release: `2.0.0`**, because the data location moves.

## Phases

Each phase is one PR with its own implementation plan.

1. **Connection status and settings**
   - Covers `paths.py`, `settings.py`, `auth.py`, the connection states, Sign in and Renew now, the API guard, and the settings panel with the judge and automatic refresh toggles.
   - Makes read-only token handling the default.
   - Works in the current source and Task Scheduler setup, so Jonathan gets the sign-in fix right away. Side effect: his judge turns off until he flips the new toggle.
2. **Portability**: vendored Chart.js and fonts, WSL distro detection, `version.py`, `requirements.txt`, `LICENSE`, `README.md`, dropping `ANTHROPIC_API_KEY` from the judge env, the lifespan handler, the UTF-8 log.
3. **Desktop shell**: `desktop.py` (window, tray, single instance, port choice, `--background`, `--smoke`), `autostart.py`, the tray attention icon and notification, `/api/app/*`.
4. **Packaging and release**: the PyInstaller spec, the Inno Setup script, `updates.py` and the update notice, the release workflow.
5. **Moving Jonathan's own setup over** (manual): stop the scheduled task, copy `data/usage.db` into `%LOCALAPPDATA%\ClaudeUsageDashboard\data\` (it holds the judged person-hours, which cost quota to regenerate), install, then disable the `ClaudeUsageDashboard` task. Don't run both copies with automatic refresh on, or they'll race on the refresh token.

## Testing

Unit tests (pytest), added with the phase that introduces each piece:

- `connection_status()` across all six states, including the blank-token file.
- A changed credentials signature clears the backoff.
- No usage-API call is made when `expiresAt` has passed.
- The guard returns 403 for a foreign `Host` and for a cross-origin POST, and serves an Origin-less POST.
- Settings defaults and live reads.
- `paths` when frozen and from source, by monkeypatching `sys.frozen` and `sys._MEIPASS`.
- Parsing `wsl.exe --list --quiet` UTF-16 output.
- Version comparison in `updates`.
- `autostart` with `winreg` mocked.
- The judge env has no `ANTHROPIC_API_KEY`.

Manual checks:

- **Phase 1**
  - Run a second instance on :8888.
  - Point `USERPROFILE` at a scratch home holding each kind of credentials file (missing, blank, expired, garbage), and check the banner, the actions and the diagnostics text for each.
  - Click Sign in and confirm a console opens with `claude auth login`.
- **Phase 3**, running `python desktop.py`:
  - Close to tray, then reopen from the tray.
  - Launch a second copy; it should focus the existing window.
  - `--background` starts hidden.
  - The "Start at login" toggle adds and removes the `Run` value.
  - Quit frees the port.
- **Phase 4**
  - The release job passes on a test tag. Delete that release and tag afterwards.
  - Install the build in Windows Sandbox, a clean machine without Claude Code. It should show `not-installed`, render with no network access to the CDN or fonts, and uninstall cleanly.
  - Install over the migrated DB on this machine and check the figures match the source instance.

## Known limitations

- **Read-only mode lets the quota gauge go stale.** It stays stale from the token's expiry until the user next runs Claude Code or clicks Renew now. Usage in claude.ai during that time isn't shown until then.
- **The installer is unsigned**, so SmartScreen warns on first run. PyInstaller builds are also sometimes flagged by antivirus.
- **The usage API and the refresh endpoint are undocumented.** If Anthropic changes them, the quota gauge stops working and the app falls back to `unavailable` or `login-required`. The token-based charts keep working.
- **Another Windows user signed in at the same time** (fast user switching) can open the first user's dashboard at `127.0.0.1`: the local-only guard checks the host, not the user. The second user's own copy falls back to another port.
