"""
FastAPI server for Claude Code usage dashboard.
"""
import asyncio
import json
import os
import threading
import time
import traceback
import urllib.request
import urllib.error
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import JSONResponse

import auth
import db
import paths
import person_hours
import settings

BASE_DIR = paths.RESOURCE_DIR  # static/ and templates/

USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_API_BETA_HEADER = "oauth-2025-04-20"
CACHE_MAX_AGE = 360       # seconds before re-fetching
CACHE_MIN_RETRY = 300     # minimum seconds between failed attempts (5 min)
CACHE_MAX_RETRY = 3600    # maximum retry backoff (1 hour)
AUTH_RETRY = 30           # short backoff for auth errors (login-required) so re-login is picked up quickly
QUOTA_CACHE_FILE = paths.DATA_DIR / "quota_cache.json"
QUOTA_CACHE_MAX_STALE = 600  # seconds: accept disk-cached data up to 10 min old on startup
# A quota percentage describes a fixed rolling window, so a cached figure is wrong — not
# merely stale — once that window has rolled over. _bound_stale_quota normally decides that
# from the reading's own resets_at; these lengths are the fallback bound for readings whose
# resets_at is missing or unparseable.
FIVE_HOUR_WINDOW = 5 * 3600
SEVEN_DAY_WINDOW = 7 * 24 * 3600

app = FastAPI(title="Claude Usage Dashboard")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

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

# Ingest state. _ingest_lock is held for the whole of every ingest run (periodic, startup,
# or /api/refresh), so two runs never write to the DB at once.
_ingest_lock = threading.Lock()
_ingest_status = {"running": False, "progress": 0, "total": 0, "done": False, "error": None}
_last_ingest_error = None  # last logged ingest failure, so a repeat skips the traceback

# Usage API cache: holds the last successful API response + metadata
_usage_cache: dict = {
    "data": None,        # parsed response dict or None
    "fetched_at": 0.0,   # monotonic time of last successful fetch
    "retry_after": 0.0,  # monotonic time before which we should not retry
    "error": None,       # last error string if data is None
    "fail_count": 0,     # consecutive failure count for exponential backoff
    "creds_sig": None,   # credentials-file signature seen on the last poll
    "last_ok_at": None,  # wall-clock time of the last successful fetch
}
_fetch_lock = asyncio.Lock()  # prevents concurrent API calls when cache is stale

# PERSON_HOURS_WORKER=off stops the judge worker entirely (no queueing either), e.g. for a
# test server. Otherwise it runs, and judges only once the person opts in (settings.py).
HOURS_WORKER_ENABLED = os.environ.get("PERSON_HOURS_WORKER", "on").lower() not in ("0", "off", "false")
_hours_lock = threading.Lock()

# Seed in-memory cache from disk on startup so restarts don't lose last known quota
try:
    _saved = json.loads(QUOTA_CACHE_FILE.read_text())
    _usage_cache["last_ok_at"] = _saved.get("time")
    if time.time() - _saved.get("time", 0) < QUOTA_CACHE_MAX_STALE:
        _usage_cache["data"] = _saved["data"]
        # Mark as stale so the next request will refresh, but non-None so fallback works
        _usage_cache["fetched_at"] = time.monotonic() - CACHE_MAX_AGE
except Exception:
    pass


# Throttle for quota snapshot writes (max 1 per 60 seconds)
_last_snapshot_time = 0.0


def _maybe_write_snapshot(five_pct: float, seven_pct: float) -> None:
    """Write a quota snapshot row if 60s have elapsed since the last write."""
    global _last_snapshot_time
    now = time.monotonic()
    if now - _last_snapshot_time < 60:
        return
    _last_snapshot_time = now
    try:
        ts = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        cutoff = (datetime.now() - timedelta(days=8)).strftime('%Y-%m-%dT%H:%M:%S')
        # closing() so a failed commit doesn't leave this connection holding its lock
        with closing(db.get_conn()) as conn:
            conn.execute(
                "INSERT INTO quota_snapshots (timestamp, five_hour_pct, seven_day_pct) VALUES (?, ?, ?)",
                (ts, five_pct, seven_pct),
            )
            conn.execute("DELETE FROM quota_snapshots WHERE timestamp < ?", (cutoff,))
            conn.commit()
    except Exception:
        pass


