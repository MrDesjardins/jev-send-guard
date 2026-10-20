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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from core import api_key as api_key_store
from core import config
from core import context
from core import jev_client
from core import notifier
from core import pre_filter
from core import status_client
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

    if not has_api_key():
        log.error(
            "No API key set for %s. Set one from the tray Settings, or `manage.py set-key` "
            "(or set %s in .env for local testing).",
            config.get_provider(),
            " / ".join(api_key_store.env_var_for(p) for p in config.get_providers()),
        )
        return 1

    # Prepare the HTTP client without blocking the watch loop or the first
    # draft evaluation.
    jev_client.warm_up_async()

    log.info("Watching:")
    for app in watched_apps:
        log.info("  - %s (%s)", app["label"], app["process_name"])
    log.info("Log file: %s", LOG_PATH)

    idle_watcher = IdleWatcher(idle_seconds=config.get_idle_seconds())
    log.info("Delay before checking: %.2fs", idle_watcher.idle_seconds)
    log.info("Provider: %s", config.get_provider())
    last_runtime_id = None
    last_logged_text = object()  # sentinel, never equal to a real text value
    last_evaluated_signature = None
    was_watched = False
    had_focused_control = True
    # A Jev request can take seconds.  Keep the watch loop responsive while
    # one runs, and use this monotonically increasing generation to make its
    # result invalid as soon as the draft or focus changes.
    evaluation_generation = 0

    def invalidate_evaluation():
        nonlocal evaluation_generation
        evaluation_generation += 1

    while not stop_event.is_set():
        if pause_event is not None and pause_event.is_set():
            invalidate_evaluation()
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
                invalidate_evaluation()
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
            invalidate_evaluation()

        process_name = backend.get_process_name(backend.get_process_id(control))
        # One config.toml read per poll: both the allowlist and the delay
        # setting are picked up live from changes made in Settings.
        current_config = config.load_config()
        current_apps = current_config["apps"]
        idle_seconds = config.get_idle_seconds(current_config)
        if idle_seconds != idle_watcher.idle_seconds:
            log.info("Delay before checking changed: %.2fs", idle_seconds)
            idle_watcher.idle_seconds = idle_seconds
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
                invalidate_evaluation()
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
            invalidate_evaluation()
            time.sleep(POLL_INTERVAL_SEC)
            continue

        text = backend.get_control_text(control)
        if text != last_logged_text:
            log.debug("text read: %s", _preview(text))
            notifier.dismiss()
            last_logged_text = text
            invalidate_evaluation()
            # The user may edit a draft and then restore its previous text
            # while its prior Jev request is still running.  That earlier
            # answer has been invalidated, so allow the restored text to be
            # evaluated again after this fresh idle window.
            idle_watcher.reset()

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
            app_label = (matched_app or {}).get("label") or process_name
            # Read here, on the polling thread that owns the accessibility
            # objects; it's bounded (core/context.py) to a fraction of a
            # second and is typically ~25ms.
            conversation = None
            if config.get_send_context(current_config):
                conversation = context.extract(backend, control)
                if conversation is None:
                    log.debug("context: none found")
                else:
                    log.info(
                        "context: source=%s place=%r messages=%d (%d nodes, %d ms)",
                        conversation.source, conversation.place,
                        len(conversation.previous_messages),
                        conversation.nodes_visited, conversation.elapsed_ms,
                    )
                    for message in conversation.previous_messages:
                        log.debug("context message: %s", _preview(message))
            # Never block accessibility polling on a remote model call: if
            # the user resumes typing, this loop immediately invalidates the
            # generation and the worker silently discards its now-stale
            # result.  Sync httpx cannot reliably abort a request already in
            # flight, but it can no longer delay polling or show stale UI.
            generation = evaluation_generation

            def is_current(generation=generation):
                return not stop_event.is_set() and generation == evaluation_generation

            threading.Thread(
                target=handle_draft,
                args=(due_text, anchor_rect, disabled_questions),
                kwargs={"is_current": is_current, "app": app_label, "context": conversation},
                daemon=True,
                name="jev-draft-check",
            ).start()
            last_evaluated_signature = evaluation_signature

        time.sleep(POLL_INTERVAL_SEC)

    log.info("watch loop stopped")
    return 0


def has_api_key(current_config=None):
    """Whether at least one of the configured providers has a key."""
    return any(api_key_store.get_api_key(p) for p in config.get_providers(current_config))


def context_note(context, enabled=True):
    """One line for the results popup saying whether — and what —
    conversation context the checks saw."""
    if not enabled:
        return "Conversation context is off"
    if context is None:
        return "No conversation context found"
    where = context.place or context.window_title or "this conversation"
    if len(where) > 40:
        where = where[:39] + "…"
    count = len(context.previous_messages)
    if count:
        plural = "" if count == 1 else "s"
        return f"💬 Used context: {where} · {count} previous message{plural}"
    return f"💬 Used context: {where} · no previous messages found"


