"""platform_backends/macos.py's draft-box size check, with the AX calls
replaced by a fake tree (only importable on macOS)."""

import sys

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS backend", allow_module_level=True)

from platform_backends import macos


WINDOW = "window"


def fake_tree(monkeypatch, box_height, window_height=1290.0):
    rects = {"box": (459.0, 700.0, 2014.0, 700.0 + box_height)}
    if window_height is not None:
        rects[WINDOW] = (64.0, 39.0, 2040.0, 39.0 + window_height)
    attrs = {"AXRole": "AXTextArea", "AXEditable": True, "AXWindow": WINDOW if window_height else None}
    monkeypatch.setattr(macos, "_get_attr", lambda element, attribute: attrs.get(attribute) if element == "box" else None)
    monkeypatch.setattr(macos, "get_bounding_rect", lambda element: rects.get(element))


def test_a_grown_slack_composer_is_still_a_draft(monkeypatch):
    """Live Slack: a long draft grows the composer to 478 px of a 1290 px
    window (37%) — it was skipped under the old fixed 400 px cap."""
    fake_tree(monkeypatch, 478)
    assert macos.is_writable_text_control("box") is True


def test_a_window_sized_text_area_is_not_a_draft(monkeypatch):
    fake_tree(monkeypatch, 1100)
    assert macos.is_writable_text_control("box") is False


def test_unknown_window_falls_back_to_the_fixed_cap(monkeypatch):
    fake_tree(monkeypatch, 478, window_height=None)
    assert macos.is_writable_text_control("box") is False
    fake_tree(monkeypatch, 300, window_height=None)
    assert macos.is_writable_text_control("box") is True


def test_tiny_hidden_text_areas_are_still_skipped(monkeypatch):
    fake_tree(monkeypatch, 1)
    assert macos.is_writable_text_control("box") is False
