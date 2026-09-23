import person_hours as ph
from helpers import assistant, text, tool_result, user, write_jsonl

T = "2026-09-20T10:00:00"


def test_index_sessions_maps_ids_to_main_files(tmp_path):
    a = write_jsonl(tmp_path / "root1" / "C--proj-a" / "s1.jsonl", [])
    b = write_jsonl(tmp_path / "root2" / "-home-me-proj" / "s2.jsonl", [])
    write_jsonl(tmp_path / "root1" / "C--proj-a" / "s1" / "subagents" / "agent-x.jsonl", [])
    index = ph.index_sessions([tmp_path / "root1", tmp_path / "root2", tmp_path / "missing"])
    assert index == {"s1": a, "s2": b}


def test_subagent_files(tmp_path):
    main = write_jsonl(tmp_path / "p" / "s1.jsonl", [])
    sub = write_jsonl(tmp_path / "p" / "s1" / "subagents" / "agent-1.jsonl", [])
    assert ph.subagent_files(main) == [sub]


def test_human_text_accepts_typed_prompts_only():
    assert ph.human_text(user(T, "hello")) == "hello"
    assert ph.human_text(user(T, [text("a"), text("b")])) == "a\nb"
    assert ph.human_text(tool_result(T, {})) is None
    assert ph.human_text(user(T, "x", isMeta=True)) is None
    assert ph.human_text(user(T, "x", isSidechain=True)) is None
    assert ph.human_text(assistant(T, text("hi"))) is None


def test_clean_prompt_strips_harness_blocks():
    raw = "<system-reminder>ignore\nme</system-reminder>Fix the bug<command-name>/x</command-name>"
    assert ph.clean_prompt(raw) == "Fix the bug"


def test_is_scheduled_session(tmp_path):
    scheduled = write_jsonl(tmp_path / "a.jsonl", [
        user(T, '<system-reminder>r</system-reminder><scheduled-task name="daily">go</scheduled-task>')])
    normal = write_jsonl(tmp_path / "b.jsonl", [
        user(T, "<system-reminder>only a reminder</system-reminder>"),
        user(T, "Please fix the calendar"),
        user(T, "<scheduled-task name='x'>later</scheduled-task>")])
    assert ph.is_scheduled_session(scheduled) is True
    assert ph.is_scheduled_session(normal) is False
    assert ph.is_scheduled_session(tmp_path / "missing.jsonl") is False