def _log_ingest_error(exc: Exception) -> None:
    """Log a failed ingest run.

    The traceback goes out only when the error differs from the last one logged, so a
    failure that repeats every 90s stays visible without flooding dashboard.log.
    """
    global _last_ingest_error
    msg = f"{type(exc).__name__}: {exc}"
    print(f"[ingest {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] failed - {msg}")
    if msg != _last_ingest_error:
        traceback.print_exception(exc)
    _last_ingest_error = msg


def _run_ingest_background(force: bool = False):
    from ingest import run_ingest
    global _ingest_status
    _ingest_status["running"] = True
    _ingest_status["error"] = None

    def progress_cb(i, total, path):
        _ingest_status["progress"] = i + 1
        _ingest_status["total"] = total

    try:
        with _ingest_lock:  # wait out a periodic run rather than write alongside it
            stats = run_ingest(progress_callback=progress_cb, force=force)
        _ingest_status["done"] = True
        _ingest_status["stats"] = stats
    except Exception as e:
        _ingest_status["error"] = str(e)
        _log_ingest_error(e)
    finally:
        _ingest_status["running"] = False


def _periodic_ingest():
    """Run an incremental ingest quietly in the background, then reschedule.

    Skips this tick if another ingest holds _ingest_lock; the next tick is 90s away.
    """
    try:
        from ingest import run_ingest
        if _ingest_lock.acquire(blocking=False):
            try:
                run_ingest(force=False)
            except Exception as e:
                _log_ingest_error(e)
            finally:
                _ingest_lock.release()
    finally:
        # Reschedule no matter what, even if logging itself fails (under the launcher,
        # stdout is a strict cp1252 file), or periodic ingest stops until a restart.
        t = threading.Timer(90, _periodic_ingest)
        t.daemon = True
        t.start()


