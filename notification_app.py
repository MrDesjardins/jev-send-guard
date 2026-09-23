"""Native, non-activating macOS popup used by Jev Send Guard.

Visually matches core/notifier.py's Windows Tk popup — same colors, same
icons, same "colored accent bar + icon + text" card layout — even though
the two are built with entirely different toolkits (AppKit here; a plain
Tk window can't host a non-activating panel the way NSPanel can, and
NSPanel doesn't exist outside AppKit).
"""

import json
import sys

import AppKit
import Foundation
import Quartz

from core.logging_setup import setup_logging


log = setup_logging()


# Keep in sync with core/notifier.py: the Windows and native macOS cards use
# the same roomy, one-line-per-check layout.
WIDTH = 380
MARGIN = 14
ACCENT_WIDTH = 5
ICON_COLUMN_WIDTH = 22
CARD_COLOR = (0.145, 0.157, 0.169)  # matches core/notifier.py's BG "#26282b"
CHECKING_COLOR = (0.74, 0.76, 0.78)
OK_COLOR = (0.51, 0.79, 0.60)
ERROR_COLOR = (0.95, 0.55, 0.51)
WARNING_COLOR = (0.99, 0.84, 0.39)


class PassivePanel(AppKit.NSPanel):
    """A floating panel that never becomes the key/main window."""

    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


def _color(red, green, blue):
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1.0)


def _origin_near_anchor(anchor_rect, width, height):
    """Translate AX's top-left display coordinates into AppKit coordinates."""
    _left, top, right, bottom = anchor_rect
    # Match the AX point to one physical display. Quartz and AX share the
    # top-left virtual coordinate system, while each AppKit screen reports
    # its own bottom-left frame in a differently offset virtual space.
    point_x = right - 1
    for screen in AppKit.NSScreen.screens():
        display_id = int(screen.deviceDescription()["NSScreenNumber"])
        bounds = Quartz.CGDisplayBounds(display_id)
        if (
            bounds.origin.x <= point_x < bounds.origin.x + bounds.size.width
            and bounds.origin.y <= top < bounds.origin.y + bounds.size.height
        ):
            frame = screen.frame()
            x = frame.origin.x + (right - bounds.origin.x) - width
            # Prefer above the editor, leaving an eight-point gap. In AppKit
            # coordinates the popup's bottom sits at the AX top edge.
            if top - bounds.origin.y >= height + 8:
                y = frame.origin.y + (bounds.origin.y + bounds.size.height - top) + 8
            else:
                # A field at the top edge gets a panel below it instead.
                y = frame.origin.y + (bounds.origin.y + bounds.size.height - bottom) - height - 8
            return x, y

    # A spanning/unknown AX rectangle is rare; put it on the main display
    # rather than allowing an invisible off-screen panel.
    visible = AppKit.NSScreen.mainScreen().visibleFrame()
    return visible.origin.x + visible.size.width - width - 16, visible.origin.y + 56


def _position(panel, anchor_rect):
    frame = panel.frame()
    if anchor_rect:
        x, y = _origin_near_anchor(anchor_rect, frame.size.width, frame.size.height)
    else:
        visible = AppKit.NSScreen.mainScreen().visibleFrame()
        x = visible.origin.x + visible.size.width - frame.size.width - 16
        y = visible.origin.y + 56
    panel.setFrameOrigin_(Foundation.NSMakePoint(x, y))
    log.debug("native popup: anchor=%r frame_origin=(%.0f, %.0f) size=(%.0f, %.0f)", anchor_rect, x, y, frame.size.width, frame.size.height)


def _label(text, color, y, height, x=MARGIN, width=None, bold=False, font_size=12):
    if width is None:
        width = WIDTH - x - MARGIN
    field = AppKit.NSTextField.alloc().initWithFrame_(
        Foundation.NSMakeRect(x, y, width, height)
    )
    field.setStringValue_(text)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setTextColor_(color)
    field.setFont_(
        AppKit.NSFont.boldSystemFontOfSize_(font_size) if bold else AppKit.NSFont.systemFontOfSize_(font_size)
    )
    field.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    field.setUsesSingleLineMode_(False)
    return field


def _row(content, icon, text, color, y, height=22, bold=False):
    """An icon + text pair on one line, the icon in its own column so it
    reads as a real glyph rather than being crammed into the text string —
    matches core/notifier.py's Windows layout."""
    content.addSubview_(
        _label(icon, color, y, height, x=MARGIN, width=ICON_COLUMN_WIDTH, font_size=15)
    )
    text_x = MARGIN + ICON_COLUMN_WIDTH + 6
    content.addSubview_(
        _label(text, color, y, height, x=text_x, width=WIDTH - text_x - MARGIN, bold=bold)
    )


def _apply_card_style(panel, accent_color):
    """Rounded corners + native drop shadow via a layer-backed content
    view, with a colored accent bar down the left edge. Best-effort and
    never allowed to be the reason the popup doesn't show at all — if
    anything here fails (untested against a real pyobjc/AppKit build; see
    PLAN.md), the caller falls back to the old flat opaque panel."""
    content = panel.contentView()
    content.setWantsLayer_(True)
    layer = content.layer()
    layer.setCornerRadius_(10.0)
    layer.setMasksToBounds_(True)
    layer.setBackgroundColor_(_color(*CARD_COLOR).CGColor())

    panel.setOpaque_(False)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())
    panel.setHasShadow_(True)

    height = content.frame().size.height
    accent = AppKit.NSView.alloc().initWithFrame_(
        Foundation.NSMakeRect(0, 0, ACCENT_WIDTH, height)
    )
    accent.setWantsLayer_(True)
    accent.layer().setBackgroundColor_(accent_color.CGColor())
    content.addSubview_(accent)


