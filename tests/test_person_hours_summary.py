from collections import Counter

import person_hours as ph
from helpers import (assistant, create_result, edit_result, text, tool_result, tool_use, user,
                     write_jsonl)

DAY = "2026-09-20"
SITE_JS = "C:/Users/me/Projects/www/.claude/worktrees/fix/site.js"
README = "C:/Users/me/Projects/www/README.md"
LIB = "C:/Users/me/Projects/www/lib.py"


def _session(tmp_path):
    main = write_jsonl(tmp_path / "proj" / "s1.jsonl", [
        user("2026-09-19T23:50:00", "Yesterday's request"),
        user("2026-09-20T09:00:00", "<system-reminder>x</system-reminder>Fix the calendar"),
        assistant("2026-09-20T09:01:00",
                  tool_use("Bash", command="ls", description="List files"),
                  tool_use("Bash", command="ls", description="List files"),
                  tool_use("Read", file_path="a.js")),
        tool_result("2026-09-20T09:02:00", edit_result(SITE_JS, 5, 2)),
        tool_result("2026-09-20T09:03:00",
                    create_result(r"C:\Users\me\.claude\projects\C--www\memory\note.md", 40)),
        tool_result("2026-09-20T09:04:00", create_result(ph._TEMP_PREFIX + "claude/scratch/pr.md", 30)),
        tool_result("2026-09-20T09:05:00", create_result(README, 12)),
        assistant("2026-09-20T09:06:00", text("Done: fixed it.")),
        user("2026-09-21T00:10:00", "Tomorrow's request"),
    ])
    write_jsonl(tmp_path / "proj" / "s1" / "subagents" / "agent-a.jsonl", [
        assistant("2026-09-20T09:02:30", tool_use("Grep", pattern="x"), text("sub text"),
                  isSidechain=True),
        tool_result("2026-09-20T09:02:40", create_result(LIB, 20), isSidechain=True),
    ])
    return main


def _empty(**over):
    base = {"prompts": [], "finals": [], "bash_desc": [], "tools_main": Counter(),
            "tools_sub": Counter(), "changes": {}, "subagents": 0}
    return {**base, **over}


def test_summarize_day_keeps_only_that_local_date(tmp_path):
    assert ph.summarize_day(_session(tmp_path), DAY)["prompts"] == ["Fix the calendar"]


def test_summarize_day_returns_none_without_events(tmp_path):
    assert ph.summarize_day(_session(tmp_path), "2026-09-25") is None


def test_summarize_day_counts_changes_and_skips_memory_and_scratch(tmp_path):
    assert ph.summarize_day(_session(tmp_path), DAY)["changes"] == {
        SITE_JS: [5, 2, "edit"],
        README: [12, 0, "new"],
        LIB: [20, 0, "new"],
    }


def test_summarize_day_tools_commands_finals_and_subagents(tmp_path):
    s = ph.summarize_day(_session(tmp_path), DAY)
    assert s["tools_main"] == {"Bash": 2, "Read": 1}
    assert s["tools_sub"] == {"Grep": 1}
    assert s["bash_desc"] == ["List files"]
    assert s["finals"] == ["Done: fixed it."]
    assert s["subagents"] == 1


def test_render_summary_has_context_and_no_timing(tmp_path):
    out = ph.render_summary(ph.summarize_day(_session(tmp_path), DAY), "Projects / www",
                            day_number=2, prev_summary="Set up the branch.")
    assert "Project: Projects / www" in out
    assert "day 2 of a longer session" in out
    assert "Earlier in this session: Set up the branch." in out
    assert "1. Fix the calendar" in out
    assert f"edit +5 -2  {SITE_JS}" in out
    assert "Bash×2" in out and "Grep×1" in out
    assert "- List files" in out
    assert "> Done: fixed it." in out
    assert "active back-and-forth" not in out and "wall-clock" not in out


def test_render_summary_elides_middle_requests():
    out = ph.render_summary(_empty(prompts=[f"req {i}" for i in range(1, 26)]), "p")
    assert "14. req 14" in out and "15. req 15" not in out
    assert "[... 6 more requests omitted ...]" in out
    assert "21. req 21" in out and "25. req 25" in out


def test_render_summary_caps_length():
    s = _empty(prompts=["x" * 600] * 20, finals=["y" * 1200] * 3)
    out = ph.render_summary(s, "p", max_chars=2000)
    assert out.endswith("[truncated]")
    assert len(out) <= 2000 + len("\n[truncated]")
