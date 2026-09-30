"""
The Claude Code OAuth token the dashboard reads from ~/.claude/.credentials.json.

The usage API needs the access token Claude Code keeps in that file. This module reads
it, renews it when asked, and tracks whether the stored credentials have been refused.
"""
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
INSTALL_DOCS_URL = "https://code.claude.com/docs/en/setup"

# OAuth token refresh — reverse-engineered from public Claude Code clients.
# If Anthropic changes these, refresh will fail and the dashboard falls back to
# needing the CLI to refresh the token.
OAUTH_REFRESH_URL = "https://claude.ai/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_REFRESH_LEEWAY = 120        # refresh if accessToken expires within this many seconds
TOKEN_REFRESH_MIN_INTERVAL = 60   # never attempt refresh more than once per this many seconds

# usable_token statuses that mean "no token to send", as opposed to a failed request.
AUTH_ERRORS = ("login-required", "no-credentials", "token-expired")

_token_refresh_lock = threading.Lock()
_last_token_refresh_attempt = 0.0  # monotonic time of last attempt; throttles refresh
_auth_dead = False  # True once a refresh returns invalid_grant — refresh token revoked, re-login required
_auth_dead_creds_sig = None  # credentials-file signature when _auth_dead was set; a change means a re-login may have landed


def redact(text: str) -> str:
    """Hide anything that looks like an Anthropic token, for text headed to a log."""
    return re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "sk-ant-…", text)


def read_credentials() -> dict | None:
    """Return the full credentials JSON, or None if missing/malformed."""
    try:
        return json.loads(CREDENTIALS_FILE.read_text())
    except Exception:
        return None


def _write_credentials_atomic(updated: dict) -> bool:
    """Atomically rewrite ~/.claude/.credentials.json. Returns True on success."""
    try:
        tmp = CREDENTIALS_FILE.with_suffix(CREDENTIALS_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(updated, indent=2))
        tmp.replace(CREDENTIALS_FILE)
        return True
    except Exception as ex:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[oauth {ts}] failed to write credentials: {ex}")
        return False


def credentials_signature() -> tuple | None:
    """(mtime, size) of the credentials file, or None if it can't be stat'd.

    Used to tell whether `claude auth login` has rewritten the file since we last found
    the stored refresh token to be expired.
    """
    try:
        st = CREDENTIALS_FILE.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def mark_rejected(sig: tuple | None) -> None:
    """Record that the credentials whose file had signature `sig` were refused.

    Pass the signature taken before the token was read: if Claude Code rewrote the file
    while the request was in flight, the refusal belongs to the old token, and rejected()
    stays False so the next poll tries the new one.
    """
    global _auth_dead, _auth_dead_creds_sig
    _auth_dead = True
    _auth_dead_creds_sig = sig


def rejected() -> bool:
    """True while the stored credentials are known bad: refused, and the file unchanged since."""
    return _auth_dead and credentials_signature() == _auth_dead_creds_sig


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
        creds = read_credentials()
        if not creds:
            return False
        oauth = creds.get("claudeAiOauth") or {}
        stored_refresh = oauth.get("refreshToken")
        if not stored_refresh:
            return False

        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": OAUTH_CLIENT_ID,
            "refresh_token": stored_refresh,
        }).encode("utf-8")
        # claude.ai is behind Cloudflare and 403s the default Python-urllib User-Agent.
        # Mimic a generic browser-ish UA so the request gets through.
        req = urllib.request.Request(
            OAUTH_REFRESH_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "claude-usage-dashboard/1.0",
            },
            method="POST",
        )
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            print(f"[oauth {ts}] refresh failed: HTTP {e.code} - {body[:300]}")
            # invalid_grant (or 401) means the stored refresh token is revoked/expired:
            # no amount of retrying will help — the user must re-login via the CLI.
            if "invalid_grant" in body or e.code == 401:
                mark_rejected(sig)
            return False
        except Exception as ex:
            print(f"[oauth {ts}] refresh failed: {ex}")
            return False

        new_access = payload.get("access_token")
        if not new_access:
            print(f"[oauth {ts}] refresh response missing access_token")
            return False

        expires_in = int(payload.get("expires_in") or 36000)
        new_expires_at = int(time.time() * 1000) + expires_in * 1000

        oauth["accessToken"] = new_access
        if payload.get("refresh_token"):
            oauth["refreshToken"] = payload["refresh_token"]
        oauth["expiresAt"] = new_expires_at
        creds["claudeAiOauth"] = oauth

        if not _write_credentials_atomic(creds):
            return False
        _auth_dead = False  # a fresh token was minted — clear any prior dead-token state
        _auth_dead_creds_sig = None
        print(f"[oauth {ts}] refreshed token (expires in {expires_in}s)")
        return True


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
        renewed = ((read_credentials() or {}).get("claudeAiOauth") or {}).get("accessToken")
        if renewed:
            return renewed, "ok"
    if seconds_left > 0:
        return token, "ok"  # close to expiry, but still good for this call
    return None, "login-required" if rejected() else "token-expired"


def token_seconds_left() -> float | None:
    """Seconds until the stored OAuth access token expires, or None without credentials."""
    oauth = (read_credentials() or {}).get("claudeAiOauth") or {}
    if not oauth.get("accessToken"):
        return None
    return (oauth.get("expiresAt") or 0) / 1000 - time.time()


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
