"""
The desktop app's tray icon: its menu (Open, Start at login, Check for updates, Quit), its
attention badge, and one Windows notification when the Claude sign-in needs the person.

Every 30 s it reads /api/connection from the app's own server. That call also runs the
usual quota fetch, so the figures stay fresh while no window is polling.
"""
import json
import math
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

TILE = (224, 122, 95, 255)        # #E07A5F, the favicon's tile
DIAL = (250, 240, 230, 255)       # #FAF0E6, the gauge's filled arc
TRACK = (250, 240, 230, 89)       # the same cream at 35%: the arc's empty part
NEEDLE = (20, 20, 19, 255)        # #141413
BADGE = (242, 177, 52, 255)       # amber: something needs the person
BADGE_EDGE = (17, 16, 16, 255)    # the page background, #111010, to set the dot off the tile
SUPERSAMPLE = 4                   # drawn larger, then scaled down, for smooth edges


def _arc(draw, scale, start, end, colour):
    """A round-capped arc of the gauge (centre 32,42, radius 18, 7 wide in the 64-unit design)."""
    cx, cy, r, w = 32 * scale, 42 * scale, 18 * scale, 7 * scale
    draw.arc((cx - r - w / 2, cy - r - w / 2, cx + r + w / 2, cy + r + w / 2),
             start=start, end=end, fill=colour, width=round(w))
    for angle in (start, end):
        x, y = cx + r * math.cos(math.radians(angle)), cy + r * math.sin(math.radians(angle))
        draw.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=colour)


def icon_image(attention: bool, size: int = 64) -> Image.Image:
    """The gauge tile (the favicon), plus an amber dot when the sign-in needs attention."""
    big = size * SUPERSAMPLE
    s = big / 64
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2 * s, 2 * s, 62 * s, 62 * s), radius=16 * s, fill=TILE)
    track = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    _arc(ImageDraw.Draw(track), s, 180, 360, TRACK)
    image.alpha_composite(track)
    _arc(draw, s, 180, 307, DIAL)  # the filled 70%
    draw.line((32 * s, 42 * s, 39.6 * s, 31.5 * s), fill=NEEDLE, width=round(6 * s))
    for x, y, r in ((32, 42, 5), (39.6, 31.5, 3)):
        draw.ellipse(((x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s), fill=NEEDLE)
    if attention:  # top right, clear of the needle
        radius, edge = 15 * s, max(SUPERSAMPLE, big // 24)
        centre_x, centre_y = big - radius - 1, radius + 1
        draw.ellipse((centre_x - radius, centre_y - radius, centre_x + radius, centre_y + radius),
                     fill=BADGE_EDGE)
        inner = radius - edge
        draw.ellipse((centre_x - inner, centre_y - inner, centre_x + inner, centre_y + inner), fill=BADGE)
    return image.resize((size, size), Image.LANCZOS)


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
        self._thread = None
        self.icon = icon or pystray.Icon(paths.APP_NAME, icon_image(False), TITLE, menu=self.menu())

    def menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Open dashboard", self.shell.show, default=True),
            pystray.MenuItem("Open in browser", lambda: webbrowser.open(self.url)),
            pystray.MenuItem("Start at login", self.toggle_autostart,
                             checked=lambda item: autostart.is_enabled()),
            pystray.MenuItem("Check for updates", self.check_for_updates),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.shell.quit),
        )

    def start(self) -> None:
        """Show the icon from its own thread. Polling starts once the icon is up."""
        self._thread = threading.Thread(target=self.icon.run, kwargs={"setup": self._run_polls},
                                        name="tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.icon.stop()

    def join(self, timeout: float) -> None:
        """Wait for the icon's thread to end, so the icon is gone from the tray before the app exits."""
        if self._thread is not None:
            self._thread.join(timeout)

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

    def check_for_updates(self) -> None:
        """Ask the server to check GitHub now. On a thread of its own: the check can take
        seconds, and menu actions run on the icon's thread."""
        threading.Thread(target=self._report_update_check, name="update-check", daemon=True).start()

    def _report_update_check(self) -> None:
        try:
            status = json.loads(instance.call(self.port, "/api/update/check", method="POST", timeout=30))
        except (OSError, ValueError):
            status = None
        if not isinstance(status, dict):
            self.icon.notify("Couldn't check for updates. Try again in a minute.", TITLE)
        elif status.get("available"):
            self.icon.notify(f"Version {status.get('latest')} is available. Opening the download page.", TITLE)
            webbrowser.open(status["url"])
        elif status.get("error"):
            self.icon.notify("Couldn't reach GitHub to check for updates. Try again later.", TITLE)
        else:
            self.icon.notify(f"You have the latest version, {status.get('current')}.", TITLE)
