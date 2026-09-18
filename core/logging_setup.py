"""Shared logging setup for every entrypoint (agent.py, tray_app.py). A
detailed trace always goes to ~/.jev-send-guard/agent.log (DEBUG level);
the console only shows INFO+ unless JEV_DEBUG=1 is set. Idempotent so it's
safe to call from more than one module without doubling up handlers.
"""

import logging
import os
from pathlib import Path

LOG_PATH = Path.home() / ".jev-send-guard" / "agent.log"


def setup_logging():
    logger = logging.getLogger("jev-send-guard")
    if logger.handlers:
        return logger

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if os.environ.get("JEV_DEBUG") else logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger
