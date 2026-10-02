"""The desktop entry point: port choice, the server thread, the window's rules, and --smoke."""
import builtins
import importlib
import platform
import socket
import sys
import types

import pytest

import app
import desktop
import tray

URL = "http://127.0.0.1:8765/"

# uvicorn calls platform.system() on its server thread. The first call in a process asks WMI,
# and when that fails or times out it runs `ver` as a subprocess, which the autouse guard in
# conftest.py turns into a failure. Answer it now, at collection, before any guard is installed.
platform.uname()


@pytest.fixture(autouse=True)
def isolated_quota_state(tmp_path, monkeypatch):
    """The smoke tests call /api/connection for real, which reads and updates the quota cache.

    Give each test its own copy of the in-memory cache and a disk cache under tmp_path, so
    nothing reads data/quota_cache.json or leaves fail_count and retry_after behind.
    """
    monkeypatch.setattr(app, "_usage_cache", dict(app._usage_cache))
    monkeypatch.setattr(app, "QUOTA_CACHE_FILE", tmp_path / "quota_cache.json")


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_bind_socket_takes_the_preferred_port_when_it_is_free():
    port = free_port()
    sock = desktop.bind_socket(port)
    try:
        assert sock.getsockname() == ("127.0.0.1", port)
    finally:
        sock.close()


def test_bind_socket_falls_back_when_the_port_is_taken():
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    try:
        sock = desktop.bind_socket(taken.getsockname()[1])
        try:
            assert sock.getsockname()[1] not in (0, taken.getsockname()[1])
        finally:
            sock.close()
    finally:
        taken.close()


def test_bind_socket_falls_back_when_windows_refuses_the_port(monkeypatch):
    """Ports in a range Windows reserves for Hyper-V or WSL fail with WinError 10013."""
    real_socket = socket.socket

    class Refusing(real_socket):
        def bind(self, address):
            if address[1] != 0:
                raise PermissionError(10013, "An attempt was made to access a socket in a way "
                                             "forbidden by its access permissions")
            return super().bind(address)

    monkeypatch.setattr(desktop.socket, "socket", Refusing)
    sock = desktop.bind_socket(8765)
    try:
        assert sock.getsockname()[1] not in (0, 8765)
    finally:
        sock.close()


@pytest.mark.parametrize("unusable", [0, 80, 70000, -1])
def test_an_unusable_preferred_port_goes_straight_to_the_os(unusable):
    sock = desktop.bind_socket(unusable)
    try:
        assert sock.getsockname()[1] >= 1024
    finally:
        sock.close()


# Work areas are (left, top, right, bottom): a monitor less its taskbar, in the window's pixels.
# The window's size and minimum come in pixels too, after pywebview has scaled them for the DPI.


def test_the_window_opens_centered_above_the_taskbar_on_a_1080p_screen():
    """2026-10-02: left to Windows, it cascaded to (234,234)-(1514,1094), its bottom under the taskbar."""
    assert desktop.fit_window((0, 0, 1920, 1032), (1280, 860), (900, 600)) == (320, 86, 1280, 860)


@pytest.mark.parametrize("work_area, expected", [
    ((0, 48, 1920, 1080), (320, 134, 1280, 860)),  # taskbar along the top
    ((48, 0, 1920, 1080), (344, 110, 1280, 860)),  # taskbar along the left
    ((-1920, 0, 0, 1040), (-1600, 90, 1280, 860)),  # a monitor to the left of the primary one
])
def test_the_window_is_centered_in_its_work_area_wherever_that_sits(work_area, expected):
    assert desktop.fit_window(work_area, (1280, 860), (900, 600)) == expected


@pytest.mark.parametrize("work_area, size, minimum, expected", [
    # a 1080p laptop at 125%: the window is 1600x1075, too tall
    ((0, 0, 1920, 1020), (1600, 1075), (1125, 750), (160, 0, 1600, 1020)),
    # at 150%: 1920x1290, too wide and too tall
    ((0, 0, 1920, 1008), (1920, 1290), (1350, 900), (0, 0, 1920, 1008)),
])
def test_a_window_bigger_than_its_work_area_shrinks_to_fit(work_area, size, minimum, expected):
    assert desktop.fit_window(work_area, size, minimum) == expected


@pytest.mark.parametrize("work_area, expected", [
    ((0, 0, 1366, 696), (0, 0, 1366, 900)),
    ((0, 72, 1366, 768), (0, 72, 1366, 900)),  # taskbar along the top
])
def test_a_work_area_smaller_than_the_minimum_keeps_the_title_bar_on_screen(work_area, expected):
    """1366x768 at 150%: the 1350x900 minimum is taller than the screen, so the window hangs off the bottom."""
    assert desktop.fit_window(work_area, (1920, 1290), (1350, 900)) == expected


