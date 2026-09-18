"""Milestone 1 spike (Windows): prove we can read the focused control's live
text via UI Automation, across arbitrary apps, without hooking keystrokes.

Not part of the shipped agent — throwaway validation only. If this doesn't
reliably see text in the apps you care about (Chrome, Discord desktop, Slack
desktop), that's the answer to the biggest open question in PLAN.md before
building anything else on top of it.

Windows-only. Run with native Windows Python (NOT from WSL), via uv:

    uv sync --extra windows
    uv run platform_backends/windows_spike.py

Then click into different apps/fields and type. It polls the focused
control every POLL_INTERVAL_SEC and prints when the focused element or its
text value changes. Ctrl+C to stop.
"""

import sys
import time

if sys.platform != "win32":
    raise SystemExit("windows_spike.py only runs on Windows.")

import uiautomation as auto  # noqa: E402

POLL_INTERVAL_SEC = 0.3

# UIA_IsPasswordPropertyId — checked directly via GetPropertyValue since not
# every version of the `uiautomation` package exposes a friendly attribute
# for it, and getting this right matters: secure fields must never be read.
UIA_IS_PASSWORD_PROPERTY_ID = 30019


ZERO_WIDTH_CHARS = "﻿​‌‍"


def _is_effectively_empty(text):
    if not text:
        return True
    stripped = text
    for ch in ZERO_WIDTH_CHARS:
        stripped = stripped.replace(ch, "")
    return stripped.strip() == ""


def get_text_by_walking_children(control, max_depth=8):
    """Fallback for editors (e.g. Discord's Slate.js message box) whose
    Chromium/Electron accessibility tree exposes each rendered text run as
    its own leaf node (the same shape screen readers rely on), rather than
    aggregating one value/text string on the parent control."""
    pieces = []

    def walk(node, depth):
        if depth > max_depth:
            return
        try:
            children = node.GetChildren()
        except Exception:
            children = []
        if not children:
            try:
                name = node.Name
            except Exception:
                name = None
            if name:
                pieces.append(name)
            return
        for child in children:
            walk(child, depth + 1)

    walk(control, 0)
    return "".join(pieces)


def get_control_text(control):
    """Best-effort current text of a control, or None if unreadable.

    Tries, in order: TextPattern (reads *rendered* text of a document/edit
    region regardless of underlying value storage), ValuePattern, the legacy
    IAccessible pattern, then finally a child-walk — needed for Slate.js-style
    rich editors (e.g. Discord's message box) where none of the standard
    patterns expose more than a placeholder on the parent control.
    """
    candidates = []

    try:
        pattern = control.GetTextPattern()
        if pattern:
            candidates.append(pattern.DocumentRange.GetText(-1))
    except Exception:
        pass
    try:
        pattern = control.GetValuePattern()
        if pattern:
            candidates.append(pattern.Value)
    except Exception:
        pass
    try:
        pattern = control.GetLegacyIAccessiblePattern()
        if pattern:
            candidates.append(pattern.Value)
    except Exception:
        pass

    for text in candidates:
        if not _is_effectively_empty(text):
            return text

    walked = get_text_by_walking_children(control)
    if not _is_effectively_empty(walked):
        return walked

    return candidates[0] if candidates else None


def is_password_field(control):
    try:
        return bool(control.GetPropertyValue(UIA_IS_PASSWORD_PROPERTY_ID))
    except Exception:
        return False


def describe(control):
    try:
        process_name = control.ProcessId
    except Exception:
        process_name = "?"
    try:
        name = control.Name
    except Exception:
        name = "?"
    try:
        control_type = control.ControlTypeName
    except Exception:
        control_type = "?"
    return f"pid={process_name} type={control_type} name={name!r}"


def main():
    print(f"Polling focused control every {POLL_INTERVAL_SEC}s. Ctrl+C to stop.\n")
    last_runtime_id = None
    last_text = None

    while True:
        try:
            control = auto.GetFocusedControl()
        except Exception as e:
            print(f"[error reading focused control: {e}]")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        if control is None:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        try:
            runtime_id = control.GetRuntimeId()
        except Exception:
            runtime_id = None

        if runtime_id != last_runtime_id:
            last_runtime_id = runtime_id
            last_text = None
            print(f"\n--- focus changed: {describe(control)} ---")
            if is_password_field(control):
                print("  [secure/password field — skipping text read]")
                time.sleep(POLL_INTERVAL_SEC)
                continue

        if is_password_field(control):
            time.sleep(POLL_INTERVAL_SEC)
            continue

        text = get_control_text(control)
        if text is not None and text != last_text:
            last_text = text
            preview = text if len(text) <= 200 else text[:200] + "…"
            print(f"  text: {preview!r}")

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
