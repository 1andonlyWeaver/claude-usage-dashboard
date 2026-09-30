"""
The desktop app: the dashboard in its own window, and a tray icon that keeps it running.

`python desktop.py` (or the frozen ClaudeUsageDashboard.exe) runs everything in one process:
- the main thread runs the pywebview window (WebView2); closing it hides it to the tray;
- a thread runs the uvicorn server on 127.0.0.1, on settings' preferred_port when it's free;
- tray.py's icon polls the connection and offers Open, Start at login and Quit.
One copy runs per Windows session; launching another brings the first one's window forward.
Without WebView2 the dashboard opens in the default browser and the tray works as usual.

--background starts in the tray only (what Start at login runs). --smoke starts the server
on a free port, checks / and /api/connection, and exits 0 or 1.
"""
import argparse
import ctypes
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from ctypes import wintypes
from datetime import datetime

import uvicorn

import app
import applog
import instance
import paths
import settings
import tray

TITLE = "Claude Usage Dashboard"
SW_RESTORE = 9

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.IsIconic.argtypes = (wintypes.HWND,)
_user32.IsIconic.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.ShowWindow.restype = wintypes.BOOL


def log(message: str) -> None:
    print(f"[desktop {datetime.now():%Y-%m-%d %H:%M:%S}] {message}")


def bind_socket(preferred: int) -> socket.socket:
    """A socket bound to 127.0.0.1: on `preferred` when that's free, else on a port the OS picks.

    Another program can hold 8765, or it can sit in a range Windows reserves (Hyper-V, WSL),
    which fails with PermissionError rather than "address in use". Either way, fall back.
    """
    if 1024 <= preferred <= 65535:
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", preferred))
            return sock
        except OSError:
            sock.close()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock


def _serve(server: uvicorn.Server, sock: socket.socket) -> None:
    try:
        server.run(sockets=[sock])
    except SystemExit:
        # uvicorn calls sys.exit(3) when the app's startup fails, after logging why. Python
        # ignores that in a thread, but a test runner reports it; wait_started sees the
        # thread end either way.
        pass


def start_server(sock: socket.socket) -> tuple[uvicorn.Server, threading.Thread]:
    """Serve the dashboard on `sock` from a daemon thread.

    uvicorn gets the app object: the "app:app" import string doesn't resolve in a frozen
    build. No access log: the page's 5-second polls would fill dashboard.log.
    """
    config = uvicorn.Config(app.app, lifespan="on", access_log=False, timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=_serve, args=(server, sock), name="server", daemon=True)
    thread.start()
    return server, thread


def wait_started(server: uvicorn.Server, thread: threading.Thread, timeout: float = 60.0) -> bool:
    """True once the server is serving; False if it stopped, or took longer than `timeout`."""
    deadline = time.monotonic() + timeout
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _restore_if_minimized(hwnd) -> None:
    if hwnd and _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)  # back to its earlier size, maximized or not


class Shell:
    """What Open dashboard, a second launch, Quit and the window's close button do.

    `window` is the pywebview window, or None when there's no WebView2 and the default
    browser stands in for it.
    """

    def __init__(self, url: str, server, open_browser=webbrowser.open, restore=_restore_if_minimized):
        self.url = url
        self.server = server
        self.window = None
        self.hwnd = None  # the window's handle, once it exists
        self.tray = None
        self.quitting = False
        self.stopped = threading.Event()
        self._open_browser = open_browser
        self._restore = restore

    def show(self) -> None:
        if self.window is None:
            self._open_browser(self.url)
            return
        self._restore(self.hwnd)
        self.window.show()

    def hides_on_close(self, user_closing: bool) -> bool:
        """Whether closing the window should hide it to the tray instead.

        Only the person's own close does. Quit, Windows signing out or shutting down, and
        Task Manager all get a real close, so the app never holds up a sign-out.
        """
        return user_closing and not self.quitting

    def use_browser(self, background: bool) -> None:
        """No WebView2: the browser stands in for the window, except at a --background start."""
        self.window = None
        if not background:
            self.show()

    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        self.server.should_exit = True
        if self.tray is not None:
            self.tray.stop()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception as ex:  # already gone; the rest of Quit must still happen
                log(f"closing the window failed ({type(ex).__name__}: {ex})")
        self.stopped.set()


