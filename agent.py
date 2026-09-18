"""Entrypoint: watches allowlisted apps' focused text fields and nudges via
Jev when a draft looks curt or ask-less. See PLAN.md for the full design.

    uv run agent.py

Ctrl+C to stop. Requires at least one app added via `manage.py add`, and an
API key set via `manage.py set-key` (or a TYPESAFE_API_KEY env var for
local testing).

Logging: a detailed trace always goes to ~/.jev-send-guard/agent.log
(DEBUG level — every focus change and text read). The console only shows
INFO+ (transitions and triggers) unless JEV_DEBUG=1 is set, which mirrors
the DEBUG trace to the console too.
"""

import logging
import os
import sys
import time
from pathlib import Path

_PROCESS_START = time.perf_counter()

POLL_INTERVAL_SEC = 0.3
LOG_PATH = Path.home() / ".jev-send-guard" / "agent.log"


def _setup_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jev-send-guard")
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


# Logging is set up before the slower imports below so each one can be timed
# individually — the aggregate "core module imports took 18.86s" reading was
# too coarse to tell which import was actually the culprit, and 18.86s is
# suspiciously close to the earlier network-latency bug's range, suggesting
# one of these imports is doing something COM/network-bound, not just
# loading Python code.
log = _setup_logging()


t = time.perf_counter()
from core import api_key as api_key_store  # noqa: E402

log.debug("import core.api_key took %.2fs", time.perf_counter() - t)

t = time.perf_counter()
from core import config  # noqa: E402

log.debug("import core.config took %.2fs", time.perf_counter() - t)

t = time.perf_counter()
from core import jev_client  # noqa: E402

log.debug("import core.jev_client took %.2fs", time.perf_counter() - t)

t = time.perf_counter()
from core import notifier  # noqa: E402

log.debug("import core.notifier took %.2fs", time.perf_counter() - t)

t = time.perf_counter()
from core import pre_filter  # noqa: E402

log.debug("import core.pre_filter took %.2fs", time.perf_counter() - t)

t = time.perf_counter()
from core.idle_watcher import IdleWatcher  # noqa: E402

log.debug("import core.idle_watcher took %.2fs", time.perf_counter() - t)


def _preview(text, limit=80):
    if text is None:
        return "None"
    text = repr(text)
    return text if len(text) <= limit else text[:limit] + "…'"


def run():
    t = time.perf_counter()
    if sys.platform == "win32":
        from platform_backends import windows as backend
    elif sys.platform == "darwin":
        log.error("macOS support isn't wired up yet.")
        return 1
    else:
        log.error("Unsupported platform: %s", sys.platform)
        return 1
    log.debug("platform backend import (incl. UI Automation/COM init) took %.2fs", time.perf_counter() - t)

    t = time.perf_counter()
    watched_apps = config.list_apps()
    log.debug("config.list_apps() took %.2fs", time.perf_counter() - t)
    if not watched_apps:
        log.error("No apps are being watched. Run `manage.py add` first.")
        return 1

    t = time.perf_counter()
    key = api_key_store.get_api_key()
    log.debug("api_key_store.get_api_key() took %.2fs", time.perf_counter() - t)
    if not key:
        log.error(
            "No API key set. Run `manage.py set-key` first (or set "
            "TYPESAFE_API_KEY for local testing)."
        )
        return 1

    log.debug("total startup: %.2fs", time.perf_counter() - _PROCESS_START)
    log.info("Watching:")
    for app in watched_apps:
        log.info("  - %s (%s)", app["label"], app["process_name"])
    log.info("Log file: %s", LOG_PATH)
    log.info("Ctrl+C to stop.")

    idle_watcher = IdleWatcher()
    last_runtime_id = None
    last_logged_text = object()  # sentinel, never equal to a real text value
    was_watched = False

    while True:
        control = backend.get_focused_control()
        if control is None:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        runtime_id = backend.safe_runtime_id(control)
        focus_changed = runtime_id != last_runtime_id
        if focus_changed:
            last_runtime_id = runtime_id
            idle_watcher.reset()
            last_logged_text = object()

        process_name = backend.get_process_name(backend.get_process_id(control))
        watched = bool(process_name) and config.is_watched(process_name, {"apps": watched_apps})

        if focus_changed:
            log.debug(
                "focus -> process=%r watched=%s runtime_id=%r",
                process_name, watched, runtime_id,
            )

        if not watched:
            if was_watched:
                log.debug("focus left watched app")
            was_watched = False
            time.sleep(POLL_INTERVAL_SEC)
            continue
        was_watched = True

        if backend.is_password_field(control):
            log.debug("skip: password/secure field")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        text = backend.get_control_text(control)
        if text != last_logged_text:
            log.debug("text read: %s", _preview(text))
            last_logged_text = text

        idle_watcher.observe(text)

        due_text = idle_watcher.due()
        if due_text is not None:
            log.info("idle threshold reached, evaluating: %s", _preview(due_text))
            anchor_rect = backend.get_bounding_rect(control)
            handle_draft(due_text, key, anchor_rect)

        time.sleep(POLL_INTERVAL_SEC)


def handle_draft(text, key, anchor_rect=None):
    if not pre_filter.should_check(text):
        log.info("pre-filter: skip (too short / plain ack)")
        return

    log.debug("calling Jev...")
    result = jev_client.check_draft(key, text)
    log.info("Jev result: %s", result)

    if result is None:
        log.info("Jev call failed, failing open silently (no false-positive checkmark)")
        return

    if not result["curt"] and not result["missing_ask"]:
        log.info("no concerns, showing checkmark")
        notifier.notify_ok(anchor_rect)
        return

    messages = []
    if result["curt"]:
        messages.append("This might read as curt or blunt.")
    if result["missing_ask"]:
        messages.append("Doesn't seem to have a clear ask.")
    log.info("notifying: %s (anchor_rect=%s)", messages, anchor_rect)
    notifier.notify(messages, anchor_rect)


if __name__ == "__main__":
    try:
        sys.exit(run() or 0)
    except KeyboardInterrupt:
        pass