def _fetch_usage_sync(_already_retried: bool = False) -> dict:
    """Call the Anthropic usage API synchronously. Returns a result dict:
    On success: {"ok": True, "data": {...}, "retry_after": None}
    On rate-limit: {"ok": False, "error": "rate-limited", "retry_after": <seconds>}
    On other failure: {"ok": False, "error": "http-<status>" or "network-error", "retry_after": None}
    Every result also carries "creds_sig": the credentials-file signature taken before the
    token was read, so the caller can tell whether a refused token has since been replaced.

    Without a usable token the error is auth.usable_token's status and nothing is sent.
    A 401 means the stored token was refused: with automatic renewal on, renew once and
    retry. Otherwise, or when the renewal fails other than in transit, record the refusal
    so the dashboard asks the person to sign in.
    """
    auto = settings.get("auto_refresh_token")
    sig = auth.credentials_signature()  # the file this token comes from, for mark_rejected
    token, auth_status = auth.usable_token(auto_refresh=auto)
    if not token:
        return {"ok": False, "error": auth_status, "retry_after": None, "creds_sig": sig}

    req = urllib.request.Request(
        USAGE_API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": USAGE_API_BETA_HEADER,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            rate_headers = {k: resp.headers[k] for k in resp.headers if 'ratelimit' in k.lower() or 'retry-after' in k.lower()}
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if rate_headers:
                print(f"[quota {ts}] OK - rate headers: {rate_headers}")
            else:
                print(f"[quota {ts}] OK - no rate-limit headers in response")
            return {"ok": True, "data": data, "retry_after": None, "rate_headers": rate_headers,
                    "creds_sig": sig}
    except urllib.error.HTTPError as e:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if e.code == 429:
            all_headers = {k: e.headers[k] for k in e.headers}
            retry_after_raw = e.headers.get("Retry-After", "")
            try:
                retry_secs = int(retry_after_raw)
            except (ValueError, TypeError):
                retry_secs = 300
            print(f"[quota {ts}] 429 - Retry-After: {retry_after_raw!r}, all headers: {all_headers}")
            return {"ok": False, "error": "rate-limited", "retry_after": retry_secs, "creds_sig": sig}
        if e.code == 401:
            if auto and not _already_retried:
                print(f"[quota {ts}] HTTP 401 - attempting OAuth refresh")
                if auth.refresh_token():
                    return _fetch_usage_sync(_already_retried=True)
                if not auth.last_refresh_transient() and auth.credentials_signature() == sig:
                    # Nothing to renew with, or the renewed token couldn't be saved: only a
                    # sign-in helps. A network or server failure stays http-401 and is
                    # retried; a changed file holds a newer token to try instead.
                    auth.mark_rejected(sig)
            else:
                # Read-only, or a freshly renewed token refused too: nothing here can fix it.
                auth.mark_rejected(sig)
            if auth.rejected():
                return {"ok": False, "error": "login-required", "retry_after": None, "creds_sig": sig}
        print(f"[quota {ts}] HTTP {e.code}")
        return {"ok": False, "error": f"http-{e.code}", "retry_after": None, "creds_sig": sig}
    except Exception as ex:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # The error code goes out through /api/connection and the diagnostics, so it stays a
        # fixed string; some exception messages quote the request headers, token included.
        print(f"[quota {ts}] exception: {auth.redact(str(ex))}")
        return {"ok": False, "error": "network-error", "retry_after": None, "creds_sig": sig}


def _resets_at_passed(value) -> bool:
    """True when an ISO-8601 reset timestamp lies in the past.

    Unparseable, absent, or naive timestamps return False, leaving the age bound as the
    fallback rather than blanking a figure we can't actually date.
    """
    if not value:
        return False
    try:
        return datetime.fromisoformat(value) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _bound_stale_quota(data: dict, age: float) -> dict:
    """Null out cached percentages that no longer describe the current window.

    A pct is dropped when its own resets_at has passed (the window has rolled over) or
    when `age` — how old `data` is, in seconds — exceeds the window length. The resets_at
    check is the load-bearing one: a figure fetched an hour before a reset is wrong two
    hours later even though its age is nowhere near the window. Keeping such a figure
    shows a confidently wrong number (a logged-out dashboard frozen at last week's 10%
    looks identical to a working one), so the pct is dropped and the UI renders the "—"
    placeholder. resets_at is left intact so the frontend can still derive the current
    window boundaries.
    """
    out = dict(data)
    for prefix, window in (("five_hour", FIVE_HOUR_WINDOW), ("seven_day", SEVEN_DAY_WINDOW)):
        if out.get(prefix + "_pct") is None:
            continue
        if age >= window or _resets_at_passed(out.get(prefix + "_resets_at")):
            out[prefix + "_pct"] = None
    return out


def _read_disk_cache_data() -> dict | None:
    """Read the quota disk cache, or None on failure.

    Data is returned regardless of age so a transient failure still shows the last known
    figures, but percentages older than the window they describe are blanked by
    _bound_stale_quota rather than served as if they were current.
    """
    try:
        saved = json.loads(QUOTA_CACHE_FILE.read_text())
        data = saved.get("data") or None
        if not data:
            return None
        return _bound_stale_quota(data, max(0.0, time.time() - saved.get("time", 0)))
    except Exception:
        return None


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


async def _get_usage_data() -> dict:
    """Return cached usage data, refreshing from the API when the cache is stale.

    Returns a dict with keys: five_hour_pct, five_hour_resets_at,
    seven_day_pct, seven_day_resets_at, plus optional extra_usage_* fields.
    On error, includes an "error" key.
    """
    now = time.monotonic()
    cache = _usage_cache
    _retry_now_if_credentials_changed(cache)

    # Return in-memory cache if still fresh
    if cache["data"] and (now - cache["fetched_at"]) < CACHE_MAX_AGE:
        return cache["data"]

    # Respect rate-limit backoff
    if now < cache["retry_after"]:
        if cache["data"]:
            return _bound_stale_quota(cache["data"], now - cache["fetched_at"])
        # No in-memory data — fall back to disk cache so UI shows real values
        disk = _read_disk_cache_data()
        if disk:
            return {**disk, "error": cache["error"] or "rate-limited"}
        return {"error": cache["error"] or "rate-limited",
                "five_hour_pct": 0, "seven_day_pct": 0}

    # Serialize concurrent fetches: only one caller hits the API at a time;
    # others wait on the lock and then re-check the cache (which will be fresh).
    async with _fetch_lock:
        # Re-check after acquiring lock — another waiter may have just fetched
        now = time.monotonic()
        if cache["data"] and (now - cache["fetched_at"]) < CACHE_MAX_AGE:
            return cache["data"]
        if now < cache["retry_after"]:
            if cache["data"]:
                return _bound_stale_quota(cache["data"], now - cache["fetched_at"])
            disk = _read_disk_cache_data()
            if disk:
                return {**disk, "error": cache["error"] or "rate-limited"}
            return {"error": cache["error"] or "rate-limited",
                    "five_hour_pct": 0, "seven_day_pct": 0}

        # Fetch in a thread so we don't block the event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _fetch_usage_sync)

        if result["ok"]:
            raw = result["data"]
            five = raw.get("five_hour") or {}
            seven = raw.get("seven_day") or {}
            extra = raw.get("extra_usage") or {}
            parsed = {
                "five_hour_pct": five.get("utilization", 0),
                "five_hour_resets_at": five.get("resets_at"),
                "seven_day_pct": seven.get("utilization", 0),
                "seven_day_resets_at": seven.get("resets_at"),
                "extra_usage_enabled": extra.get("is_enabled", False),
                "extra_usage_limit": extra.get("monthly_limit"),
                "extra_usage_used": extra.get("used_credits"),
                "extra_usage_utilization": extra.get("utilization"),
            }
            cache["data"] = parsed
            cache["fetched_at"] = now
            cache["retry_after"] = 0.0
            cache["error"] = None
            cache["fail_count"] = 0
            cache["last_ok_at"] = time.time()
            try:
                QUOTA_CACHE_FILE.write_text(json.dumps({"data": parsed, "time": time.time()}))
            except Exception:
                pass
            _maybe_write_snapshot(  # write quota snapshot for calibration
                parsed["five_hour_pct"], parsed["seven_day_pct"]
            )
            return parsed
        else:
            # Exponential backoff for transient failures; 401 stays flat (auth errors
            # don't benefit from long waits and may resolve on the next poll).
            cache["fail_count"] = cache.get("fail_count", 0) + 1
            api_retry = result.get("retry_after") or 0
            if result.get("error") in auth.AUTH_ERRORS:
                # Auth errors resolve via re-login, not waiting. Re-check often and cheaply
                # (the dead-token short-circuit spends no network) so recovery is near-automatic.
                backoff = AUTH_RETRY
            elif result.get("error") == "http-401":
                backoff = CACHE_MIN_RETRY
            else:
                backoff = min(CACHE_MIN_RETRY * (2 ** (cache["fail_count"] - 1)), CACHE_MAX_RETRY)
            retry_secs = max(api_retry, backoff)
            if (result["error"] in (*auth.AUTH_ERRORS, "http-401")
                    and auth.credentials_signature() != result["creds_sig"]):
                # The refused token has been replaced since it was read (Claude Code renewed
                # it mid-request). Another poller may already have recorded the new file, in
                # which case _retry_now_if_credentials_changed won't lift a backoff: skip it.
                retry_secs = 0
            cache["retry_after"] = now + retry_secs
            # An unchanging failure logged every poll grew dashboard.log past 45 MB during
            # a multi-day logged-out stretch. Log the transition, then only a periodic
            # heartbeat, so an ongoing outage stays visible without flooding the file.
            if cache["error"] != result["error"] or cache["fail_count"] % 60 == 1:
                print(f"[quota {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] fetch failed ({result['error']}), fail #{cache['fail_count']}, retry in {retry_secs}s")
            cache["error"] = result["error"]
            # Return stale in-memory data if available, then try disk cache, then error shell
            if cache["data"]:
                return {**_bound_stale_quota(cache["data"], now - cache["fetched_at"]),
                        "error": result["error"]}
            disk = _read_disk_cache_data()
            if disk:
                return {**disk, "error": result["error"]}
            return {"error": result["error"], "five_hour_pct": 0, "seven_day_pct": 0}


def _refetch_quota_now() -> None:
    """Make the next quota poll call the API: clear the backoff and mark the cache stale.

    fetched_at moves back only as far as stale, never to 0: if the refetch fails, the
    cached figures are still judged by their real age, not by how long the PC has been up.
    """
    _usage_cache["retry_after"] = 0.0
    _usage_cache["fail_count"] = 0
    _usage_cache["fetched_at"] = min(_usage_cache["fetched_at"], time.monotonic() - CACHE_MAX_AGE)


def _cached_five_hour_pct() -> float | None:
    """Last known 5-hour quota %, or None when unknown or from a window that has reset."""
    data = _usage_cache.get("data")
    if data:
        return _bound_stale_quota(data, time.monotonic() - _usage_cache["fetched_at"]).get("five_hour_pct")
    disk = _read_disk_cache_data()
    return disk.get("five_hour_pct") if disk else None


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
            # A re-login rewrites the credentials file; don't stay paused until a
            # quota poll happens to notice.
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


@app.on_event("startup")
async def startup():
    """Kick off ingest if DB is missing or stale, then schedule periodic ingest and judging."""
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)  # the quota cache write assumes it exists
    stats = db.db_stats()
    if stats.get("exists"):
        # Existing DBs get newer tables (person_hour_estimates) before any request or the
        # startup ingest needs them. run_ingest repeats this every 90 s, so a failure here
        # (e.g. another process holding the write lock) is logged, not fatal.
        try:
            from ingest import _open_db, init_db
            with _open_db() as conn:
                init_db(conn)
        except Exception as ex:
            print(f"[startup {datetime.now():%Y-%m-%d %H:%M:%S}] schema check failed - "
                  f"{type(ex).__name__}: {ex}")
    if not stats.get("exists") or stats.get("message_count", 0) == 0:
        thread = threading.Thread(target=_run_ingest_background, daemon=True)
        thread.start()
    else:
        _ingest_status["done"] = True
    if settings.get("auto_refresh_token"):
        # Renew a token that expired while the machine was off, before the first poll needs it.
        threading.Thread(target=auth.usable_token, args=(True,), daemon=True).start()
    t = threading.Timer(90, _periodic_ingest)
    t.daemon = True
    t.start()
    if HOURS_WORKER_ENABLED:
        h = threading.Timer(60, _hours_tick)
        h.daemon = True
        h.start()


