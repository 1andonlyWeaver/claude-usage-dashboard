"""Start at login: the HKCU Run value, and Task Manager's switch for it."""
import sys
from pathlib import Path

import autostart

RUN = autostart.RUN_KEY
APPROVED = autostart.APPROVED_KEY
NAME = "ClaudeUsageDashboard"


def test_command_from_source_runs_desktop_py_without_a_console():
    cmd = autostart.command()
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    expected_exe = windowless if windowless.exists() else exe
    desktop_py = Path(autostart.__file__).with_name("desktop.py")
    assert cmd == f'"{expected_exe}" "{desktop_py}" --background'


def test_command_when_frozen_is_the_exe_itself(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\Claude Usage Dashboard\ClaudeUsageDashboard.exe")
    assert autostart.command() == r'"C:\Apps\Claude Usage Dashboard\ClaudeUsageDashboard.exe" --background'


def test_off_until_enabled_then_off_again(fake_registry):
    assert not autostart.is_enabled()
    autostart.enable()
    assert fake_registry.value(RUN, NAME) == autostart.command()
    assert autostart.is_enabled()
    autostart.disable()
    assert fake_registry.value(RUN, NAME) is None
    assert not autostart.is_enabled()


def test_disable_with_nothing_there_is_fine():
    autostart.disable()
    assert not autostart.is_enabled()


def test_a_value_for_another_copy_is_not_this_copy(fake_registry):
    fake_registry.set(RUN, NAME, r'"D:\Old\ClaudeUsageDashboard.exe" --background')
    assert not autostart.is_enabled()


def test_the_comparison_ignores_case_and_stray_spaces(fake_registry):
    fake_registry.set(RUN, NAME, autostart.command().upper() + " ")
    assert autostart.is_enabled()


def test_task_manager_can_switch_it_off_and_enable_switches_it_back_on(fake_registry):
    autostart.enable()
    # Task Manager's Startup tab writes 12 bytes; an odd first byte means "Disabled".
    fake_registry.set(APPROVED, NAME, bytes([3]) + bytes(11), fake_registry.REG_BINARY)
    assert not autostart.is_enabled()
    autostart.enable()
    assert fake_registry.value(APPROVED, NAME) is None
    assert autostart.is_enabled()


def test_a_task_manager_entry_marked_enabled_counts_as_on(fake_registry):
    autostart.enable()
    fake_registry.set(APPROVED, NAME, bytes([2]) + bytes(11), fake_registry.REG_BINARY)
    assert autostart.is_enabled()


def test_an_unreadable_registry_reads_as_off(fake_registry, monkeypatch):
    autostart.enable()

    def denied(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(fake_registry, "OpenKey", denied)
    assert not autostart.is_enabled()
