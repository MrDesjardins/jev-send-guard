"""Exercises core/watch_loop.py's actual decision logic (allowlist
matching, browser-host gating, password-field skipping, pause) using
platform_backends.mock instead of a live OS session. handle_draft itself
is monkeypatched out here — these tests are about whether it gets called
at all, not what it does; see test_jev_client.py / test_pre_filter.py for
that.
"""

import threading
import time

import pytest

from core import config, watch_loop
from platform_backends.mock import MockBackend, MockControl


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    yield


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    # Real POLL_INTERVAL_SEC (0.3s) would make this suite slow; the idle
    # threshold itself (core/idle_watcher.py's IDLE_SECONDS) still uses
    # real wall-clock time, so tests sleep for real but only briefly.
    monkeypatch.setattr(watch_loop, "POLL_INTERVAL_SEC", 0.02)


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    monkeypatch.setattr("core.api_key.get_api_key", lambda provider="jev": f"fake-{provider}-key")


def _run_for(backend, seconds, pause_event=None):
    stop_event = threading.Event()
    thread = threading.Thread(target=watch_loop.run, args=(stop_event, pause_event, backend))
    thread.start()
    time.sleep(seconds)
    stop_event.set()
    thread.join(timeout=2)


def test_unwatched_app_never_triggers_handle_draft(monkeypatch):
    config.add_app(label="Discord", process_name="Discord.exe")
    calls = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append(a))

    backend = MockBackend()
    backend.focused_control = MockControl("notepad.exe", text="hello there, this is a long draft")

    _run_for(backend, 0.3)
    assert calls == []


def test_watched_app_triggers_handle_draft_after_idle(monkeypatch):
    config.add_app(label="Discord", process_name="Discord.exe")
    calls = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append(a))

    backend = MockBackend()
    backend.focused_control = MockControl(
        "Discord.exe", text="This is a long enough draft to be evaluated"
    )

    _run_for(backend, 1.0)
    assert len(calls) == 1
    assert calls[0][0] == "This is a long enough draft to be evaluated"


def test_configured_idle_seconds_delays_evaluation(monkeypatch):
    config.add_app(label="Discord", process_name="Discord.exe")
    config.set_idle_seconds(5)
    calls = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append(a))

    backend = MockBackend()
    backend.focused_control = MockControl(
        "Discord.exe", text="This is a long enough draft to be evaluated"
    )

    # Well past the 0.6s default, but short of the configured 5s.
    _run_for(backend, 1.0)
    assert calls == []


def test_password_field_never_evaluated(monkeypatch):
    config.add_app(label="Discord", process_name="Discord.exe")
    calls = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append(a))

    backend = MockBackend()
    backend.focused_control = MockControl(
        "Discord.exe", text="a secret password value here", is_password=True
    )

    _run_for(backend, 1.0)
    assert calls == []


def test_non_writable_control_never_evaluated(monkeypatch):
    config.add_app(label="Discord", process_name="Discord.exe")
    calls = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append(a))

    backend = MockBackend()
    backend.focused_control = MockControl(
        "Discord.exe", text="read-only status text of some kind", is_writable=False
    )

    _run_for(backend, 1.0)
    assert calls == []


def test_browser_without_matching_host_not_watched(monkeypatch):
    config.add_app(label="Google Docs", process_name="chrome.exe", domains=["docs.google.com"])
    calls = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append(a))

    backend = MockBackend()
    backend.focused_control = MockControl("chrome.exe", text="a long enough comment to check")
    backend.browser_hosts["chrome.exe"] = "mail.google.com"  # wrong host

    _run_for(backend, 0.5)
    assert calls == []


def test_browser_with_matching_host_is_watched(monkeypatch):
    config.add_app(label="Google Docs", process_name="chrome.exe", domains=["docs.google.com"])
    calls = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append(a))

    backend = MockBackend()
    backend.focused_control = MockControl("chrome.exe", text="a long enough comment to check")
    backend.browser_hosts["chrome.exe"] = "docs.google.com"

    _run_for(backend, 1.0)
    assert len(calls) == 1


def test_paused_skips_evaluation_entirely(monkeypatch):
    config.add_app(label="Discord", process_name="Discord.exe")
    calls = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append(a))

    backend = MockBackend()
    backend.focused_control = MockControl(
        "Discord.exe", text="a long enough draft to check here"
    )

    pause_event = threading.Event()
    pause_event.set()
    _run_for(backend, 1.0, pause_event=pause_event)
    assert calls == []


