import json
import subprocess

import person_hours as ph
from helpers import GOOD_ESTIMATE, FakeRun, envelope


def test_call_judge_success_uses_headless_isolated_flags():
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    res = ph.call_judge("SUMMARY", "claude.exe", runner=run)
    assert res["ok"] and res["estimate"]["hours_likely"] == 3.0
    assert res["in_tokens"] == 4600 and res["out_tokens"] == 1500
    assert res["cost_usd"] == 0.05 and res["model"] == "claude-sonnet-5"
    cmd, kw = run.calls[0]
    assert cmd[:2] == ["claude.exe", "-p"]
    assert "--safe-mode" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--system-prompt") + 1] == ph.SYSTEM_PROMPT
    assert kw["input"] == "SUMMARY" and kw["timeout"] == ph.CALL_TIMEOUT_S


def test_call_judge_reports_timeout():
    run = FakeRun(raises=subprocess.TimeoutExpired("claude", 300))
    assert ph.call_judge("S", "claude", runner=run) == {"ok": False, "error": "timeout"}


def test_call_judge_reports_launch_failure():
    res = ph.call_judge("S", "claude", runner=FakeRun(raises=FileNotFoundError("nope")))
    assert not res["ok"] and res["error"].startswith("launch:")


def test_call_judge_reports_non_json_output():
    res = ph.call_judge("S", "claude", runner=FakeRun(stdout="Error: not logged in", returncode=1))
    assert not res["ok"] and res["error"] == "exit 1: Error: not logged in"


def test_call_judge_reports_cli_errors_and_unusable_estimates():
    err = ph.call_judge("S", "claude",
                        runner=FakeRun(stdout=envelope("Credit balance too low", is_error=True)))
    assert not err["ok"] and err["error"].startswith("cli:") and err["cost_usd"] == 0.05
    bad = ph.call_judge("S", "claude", runner=FakeRun(stdout=envelope("I won't estimate this.")))
    assert not bad["ok"] and bad["error"] == "no JSON object in response"


def test_find_claude_cli_falls_back_to_local_bin(tmp_path, monkeypatch):
    monkeypatch.setattr(ph.shutil, "which", lambda name: None)
    monkeypatch.setattr(ph.Path, "home", lambda: tmp_path)
    assert ph.find_claude_cli() is None
    exe = tmp_path / ".local" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert ph.find_claude_cli() == str(exe)


def test_call_judge_runs_in_the_data_dir_without_a_console(tmp_path, monkeypatch):
    monkeypatch.setattr(ph.db, "DB_PATH", tmp_path / "missing" / "usage.db")
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    ph.call_judge("S", "claude", runner=run)
    kw = run.calls[0][1]
    assert kw["cwd"] == str(tmp_path / "missing") and (tmp_path / "missing").is_dir()
    assert kw["encoding"] == "utf-8" and kw["errors"] == "replace"
    assert kw["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_call_judge_labels_cli_errors_and_refusals():
    failed = {**json.loads(envelope("")), "is_error": True, "result": None,
              "subtype": "error_during_execution", "api_error_status": 429}
    res = ph.call_judge("S", "claude", runner=FakeRun(stdout=json.dumps(failed)))
    assert res["error"] == "cli: error_during_execution (HTTP 429)"
    refused = {**json.loads(envelope("I can't help with that.")), "stop_reason": "refusal"}
    assert ph.call_judge("S", "claude", runner=FakeRun(stdout=json.dumps(refused)))["error"] == "refusal"


def test_call_judge_ignores_stray_lines_before_the_envelope():
    run = FakeRun(stdout="Update available: 2.2.0\n" + envelope(GOOD_ESTIMATE) + "\n")
    assert ph.call_judge("S", "claude", runner=run)["ok"]


def test_find_claude_cli_skips_cmd_shims(tmp_path, monkeypatch):
    monkeypatch.setattr(ph.shutil, "which", lambda name: r"C:\npm\claude.CMD")
    monkeypatch.setattr(ph.Path, "home", lambda: tmp_path)
    assert ph.find_claude_cli() is None
