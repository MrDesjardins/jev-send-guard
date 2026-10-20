"""Keep the test run out of the real ~/.jev-send-guard/agent.log.

core/watch_loop.py calls setup_logging() at import time, which opens the
log file once for the whole process. Pointing LOG_PATH at a temp dir here,
before any test module imports it, stops fake runs (Discord.exe, a
corrupted config.toml, failing-open requests) from landing in the log the
real tray app writes to, where they read like real events.
"""

import tempfile
from pathlib import Path

from core import logging_setup

logging_setup.LOG_PATH = Path(tempfile.mkdtemp(prefix="jev-send-guard-tests-")) / "agent.log"


import pytest


@pytest.fixture(autouse=True)
def _no_real_popups(monkeypatch):
    """A test that forgets to stub a notify_* function must not open a real
    popup (a notification_app.py helper process on macOS, a Tk thread on
    Windows)."""
    from core import notifier

    class _NoPopup:
        pid = 0

        def poll(self):
            return 0

    # Stub the last step only, so tests of the notifier itself still run
    # (and can set their own Popen).
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_args, **_kwargs: _NoPopup())
    monkeypatch.setattr(notifier, "_ensure_started", lambda: None)