class FakeWindow:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def destroy(self):
        self.calls.append("destroy")


class FakeServer:
    should_exit = False


class FakeTray:
    stopped = False

    def stop(self):
        self.stopped = True


def make_shell():
    opened, restored = [], []
    shell = desktop.Shell(URL, FakeServer(), open_browser=opened.append, restore=restored.append)
    return shell, opened, restored


def test_show_restores_a_minimized_window_then_brings_it_forward():
    shell, opened, restored = make_shell()
    shell.window, shell.hwnd = FakeWindow(), 4242
    shell.show()
    assert restored == [4242]
    assert shell.window.calls == ["show"]
    assert opened == []


def test_without_a_window_show_opens_the_browser():
    shell, opened, _ = make_shell()
    shell.show()
    assert opened == [URL]


def test_the_browser_stands_in_for_the_window_but_not_at_a_background_start():
    shell, opened, _ = make_shell()
    shell.window = FakeWindow()
    shell.use_browser(background=True)
    assert shell.window is None and opened == []
    shell.use_browser(background=False)
    assert opened == [URL]


def test_only_the_persons_own_close_hides_the_window():
    shell, _, _ = make_shell()
    assert shell.hides_on_close(user_closing=True)
    assert not shell.hides_on_close(user_closing=False)  # Windows signing out, Task Manager
    shell.quitting = True
    assert not shell.hides_on_close(user_closing=True)  # the close that Quit makes


def test_quit_stops_the_server_the_tray_and_the_window_once():
    shell, _, _ = make_shell()
    shell.window, shell.tray = FakeWindow(), FakeTray()
    shell.quit()
    shell.quit()
    assert shell.server.should_exit
    assert shell.tray.stopped
    assert shell.window.calls == ["destroy"]
    assert shell.stopped.is_set()


def test_quit_still_finishes_when_the_window_is_already_gone():
    shell, _, _ = make_shell()

    class GoneWindow(FakeWindow):
        def destroy(self):
            raise RuntimeError("window already closed")

    shell.window = GoneWindow()
    shell.quit()
    assert shell.server.should_exit and shell.stopped.is_set()


class Stop(Exception):
    """Raised by a stub to end main() once it has shown which way it went."""


@pytest.fixture
def quiet_main(monkeypatch):
    monkeypatch.setattr(desktop, "setup_logging", lambda: None)


def test_a_second_launch_asks_the_first_to_show_and_exits(monkeypatch, quiet_main):
    monkeypatch.setattr(desktop.instance, "acquire", lambda: None)
    asked = []
    monkeypatch.setattr(desktop.instance, "show_running", lambda: asked.append(True) or True)

    def must_not_serve(preferred):
        raise AssertionError("a second copy must not start a server")

    monkeypatch.setattr(desktop, "bind_socket", must_not_serve)
    assert desktop.main([]) == 0
    assert asked == [True]


def test_a_second_launch_leaves_the_server_and_the_tray_unloaded(monkeypatch):
    """It only hands off and exits; importing them took half or more of its time."""
    imported = []
    real_import = builtins.__import__

    def recording_import(name, *args, **kwargs):
        imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "desktop")  # so its own imports run again
    monkeypatch.setattr(builtins, "__import__", recording_import)
    fresh = importlib.import_module("desktop")
    monkeypatch.setattr(fresh, "setup_logging", lambda: None)
    monkeypatch.setattr(fresh.instance, "acquire", lambda: None)
    monkeypatch.setattr(fresh.instance, "show_running", lambda: True)
    assert fresh.main([]) == 0
    assert {"app", "settings", "tray", "uvicorn", "webbrowser"}.isdisjoint(imported)


def test_a_second_launch_takes_over_when_the_first_copy_has_gone(monkeypatch, quiet_main):
    handles = iter([None, 1234])
    monkeypatch.setattr(desktop.instance, "acquire", lambda: next(handles))
    monkeypatch.setattr(desktop.instance, "show_running", lambda: False)

    def proceed(preferred):
        raise Stop

    monkeypatch.setattr(desktop, "bind_socket", proceed)
    with pytest.raises(Stop):
        desktop.main([])


def test_a_second_launch_that_reaches_nobody_fails_quietly(monkeypatch, quiet_main):
    monkeypatch.setattr(desktop.instance, "acquire", lambda: None)
    monkeypatch.setattr(desktop.instance, "show_running", lambda: False)
    assert desktop.main([]) == 1


