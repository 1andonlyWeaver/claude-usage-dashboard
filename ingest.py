"""
Parse Claude Code session JSONL files into SQLite for fast querying.
Supports incremental updates - only re-parses changed files.
"""
import sqlite3
import os
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    import orjson as json_lib
    def loads(s):
        return json_lib.loads(s)
except ImportError:
    import json as json_lib
    def loads(s):
        return json_lib.loads(s)

PROJECTS_DIR = Path(os.path.expanduser("~")) / ".claude" / "projects"
DESKTOP_SESSIONS_DIR = Path(os.environ.get("APPDATA", "")) / "Claude" / "local-agent-mode-sessions"


def _get_wsl_projects_dir():
    """Return the WSL ~/.claude/projects Path if accessible, else None.

    Scans /home/ inside the Ubuntu distro via UNC path rather than running
    wsl.exe, so it works regardless of whether the Linux username matches the
    Windows username (e.g. on a different machine).
    """
    for prefix in [r"\\wsl.localhost\Ubuntu", r"\\wsl$\Ubuntu"]:
        home_root = Path(prefix) / "home"
        try:
            if not home_root.exists():
                continue
            for user_dir in home_root.iterdir():
                wsl_projects = user_dir / ".claude" / "projects"
                try:
                    if wsl_projects.exists():
                        return wsl_projects
                except (OSError, PermissionError):
                    pass
            break  # found the UNC prefix, no need to try the fallback
        except (OSError, PermissionError):
            pass
    return None


def get_project_dirs() -> list:
    """Return list of all ~/.claude/projects roots to ingest (Windows + any WSL distros)."""
    dirs = [PROJECTS_DIR]
    wsl = _get_wsl_projects_dir()
    if wsl:
        dirs.append(wsl)
    return dirs


DB_PATH = Path(__file__).parent / "data" / "usage.db"

# API pricing per 1M tokens (input, output).
# NOTE: currently unused — cost is computed at query time in db.py via price_for_model().
# Kept in sync with db.py for documentation parity only.
MODEL_PRICING = {
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-4-6": (15.00, 75.00),
    "claude-opus-4-5": (15.00, 75.00),
    "claude-opus-4-5-20251101": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (0.25, 1.25),
    "claude-haiku-4-5-20251001": (0.25, 1.25),
}
DEFAULT_PRICING = (3.00, 15.00)  # fallback to Sonnet pricing


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            msg_id TEXT UNIQUE,
            timestamp TEXT NOT NULL,
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            project TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_5m_tokens INTEGER DEFAULT 0,
            cache_1h_tokens INTEGER DEFAULT 0,
            entrypoint TEXT DEFAULT '',
            speed TEXT DEFAULT 'standard',
            git_branch TEXT DEFAULT '',
            web_search_count INTEGER DEFAULT 0,
            web_fetch_count INTEGER DEFAULT 0,
            source TEXT DEFAULT 'claude-code'
        );
        CREATE INDEX IF NOT EXISTS idx_date ON messages(date);
        CREATE INDEX IF NOT EXISTS idx_project ON messages(project);
        CREATE INDEX IF NOT EXISTS idx_model ON messages(model);
        CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id);
        CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp);

        CREATE TABLE IF NOT EXISTS ingest_meta (
            file_path TEXT PRIMARY KEY,
            file_size INTEGER,
            last_modified REAL
        );

        CREATE TABLE IF NOT EXISTS quota_snapshots (
            timestamp TEXT PRIMARY KEY,
            five_hour_pct REAL,
            seven_day_pct REAL
        );

    """)
    _migrate_db(conn)
    conn.commit()


def _migrate_db(conn):
    """Add new columns to existing databases."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    new_cols = [
        ('cache_5m_tokens', 'INTEGER DEFAULT 0'),
        ('cache_1h_tokens', 'INTEGER DEFAULT 0'),
        ('entrypoint', "TEXT DEFAULT ''"),
        ('speed', "TEXT DEFAULT 'standard'"),
        ('git_branch', "TEXT DEFAULT ''"),
        ('web_search_count', 'INTEGER DEFAULT 0'),
        ('web_fetch_count', 'INTEGER DEFAULT 0'),
        ('source', "TEXT DEFAULT 'claude-code'"),
    ]
    for col, typedef in new_cols:
        if col not in existing:
            conn.execute(f'ALTER TABLE messages ADD COLUMN {col} {typedef}')