def _asset_url(rel_path: str) -> str:
    """Cache-busted URL for a file under static/, e.g. /static/dashboard.js?v=<token>.

    The token is derived from the file's mtime+size, so it changes whenever the
    asset changes (edit, pull, checkout). This forces browsers to refetch instead
    of serving a stale cached copy — StaticFiles sends no Cache-Control header, so
    without this a browser can keep an old dashboard.js across reloads.
    """
    f = BASE_DIR / "static" / rel_path
    try:
        st = f.stat()
        token = f"{int(st.st_mtime)}-{st.st_size}"
    except OSError:
        token = "0"
    return f"/static/{rel_path}?v={token}"


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"asset_url": _asset_url})


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
    try:
        auth.launch_login(cli)
    except OSError as ex:  # the CLI moved since it was found, or Windows wouldn't start it
        print(f"[login {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] couldn't start {cli}: {ex}")
        raise HTTPException(500, f"Couldn't start the sign-in window ({cli}): {ex.strerror or ex}")
    return _connection()


@app.post("/api/connection/renew")
async def connection_renew():
    """One token renewal the person asked for, then a fresh fetch with the result."""
    renewed = await asyncio.get_running_loop().run_in_executor(
        None, lambda: auth.refresh_token(force=True))
    if renewed:
        _refetch_quota_now()
    await _get_usage_data()
    return {"renewed": renewed, **_connection()}


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


