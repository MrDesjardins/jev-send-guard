"""macOS accessibility backend: read the focused control's live text via the
Accessibility API (AX*), mirroring platform_backends/windows.py's interface
exactly so agent.py and manage.py work unchanged on either OS.

Unlike windows.py, this hasn't been run against a live macOS session yet —
it's built from the documented AXUIElement/pyobjc API shape, following the
same fallback chain validated on Windows (direct value -> child-walk,
password-field hard skip before ever reading text). Treat the first real
run here the way windows_spike.py was treated for Windows: the step that
actually validates this, not a formality — particularly worth confirming on
Electron apps (Discord, Slack), where Chromium's accessibility tree needed
the child-walk fallback on Windows and likely will here too.
"""

import logging
import os
import subprocess
import sys
import time
from urllib.parse import urlsplit

if sys.platform != "darwin":
    raise SystemExit("platform_backends/macos.py only runs on macOS.")

import psutil  # noqa: E402

log = logging.getLogger("jev-send-guard")

# These AX* symbols live in ApplicationServices in most pyobjc versions, but
# have moved between ApplicationServices/HIServices/Quartz across releases —
# this hasn't been run against a real pyobjc install yet (see PLAN.md's
# Milestone 1b), so fall back to Quartz if the direct import fails rather
# than hard-failing on an import path that turns out to differ.
try:
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCreateSystemWide,
        AXUIElementGetPid,
        AXIsProcessTrusted,
        AXValueGetValue,
        kAXChildrenAttribute,
        kAXDescriptionAttribute,
        kAXFocusedApplicationAttribute,
        kAXFocusedUIElementAttribute,
        kAXPositionAttribute,
        kAXRoleAttribute,
        kAXSizeAttribute,
        kAXSubroleAttribute,
        kAXTitleAttribute,
        kAXValueAttribute,
    )
except ImportError:  # pragma: no cover - fallback for pyobjc versions that split these into Quartz
    from Quartz import (
        AXUIElementCopyAttributeValue,
        AXUIElementCreateSystemWide,
        AXUIElementGetPid,
        AXIsProcessTrusted,
        AXValueGetValue,
        kAXChildrenAttribute,
        kAXDescriptionAttribute,
        kAXFocusedApplicationAttribute,
        kAXFocusedUIElementAttribute,
        kAXPositionAttribute,
        kAXRoleAttribute,
        kAXSizeAttribute,
        kAXSubroleAttribute,
        kAXTitleAttribute,
        kAXValueAttribute,
    )

try:
    from ApplicationServices import kAXValueCGPointType, kAXValueCGSizeType
except ImportError:
    try:
        from Quartz import kAXValueCGPointType, kAXValueCGSizeType
    except ImportError:  # pragma: no cover - last resort: the raw AXValueType enum values
        kAXValueCGPointType = 1
        kAXValueCGSizeType = 2

ZERO_WIDTH_CHARS = "﻿​‌‍"
POLL_INTERVAL_SEC = 0.3
ADD_FLOW_TIMEOUT_SEC = 15
MAX_EDITOR_HEIGHT = 400
MIN_EDITOR_HEIGHT = 12
# PyObjC does not export this constant in every ApplicationServices build.
kAXEditableAttribute = "AXEditable"
_EDITABLE_TEXT_ROLES = {"AXTextArea", "AXTextField", "AXComboBox"}

# AppleScript is the only supported no-extension way on macOS to obtain an
# active browser tab URL. The lookup is restricted to well-known browser app
# names, so a detected process name never becomes executable script text.
_BROWSER_APP_NAMES = {
    "google chrome": "Google Chrome",
    "chromium": "Chromium",
    "microsoft edge": "Microsoft Edge",
    "firefox": "Firefox",
}

_SYSTEM_WIDE = AXUIElementCreateSystemWide()


def _is_effectively_empty(text):
    if not text:
        return True
    stripped = text
    for ch in ZERO_WIDTH_CHARS:
        stripped = stripped.replace(ch, "")
    return stripped.strip() == ""


def _get_attr(element, attribute):
    if element is None:
        return None
    try:
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    if err != 0:
        return None
    return value


def get_focused_control():
    try:
        return _get_attr(_SYSTEM_WIDE, kAXFocusedUIElementAttribute)
    except Exception:
        return None


def get_focused_application():
    """Return the active app's AX element, even if it exposes no text field."""
    try:
        return _get_attr(_SYSTEM_WIDE, kAXFocusedApplicationAttribute)
    except Exception:
        return None