def test_startup_survives_a_runtime_file_it_cannot_write_and_quit_cleans_up(monkeypatch, quiet_main, capsys):
    calls = []

    class Server:
        started = True
        should_exit = False

    class Thread:
        def is_alive(self):
            return False

        def join(self, timeout=None):
            calls.append("server joined")

    class Tray:
        def __init__(self, shell, url, port):
            pass

        def start(self):
            calls.append("tray started")

        def stop(self):
            calls.append("tray stopped")

        def join(self, timeout):
            calls.append("tray joined")

    def locked(port):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(desktop.instance, "acquire", lambda: 99)
    monkeypatch.setattr(desktop.instance, "release", lambda handle: calls.append(f"released {handle}"))
    monkeypatch.setattr(desktop.instance, "write_runtime", locked)
    monkeypatch.setattr(desktop, "start_server", lambda sock: (Server(), Thread()))
    monkeypatch.setattr(tray, "Tray", Tray)  # main imports tray once it holds the mutex
    monkeypatch.setattr(desktop, "run_window", lambda shell, background: calls.append("window ran"))
    assert desktop.main([]) == 0
    assert calls == ["tray started", "window ran", "tray stopped", "tray joined", "server joined", "released 99"]
    assert "couldn't write runtime.json" in capsys.readouterr().out


def test_smoke_skips_the_mutex(monkeypatch, quiet_main):
    def no_mutex():
        raise AssertionError("--smoke must not take the mutex")

    monkeypatch.setattr(desktop.instance, "acquire", no_mutex)
    monkeypatch.setattr(desktop, "smoke", lambda: 0)
    assert desktop.main(["--smoke"]) == 0


def test_smoke_passes_against_a_working_server(monkeypatch):
    async def no_startup():
        pass

    monkeypatch.setattr(app, "startup", no_startup)
    assert desktop.smoke() == 0


def test_smoke_fails_when_the_server_cannot_start(monkeypatch):
    async def broken_startup():
        raise RuntimeError("startup broke")

    monkeypatch.setattr(app, "startup", broken_startup)
    assert desktop.smoke() == 1


class FakeEvent:
    """A .NET event as pythonnet shows it: handlers are added with +=."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


def webview2_window():
    """A pywebview window whose native form holds a WebView2 control, plus that control's settings."""
    control = types.SimpleNamespace(CoreWebView2InitializationCompleted=FakeEvent())
    form = types.SimpleNamespace(browser=types.SimpleNamespace(webview=control))
    browser_settings = types.SimpleNamespace(AreBrowserAcceleratorKeysEnabled=False,
                                             AreDevToolsEnabled=False)
    return types.SimpleNamespace(native=form), control, browser_settings


def test_the_zoom_keys_come_back_once_webview2_is_ready():
    window, control, browser_settings = webview2_window()
    desktop._allow_zoom_keys(window)
    (on_ready,) = control.CoreWebView2InitializationCompleted.handlers
    sender = types.SimpleNamespace(CoreWebView2=types.SimpleNamespace(Settings=browser_settings))
    on_ready(sender, types.SimpleNamespace(IsSuccess=False))
    assert browser_settings.AreBrowserAcceleratorKeysEnabled is False
    on_ready(sender, types.SimpleNamespace(IsSuccess=True))
    assert browser_settings.AreBrowserAcceleratorKeysEnabled is True
    assert browser_settings.AreDevToolsEnabled is False


def test_the_zoom_keys_leave_a_window_without_webview2_alone():
    desktop._allow_zoom_keys(types.SimpleNamespace(native=types.SimpleNamespace(browser=None)))


def test_before_show_sets_up_close_to_tray_and_the_zoom_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "_close_to_tray", lambda shell, window: calls.append(("tray", shell, window)))
    monkeypatch.setattr(desktop, "_allow_zoom_keys", lambda window: calls.append(("zoom", window)))
    desktop._prepare_window("shell", "window")
    assert calls == [("tray", "shell", "window"), ("zoom", "window")]


def test_smoke_fails_when_a_page_file_is_missing(monkeypatch):
    async def no_startup():
        pass

    monkeypatch.setattr(app, "startup", no_startup)
    real_call = desktop.instance.call

    def call(port, path, method="GET", timeout=5.0):
        if path == desktop.SMOKE_FILE:
            raise OSError("HTTP Error 404: Not Found")
        return real_call(port, path, method, timeout)

    monkeypatch.setattr(desktop.instance, "call", call)
    assert desktop.smoke() == 1


def test_a_frozen_smoke_also_loads_the_window_libraries(monkeypatch):
    async def no_startup():
        pass

    monkeypatch.setattr(app, "startup", no_startup)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop, "_window_libraries_load", lambda: False)
    assert desktop.smoke() == 1
    monkeypatch.setattr(desktop, "_window_libraries_load", lambda: True)
    assert desktop.smoke() == 0


def test_from_source_smoke_leaves_the_window_libraries_alone(monkeypatch):
    async def no_startup():
        pass

    def must_not_load():
        raise AssertionError("loading .NET into the test process")

    monkeypatch.setattr(app, "startup", no_startup)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(desktop, "_window_libraries_load", must_not_load)
    assert desktop.smoke() == 0
