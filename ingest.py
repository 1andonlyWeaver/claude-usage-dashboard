"""
Parse Claude Code session JSONL files into SQLite for fast querying.
Supports incremental updates - only re-parses changed files.
"""
import sqlite3
import os
import glob
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
DB_PATH = Path(__file__).parent / "data" / "usage.db"

# API pricing per 1M tokens (input, output)
MODEL_PRICING = {
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
            output_tokens INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_date ON messages(date);
        CREATE INDEX IF NOT EXISTS idx_project ON messages(project);
        CREATE INDEX IF NOT EXISTS idx_model ON messages(model);
        CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id);

        CREATE TABLE IF NOT EXISTS ingest_meta (
            file_path TEXT PRIMARY KEY,
            file_size INTEGER,
            last_modified REAL
        );
    """)
    conn.commit()


def extract_project_name(dir_name: str) -> str:
    """Convert directory name like 'C--Users-weaverjc-Projects-Personal-music-analyst' to 'music-analyst'."""
    # Strip leading drive/user path
    name = re.sub(r'^[Cc]--Users-\w+-', '', dir_name)
    # Handle ssh sessions
    if name.startswith('ssh-'):
        return 'ssh-session'
    # Remove Projects- or PycharmProjects- prefix
    name = re.sub(r'^(Projects|PycharmProjects)-', '', name, flags=re.IGNORECASE)
    # Replace remaining dashes with slashes to show project/subproject
    # But keep the last component(s) as the display name
    parts = name.split('-')
    # If looks like a path (Personal-music-analyst), show as "Personal / music-analyst"
    if len(parts) > 2:
        return f"{parts[0]} / {'-'.join(parts[1:])}"
    return name or dir_name


def parse_timestamp(ts: str):
    """Parse ISO timestamp, return (date_str, hour, day_of_week)."""
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        # Convert to local time for display
        local_dt = dt.astimezone()
        return local_dt.strftime('%Y-%m-%d'), local_dt.hour, local_dt.weekday()
    except Exception:
        return None, None, None


def ingest_file(conn, file_path: str, project_name: str) -> int:
    """Parse a single JSONL file and insert new messages. Returns count inserted."""
    count = 0
    seen_msg_ids = set()

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
                timestamp = obj.get('timestamp', '')
                session_id = obj.get('sessionId', '')

                if not timestamp or not session_id:
                    continue

                date, hour, dow = parse_timestamp(timestamp)
                if date is None:
                    continue

                record = {
                    'msg_id': msg_id or f"{session_id}_{timestamp}",
                    'timestamp': timestamp,
                    'date': date,
                    'hour': hour,
                    'day_of_week': dow,
                    'session_id': session_id,
                    'project': project_name,
                    'model': model,
                    'input_tokens': usage.get('input_tokens', 0),
                    'cache_creation_tokens': usage.get('cache_creation_input_tokens', 0),
                    'cache_read_tokens': usage.get('cache_read_input_tokens', 0),
                    'output_tokens': usage.get('output_tokens', 0),
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
                 input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record['msg_id'], record['timestamp'], record['date'],
                record['hour'], record['day_of_week'], record['session_id'],
                record['project'], record['model'],
                record['input_tokens'], record['cache_creation_tokens'],
                record['cache_read_tokens'], record['output_tokens'],
            ))
            count += 1
        except Exception:
            pass

    return count


def run_ingest(progress_callback=None):
    """Main ingest entry point. Returns dict with stats."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = get_db()
    init_db(conn)

    # Find all non-subagent JSONL files
    all_files = []
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        project_name = extract_project_name(project_dir.name)
        for jsonl_file in project_dir.glob("*.jsonl"):
            all_files.append((str(jsonl_file), project_name))

    # Check which files need re-ingesting
    cursor = conn.execute("SELECT file_path, file_size, last_modified FROM ingest_meta")
    meta_cache = {row[0]: (row[1], row[2]) for row in cursor}

    to_process = []
    for file_path, project_name in all_files:
        try:
            stat = os.stat(file_path)
            cached = meta_cache.get(file_path)
            if cached is None or cached[0] != stat.st_size or cached[1] != stat.st_mtime:
                to_process.append((file_path, project_name, stat.st_size, stat.st_mtime))
        except OSError:
            pass

    stats = {'total_files': len(all_files), 'processed': 0, 'messages': 0, 'skipped': len(all_files) - len(to_process)}

    for i, (file_path, project_name, fsize, fmtime) in enumerate(to_process):
        if progress_callback:
            progress_callback(i, len(to_process), file_path)

        count = ingest_file(conn, file_path, project_name)
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
