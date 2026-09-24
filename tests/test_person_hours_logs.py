import person_hours as ph
from helpers import assistant, text, tool_result, user, write_jsonl

T = "2026-09-20T10:00:00"


def test_index_sessions_maps_ids_to_main_files(tmp_path):
    a = write_jsonl(tmp_path / "root1" / "C--proj-a" / "s1.jsonl", [])
    b = write_jsonl(tmp_path / "root2" / "-home-me-proj" / "s2.jsonl", [])
    write_jsonl(tmp_path / "root1" / "C--proj-a" / "s1" / "subagents" / "agent-x.jsonl", [])
    index = ph.index_sessions([tmp_path / "root1", tmp_path / "root2", tmp_path / "missing"])
    assert index == {"s1": a, "s2": b}


def test_subagent_files_include_workflow_agents_but_not_journals(tmp_path):
    main = write_jsonl(tmp_path / "p" / "s1.jsonl", [])
    direct = write_jsonl(tmp_path / "p" / "s1" / "subagents" / "agent-1.jsonl", [])
    nested = write_jsonl(
        tmp_path / "p" / "s1" / "subagents" / "workflows" / "wf_a" / "agent-2.jsonl", [])
    write_jsonl(tmp_path / "p" / "s1" / "subagents" / "workflows" / "wf_a" / "journal.jsonl", [])
    assert ph.subagent_files(main) == sorted([direct, nested])


def test_human_text_accepts_typed_prompts_only():
    assert ph.human_text(user(T, "hello")) == "hello"
    assert ph.human_text(user(T, [text("a"), text("b")])) == "a\nb"
    assert ph.human_text(tool_result(T, {})) is None
    assert ph.human_text(user(T, "x", isMeta=True)) is None
    assert ph.human_text(user(T, "x", isSidechain=True)) is None
    assert ph.human_text(assistant(T, text("hi"))) is None
    assert ph.human_text(user(T, "recap of earlier work", isCompactSummary=True)) is None
    assert ph.human_text(user(T, "[Request interrupted by user for tool use]")) is None
    assert ph.human_text({"type": "user", "message": "not a dict"}) is None
    assert ph.human_text(user(T, [{"type": "text", "text": None}, text("b")])) == "b"


def test_clean_prompt_strips_harness_blocks():
    raw = "<system-reminder>ignore\nme</system-reminder>Fix the bug<bash-stdout>out</bash-stdout>"
    assert ph.clean_prompt(raw) == "Fix the bug"


def test_clean_prompt_keeps_slash_commands_and_their_arguments():
    raw = ("<command-message>review-pr</command-message>\n<command-name>/review-pr</command-name>\n"
           "<command-args>PR 539</command-args>")
    assert ph.clean_prompt(raw) == "/review-pr PR 539"
    assert ph.clean_prompt("<command-name>/custom-cmd</command-name>") == "/custom-cmd"


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


def test_clean_prompt_drops_built_in_session_commands():
    # Built-ins write <command-name> first, then <command-message>, then <command-args>.
    raw = ("<command-name>/model</command-name>\n<command-message>model</command-message>\n"
           "<command-args>sonnet</command-args>")
    assert ph.clean_prompt(raw) == ""
    assert ph.clean_prompt("<command-name>/exit</command-name>") == ""
    custom = ("<command-name>/deploy</command-name>\n<command-message>deploy</command-message>\n"
              "<command-args>prod</command-args>")
    assert ph.clean_prompt(custom) == "/deploy prod"


def test_clean_prompt_drops_leading_desktop_marker():
    assert ph.clean_prompt("<!-- attach -->\nPlease look at this") == "Please look at this"
    assert ph.clean_prompt("Keep <!-- this --> comment") == "Keep <!-- this --> comment"
    assert ph.clean_prompt("<!-- attach: Terminal 1 | tab:0 -->\nRun it") == "Run it"
