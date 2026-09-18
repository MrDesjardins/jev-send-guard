"""System tray entrypoint — the friendly alternative to running agent.py
and manage.py from a console. Click/right-click the tray (Windows) or
menu-bar (macOS) icon for Settings, Pause/Resume, and Quit.

    uv run tray_app.py

Requires the same setup as agent.py: `uv sync --extra windows` or
`--extra macos`, at least one app added (via the Settings window this
opens, or `manage.py add`), and an API key set (ditto, or `manage.py
set-key`).

NOTE: this hasn't been run against a real Windows or macOS session yet —
see PLAN.md for the specific threading assumption that needs validating
first (opening a Tk settings window from a pystray menu callback thread).
"""

import sys
import threading

from PIL import Image, ImageDraw

from core.logging_setup import setup_logging
from core.settings_window import open_settings_window
from core.watch_loop import run as run_watch_loop

log = setup_logging()

stop_event = threading.Event()
pause_event = threading.Event()

# Guards against opening a second Settings window (and therefore a second,
# competing Tk mainloop) while one is already up.
_settings_lock = threading.Lock()


def _make_icon_image(rgba):
    """A generated icon (filled circle) so there's no external asset file
    to ship. Color distinguishes running vs paused at a glance."""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 6
    draw.ellipse((margin, margin, size - margin, size - margin), fill=rgba)
    return image


ICON_RUNNING = _make_icon_image((66, 133, 244, 255))  # blue
ICON_PAUSED = _make_icon_image((154, 160, 166, 255))  # gray


def start_watch_thread():
    thread = threading.Thread(target=run_watch_loop, args=(stop_event, pause_event), daemon=True)
    thread.start()


def on_settings(icon, item):
    def show():
        if not _settings_lock.acquire(blocking=False):
            return  # already open
        try:
            open_settings_window()
        finally:
            _settings_lock.release()

    threading.Thread(target=show, daemon=True).start()


def on_toggle_pause(icon, item):
    if pause_event.is_set():
        pause_event.clear()
        icon.icon = ICON_RUNNING
        log.info("resumed")
    else:
        pause_event.set()
        icon.icon = ICON_PAUSED
        log.info("paused")


def _is_paused(item):
    return pause_event.is_set()


def on_quit(icon, item):
    stop_event.set()
    icon.stop()


def main():
    import pystray

    icon = pystray.Icon(
        "jev-send-guard",
        ICON_RUNNING,
        "Jev Send Guard",
        menu=pystray.Menu(
            pystray.MenuItem("Settings...", on_settings),
            pystray.MenuItem("Paused", on_toggle_pause, checked=_is_paused),
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    icon.run(setup=lambda icon: start_watch_thread())


if __name__ == "__main__":
    if sys.platform not in ("win32", "darwin"):
        log.error("Unsupported platform: %s", sys.platform)
        sys.exit(1)
    main()