def _greedy_resolve(base_path: Path, rest: str) -> str:
    """Resolve a dash-encoded path suffix against base_path using greedy filesystem matching.

    Each '-' in rest may be a path separator OR a literal hyphen in a directory name.
    We try to match the shortest prefix of remaining tokens to a real directory at each step.
    """
    if not base_path.exists():
        return rest  # filesystem not accessible

    tokens = rest.split('-')
    resolved = []
    current = base_path
    i = 0

    while i < len(tokens):
        matched = False
        for j in range(i + 1, len(tokens) + 1):
            candidate = '-'.join(tokens[i:j])
            if (current / candidate).is_dir():
                resolved.append(candidate)
                current = current / candidate
                i = j
                matched = True
                break
        if not matched:
            resolved.append('-'.join(tokens[i:]))
            break

    return ' / '.join(resolved) if resolved else 'Home'


def _get_wsl_home(username: str):
    """Return the WSL home Path for username if accessible, else None."""
    for prefix in [r"\\wsl.localhost\Ubuntu", r"\\wsl$\Ubuntu"]:
        p = Path(prefix) / "home" / username
        try:
            if p.exists():
                return p
        except (OSError, PermissionError):
            pass
    return None


def extract_project_name(dir_name: str) -> str:
    """Convert encoded directory name to a readable path using filesystem resolution.

    The directory names under ~/.claude/projects/ encode the original CWD path with
    dashes replacing path separators. We resolve ambiguous dashes by checking which
    segments correspond to real directories on disk.

    Windows Claude Code examples:
      C--Users-weaverjc                              → Home
      C--Users-weaverjc-Projects                    → Projects
      C--Users-weaverjc-Projects-Personal-foo-bar   → Projects / Personal / foo-bar
      c--Users-weaverjc-Projects-march-madness       → Projects / march-madness

    WSL Claude Code examples (leading '-' encodes the leading '/'):
      -home-weaverjc-Projects-email                 → Projects / email
      -mnt-c-Users-weaverjc-Projects-www            → Projects / www  (same as Windows)
      -home-weaverjc--claude                        → .claude
    """
    if 'ssh-' in dir_name:
        return 'ssh-session'

    # --- WSL POSIX-encoded paths: start with '-' but not '--' ---
    # Format: -<posix-path-with-dashes>
    if dir_name.startswith('-') and not dir_name.startswith('--'):
        # /mnt/<drive>/... → treat as Windows path, reuse Windows resolution
        m_mnt = re.match(r'^-mnt-([a-zA-Z])-(.+)$', dir_name)
        if m_mnt:
            drive = m_mnt.group(1).upper()
            rest = m_mnt.group(2)
            # Check for Users/<username> prefix
            m_users = re.match(r'^Users-(\w+)(-(.+))?$', rest)
            if m_users:
                username = m_users.group(1)
                sub = m_users.group(3) or ''
                if not sub:
                    return 'Home'
                return _greedy_resolve(Path(f'{drive}:\\Users\\{username}'), sub)
            else:
                return _greedy_resolve(Path(f'{drive}:\\'), rest)

        # /home/<username>/... → resolve via WSL UNC path
        m_home = re.match(r'^-home-(\w+)(-(.+))?$', dir_name)
        if m_home:
            username = m_home.group(1)
            rest = m_home.group(3) or ''
            if not rest:
                return 'Home'
            wsl_home = _get_wsl_home(username)
            if wsl_home:
                return _greedy_resolve(wsl_home, rest)
            # WSL not reachable — plain display
            return rest.replace('-', ' / ')

        # Other POSIX paths (e.g. /root/...) — strip leading '-', plain display
        return dir_name[1:].replace('-', ' / ')

    # --- Windows-encoded paths ---
    # Two formats:
    #   {Drive}--Users-{username}[-{rest}]  e.g. C--Users-weaverjc-Projects-foo
    #   {Drive}--{rest}                      e.g. u--Projects-WCAG-PDF
    m_users = re.match(r'^([A-Za-z])--Users-(\w+)(-(.+))?$', dir_name)
    m_drive = re.match(r'^([A-Za-z])--(.+)$', dir_name)

    if m_users:
        drive = m_users.group(1).upper()
        username = m_users.group(2)
        rest = m_users.group(4) or ''
        if not rest:
            return 'Home'
        return _greedy_resolve(Path(f'{drive}:\\Users\\{username}'), rest)
    elif m_drive:
        drive = m_drive.group(1).upper()
        rest = m_drive.group(2)
        return _greedy_resolve(Path(f'{drive}:\\'), rest)
    else:
        return dir_name  # unknown format


