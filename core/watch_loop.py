"""The watch loop itself, shared by the headless CLI (agent.py) and the
tray app (tray_app.py): polls the focused control in allowlisted apps,
debounces on typing pauses, and runs the pre-filter/Jev check.

Runs until `stop_event` is set. `pause_event`, if given and set, keeps the
loop alive (so resuming is instant) but skips all evaluation — used by the
tray app's Pause/Resume menu item. The allowlist is re-read from disk each
iteration so changes made in the tray's Settings window take effect live,
without restarting the loop.
"""

import sys
import time

from core import api_key as api_key_store
from core import config
from core import jev_client
from core import notifier
from core import pre_filter
from core import stats
from core.idle_watcher import IdleWatcher
from core.logging_setup import LOG_PATH, setup_logging

log = setup_logging()

POLL_INTERVAL_SEC = 0.3


def _preview(text, limit=80):
    if text is None:
        return "None"
    text = repr(text)
    return text if len(text) <= limit else text[:limit] + "…'"


def get_backend():
    if sys.platform == "win32":
        from platform_backends import windows as backend

        return backend
    if sys.platform == "darwin":
        from platform_backends import macos as backend

        return backend
    return None


def run(stop_event, pause_event=None, backend=None):
    """`backend`, if given, is used as-is instead of picking one by
    sys.platform — this is how tests inject platform_backends.mock's
    MockBackend to exercise this loop's logic without a live OS session."""
    backend = backend or get_backend()
    if backend is None:
        log.error("Unsupported platform: %s", sys.platform)
        return 1

    watched_apps = config.list_apps()
    if not watched_apps:
        log.error("No apps are being watched. Add one from the tray Settings, or `manage.py add`.")
        return 1

    key = api_key_store.get_api_key()
    if not key:
        log.error(
            "No API key set. Set one from the tray Settings, or `manage.py set-key` "
            "(or set TYPESAFE_API_KEY for local testing)."
        )
        return 1

    log.info("Watching:")
    for app in watched_apps:
        log.info("  - %s (%s)", app["label"], app["process_name"])
    log.info("Log file: %s", LOG_PATH)

    idle_watcher = IdleWatcher()
    last_runtime_id = None
    last_logged_text = object()  # sentinel, never equal to a real text value
    last_evaluated_signature = None
    was_watched = False
    had_focused_control = True

    while not stop_event.is_set():
        if pause_event is not None and pause_event.is_set():
            time.sleep(POLL_INTERVAL_SEC)
            continue

        control = backend.get_focused_control()
        if control is None:
            if had_focused_control:
                active_process = None
                get_active_app = getattr(backend, "get_focused_application", None)
                if get_active_app is not None:
                    active_app = get_active_app()
                    active_process = backend.get_process_name(
                        backend.get_process_id(active_app)
                    )
                log.debug(
                    "no focused accessibility control (active app=%r)",
                    active_process,
                )
                had_focused_control = False
            time.sleep(POLL_INTERVAL_SEC)
            continue
        if not had_focused_control:
            log.debug("focused accessibility control became available")
            had_focused_control = True

        runtime_id = backend.safe_runtime_id(control)
        focus_changed = runtime_id != last_runtime_id
        if focus_changed:
            last_runtime_id = runtime_id
            idle_watcher.reset()
            last_logged_text = object()

        process_name = backend.get_process_name(backend.get_process_id(control))
        current_apps = config.list_apps()
        active_host = None
        get_active_browser_host = getattr(backend, "get_active_browser_host", None)
        if (
            get_active_browser_host is not None
            and config.app_requires_browser_host(process_name)
        ):
            active_host = get_active_browser_host(process_name)
        watched = bool(process_name) and config.is_watched(
            process_name, domain=active_host, config={"apps": current_apps}
        )

        if focus_changed:
            log.debug(
                "focus -> process=%r host=%r watched=%s runtime_id=%r",
                process_name, active_host, watched, runtime_id,
            )

        if not watched:
            if was_watched:
                log.debug("focus left watched app")
                notifier.dismiss()
            was_watched = False
            time.sleep(POLL_INTERVAL_SEC)
            continue
        was_watched = True

        if backend.is_password_field(control):
            # The loop polls continuously. Log a rejection when focus enters
            # the field, not once per poll while it remains there.
            if focus_changed:
                log.debug("skip: password/secure field")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        is_writable = getattr(backend, "is_writable_text_control", None)
        if is_writable is not None and not is_writable(control):
            if focus_changed:
                log.debug("skip: non-editor, read-only, or implausibly sized text control")
            idle_watcher.reset()
            notifier.dismiss()
            time.sleep(POLL_INTERVAL_SEC)
            continue

        text = backend.get_control_text(control)
        if text != last_logged_text:
            log.debug("text read: %s", _preview(text))
            notifier.dismiss()
            last_logged_text = text

        idle_watcher.observe(text)

        due_text = idle_watcher.due()
        if due_text is not None:
            evaluation_signature = (process_name, runtime_id, due_text)
            if evaluation_signature == last_evaluated_signature:
                if focus_changed:
                    log.debug("skip: unchanged draft was already evaluated before refocus")
                time.sleep(POLL_INTERVAL_SEC)
                continue
            log.info("idle threshold reached, evaluating: %s", _preview(due_text))
            anchor_rect = backend.get_bounding_rect(control)
            # Reuse current_apps (already read above for the watched-check)
            # instead of a second, redundant config.toml read via get_app().
            matched_app = next(
                (a for a in current_apps if a["process_name"].lower() == process_name.lower()),
                None,
            )
            disabled_questions = set((matched_app or {}).get("disabled_questions", []))
            handle_draft(due_text, key, anchor_rect, disabled_questions)
            last_evaluated_signature = evaluation_signature

        time.sleep(POLL_INTERVAL_SEC)

    log.info("watch loop stopped")
    return 0


def handle_draft(text, key, anchor_rect=None, disabled_questions=None):
    if not pre_filter.should_check(text):
        log.info("pre-filter: skip (too short / plain ack)")
        return

    log.debug("calling Jev...")
    result = jev_client.check_draft(key, text, disabled_keys=disabled_questions)
    log.info("Jev result: %s", result)

    if result is None:
        log.info("Jev call failed, failing open silently (no false-positive checkmark)")
        return

    if not result:
        log.info("no active questions for this app (all disabled), skipping")
        return

    flagged = [q_key for q_key, concern in result.items() if concern]
    stats.record_check(flagged)

    if not flagged:
        log.info("no concerns, showing checkmark")
        notifier.notify_ok(anchor_rect)
        return

    items = [(jev_client.message_for(q_key), jev_client.severity_of(q_key)) for q_key in flagged]
    log.info("notifying: %s (anchor_rect=%s)", items, anchor_rect)
    notifier.notify(items, anchor_rect)