@app.get("/api/ingest-status")
async def ingest_status():
    return _ingest_status


@app.post("/api/refresh")
async def refresh(force: bool = Query(default=False)):
    """Trigger a re-ingest of JSONL files.

    Use ?force=true to clear ingest_meta and re-process all files (needed after
    schema migrations or project name changes).

    Also resets the quota fetch backoff so a stuck quota error is retried immediately.
    """
    _refetch_quota_now()

    if _ingest_status.get("running"):
        return {"message": "Ingest already running"}
    thread = threading.Thread(target=lambda: _run_ingest_background(force=force), daemon=True)
    thread.start()
    return {"message": "Ingest started", "force": force}


@app.get("/api/daily")
async def daily(days: int = 90):
    return db.daily_tokens(days)


@app.get("/api/projects")
async def projects(days: int = 90):
    return db.by_project(days)


@app.get("/api/models")
async def models(days: int = 90):
    return db.by_model(days)


@app.get("/api/heatmap")
async def heatmap(days: int = 90):
    return db.session_heatmap(days)


@app.get("/api/sessions")
def sessions(days: int = 30):
    return db.session_list(days)


@app.get("/api/session/{session_id}")
async def session_detail(session_id: str):
    data = db.session_detail(session_id)
    if not data:
        raise HTTPException(404, "Session not found")
    return data