def _system_attribute_with_status(attribute):
    """Read a system-wide AX attribute and retain its error for diagnostics."""
    try:
        error, value = AXUIElementCopyAttributeValue(_SYSTEM_WIDE, attribute, None)
    except Exception as exc:
        return None, f"exception {type(exc).__name__}: {exc}"
    if error != 0:
        return None, f"AXError {error}"
    return value, "ok"


def get_process_id(control):
    if control is None:
        return None
    try:
        err, pid = AXUIElementGetPid(control, None)
    except Exception:
        return None
    if err != 0:
        return None
    return pid


def get_process_name(pid):
    if pid is None:
        return None
    try:
        return psutil.Process(pid).name()
    except Exception:
        return None


def get_active_browser_host(process_name):
    """Return the active HTTP(S) tab hostname for a supported browser.

    This invokes macOS Automation, which prompts the user once to authorize
    Python to communicate with that browser. Only the hostname is returned;
    the full URL is immediately discarded. Any unavailable browser, denied
    permission, non-web tab, or AppleScript error is a fail-closed ``None``.
    """
    if not process_name:
        return None
    key = process_name.lower().removesuffix(".exe")
    app_name = _BROWSER_APP_NAMES.get(key)
    if app_name is None:
        return None
    script = (
        f'tell application "{app_name}"\n'
        "  if not (exists front window) then return \"\"\n"
        "  return URL of active tab of front window\n"
        "end tell"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        url = urlsplit(result.stdout.strip())
        if url.scheme not in {"http", "https"}:
            return None
        return url.hostname.lower() if url.hostname else None
    except ValueError:
        return None


def is_password_field(control):
    """Checked directly via role/subrole since getting this right matters:
    secure fields must never be read, allowlist or not."""
    if control is None:
        return False
    role = _get_attr(control, kAXRoleAttribute) or ""
    subrole = _get_attr(control, kAXSubroleAttribute) or ""
    return "SecureTextField" in f"{role} {subrole}"


def is_writable_text_control(control):
    """Reject read-only and window-sized AX text areas (e.g. Slack threads)."""
    if control is None:
        return False
    if _get_attr(control, kAXRoleAttribute) not in _EDITABLE_TEXT_ROLES:
        return False
    editable = _get_attr(control, kAXEditableAttribute)
    if editable is False:
        return False
    rect = get_bounding_rect(control)
    if rect is not None:
        height = rect[3] - rect[1]
        if height < MIN_EDITOR_HEIGHT or height > MAX_EDITOR_HEIGHT:
            return False
    return True


def get_bounding_rect(control):
    """Screen-coordinate (left, top, right, bottom), matching the Windows
    backend's format. AX position/size are already in the same top-left-
    origin global display coordinate system Windows uses, so no axis flip
    is needed despite AppKit's own bottom-left-origin convention elsewhere."""
    if control is None:
        return None

    position_value = _get_attr(control, kAXPositionAttribute)
    size_value = _get_attr(control, kAXSizeAttribute)
    if position_value is None or size_value is None:
        return None

    try:
        ok1, point = AXValueGetValue(position_value, kAXValueCGPointType, None)
        ok2, size = AXValueGetValue(size_value, kAXValueCGSizeType, None)
    except Exception:
        return None
    if not ok1 or not ok2:
        return None

    try:
        left, top = point.x, point.y
        width, height = size.width, size.height
    except AttributeError:
        return None

    return (left, top, left + width, top + height)


def safe_runtime_id(control):
    """No true stable element ID exists in the AX API the way UI Automation
    exposes RuntimeId, so this builds a best-effort fingerprint from pid +
    role + subrole + bounding rect. Good enough for "did focus move to a
    different control" — an occasional false "changed" just resets the idle
    timer a beat early, which is safe; it never causes a wrong evaluation."""
    if control is None:
        return None
    return (
        get_process_id(control),
        _get_attr(control, kAXRoleAttribute),
        _get_attr(control, kAXSubroleAttribute),
        get_bounding_rect(control),
    )


def get_text_by_walking_children(control, max_depth=8):
    """Fallback for editors (e.g. Discord's Slate.js message box) whose
    Chromium/Electron accessibility tree exposes each rendered text run as
    its own leaf node, rather than aggregating one value string on the
    parent control — the same shape this turned out to need on Windows."""
    pieces = []

    def walk(node, depth):
        if depth > max_depth or node is None:
            return
        try:
            children = _get_attr(node, kAXChildrenAttribute)
        except Exception:
            children = None
        if not children:
            value = (
                _get_attr(node, kAXValueAttribute)
                or _get_attr(node, kAXTitleAttribute)
                or _get_attr(node, kAXDescriptionAttribute)
            )
            if isinstance(value, str) and value:
                pieces.append(value)
            return
        for child in children:
            walk(child, depth + 1)

    walk(control, 0)
    return "".join(pieces)


def get_control_text(control):
    """Best-effort current text of a control, or None if unreadable.
    Mirrors windows.py's fallback chain: direct value first, then a
    child-walk for rich editors that don't aggregate text on the parent."""
    if control is None:
        return None

    value = _get_attr(control, kAXValueAttribute)
    if isinstance(value, str) and not _is_effectively_empty(value):
        return value

    walked = get_text_by_walking_children(control)
    if not _is_effectively_empty(walked):
        return walked

    return value if isinstance(value, str) else None


def run_add_flow(prompt=input, output=print):
    """Interactive detection for `manage.py add`: waits for the user to
    switch focus to a new app and start typing, then returns
    {"process_name": ..., "control_name_hint": ...} or None on timeout.

    Requires this process to have Accessibility permission (System Settings
    > Privacy & Security > Accessibility) — without it, get_focused_control()
    will just return None the whole time rather than raising, so a timeout
    here is the first thing to check for a missing-permission diagnosis.
    """
    output(
        "Switch to the window you want the guard to watch, then type a "
        "couple of words there."
    )
    prompt(
        f"Press Enter here first — you'll have {ADD_FLOW_TIMEOUT_SEC}s "
        "once you do... "
    )

    log.debug(
        "add flow: started (settings pid=%s accessibility_trusted=%s)",
        os.getpid(),
        bool(AXIsProcessTrusted()),
    )
    baseline = get_focused_control()
    baseline_id = safe_runtime_id(baseline)
    log.debug(
        "add flow: baseline pid=%r role=%r runtime_id=%r",
        get_process_id(baseline),
        _get_attr(baseline, kAXRoleAttribute),
        baseline_id,
    )
    deadline = time.time() + ADD_FLOW_TIMEOUT_SEC
    last_focus_signature = object()

    while time.time() < deadline:
        control = get_focused_control()
        if control is None:
            _focused_control, control_status = _system_attribute_with_status(
                kAXFocusedUIElementAttribute
            )
            app, app_status = _system_attribute_with_status(kAXFocusedApplicationAttribute)
            app_process_id = get_process_id(app)
            app_process_name = get_process_name(app_process_id)
            signature = (
                "focused-app",
                control_status,
                app_status,
                app_process_id,
                app_process_name,
            )
            if signature != last_focus_signature:
                log.debug(
                    "add flow: no focused control (%s); active app status=%s pid=%r process=%r",
                    control_status,
                    app_status,
                    app_process_id,
                    app_process_name,
                )
                last_focus_signature = signature
            if app_process_id is not None and app_process_id != os.getpid() and app_process_name:
                log.info(
                    "add flow: selected active app process=%r (no focused control exposed)",
                    app_process_name,
                )
                return {
                    "process_name": app_process_name,
                    "control_name_hint": None,
                }
            time.sleep(POLL_INTERVAL_SEC)
            continue

        rid = safe_runtime_id(control)
        process_id = get_process_id(control)
        role = _get_attr(control, kAXRoleAttribute)
        process_name = get_process_name(process_id)
        signature = (rid, process_id, role, process_name)
        focus_changed = signature != last_focus_signature
        if focus_changed:
            log.debug(
                "add flow: focus pid=%r process=%r role=%r runtime_id=%r",
                process_id,
                process_name,
                role,
                rid,
            )
            last_focus_signature = signature
        if rid == baseline_id:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        if is_password_field(control):
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # The Settings helper itself has editable fields (including the
        # API-key entry). Ignore them: only an app outside this process can
        # be the user-selected watch target.
        if process_id == os.getpid():
            if focus_changed:
                log.debug("add flow: ignored Settings helper control")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # Slack's Electron accessibility tree may expose the compose box as
        # an AXGroup/AXWebArea and omit its text value altogether. We only
        # store an app-level allowlist, not a particular field, so the newly
        # focused external process is enough after the user explicitly chose
        # it in the add flow.
        if not process_name:
            if focus_changed:
                log.debug("add flow: ignored external control with no process name")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        log.info("add flow: selected process=%r role=%r", process_name, role)
        return {
            "process_name": process_name,
            "control_name_hint": _get_attr(control, kAXTitleAttribute),
        }

    return None
