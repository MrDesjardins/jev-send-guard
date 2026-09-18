"""Windows accessibility backend: read the focused control's live text via
UI Automation, and the interactive flow used by manage.py to let a user
register a new app by switching to it and typing a couple of words.

See windows_spike.py for the original throwaway validation script this was
promoted from (Milestone 1) — that file still exists standalone for quick
manual poking, but manage.py and the real agent should import from here.
"""

import os
import sys
import time
from collections import deque
from urllib.parse import urlsplit

if sys.platform != "win32":
    raise SystemExit("platform_backends/windows.py only runs on Windows.")

import psutil  # noqa: E402
import uiautomation as auto  # noqa: E402

# UIA_IsPasswordPropertyId — checked directly since not every version of the
# `uiautomation` package exposes a friendly attribute for it, and getting
# this right matters: secure fields must never be read, allowlist or not.
UIA_IS_PASSWORD_PROPERTY_ID = 30019
UIA_VALUE_IS_READ_ONLY_PROPERTY_ID = 30046
MAX_EDITOR_HEIGHT = 400
MIN_EDITOR_HEIGHT = 12

ZERO_WIDTH_CHARS = "﻿​‌‍"

POLL_INTERVAL_SEC = 0.3
ADD_FLOW_TIMEOUT_SEC = 15

# Windows has no AppleScript-equivalent "ask the browser for its active tab
# URL" API. The address bar's on-screen text is read instead via the same UI
# Automation mechanism already used for every other text field — only the
# hostname is ever kept, the rest of the URL is discarded immediately.
_BROWSER_PROCESS_KEYS = {"chrome", "msedge", "firefox", "chromium"}
_ADDRESS_BAR_SEARCH_MAX_NODES = 400
_ADDRESS_BAR_SEARCH_MAX_DEPTH = 6
_address_bar_cache = {"hwnd": None, "control": None}


def _is_effectively_empty(text):
    if not text:
        return True
    stripped = text
    for ch in ZERO_WIDTH_CHARS:
        stripped = stripped.replace(ch, "")
    return stripped.strip() == ""


def is_password_field(control):
    try:
        return bool(control.GetPropertyValue(UIA_IS_PASSWORD_PROPERTY_ID))
    except Exception:
        return False


def is_writable_text_control(control):
    """Reject read-only and window-sized text areas before draft analysis."""
    try:
        if control.GetPropertyValue(UIA_VALUE_IS_READ_ONLY_PROPERTY_ID) is True:
            return False
        rect = control.BoundingRectangle
        if rect:
            height = rect.bottom - rect.top
            if height < MIN_EDITOR_HEIGHT or height > MAX_EDITOR_HEIGHT:
                return False
    except Exception:
        pass
    return True


def get_text_by_walking_children(control, max_depth=8):
    """Fallback for editors (e.g. Discord's Slate.js message box) whose
    Chromium/Electron accessibility tree exposes each rendered text run as
    its own leaf node, rather than aggregating one value/text string on the
    parent control."""
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
    region regardless of underlying value storage — needed for Discord's
    message box, whose ValuePattern is a placeholder), ValuePattern, the
    legacy IAccessible pattern, then a child-walk for editors that don't
    aggregate text onto the parent control at all.
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


def get_process_name(pid):
    if pid is None:
        return None
    try:
        return psutil.Process(pid).name()
    except Exception:
        return None


def get_process_id(control):
    try:
        return control.ProcessId
    except Exception:
        return None


def safe_runtime_id(control):
    try:
        return control.GetRuntimeId()
    except Exception:
        return None


# Kept as an alias so the pre-existing internal call sites in this module
# (run_add_flow) keep working without a rename churn.
_safe_runtime_id = safe_runtime_id


def _safe_name(control):
    try:
        return control.Name
    except Exception:
        return None


def get_bounding_rect(control):
    """Screen-coordinate (left, top, right, bottom) of the control, used to
    anchor the nudge popup near the actual textbox instead of a generic
    corner. None if unavailable."""
    try:
        r = control.BoundingRectangle
    except Exception:
        return None
    try:
        return (r.left, r.top, r.right, r.bottom)
    except AttributeError:
        try:
            return tuple(r)
        except Exception:
            return None


def get_focused_control():
    try:
        return auto.GetFocusedControl()
    except Exception:
        return None


