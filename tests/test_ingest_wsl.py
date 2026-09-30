"""Finding Claude Code transcripts inside WSL distros without starting any."""
import subprocess

import pytest

import ingest
from helpers import FakeRun

# Captured at import, before the autouse no_wsl fixture swaps in a stub that reports no wsl.exe.
_real_wsl_exe = ingest._wsl_exe
WSL_EXE = r"C:\Windows\System32\wsl.exe"


def utf16(text):
    return text.encode("utf-16-le")


@pytest.mark.parametrize("raw, names", [
    (utf16("Ubuntu\r\ndocker-desktop\r\n"), ["Ubuntu", "docker-desktop"]),
    (b"\xff\xfe" + utf16("Ubuntu-24.04\r\nOracleLinux_9_1\r\n"), ["Ubuntu-24.04", "OracleLinux_9_1"]),
    (utf16("Ubuntu\r\n\r\n\r\n"), ["Ubuntu"]),                      # --running pads with blank lines
    (b"Debian\nkali-linux\n", ["Debian", "kali-linux"]),            # WSL_UTF8=1 output
    (b"", []),
    (utf16("There are no running distributions.\r\n"), []),
    (utf16("Windows Subsystem for Linux has no installed distributions.\r\n"), []),
])
def test_parse_wsl_list(raw, names):
    assert ingest.parse_wsl_list(raw) == names


def with_wsl(monkeypatch, run):
    monkeypatch.setattr(ingest, "_wsl_exe", lambda: WSL_EXE)
    monkeypatch.setattr(ingest.subprocess, "run", run)


def test_running_distros_come_from_wsl_exe_without_a_console(monkeypatch):
    run = FakeRun(stdout=utf16("Ubuntu\r\n"))
    with_wsl(monkeypatch, run)
    assert ingest.running_wsl_distros() == ["Ubuntu"]
    cmd, kw = run.calls[0]
    assert cmd == [WSL_EXE, "--list", "--running", "--quiet"]
    assert kw["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert kw["timeout"] == 10


def test_running_distros_are_reused_only_briefly(monkeypatch):
    run = FakeRun(stdout=utf16("Ubuntu\r\n"))
    with_wsl(monkeypatch, run)
    clock = [1000.0]
    monkeypatch.setattr(ingest, "_clock", lambda: clock[0])
    ingest.running_wsl_distros()
    clock[0] += ingest.WSL_CHECK_SECONDS - 1
    ingest.running_wsl_distros()
    assert len(run.calls) == 1
    clock[0] += 1
    ingest.running_wsl_distros()
    assert len(run.calls) == 2


@pytest.mark.parametrize("run", [
    FakeRun(stdout=utf16("Windows Subsystem for Linux is not installed.\r\n"), returncode=1),
    FakeRun(raises=subprocess.TimeoutExpired("wsl.exe", 10)),
    FakeRun(raises=OSError("blocked by policy")),
], ids=["not-installed", "hung", "blocked"])
def test_a_broken_wsl_means_no_distros(monkeypatch, run):
    with_wsl(monkeypatch, run)
    assert ingest.running_wsl_distros() == []


def test_without_wsl_exe_nothing_runs():
    # no_wsl reports no wsl.exe, and no_real_cli fails the test if anything tries to run.
    assert ingest.running_wsl_distros() == []


def test_wsl_exe_prefers_system32_then_path(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None)
    assert _real_wsl_exe() is None
    monkeypatch.setattr(ingest.shutil, "which", lambda name: r"C:\bin\wsl.exe")
    assert _real_wsl_exe() == r"C:\bin\wsl.exe"
    exe = tmp_path / "System32" / "wsl.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    assert _real_wsl_exe() == str(exe)


def fake_shares(tmp_path, monkeypatch, running):
    """Stand-ins for \\\\wsl.localhost and \\\\wsl$, and the list of running distros."""
    new, old = tmp_path / "wsl.localhost", tmp_path / "wsl$"
    monkeypatch.setattr(ingest, "WSL_UNC_PREFIXES", (str(new), str(old)))
    monkeypatch.setattr(ingest, "running_wsl_distros", lambda: list(running))
    return new, old


def claude_projects(share, distro, user):
    path = share / distro / "home" / user / ".claude" / "projects"
    path.mkdir(parents=True)
    return path


def test_every_user_in_every_running_distro_is_scanned(tmp_path, monkeypatch):
    new, old = fake_shares(tmp_path, monkeypatch, running=["Ubuntu", "Debian", "Arch"])
    alice = claude_projects(new, "Ubuntu", "alice")
    bob = claude_projects(new, "Ubuntu", "bob")
    (new / "Ubuntu" / "home" / "carol").mkdir()   # a user without Claude Code
    dan = claude_projects(old, "Debian", "dan")   # reachable only under the older \\wsl$ name
    # Arch is running but has nothing under /home on either share.
    assert ingest.get_project_dirs() == [ingest.PROJECTS_DIR, alice, bob, dan]


def test_stopped_distros_are_never_touched(tmp_path, monkeypatch):
    new, _ = fake_shares(tmp_path, monkeypatch, running=[])
    claude_projects(new, "Ubuntu", "alice")
    assert ingest.get_project_dirs() == [ingest.PROJECTS_DIR]


def test_a_distro_under_both_names_is_scanned_once(tmp_path, monkeypatch):
    new, old = fake_shares(tmp_path, monkeypatch, running=["Ubuntu"])
    alice = claude_projects(new, "Ubuntu", "alice")
    claude_projects(old, "Ubuntu", "alice")
    assert ingest.get_project_dirs() == [ingest.PROJECTS_DIR, alice]


def test_wsl_project_names_resolve_in_the_distro_that_has_the_user(tmp_path, monkeypatch):
    new, _ = fake_shares(tmp_path, monkeypatch, running=["Ubuntu", "Debian"])
    (new / "Ubuntu" / "home" / "alice").mkdir(parents=True)
    (new / "Debian" / "home" / "bob" / "Projects" / "my-tool").mkdir(parents=True)
    assert ingest.extract_project_name("-home-bob-Projects-my-tool") == "Projects / my-tool"