def _timed_check(provider, text, questions, context_state=None):
    """Run one provider's check. Returns (scores or None, elapsed ms)."""
    key = api_key_store.get_api_key(provider)
    if not key:
        log.warning(
            "no %s API key (set it in Settings or %s), skipping it",
            jev_client.provider_label(provider), api_key_store.env_var_for(provider),
        )
        return None, None
    start = time.perf_counter()
    scores = jev_client.check_draft(
        key, text, questions=questions, provider=provider, context=context_state
    )
    return scores, round((time.perf_counter() - start) * 1000)


def _result_rows(active_questions, outcomes):
    """(rows, stat outcomes) for the card. ``outcomes`` holds one
    (scores or None, elapsed ms) per provider, or None for a provider that
    hasn't answered yet. A question is flagged when any provider that
    answered is confident enough; each provider's own score is still shown
    side by side."""
    rows = []
    stat_outcomes = {}
    for question_key, question in active_questions.items():
        question_scores = [
            None if outcome is None or outcome[0] is None else outcome[0].get(question_key)
            for outcome in outcomes
        ]
        concern = any(jev_client.is_concern(score) for score in question_scores)
        stat_outcomes[question_key] = question["severity"] if concern else "good"
        rows.append((
            jev_client.label_for(question_key, question),
            concern,
            question["message"],
            question["severity"],
            question_scores,
        ))
    return rows, stat_outcomes


def _result_columns(provider_names, outcomes):
    """(name, elapsed ms or None if it failed) per provider, plus a third
    True item for a provider still pending (core/notifier.py's table_view)."""
    columns = []
    for name, outcome in zip(provider_names, outcomes):
        if outcome is None:
            columns.append((name, None, True))
        else:
            scores, elapsed_ms = outcome
            columns.append((name, elapsed_ms if scores is not None else None))
    return columns


def handle_draft(
    text, anchor_rect=None, disabled_questions=None, is_current=None, app=None, context=None
):
    """Evaluate one draft and notify only if it is still the current one.

    ``is_current`` is supplied by ``run`` for asynchronous evaluations.  It
    remains optional so this small unit is also usable directly.  ``app``
    is the watched app's label, used only to bucket usage stats.
    ``context`` is core/context.py's ConversationContext (or None): it is
    sent with the draft, and gates questions that require it.
    """
    is_current = is_current or (lambda: True)
    if not pre_filter.should_check(text):
        log.info("pre-filter: skip (too short / plain ack)")
        return

    context_state = context.to_state() if context is not None else {}
    active_questions = jev_client.get_active_questions(disabled_questions, set(context_state))
    if not active_questions:
        log.info("no active questions for this app (all disabled), skipping")
        return
    if not is_current():
        return

    providers = config.get_providers()
    provider_names = [jev_client.provider_label(p) for p in providers]
    labels = [
        jev_client.label_for(question_key, question)
        for question_key, question in active_questions.items()
    ]
    note = context_note(context, enabled=config.get_send_context())
    notifier.notify_checking(labels, anchor_rect, provider_names, note)

    log.debug("calling %s...", " + ".join(provider_names))
    # "both" runs the providers side by side, so the popup waits for the
    # slower one rather than the sum of the two — and fills in each
    # provider's column as soon as it answers, in the same card.
    outcomes = [None] * len(providers)
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futures = {
            pool.submit(_timed_check, p, text, active_questions, context_state): index
            for index, p in enumerate(providers)
        }
        remaining = len(futures)
        for future in as_completed(futures):
            outcomes[futures[future]] = future.result()
            remaining -= 1
            if remaining and is_current():
                rows, _stats = _result_rows(active_questions, outcomes)
                notifier.notify_progress(
                    rows, _result_columns(provider_names, outcomes), anchor_rect, note
                )
    if not is_current():
        log.info("discarding stale result")
        return
    for name, (scores, elapsed_ms) in zip(provider_names, outcomes):
        log.info("%s result (%s ms): %s", name, elapsed_ms, scores)

    if all(scores is None for scores, _ms in outcomes):
        log.info("every provider call failed, showing unavailable state without a verdict")
        notifier.notify_unavailable(labels, anchor_rect, provider_names)
        if "jev" in providers:
            issue = status_client.get_api_issue()
            if issue and is_current():
                log.warning("TypeSafe status page reports: %s", issue)
                notifier.notify_service_issue(issue, anchor_rect)
        return

    rows, stat_outcomes = _result_rows(active_questions, outcomes)
    columns = _result_columns(provider_names, outcomes)
    # Do not count or display an answer for a draft which changed while the
    # request was completing.
    if not is_current():
        log.info("discarding stale result")
        return
    stats.record_check(stat_outcomes, app=app)

    log.info("notifying results: %s %s %r (anchor_rect=%s)", columns, rows, note, anchor_rect)
    notifier.notify_results(rows, columns, anchor_rect, note)