def _extract_hostname(candidate_text):
    """Parses address-bar text as a URL, rejecting anything that looks like
    a search query (contains whitespace) rather than an actual address."""
    if not candidate_text:
        return None
    text = candidate_text.strip()
    if not text or " " in text:
        return None
    try:
        parsed = urlsplit(text if "://" in text else f"https://{text}")
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    hostname = parsed.hostname
    if not hostname or "." not in hostname:
        return None
    return hostname.lower()


def _find_address_bar_control(window):
    """Breadth-first search of the browser window for an Edit/ComboBox
    control whose text parses as a URL — this is the address bar. Bounded
    by node count and depth so a large page's accessibility tree can't
    stall the 300ms poll loop; BFS means the toolbar (shallow) is checked
    well before diving into page content (typically much deeper)."""
    queue = deque([(window, 0)])
    visited = 0
    while queue and visited < _ADDRESS_BAR_SEARCH_MAX_NODES:
        node, depth = queue.popleft()
        visited += 1
        try:
            control_type = node.ControlTypeName
        except Exception:
            control_type = None
        if control_type in ("EditControl", "ComboBoxControl"):
            if _extract_hostname(get_control_text(node)):
                return node
        if depth >= _ADDRESS_BAR_SEARCH_MAX_DEPTH:
            continue
        try:
            children = node.GetChildren()
        except Exception:
            children = []
        for child in children:
            queue.append((child, depth + 1))
    return None


def get_active_browser_host(process_name):
    """Return the active tab's hostname for a supported browser, or None.

    Reads the address bar's on-screen text directly (there's no tab-URL API
    on Windows the way AppleScript provides on macOS) and parses it as a
    URL. The found control is cached per foreground window so repeated
    polls just re-read its text rather than re-walking the whole window
    tree each time. Fail-closed None on anything unexpected: no foreground
    window, no matching control, or the field showing a search query
    instead of a URL.
    """
    if not process_name:
        return None
    key = process_name.lower().removesuffix(".exe")
    if key not in _BROWSER_PROCESS_KEYS:
        return None

    try:
        hwnd = auto.GetForegroundWindow()
    except Exception:
        return None
    if not hwnd:
        return None

    control = None
    if _address_bar_cache["hwnd"] == hwnd and _address_bar_cache["control"] is not None:
        try:
            _ = _address_bar_cache["control"].ControlTypeName  # liveness check
            control = _address_bar_cache["control"]
        except Exception:
            control = None

    if control is None:
        try:
            window = auto.ControlFromHandle(hwnd)
        except Exception:
            window = None
        if window is None:
            return None
        control = _find_address_bar_control(window)
        _address_bar_cache["hwnd"] = hwnd
        _address_bar_cache["control"] = control

    if control is None:
        return None

    return _extract_hostname(get_control_text(control))


def run_add_flow(prompt=input, output=print):
    """Interactive detection for `manage.py add`: waits for the user to
    switch focus to a new app and start typing, then returns
    {"process_name": ..., "control_name_hint": ...} or None on timeout.

    `prompt`/`output` are injectable for testability; default to real
    stdin/stdout.
    """
    output(
        "Switch to the window you want the guard to watch, then type a "
        "couple of words there."
    )
    prompt(
        f"Press Enter here first — you'll have {ADD_FLOW_TIMEOUT_SEC}s "
        "once you do... "
    )

    baseline = auto.GetFocusedControl()
    baseline_id = _safe_runtime_id(baseline)
    deadline = time.time() + ADD_FLOW_TIMEOUT_SEC

    while time.time() < deadline:
        control = auto.GetFocusedControl()
        if control is None:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        rid = _safe_runtime_id(control)
        if rid == baseline_id:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        if is_password_field(control):
            time.sleep(POLL_INTERVAL_SEC)
            continue

        text = get_control_text(control)
        if not _is_effectively_empty(text):
            # Never add the Settings/CLI process itself when one of its own
            # fields receives focus during the add flow.
            if control.ProcessId == os.getpid():
                time.sleep(POLL_INTERVAL_SEC)
                continue
            process_name = get_process_name(control.ProcessId)
            if not process_name:
                time.sleep(POLL_INTERVAL_SEC)
                continue
            return {
                "process_name": process_name,
                "control_name_hint": _safe_name(control),
            }

        time.sleep(POLL_INTERVAL_SEC)

    return None
