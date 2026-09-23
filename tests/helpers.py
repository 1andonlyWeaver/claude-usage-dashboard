"""Builders for Claude Code transcripts, DB rows and claude CLI output used across tests."""
import json
import subprocess
from datetime import datetime, timezone


def utc(local: str) -> str:
    """Local wall-clock 'YYYY-MM-DDTHH:MM:SS' -> the UTC 'Z' form Claude Code writes."""
    return datetime.fromisoformat(local).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def user(local_ts, content, **extra):
    """A user entry; content is a string or a list of content blocks."""
    return {"type": "user", "timestamp": utc(local_ts), "sessionId": "s1",
            "message": {"role": "user", "content": content}, **extra}


def assistant(local_ts, *blocks, **extra):
    return {"type": "assistant", "timestamp": utc(local_ts), "sessionId": "s1",
            "message": {"id": f"msg-{local_ts}", "role": "assistant", "model": "claude-sonnet-5",
                        "content": list(blocks), "usage": {"input_tokens": 1, "output_tokens": 1}},
            **extra}


def text(t):
    return {"type": "text", "text": t}


def tool_use(name, **inputs):
    return {"type": "tool_use", "id": f"tu-{name}", "name": name, "input": inputs}


def tool_result(local_ts, result, **extra):
    """A tool-result entry carrying Claude Code's structured toolUseResult payload."""
    return {"type": "user", "timestamp": utc(local_ts), "sessionId": "s1",
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
            "toolUseResult": result, **extra}


def edit_result(path, added, removed):
    lines = [" context"] + ["+new"] * added + ["-old"] * removed
    return {"filePath": path, "structuredPatch": [
        {"oldStart": 1, "oldLines": removed + 1, "newStart": 1, "newLines": added + 1, "lines": lines}]}


def create_result(path, n_lines):
    return {"type": "create", "filePath": path,
            "content": "\n".join(f"line {i}" for i in range(n_lines))}


def add_message(conn, session_id, local_ts, *, project="proj", source="claude-code"):
    """Insert an assistant-message row the way ingest.py does (local timestamp and date)."""
    dt = datetime.fromisoformat(local_ts)
    conn.execute(
        "INSERT INTO messages (msg_id, timestamp, date, hour, day_of_week, session_id, project, model,"
        " input_tokens, output_tokens, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'claude-sonnet-5', 1, 1, ?)",
        (f"{session_id}@{local_ts}", local_ts, dt.strftime("%Y-%m-%d"), dt.hour, dt.weekday(),
         session_id, project, source))
    conn.commit()


GOOD_ESTIMATE = {"summary": "Fixed the duplicate month bug.", "role": "Software engineer",
                 "hours_low": 2, "hours_likely": 3, "hours_high": 5,
                 "rationale": "Tracing plus testing."}


def envelope(result, *, is_error=False, model="claude-sonnet-5"):
    """What `claude -p --output-format json` prints."""
    if isinstance(result, dict):
        result = json.dumps(result)
    return json.dumps({
        "type": "result", "is_error": is_error, "result": result, "total_cost_usd": 0.05,
        "usage": {"input_tokens": 4000, "cache_creation_input_tokens": 600,
                  "cache_read_input_tokens": 0, "output_tokens": 1500},
        "modelUsage": {model: {"inputTokens": 4600, "outputTokens": 1500}},
    })


class FakeRun:
    """Stand-in for subprocess.run that records each call and returns canned output."""

    def __init__(self, stdout="", returncode=0, stderr="", raises=None):
        self.stdout, self.returncode, self.stderr, self.raises = stdout, returncode, stderr, raises
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def queued_session(conn, tmp_path, sid, now):
    """A one-day session with a transcript and a messages row, already queued by discover()."""
    import person_hours
    main = write_jsonl(tmp_path / "p" / f"{sid}.jsonl", [
        user("2026-09-20T09:00:00", "Fix the calendar"),
        assistant("2026-09-20T09:05:00", text("Fixed.")),
    ])
    add_message(conn, sid, "2026-09-20T09:05:00", project="Projects / www")
    person_hours.discover(conn, now, index={sid: main})
    return {sid: main}