def parse_timestamp(ts: str):
    """Parse ISO timestamp, return (local_iso_str, date_str, hour, day_of_week)."""
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        # Convert to local time for display (DB stores local time)
        local_dt = dt.astimezone()
        local_iso = local_dt.strftime('%Y-%m-%dT%H:%M:%S')
        return local_iso, local_dt.strftime('%Y-%m-%d'), local_dt.hour, local_dt.weekday()
    except Exception:
        return None, None, None, None


_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)

def _is_uuid(name: str) -> bool:
    return bool(_UUID_RE.match(name))


def _load_desktop_session_meta(org_dir: Path) -> dict:
    """Parse companion JSON files in an org dir, keyed by session dir name (e.g. 'local_<uuid>')."""
    meta = {}
    for json_file in org_dir.glob("local_*.json"):
        try:
            with open(json_file, encoding='utf-8', errors='replace') as f:
                d = loads(f.read())
            key = json_file.stem  # e.g. 'local_023d3861-...'
            meta[key] = {
                'title': d.get('title', ''),
                'userSelectedFolders': d.get('userSelectedFolders', []),
            }
        except Exception:
            pass
    return meta


def _desktop_project_name(meta: dict) -> str:
    """Derive a project display name from Desktop session metadata."""
    title = meta.get('title', '').strip()
    if title:
        return f"Desktop: {title}"
    folders = meta.get('userSelectedFolders', [])
    if folders:
        last = Path(folders[0]).name
        return f"Desktop: {last}" if last else "Desktop: Untitled"
    return "Desktop: Untitled"