def test_disabled_questions_for_app_are_passed_through(monkeypatch):
    config.add_app(label="Discord", process_name="Discord.exe")
    config.set_app_disabled_questions("Discord.exe", ["unprofessional"])
    calls = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append((a, k)))

    backend = MockBackend()
    backend.focused_control = MockControl(
        "Discord.exe", text="This is a long enough draft to be evaluated"
    )

    _run_for(backend, 1.0)
    assert len(calls) == 1
    args, kwargs = calls[0]
    # handle_draft(due_text, anchor_rect, disabled_questions) - positional
    assert args[2] == {"unprofessional"}
    assert kwargs["app"] == "Discord"


def test_typing_discards_an_inflight_jev_result(monkeypatch):
    """Polling must continue while Jev is slow, and its old answer is useless."""
    config.add_app(label="Discord", process_name="Discord.exe")
    started = threading.Event()
    release = threading.Event()
    checking = []
    notified = []

    def slow_check(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=2)
        return {"curt": 0.9}

    monkeypatch.setattr(watch_loop.jev_client, "check_draft", slow_check)
    monkeypatch.setattr(
        watch_loop.notifier, "notify_checking", lambda *args: checking.append(args)
    )
    monkeypatch.setattr(
        watch_loop.notifier, "notify_results", lambda *args: notified.append(args)
    )

    backend = MockBackend()
    backend.focused_control = MockControl(
        "Discord.exe", text="This is a long enough draft to be evaluated"
    )
    stop_event = threading.Event()
    thread = threading.Thread(target=watch_loop.run, args=(stop_event, None, backend))
    thread.start()
    assert started.wait(timeout=2)

    # The next poll sees this edit while the old HTTP request is still
    # blocked.  Releasing the old request must not produce a popup.
    backend.focused_control.text = "This revised draft supersedes the previous one"
    time.sleep(watch_loop.POLL_INTERVAL_SEC * 3)
    release.set()
    time.sleep(watch_loop.POLL_INTERVAL_SEC * 3)
    stop_event.set()
    thread.join(timeout=2)

    assert checking == [(LABELS, None, ["Jev"], NO_CONTEXT)]
    assert notified == []


# Without conversation context: "Answers the thread" requires previous
# messages, so it isn't asked.
NO_CONTEXT = "No conversation context found"
LABELS = ["Tone", "Clear ask", "Professionalism", "Respectfulness", "Sarcasm", "Accuracy"]


def test_checking_panel_is_replaced_with_one_row_per_result(monkeypatch):
    checking = []
    results = []
    monkeypatch.setattr(
        watch_loop.notifier, "notify_checking", lambda *args: checking.append(args)
    )
    monkeypatch.setattr(
        watch_loop.notifier, "notify_results", lambda *args: results.append(args)
    )
    monkeypatch.setattr(
        watch_loop.jev_client,
        "check_draft",
        lambda *_args, **_kwargs: {
            "curt": 0.1,
            "missing_ask": 0.8,
            "unprofessional": 0.2,
            "impolite": 0.0,
            "sarcasm": 0.3,
            "accuracy": 0.05,
        },
    )
    monkeypatch.setattr(watch_loop.stats, "record_check", lambda *_args, **_kwargs: None)

    watch_loop.handle_draft("Please decide whether we should proceed.")

    assert checking == [(LABELS, None, ["Jev"], NO_CONTEXT)]
    rows, columns, anchor, note = results[0]
    assert rows == [
        ("Tone", False, "This might read as curt or blunt.", "error", [0.1]),
        ("Clear ask", True, "Doesn't seem to have a clear ask.", "error", [0.8]),
        ("Professionalism", False, "This might read as unprofessional.", "warning", [0.2]),
        ("Respectfulness", False, "This might read as impolite or disrespectful.", "error", [0.0]),
        ("Sarcasm", False, "This might read as sarcastic or mocking.", "error", [0.3]),
        ("Accuracy", False, "This might state something that isn't true.", "warning", [0.05]),
    ]
    assert [name for name, _ms in columns] == ["Jev"]
    assert isinstance(columns[0][1], int)
    assert anchor is None
    assert note == "No conversation context found"


def test_both_providers_run_in_parallel_with_one_column_each(monkeypatch):
    config.set_provider("both")
    results = []
    recorded = []
    both_started = threading.Barrier(2, timeout=2)
    calls = {}

    def fake_check(key, _text, questions, provider, context=None):
        calls[provider] = key
        # Fails with BrokenBarrierError unless both calls are in flight at once.
        both_started.wait()
        score = 0.9 if provider == "mercury" else 0.2
        return {question_key: score for question_key in questions}

    monkeypatch.setattr(watch_loop.notifier, "notify_checking", lambda *_args: None)
    monkeypatch.setattr(
        watch_loop.notifier, "notify_results", lambda *args: results.append(args)
    )
    monkeypatch.setattr(watch_loop.jev_client, "check_draft", fake_check)
    monkeypatch.setattr(
        watch_loop.stats, "record_check", lambda outcomes, app=None: recorded.append(outcomes)
    )

    watch_loop.handle_draft("Please decide whether we should proceed.")

    assert calls == {"jev": "fake-jev-key", "mercury": "fake-mercury-key"}
    rows, columns, _anchor, _note = results[0]
    assert [name for name, _ms in columns] == ["Jev", "Mercury"]
    assert all(isinstance(ms, int) for _name, ms in columns)
    # Flagged because Mercury is confident, even though Jev isn't.
    assert rows[0] == ("Tone", True, "This might read as curt or blunt.", "error", [0.2, 0.9])
    assert recorded == [{
        "curt": "error",
        "missing_ask": "error",
        "unprofessional": "warning",
        "impolite": "error",
        "sarcasm": "error",
        "accuracy": "warning",
    }]


