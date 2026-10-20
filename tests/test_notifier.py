from core import notifier


def test_macos_replacement_waits_for_the_old_helper_to_exit(monkeypatch):
    class PreviousHelper:
        def __init__(self):
            self.terminated = False
            self.wait_timeouts = []

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)

    class NewHelper:
        pid = 12345

    previous = PreviousHelper()
    new = NewHelper()
    monkeypatch.setattr(notifier, "_popup_process", previous)
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_args, **_kwargs: new)

    notifier._show_macos_panel("results", [], None, 6000)

    assert previous.terminated is True
    assert previous.wait_timeouts == [0.25]
    assert notifier._popup_process is new


ROWS = [
    ("Tone", False, "This might read as curt or blunt.", "error", [0.11, 0.03]),
    ("Sarcasm", True, "This might read as sarcastic or mocking.", "error", [0.41, 0.71]),
]


def test_loading_card_has_the_results_layout():
    """Same title line, provider columns and one row per check as the
    result, with "…" where the scores will be."""
    view = notifier.table_view(
        notifier.pending_rows(["Tone", "Sarcasm"], 2),
        [("Jev", None, True), ("Mercury", None, True)],
        "No conversation context found",
    )
    assert view["state"] == "checking"
    assert view["title"] == "Jev + Mercury are checking your draft"
    assert view["headers"] == ["Jev\nchecking…", "Mercury\nchecking…"]
    assert [row["text"] for row in view["rows"]] == ["Tone", "Sarcasm"]
    assert all(row["state"] == "checking" for row in view["rows"])
    assert all(cell == {"text": "…", "state": "checking"} for row in view["rows"] for cell in row["cells"])
    assert view["context_note"] == "No conversation context found"


def test_one_provider_answered_fills_its_column_only():
    rows = [
        ("Tone", False, "This might read as curt or blunt.", "error", [0.11, None]),
        ("Sarcasm", True, "This might read as sarcastic or mocking.", "error", [0.81, None]),
    ]
    view = notifier.table_view(rows, [("Jev", 108), ("Mercury", None, True)])
    assert view["headers"] == ["Jev\n108 ms", "Mercury\nchecking…"]
    tone, sarcasm = view["rows"]
    # Mercury could still flag Tone: not final yet.
    assert tone["state"] == "checking"
    assert tone["cells"] == [{"text": "11%", "state": "ok"}, {"text": "…", "state": "checking"}]
    # Jev already flagged Sarcasm: final.
    assert sarcasm["state"] == "error"
    assert view["state"] == "error"
    assert view["title"] == "Before you send — Jev + Mercury noticed:"


def test_only_mercury_left_says_so():
    rows = [("Tone", False, "m", "error", [0.11, None])]
    view = notifier.table_view(rows, [("Jev", 108), ("Mercury", None, True)])
    assert view["title"] == "Mercury is still checking…"


def test_final_card_names_checks_without_looks_good():
    view = notifier.table_view(ROWS, [("Jev", 108), ("Mercury", 283)])
    tone, sarcasm = view["rows"]
    assert tone == {
        "state": "ok",
        "text": "Tone",
        "cells": [{"text": "11%", "state": "ok"}, {"text": "3%", "state": "ok"}],
    }
    assert sarcasm["text"] == "Sarcasm: This might read as sarcastic or mocking."
    assert sarcasm["cells"][1] == {"text": "71%", "state": "error"}
    assert "Looks good" not in str(view)


def test_scores_are_labelled_as_the_chance_of_a_problem():
    view = notifier.table_view(ROWS, [("Jev", 108), ("Mercury", 283)])
    assert view["score_header"] == "Chance of a problem"
    assert view["caption"] == "Lower is better · 70%+ is flagged"


def test_failed_provider_shows_failed_and_dashes():
    rows = [("Tone", False, "m", "error", [None, 0.1])]
    view = notifier.table_view(rows, [("Jev", None), ("Mercury", 283)])
    assert view["headers"][0] == "Jev\nfailed"
    assert view["rows"][0]["cells"][0] == {"text": "—", "state": "none"}
    assert view["state"] == "ok"