def show(payload):
    log.debug("native popup: starting kind=%s", payload["kind"])
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    kind = payload["kind"]
    items = payload["items"]
    lines = 1 if kind == "ok" else max(1, len(items) + 1)
    height = 34 + lines * 28
    style = AppKit.NSBorderlessWindowMask | AppKit.NSNonactivatingPanelMask
    panel = PassivePanel.alloc().initWithContentRect_styleMask_backing_defer_(
        Foundation.NSMakeRect(0, 0, WIDTH, height),
        style,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    panel.setLevel_(AppKit.NSFloatingWindowLevel)
    panel.setHidesOnDeactivate_(False)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setIgnoresMouseEvents_(True)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    )

    if kind == "ok":
        overall_error = False
        overall_color = _color(*OK_COLOR)
    elif kind == "checking":
        overall_error = False
        overall_color = _color(*CHECKING_COLOR)
    elif kind == "results":
        concerns = [item for item in items if item["concern"]]
        overall_error = any(item["severity"] == "error" for item in concerns)
        if not concerns:
            overall_color = _color(*OK_COLOR)
        else:
            overall_color = _color(*(ERROR_COLOR if overall_error else WARNING_COLOR))
    elif kind == "unavailable":
        overall_error = False
        overall_color = _color(*WARNING_COLOR)
    elif kind == "service_issue":
        overall_error = False
        overall_color = _color(*WARNING_COLOR)
    else:
        # Matches core/notifier.py's Windows popup: the title reflects the
        # *worst* severity actually present, not a hardcoded red — an
        # unprofessional-only (warning) result should show yellow
        # throughout, not a red title implying an error-level concern.
        overall_error = any(severity == "error" for _message, severity in items)
        overall_color = _color(*ERROR_COLOR) if overall_error else _color(*WARNING_COLOR)

    try:
        _apply_card_style(panel, overall_color)
    except Exception:
        log.exception("native popup: rounded-corner card styling failed, falling back to a flat panel")
        panel.setBackgroundColor_(_color(*CARD_COLOR))
        panel.setOpaque_(True)

    content = panel.contentView()
    if kind == "ok":
        # y=18 matches the vertical position the original (pre-accent-bar)
        # version of this popup used for its single line of text.
        _row(content, "✓", "Looks good", overall_color, 18, bold=True)
    elif kind == "checking":
        _row(content, "◌", "Jev is checking your draft", overall_color, height - 34, height=20, bold=True)
        for index, label in enumerate(items):
            y = height - 62 - index * 28
            _row(content, "◌", f"{label}: Checking…", overall_color, y)
    elif kind == "results":
        concerns = [item for item in items if item["concern"]]
        title = "Before you send — Jev noticed:" if concerns else "Jev checked your draft"
        title_icon = "⛔" if overall_error else ("⚠" if concerns else "✓")
        _row(content, title_icon, title, overall_color, height - 34, height=20, bold=True)
        for index, item in enumerate(items):
            y = height - 62 - index * 28
            if item["concern"]:
                color = _color(*(ERROR_COLOR if item["severity"] == "error" else WARNING_COLOR))
                icon = "⛔" if item["severity"] == "error" else "⚠"
                text = f"{item['label']}: {item['message']}"
            else:
                color = _color(*OK_COLOR)
                icon = "✓"
                text = f"{item['label']}: Looks good"
            _row(content, icon, text, color, y)
    elif kind == "unavailable":
        _row(content, "⚠", "Jev couldn't finish checking", overall_color, height - 34, height=20, bold=True)
        for index, label in enumerate(items):
            y = height - 62 - index * 28
            _row(content, "—", f"{label}: Not checked", _color(*CHECKING_COLOR), y)
    elif kind == "service_issue":
        _row(content, "⚠", "TypeSafe Jev backend issue", overall_color, height - 34, height=20, bold=True)
        _row(content, "⚠", f"{items[0]}. Your draft wasn't checked.", overall_color, height - 62)
    else:
        title_icon = "⛔" if overall_error else "⚠"
        _row(content, title_icon, "Before you send — Jev noticed:", overall_color, height - 34, height=20, bold=True)
        for index, (message, severity) in enumerate(items):
            color = _color(*ERROR_COLOR) if severity == "error" else _color(*WARNING_COLOR)
            icon = "⛔" if severity == "error" else "⚠"
            y = height - 62 - index * 28
            _row(content, icon, message, color, y)

    _position(panel, payload["anchor_rect"])
    panel.orderFrontRegardless()
    log.debug("native popup: ordered front without activation")

    def close(_timer):
        panel.orderOut_(None)
        app.terminate_(None)

    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        payload["auto_dismiss_ms"] / 1000.0, False, close
    )
    app.run()


if __name__ == "__main__":
    try:
        show(json.loads(sys.argv[1]))
    except Exception:
        log.exception("native popup helper failed")
        raise
