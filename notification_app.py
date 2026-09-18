"""Native, non-activating macOS popup used by Jev Send Guard."""

import json
import sys

import AppKit
import Foundation
import Quartz

from core.logging_setup import setup_logging


log = setup_logging()


WIDTH = 300
MARGIN = 14


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


def _label(text, color, y, height, bold=False):
    field = AppKit.NSTextField.alloc().initWithFrame_(
        Foundation.NSMakeRect(MARGIN, y, WIDTH - MARGIN * 2, height)
    )
    field.setStringValue_(text)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setTextColor_(color)
    field.setFont_(AppKit.NSFont.boldSystemFontOfSize_(12) if bold else AppKit.NSFont.systemFontOfSize_(12))
    field.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    field.setUsesSingleLineMode_(False)
    return field


def show(payload):
    log.debug("native popup: starting kind=%s", payload["kind"])
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    items = payload["items"]
    lines = 1 if payload["kind"] == "ok" else max(1, len(items) + 1)
    height = 34 + lines * 28
    style = AppKit.NSBorderlessWindowMask | AppKit.NSNonactivatingPanelMask
    panel = PassivePanel.alloc().initWithContentRect_styleMask_backing_defer_(
        Foundation.NSMakeRect(0, 0, WIDTH, height),
        style,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    panel.setBackgroundColor_(_color(0.125, 0.13, 0.14))
    panel.setOpaque_(True)
    panel.setLevel_(AppKit.NSFloatingWindowLevel)
    panel.setHidesOnDeactivate_(False)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setIgnoresMouseEvents_(True)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    )

    content = panel.contentView()
    if payload["kind"] == "ok":
        content.addSubview_(_label("✓ Looks good", _color(0.51, 0.79, 0.60), 18, 22, bold=True))
    else:
        content.addSubview_(_label("Before you send — Jev noticed:", _color(0.95, 0.55, 0.51), height - 34, 20, bold=True))
        for index, (message, severity) in enumerate(items):
            color = _color(0.95, 0.55, 0.51) if severity == "error" else _color(0.99, 0.84, 0.39)
            icon = "⛔" if severity == "error" else "⚠"
            y = height - 62 - index * 28
            content.addSubview_(_label(f"{icon} {message}", color, y, 22))

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
