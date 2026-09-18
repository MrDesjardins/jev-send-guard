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
    monkeypatch.setattr("core.api_key.get_api_key", lambda: "fake-key")


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
    # handle_draft(due_text, key, anchor_rect, disabled_questions) - positional
    assert args[3] == {"unprofessional"}