def ingest_file(conn, file_path: str, project_name: str,
                source: str = 'claude-code',
                timestamp_key: str = 'timestamp',
                session_id_key: str = 'sessionId') -> int:
    """Parse a single JSONL file and insert new messages. Returns count inserted."""
    count = 0

    # Collect all qualifying messages, keeping last occurrence per msg_id
    messages = {}

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                # Fast pre-filter before JSON parse
                if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
                    continue
                if '"usage"' not in line:
                    continue
                try:
                    obj = loads(line)
                except Exception:
                    continue

                if obj.get('type') != 'assistant':
                    continue

                msg = obj.get('message', {})
                usage = msg.get('usage')
                if not usage:
                    continue

                msg_id = msg.get('id')
                model = msg.get('model', 'unknown')
                timestamp = obj.get(timestamp_key, '')
                session_id = obj.get(session_id_key, '')

                if not timestamp or not session_id:
                    continue

                local_iso, date, hour, dow = parse_timestamp(timestamp)
                if date is None:
                    continue

                cache_creation = usage.get('cache_creation', {})
                server_tool_use = usage.get('server_tool_use', {})

                input_tokens = usage.get('input_tokens', 0)
                cache_creation_tokens = usage.get('cache_creation_input_tokens', 0)
                cache_read_tokens = usage.get('cache_read_input_tokens', 0)
                output_tokens = usage.get('output_tokens', 0)

                # Skip messages with no token usage — these are streaming fragments
                # or synthetic entries that add noise without contributing to analytics.
                if not (input_tokens or cache_creation_tokens or cache_read_tokens or output_tokens):
                    continue

                record = {
                    'msg_id': msg_id or f"{session_id}_{timestamp}",
                    'timestamp': local_iso,
                    'date': date,
                    'hour': hour,
                    'day_of_week': dow,
                    'session_id': session_id,
                    'project': project_name,
                    'model': model,
                    'input_tokens': input_tokens,
                    'cache_creation_tokens': cache_creation_tokens,
                    'cache_read_tokens': cache_read_tokens,
                    'output_tokens': output_tokens,
                    'cache_5m_tokens': cache_creation.get('ephemeral_5m_input_tokens', 0),
                    'cache_1h_tokens': cache_creation.get('ephemeral_1h_input_tokens', 0),
                    'entrypoint': obj.get('entrypoint', '') or ('desktop' if source == 'claude-desktop' else ''),
                    'speed': usage.get('speed', 'standard'),
                    'git_branch': obj.get('gitBranch', ''),
                    'web_search_count': server_tool_use.get('web_search_requests', 0),
                    'web_fetch_count': server_tool_use.get('web_fetch_requests', 0),
                    'source': source,
                }
                # Keep last occurrence (streaming sends multiple chunks)
                messages[record['msg_id']] = record
    except Exception as e:
        print(f"  Error reading {file_path}: {e}")
        return 0

    # Insert with upsert
    for record in messages.values():
        try:
            conn.execute("""
                INSERT OR REPLACE INTO messages
                (msg_id, timestamp, date, hour, day_of_week, session_id, project, model,
                 input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens,
                 cache_5m_tokens, cache_1h_tokens, entrypoint, speed, git_branch,
                 web_search_count, web_fetch_count, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record['msg_id'], record['timestamp'], record['date'],
                record['hour'], record['day_of_week'], record['session_id'],
                record['project'], record['model'],
                record['input_tokens'], record['cache_creation_tokens'],
                record['cache_read_tokens'], record['output_tokens'],
                record['cache_5m_tokens'], record['cache_1h_tokens'],
                record['entrypoint'], record['speed'], record['git_branch'],
                record['web_search_count'], record['web_fetch_count'],
                record['source'],
            ))
            count += 1
        except Exception:
            pass

    return count


def run_ingest(progress_callback=None, force=False):
    """Main ingest entry point. Returns dict with stats.

    Args:
        force: If True, clear ingest_meta to force re-processing all files.
    """
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = get_db()
    init_db(conn)

    if force:
        conn.execute("DELETE FROM ingest_meta")
        conn.commit()

    # Find all JSONL files: (file_path, project_name, source, timestamp_key, session_id_key)
    all_files = []
    for projects_root in get_project_dirs():
        try:
            project_dirs = list(projects_root.iterdir())
        except (OSError, PermissionError):
            continue
        for project_dir in project_dirs:
            if not project_dir.is_dir():
                continue
            project_name = extract_project_name(project_dir.name)
            for jsonl_file in project_dir.glob("*.jsonl"):
                all_files.append((str(jsonl_file), project_name, 'claude-code', 'timestamp', 'sessionId'))

    # Scan Claude Desktop Cowork/Agent session audit files
    if DESKTOP_SESSIONS_DIR.exists():
        for account_dir in DESKTOP_SESSIONS_DIR.iterdir():
            if not account_dir.is_dir() or not _is_uuid(account_dir.name):
                continue
            for org_dir in account_dir.iterdir():
                if not org_dir.is_dir() or not _is_uuid(org_dir.name):
                    continue
                session_meta = _load_desktop_session_meta(org_dir)
                for session_dir in org_dir.iterdir():
                    if not session_dir.is_dir() or not session_dir.name.startswith('local_'):
                        continue
                    audit_file = session_dir / 'audit.jsonl'
                    if audit_file.exists():
                        meta = session_meta.get(session_dir.name, {})
                        project_name = _desktop_project_name(meta)
                        all_files.append((str(audit_file), project_name, 'claude-desktop', '_audit_timestamp', 'session_id'))

    # Check which files need re-ingesting
    cursor = conn.execute("SELECT file_path, file_size, last_modified FROM ingest_meta")
    meta_cache = {row[0]: (row[1], row[2]) for row in cursor}

    to_process = []
    for file_path, project_name, source, ts_key, sid_key in all_files:
        try:
            stat = os.stat(file_path)
            cached = meta_cache.get(file_path)
            if cached is None or cached[0] != stat.st_size or cached[1] != stat.st_mtime:
                to_process.append((file_path, project_name, source, ts_key, sid_key, stat.st_size, stat.st_mtime))
        except OSError:
            pass

    stats = {'total_files': len(all_files), 'processed': 0, 'messages': 0, 'skipped': len(all_files) - len(to_process)}

    for i, (file_path, project_name, source, ts_key, sid_key, fsize, fmtime) in enumerate(to_process):
        if progress_callback:
            progress_callback(i, len(to_process), file_path)

        count = ingest_file(conn, file_path, project_name, source=source,
                            timestamp_key=ts_key, session_id_key=sid_key)
        stats['messages'] += count
        stats['processed'] += 1

        # Update meta
        conn.execute("""
            INSERT OR REPLACE INTO ingest_meta (file_path, file_size, last_modified)
            VALUES (?, ?, ?)
        """, (file_path, fsize, fmtime))

        # Commit in batches
        if i % 20 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    return stats


if __name__ == '__main__':
    def progress(i, total, path):
        print(f"  [{i+1}/{total}] {os.path.basename(path)}", end='\r')

    print("Starting ingest...")
    stats = run_ingest(progress_callback=progress)
    print(f"\nDone. Processed {stats['processed']} files, "
          f"skipped {stats['skipped']}, "
          f"inserted/updated {stats['messages']} messages.")
