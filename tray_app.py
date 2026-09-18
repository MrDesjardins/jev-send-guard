"""System tray entrypoint — the friendly alternative to running agent.py
and manage.py from a console. Click/right-click the tray (Windows) or
menu-bar (macOS) icon for Settings, Pause/Resume, and Quit.

    uv run tray_app.py

Requires the same setup as agent.py: `uv sync --extra windows` or
`--extra macos`, at least one app added (via the Settings window this
opens, or `manage.py add`), and an API key set (ditto, or `manage.py
set-key`).

On macOS, Settings and draft-result popups run in helper processes because
Tk and pystray's Cocoa event loop cannot safely share one interpreter.
"""

import subprocess
import sys
import threading
from pathlib import Path

from core import api_key as api_key_store
from core import config
from core import icon as icon_gen
from core.logging_setup import setup_logging
from core.watch_loop import run as run_watch_loop

log = setup_logging()

stop_event = threading.Event()
pause_event = threading.Event()

_settings_process = None

_snooze_timer = None
_snooze_lock = threading.Lock()

# Generated once here, and this exact image is what core/icon.py's
# set_window_icon() re-derives for Settings — one shared source so the
# tray icon and every Tk window's title-bar icon are guaranteed to match
# instead of Settings showing Tk's default feather icon.
ICON_RUNNING = icon_gen.make_image(icon_gen.COLOR_RUNNING)
ICON_PAUSED = icon_gen.make_image(icon_gen.COLOR_PAUSED)


def start_watch_thread():
    def watch():
        last_wait_reason = None
        while not stop_event.is_set():
            if not config.list_apps():
                reason = "No apps are being watched. Add one from the tray Settings."
            elif not api_key_store.get_api_key():
                reason = "No API key set. Set one from the tray Settings."
            else:
                reason = None

            if reason is not None:
                if reason != last_wait_reason:
                    log.error(reason)
                    last_wait_reason = reason
                stop_event.wait(2)
                continue

            last_wait_reason = None
            if run_watch_loop(stop_event, pause_event) == 0:
                return
            stop_event.wait(2)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()


def on_settings(icon, item):
    # Tk and Cocoa each require ownership of the macOS GUI event loop. Running
    # Tk in this process after pystray starts Cocoa crashes in Tcl/Tk, so the
    # Settings UI gets its own Python process. Config and keychain storage are
    # shared with the tray app.
    global _settings_process
    if _settings_process is not None and _settings_process.poll() is None:
        return
    settings_script = Path(__file__).with_name("settings_app.py")
    _settings_process = subprocess.Popen([sys.executable, str(settings_script)])


def _cancel_snooze():
    """Cancels any pending auto-resume timer. Called before any other
    pause-state change so a stale snooze timer can never re-pause after a
    manual resume, or fire early after a fresh snooze."""
    global _snooze_timer
    with _snooze_lock:
        if _snooze_timer is not None:
            _snooze_timer.cancel()
            _snooze_timer = None


def on_toggle_pause(icon, item):
    _cancel_snooze()
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


def on_snooze(minutes):
    def handler(icon, item):
        global _snooze_timer
        _cancel_snooze()
        pause_event.set()
        icon.icon = ICON_PAUSED
        log.info("snoozed for %d minutes", minutes)

        def resume():
            pause_event.clear()
            icon.icon = ICON_RUNNING
            log.info("snooze ended, resumed")

        with _snooze_lock:
            _snooze_timer = threading.Timer(minutes * 60, resume)
            _snooze_timer.daemon = True
            _snooze_timer.start()

    return handler


def on_quit(icon, item):
    _cancel_snooze()
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
            pystray.MenuItem(
                "Snooze",
                pystray.Menu(
                    pystray.MenuItem("15 minutes", on_snooze(15)),
                    pystray.MenuItem("30 minutes", on_snooze(30)),
                    pystray.MenuItem("60 minutes", on_snooze(60)),
                ),
            ),
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    def setup(icon):
        # pystray icons start hidden. Passing a custom setup callback replaces
        # its default callback, which would otherwise set this for us.
        icon.visible = True
        start_watch_thread()

    icon.run(setup=setup)


if __name__ == "__main__":
    if sys.platform not in ("win32", "darwin"):
        log.error("Unsupported platform: %s", sys.platform)
        sys.exit(1)
    main()