def _close_to_tray(shell: Shell, window) -> None:
    """Make the close button hide the window. Runs on the GUI thread before the window first shows.

    pywebview's closing event can't tell the close button from Windows signing out, so this
    handles the WinForms form's own FormClosing, whose CloseReason can.
    """
    from System.Windows.Forms import CloseReason  # pythonnet; pywebview has loaded WinForms

    form = window.native
    shell.hwnd = form.Handle.ToInt64()

    def on_form_closing(sender, args):
        if shell.hides_on_close(args.CloseReason == CloseReason.UserClosing):
            args.Cancel = True
            sender.Hide()

    form.FormClosing += on_form_closing


def run_window(shell: Shell, background: bool) -> None:
    """Run the window until Quit, or fall back to the browser. Blocks the main thread either way."""
    renderer = None
    try:
        import webview  # here, not at the top: tests import this module without WinForms

        window = webview.create_window(TITLE, shell.url, width=1280, height=860, min_size=(900, 600),
                                       hidden=background, background_color="#111010",
                                       text_select=True, zoomable=True)

        def on_initialized(name):
            nonlocal renderer
            renderer = name
            return name == "edgechromium"  # False stops here: MSHTML can't run the dashboard

        window.events.initialized += on_initialized
        window.events.before_show += lambda: _close_to_tray(shell, window)
        shell.window = window
        app.desktop_show = shell.show
        webview.start(private_mode=False, storage_path=str(paths.DATA_DIR / "webview"))
    except Exception as ex:  # pythonnet, .NET or WebView2 failed to load
        log(f"couldn't open the window ({type(ex).__name__}: {ex})")
        renderer = None
    if renderer == "edgechromium":
        return  # the window ran until Quit, or until Windows closed it
    log(f"no WebView2 (renderer: {renderer}); the default browser stands in for the window")
    shell.use_browser(background)
    app.desktop_show = shell.show
    shell.stopped.wait()


def smoke() -> int:
    """Start the server on a free port, fetch / and /api/connection, stop. 0 when both answer."""
    sock = bind_socket(0)
    port = sock.getsockname()[1]
    server, thread = start_server(sock)
    try:
        if not wait_started(server, thread):
            log("smoke: the server didn't start; see the lines above")
            return 1
        page = instance.call(port, "/", timeout=30)
        status = json.loads(instance.call(port, "/api/connection", timeout=30))
        if TITLE.encode() not in page or not isinstance(status, dict) or "state" not in status:
            log("smoke: unexpected answer")
            return 1
        log(f"smoke: ok on port {port}; connection {status['state']}")
        return 0
    except (OSError, ValueError) as ex:
        log(f"smoke: failed ({type(ex).__name__}: {ex})")
        return 1
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()  # uvicorn closes it on a clean stop; not when startup failed


def setup_logging() -> None:
    if sys.stdout is None or sys.stderr is None:
        # pythonw.exe and the windowed exe start without stdout/stderr: log to the file
        sys.stdout = sys.stderr = applog.open_log()
    else:
        applog.utf8_stdio()


def parse_args(argv):
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--background", action="store_true",
                        help="start in the tray with the window hidden (Start at login uses this)")
    parser.add_argument("--smoke", action="store_true",
                        help="start the server on a free port, check that it answers, exit 0 or 1")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    setup_logging()
    args = parse_args(argv)
    if args.smoke:
        return smoke()
    lock = instance.acquire()
    if lock is None:
        if instance.show_running():
            log("already running; brought its window forward")
            return 0
        lock = instance.acquire()  # the other copy may have quit meanwhile
        if lock is None:
            log("already running, but it didn't answer")
            return 1
    instance.clear_runtime()  # this copy holds the mutex, so any runtime.json is from an earlier run
    sock = bind_socket(settings.get("preferred_port"))
    port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    server, thread = start_server(sock)
    if not wait_started(server, thread):
        log(f"the server didn't start on port {port}; see the lines above")
        server.should_exit = True
        instance.release(lock)
        return 1
    instance.write_runtime(port)
    log(f"serving {url}")
    shell = Shell(url, server)
    shell.tray = tray.Tray(shell, url, port)
    shell.tray.start()
    try:
        run_window(shell, background=args.background)
    finally:
        shell.quit()  # also after Windows closed the window at sign-out
        thread.join(timeout=10)
        instance.clear_runtime(os.getpid())
        instance.release(lock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
