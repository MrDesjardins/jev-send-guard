"""Headless CLI entrypoint: watches allowlisted apps and nudges via Jev.

    uv run agent.py

Ctrl+C to stop. Requires at least one app added via `manage.py add`, and an
API key set via `manage.py set-key` (or a TYPESAFE_API_KEY env var for
local testing).

For a friendlier setup experience — a system tray icon with a Settings
window instead of console commands — use `tray_app.py` instead. Both share
the same watch loop (core/watch_loop.py); this one just runs it directly
on the main thread with no pause/resume control.
"""

import sys
import threading

from core.watch_loop import run

if __name__ == "__main__":
    stop_event = threading.Event()
    try:
        sys.exit(run(stop_event) or 0)
    except KeyboardInterrupt:
        stop_event.set()
