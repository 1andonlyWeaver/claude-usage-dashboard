"""
The desktop app's tray icon: its menu, its attention badge, and one Windows notification
when the Claude sign-in needs the person.

Every 30 s it reads /api/connection from the app's own server. That call also runs the
usual quota fetch, so the figures stay fresh while no window is polling.
"""
import json
import threading
import webbrowser
from datetime import datetime

import pystray
from PIL import Image, ImageDraw

import autostart
import instance
import paths

TITLE = "Claude Usage Dashboard"
POLL_SECONDS = 30
# The states the person has to act on. token-expired isn't one: without automatic renewal
# it happens most nights and clears the next time they use Claude Code.
ALERT_STATES = frozenset({"not-installed", "signed-out", "login-required"})

RING = (224, 122, 95, 255)        # #E07A5F, the favicon's ring
BADGE = (242, 177, 52, 255)       # amber: something needs the person
BADGE_EDGE = (17, 16, 16, 255)    # the page background, #111010, to set the dot off the ring


def icon_image(attention: bool, size: int = 64) -> Image.Image:
    """The favicon's open ring, plus an amber dot when the sign-in needs attention."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    width = max(2, size // 8)
    inset = width // 2 + 1
    # The favicon's dash covers 60 of the circle's 88 units from the top: about 245 degrees.
    draw.arc((inset, inset, size - 1 - inset, size - 1 - inset), start=-90, end=155,
             fill=RING, width=width)
    if attention:
        radius = size * 0.22
        centre = size - 1 - radius
        draw.ellipse((centre - radius, centre - radius, centre + radius, centre + radius),
                     fill=BADGE, outline=BADGE_EDGE, width=max(1, size // 32))
    return image


class Alerts:
    """Turns successive connection states into the badge and at most one notification per problem."""

    def __init__(self):
        self.state = None

    @property
    def attention(self) -> bool:
        return self.state in ALERT_STATES

    def observe(self, status: dict) -> str | None:
        """Record a /api/connection result. Returns the notification text when it enters an
        alert state, the first result included."""
        state = status.get("state")
        entered = state in ALERT_STATES and state != self.state
        self.state = state
        if not entered:
            return None
        return " ".join(part for part in (status.get("title"), status.get("detail")) if part)


class Tray:
    """The icon and its menu. `shell` is desktop.Shell: show() and quit()."""

    def __init__(self, shell, url: str, port: int, icon=None):
        self.shell = shell
        self.url = url
        self.port = port
        self.alerts = Alerts()
        self._badge_shown = False
        self._stop = threading.Event()
        self.icon = icon or pystray.Icon(paths.APP_NAME, icon_image(False), TITLE, menu=self.menu())

    def menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Open dashboard", self.shell.show, default=True),
            pystray.MenuItem("Open in browser", lambda: webbrowser.open(self.url)),
            pystray.MenuItem("Start at login", self.toggle_autostart,
                             checked=lambda item: autostart.is_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.shell.quit),
        )

    def start(self) -> None:
        """Show the icon from its own thread. Polling starts once the icon is up."""
        threading.Thread(target=self.icon.run, kwargs={"setup": self._run_polls},
                         name="tray", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.icon.stop()

    def _run_polls(self, icon) -> None:
        # pystray calls this on its own thread once the icon exists, so notify() works.
        icon.visible = True
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as ex:  # keep polling: one bad round mustn't freeze the badge
                print(f"[tray {datetime.now():%Y-%m-%d %H:%M:%S}] poll failed - "
                      f"{type(ex).__name__}: {ex}")
            self._stop.wait(POLL_SECONDS)

    def poll_once(self) -> None:
        try:
            status = json.loads(instance.call(self.port, "/api/connection", timeout=20))
        except (OSError, ValueError):
            return  # the server is starting or stopping; the next poll tries again
        if not isinstance(status, dict):
            return
        message = self.alerts.observe(status)
        if self.alerts.attention != self._badge_shown:
            self._badge_shown = self.alerts.attention
            self.icon.icon = icon_image(self._badge_shown)
        title = f"{TITLE}: {status.get('title')}" if self._badge_shown else TITLE
        if self.icon.title != title[:127]:
            self.icon.title = title[:127]  # Windows cuts tray tooltips at 127 characters
        if message:
            self.icon.notify(message[:255], TITLE)  # Windows' limit for notification text

    def toggle_autostart(self) -> None:
        try:
            if autostart.is_enabled():
                autostart.disable()
            else:
                autostart.enable()
        except OSError as ex:
            self.icon.notify(f"Couldn't change Start at login: {ex.strerror or ex}", TITLE)
        self.icon.update_menu()