@app.get("/api/session/{session_id}/hours")
def session_hours(session_id: str):
    """Per-day person-hours for one session (empty for Desktop sessions)."""
    return db.session_hours(session_id)


@app.get("/api/hours")
def hours(days: int = 30):
    """Person-hours for the cost card's hours view, plus the judge worker's state."""
    data = db.person_hours(days)
    with closing(db.get_conn()) as conn:
        counts = person_hours.queue_counts(conn)
    worker = person_hours.worker_status(counts)
    if not _judge_enabled():
        worker = {**worker, "state": "paused", "reason": "disabled"}
    data["worker"] = worker
    return data


@app.get("/api/rate")
async def rate(hours: int = 3):
    return db.recent_rate(hours)


@app.get("/api/cost")
async def cost(days: int = 30):
    return db.estimate_cost(days)


@app.get("/api/sources")
async def sources(days: int = 90):
    return db.by_source(days)


@app.get("/api/stats")
async def stats():
    return db.db_stats()


@app.get("/api/window")
async def window(
    type: str = Query(default="5h", pattern="^(5h|7d)$"),
    group_by: str = Query(default="none", pattern="^(none|token_type|project|model)$"),
):
    """Return token data bucketed within the current quota window.

    The window boundaries are derived from the Anthropic usage API reset times.
    Timestamps are converted to local time to match the DB's timestamp column.

    Returns:
        window_start, window_end: local-time ISO strings
        quota_pct: percentage of quota used, or None when that is unknown
            (quota API unavailable, or the cached reading belongs to a window that
            has since reset). 0 is a real reading — a window that just reset — and
            must stay distinguishable from None.
        bucket_minutes: bucket size used
        buckets: list of {time, group, tokens}
    """
    quota = await _get_usage_data()

    # If quota API returned an error, try the disk cache for window boundaries
    if quota.get("error") and not quota.get("five_hour_resets_at"):
        # Read via _read_disk_cache_data so percentages whose window has already elapsed
        # come back as None and are skipped, rather than seeding the chart with a figure
        # from a window that has since reset.
        disk = _read_disk_cache_data() or {}
        for key in ("five_hour_resets_at", "seven_day_resets_at",
                    "five_hour_pct", "seven_day_pct"):
            if not quota.get(key) and disk.get(key) is not None:
                quota[key] = disk[key]

    now_local = datetime.now()
    if type == "5h" and quota.get("five_hour_resets_at"):
        try:
            resets_utc = datetime.fromisoformat(quota["five_hour_resets_at"])
            resets_local = resets_utc.astimezone().replace(tzinfo=None)
            quota_pct = quota.get("five_hour_pct")
            period = timedelta(hours=5)
            if resets_local <= now_local:
                # Cached resets_at has already passed — advance to the current window
                while resets_local <= now_local:
                    resets_local += period
                quota_pct = None  # old pct measured the previous window; this one is unknown
            window_end = resets_local
            window_start = resets_local - period
        except Exception:
            window_end = now_local
            window_start = now_local - timedelta(hours=5)
            quota_pct = quota.get("five_hour_pct")
    elif type == "7d" and quota.get("seven_day_resets_at"):
        try:
            resets_utc = datetime.fromisoformat(quota["seven_day_resets_at"])
            resets_local = resets_utc.astimezone().replace(tzinfo=None)
            quota_pct = quota.get("seven_day_pct")
            period = timedelta(days=7)
            if resets_local <= now_local:
                # Cached resets_at has already passed — advance to the current window
                while resets_local <= now_local:
                    resets_local += period
                quota_pct = None  # old pct measured the previous window; this one is unknown
            window_end = resets_local
            window_start = resets_local - period
        except Exception:
            window_end = now_local
            window_start = now_local - timedelta(days=7)
            quota_pct = quota.get("seven_day_pct")
    else:
        window_end = now_local
        window_start = now_local - (timedelta(hours=5) if type == "5h" else timedelta(days=7))
        pct_key = "five_hour_pct" if type == "5h" else "seven_day_pct"
        quota_pct = quota.get(pct_key)

    bucket_minutes = 5 if type == "5h" else 60

    # Snap window_start to the nearest bucket boundary so the frontend's generated
    # time axis aligns with SQLite's bucket timestamps (which floor to bucket edges).
    window_start = window_start.replace(second=0, microsecond=0)
    window_start = window_start.replace(minute=(window_start.minute // bucket_minutes) * bucket_minutes)

    group_by_param = None if group_by == "none" else group_by

    ws = window_start.strftime('%Y-%m-%dT%H:%M:%S')
    we = window_end.strftime('%Y-%m-%dT%H:%M:%S')

    buckets = db.window_tokens(ws, we, bucket_minutes, group_by_param)
    gap_info = db.detect_other_pct(ws, we, type)

    return {
        "window_start": ws,
        "window_end": we,
        "quota_pct": quota_pct,
        "bucket_minutes": bucket_minutes,
        "buckets": buckets,
        "value_type": "cost" if group_by_param == "model" else "tokens",
        "other_pct": gap_info["other_pct"],
        "has_snapshots": gap_info["has_snapshots"],
    }


if __name__ == "__main__":
    import sys
    import uvicorn
    import argparse

    # When launched via pythonw.exe, stdout/stderr are None — redirect to log file
    if sys.stdout is None or sys.stderr is None:
        log_dir = paths.LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "dashboard.log", "a", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run("app:app", host="127.0.0.1", port=args.port, reload=False)