def test_one_failed_provider_still_shows_the_other(monkeypatch):
    config.set_provider("both")
    results = []

    def fake_check(_key, _text, questions, provider, context=None):
        if provider == "jev":
            return None
        return {question_key: 0.1 for question_key in questions}

    monkeypatch.setattr(watch_loop.notifier, "notify_checking", lambda *_args: None)
    monkeypatch.setattr(
        watch_loop.notifier, "notify_results", lambda *args: results.append(args)
    )
    monkeypatch.setattr(watch_loop.jev_client, "check_draft", fake_check)
    monkeypatch.setattr(watch_loop.stats, "record_check", lambda *_args, **_kwargs: None)

    watch_loop.handle_draft("Please decide whether we should proceed.")

    rows, columns, _anchor, _note = results[0]
    assert columns[0] == ("Jev", None)
    assert columns[1][0] == "Mercury"
    assert rows[0][1] is False
    assert rows[0][4] == [None, 0.1]


def test_timeout_replaces_progress_with_an_unavailable_state(monkeypatch):
    checking = []
    unavailable = []
    monkeypatch.setattr(
        watch_loop.notifier, "notify_checking", lambda *args: checking.append(args)
    )
    monkeypatch.setattr(
        watch_loop.notifier, "notify_unavailable", lambda *args: unavailable.append(args)
    )
    monkeypatch.setattr(watch_loop.jev_client, "check_draft", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(watch_loop.status_client, "get_api_issue", lambda: None)

    watch_loop.handle_draft("Please decide whether we should proceed.")

    assert checking == [(LABELS, None, ["Jev"], NO_CONTEXT)]
    assert unavailable == [(LABELS, None, ["Jev"])]


def test_timeout_with_typesafe_api_incident_notifies_the_user(monkeypatch):
    issue = []
    monkeypatch.setattr(watch_loop.notifier, "notify_checking", lambda *_args: None)
    monkeypatch.setattr(watch_loop.notifier, "notify_unavailable", lambda *_args: None)
    monkeypatch.setattr(watch_loop.notifier, "notify_service_issue", lambda *args: issue.append(args))
    monkeypatch.setattr(watch_loop.jev_client, "check_draft", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(watch_loop.status_client, "get_api_issue", lambda: "TypeSafe API is degraded")

    watch_loop.handle_draft("Please decide whether we should proceed.")

    assert issue == [("TypeSafe API is degraded", None)]


def test_mercury_only_failure_does_not_consult_typesafe_status(monkeypatch):
    config.set_provider("mercury")
    monkeypatch.setattr(watch_loop.notifier, "notify_checking", lambda *_args: None)
    monkeypatch.setattr(watch_loop.notifier, "notify_unavailable", lambda *_args: None)
    monkeypatch.setattr(watch_loop.jev_client, "check_draft", lambda *_args, **_kwargs: None)

    def fail_if_called():
        raise AssertionError("TypeSafe status is irrelevant to a Mercury failure")

    monkeypatch.setattr(watch_loop.status_client, "get_api_issue", fail_if_called)
    watch_loop.handle_draft("Please decide whether we should proceed.")


def test_custom_questions_are_recorded_in_stats(monkeypatch, tmp_path):
    config.add_custom_question("jargon", "Too much jargon?", "Lots of jargon.", severity="error")
    config.add_custom_question("emoji", "Too many emoji?", "Lots of emoji.", severity="warning")
    monkeypatch.setattr(watch_loop.stats, "STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(watch_loop.notifier, "notify_checking", lambda *_args: None)
    monkeypatch.setattr(watch_loop.notifier, "notify_results", lambda *_args: None)

    def fake_check(_key, _text, questions, provider, context=None):
        scores = {question_key: 0.1 for question_key in questions}
        scores.update(jargon=0.9, emoji=0.8)
        return scores

    monkeypatch.setattr(watch_loop.jev_client, "check_draft", fake_check)

    watch_loop.handle_draft("Please decide whether we should proceed.", app="Discord")

    per_question = watch_loop.stats.summary(app="Discord")["per_question"]
    assert per_question["jargon"] == {"good": 0, "warning": 0, "error": 1}
    assert per_question["emoji"] == {"good": 0, "warning": 1, "error": 0}
    assert per_question["curt"] == {"good": 1, "warning": 0, "error": 0}


def test_context_is_sent_and_enables_context_questions(monkeypatch):
    from core.context import ConversationContext

    sent = []
    monkeypatch.setattr(watch_loop.notifier, "notify_checking", lambda *_args: None)
    monkeypatch.setattr(watch_loop.notifier, "notify_results", lambda *_args: None)
    monkeypatch.setattr(watch_loop.stats, "record_check", lambda *_args, **_kwargs: None)

    def fake_check(_key, _text, questions, provider, context=None):
        sent.append((set(questions), context))
        return {question_key: 0.1 for question_key in questions}

    monkeypatch.setattr(watch_loop.jev_client, "check_draft", fake_check)

    conversation = ConversationContext(place="general", previous_messages=["sam: ready?"])
    watch_loop.handle_draft("Please decide whether we should proceed.", context=conversation)
    watch_loop.handle_draft("Please decide whether we should proceed.")

    with_context, without_context = sent
    assert "unanswered" in with_context[0]
    assert with_context[1] == {"place": "general", "previous_messages": ["sam: ready?"]}
    assert "unanswered" not in without_context[0]
    assert without_context[1] == {}


def test_context_is_extracted_only_when_enabled(monkeypatch):
    config.add_app(label="Discord", process_name="Discord.exe")
    calls = []
    extracted = []
    monkeypatch.setattr(watch_loop, "handle_draft", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(
        watch_loop.context, "extract", lambda backend, control: extracted.append(control)
    )

    backend = MockBackend()
    backend.focused_control = MockControl(
        "Discord.exe", text="This is a long enough draft to be evaluated"
    )
    _run_for(backend, 1.0)
    assert len(extracted) == 1
    assert calls[0]["context"] is None  # the stub returned nothing

    config.set_send_context(False)
    extracted.clear()
    backend.focused_control = MockControl(
        "Discord.exe", text="Another long enough draft to be evaluated"
    )
    _run_for(backend, 1.0)
    assert extracted == []


def test_context_note_describes_what_the_checks_saw():
    from core.context import ConversationContext

    assert watch_loop.context_note(None, enabled=False) == "Conversation context is off"
    assert watch_loop.context_note(None) == "No conversation context found"
    assert watch_loop.context_note(
        ConversationContext(place="general (channel)", previous_messages=["a", "b"])
    ) == "💬 Used context: general (channel) · 2 previous messages"
    assert watch_loop.context_note(
        ConversationContext(window_title="Doc - Google Docs", previous_messages=["a"])
    ) == "💬 Used context: Doc - Google Docs · 1 previous message"
    long_title = ConversationContext(window_title="x" * 60)
    assert watch_loop.context_note(long_title) == (
        "💬 Used context: " + "x" * 39 + "… · no previous messages found"
    )


def test_both_fills_in_the_first_answer_before_the_second(monkeypatch):
    """With Jev + Mercury, the card updates when the faster one answers."""
    monkeypatch.setattr(watch_loop.config, "get_providers", lambda *_args: ["jev", "mercury"])
    monkeypatch.setattr(watch_loop.api_key_store, "get_api_key", lambda *_args: "key")
    mercury_may_answer = threading.Event()
    progress = []
    results = []

    def check(_key, _text, questions=None, provider="jev", context=None):
        if provider == "mercury":
            assert mercury_may_answer.wait(timeout=2)
            return {key: 0.9 for key in questions}
        return {key: 0.1 for key in questions}

    def on_progress(*args):
        progress.append(args)
        mercury_may_answer.set()

    monkeypatch.setattr(watch_loop.jev_client, "check_draft", check)
    monkeypatch.setattr(watch_loop.notifier, "notify_checking", lambda *_args: None)
    monkeypatch.setattr(watch_loop.notifier, "notify_progress", on_progress)
    monkeypatch.setattr(watch_loop.notifier, "notify_results", lambda *args: results.append(args))
    monkeypatch.setattr(watch_loop.stats, "record_check", lambda *_args, **_kwargs: None)

    watch_loop.handle_draft("Please decide whether we should proceed.")

    assert len(progress) == 1
    rows, columns, _anchor, _note = progress[0]
    assert columns[0][0] == "Jev" and len(columns[0]) == 2
    assert columns[1] == ("Mercury", None, True)
    assert rows[0][4] == [0.1, None]
    rows, columns, _anchor, _note = results[0]
    assert [len(column) for column in columns] == [2, 2]
    assert rows[0][4] == [0.1, 0.9]
