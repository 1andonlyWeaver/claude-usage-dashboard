"""
Start at login: the HKCU Run value that starts the desktop app in the tray.

Windows runs each value under the Run key when the person signs in. Task Manager's Startup
tab can switch an entry off without deleting it: it records that under
Explorer\\StartupApproved\\Run. So is_enabled() reads both, and enable() clears Task
Manager's switch.
"""
import os
import sys
import winreg
from pathlib import Path

import paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = paths.APP_NAME  # the installer (Phase 4) writes the same value


def command() -> str:
    """What Windows runs at sign-in: this copy of the app, started hidden in the tray."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")  # no console window at sign-in
    if windowless.exists():
        exe = windowless
    return f'"{exe}" "{Path(__file__).with_name("desktop.py")}" --background'


def _query(key_path: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)[0]
    except OSError:
        return None


def is_enabled() -> bool:
    """True when Windows will start this copy of the app at sign-in."""
    value = _query(RUN_KEY)
    if not isinstance(value, str):
        return False
    if os.path.normcase(value.strip()) != os.path.normcase(command()):
        return False
    approved = _query(APPROVED_KEY)
    switched_off = isinstance(approved, bytes) and len(approved) > 0 and bool(approved[0] & 1)
    return not switched_off


def enable() -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
    _delete(APPROVED_KEY)  # undo a Task Manager "Disable"; no entry there counts as enabled


def disable() -> None:
    _delete(RUN_KEY)


def _delete(key_path: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
