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

# OAuth token refresh — reverse-engineered from public Claude Code clients.
# If Anthropic changes these, refresh will fail and the dashboard falls back to
# needing the CLI to refresh the token.
OAUTH_REFRESH_URL = "https://claude.ai/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_REFRESH_LEEWAY = 120        # refresh if accessToken expires within this many seconds
TOKEN_REFRESH_MIN_INTERVAL = 60   # never attempt refresh more than once per this many seconds

_token_refresh_lock = threading.Lock()
_last_token_refresh_attempt = 0.0  # monotonic time of last attempt; throttles refresh
_auth_dead = False  # True once a refresh returns invalid_grant — refresh token revoked, re-login required
_auth_dead_creds_sig = None  # credentials-file signature when _auth_dead was set; a change means a re-login may have landed


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


def rejected() -> bool:
    """True while the stored credentials are known bad: refused, and the file unchanged since."""
    return _auth_dead and credentials_signature() == _auth_dead_creds_sig


def refresh_token() -> bool:
    """Refresh the OAuth access token using the stored refreshToken.

    Calls Anthropic's OAuth token endpoint and rewrites ~/.claude/.credentials.json
    with the new accessToken/expiresAt on success. Throttled to one attempt per
    TOKEN_REFRESH_MIN_INTERVAL seconds to prevent tight loops if refresh fails.
    Returns True on success.
    """
    global _last_token_refresh_attempt, _auth_dead, _auth_dead_creds_sig
    with _token_refresh_lock:
        if _auth_dead:
            if credentials_signature() == _auth_dead_creds_sig:
                # The stored refresh token is expired and nothing has rewritten the
                # credentials file since we learned that. Re-POSTing it every minute can
                # only fail again — wait for `claude auth login` to replace the file.
                return False
            # Credentials changed on disk, so a re-login may have landed. Retry now rather
            # than waiting out the throttle left over from the last doomed attempt.
            _auth_dead = False
            _auth_dead_creds_sig = None
            _last_token_refresh_attempt = 0.0
        now = time.monotonic()
        if now - _last_token_refresh_attempt < TOKEN_REFRESH_MIN_INTERVAL:
            return False
        _last_token_refresh_attempt = now

        creds = read_credentials()
        if not creds:
            return False
        oauth = creds.get("claudeAiOauth") or {}
        refresh_token = oauth.get("refreshToken")
        if not refresh_token:
            return False

        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": OAUTH_CLIENT_ID,
            "refresh_token": refresh_token,
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
                _auth_dead = True
                _auth_dead_creds_sig = credentials_signature()
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


def read_oauth_token() -> tuple[str | None, str]:
    """Return (accessToken, status), refreshing pre-emptively if it's about to expire.

    status is one of:
      'ok'             — a usable token is available
      'login-required' — token is expired and the refresh token is dead (re-login needed)
      'no-credentials' — no credentials file or no stored token
    """
    global _auth_dead, _auth_dead_creds_sig
    creds = read_credentials()
    if not creds:
        return None, "no-credentials"
    oauth = creds.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        return None, "no-credentials"
    expires_at_ms = oauth.get("expiresAt") or 0
    seconds_left = (expires_at_ms / 1000) - time.time()
    if seconds_left >= TOKEN_REFRESH_LEEWAY:
        _auth_dead = False  # comfortably-valid token present; clear any stale dead-token flag
        _auth_dead_creds_sig = None
        return token, "ok"
    # Expired or near-expiry — attempt a refresh (throttled internally).
    if refresh_token():
        creds = read_credentials() or {}
        token = (creds.get("claudeAiOauth") or {}).get("accessToken")
        return token, "ok"
    # Refresh didn't succeed. If the token is actually expired and the refresh token is
    # known-dead, the user must re-login; otherwise keep using the still-valid token.
    if seconds_left < 0 and _auth_dead:
        return token, "login-required"
    return token, "ok"


def token_seconds_left() -> float | None:
    """Seconds until the stored OAuth access token expires, or None without credentials."""
    oauth = (read_credentials() or {}).get("claudeAiOauth") or {}
    if not oauth.get("accessToken"):
        return None
    return (oauth.get("expiresAt") or 0) / 1000 - time.time()
