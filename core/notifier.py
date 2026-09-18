"""The nudge popup: a small, dismissible, always-on-top window anchored
near the actual textbox (via the focused control's screen bounding rect),
rather than a generic OS notification-center toast in a corner you might
not even glance at. Visually the same idea as v1's in-page banner.

Unlike v1, there's no "Send anyway"/"let me edit" choice — v2 isn't hooked
to a send gesture at all (see PLAN.md's "why idle-based, not gesture-based"),
so there's nothing for a button to gate. It's purely informational: it
shows up, you glance at it, it auto-dismisses (or click it to close early).

Both outcomes of an actual Jev check show something: `notify()` for
concerns, `notify_ok()` (a brief green checkmark) when it came back clean.
Given the call can take several seconds (see jev_client.py's latency
warning), silence on the clean path is easy to mistake for "still working"
or "broken" — showing something either way makes it legible that a check
actually ran.

Built on tkinter (stdlib, ships with Python on both Windows and macOS) so
this same module should work unchanged once the macOS backend exists.
"""

import logging
import threading
import tkinter as tk

log = logging.getLogger("jev-send-guard")

WIDTH = 280
SCREEN_MARGIN = 16
BG = "#202124"


def _position(root, anchor_rect):
    root.update_idletasks()
    width = root.winfo_width() or WIDTH
    height = root.winfo_height()

    if anchor_rect:
        # Trust the accessibility API's coordinates as-is — they're already
        # in real virtual-desktop space, which can include negative values
        # on a multi-monitor setup where a display sits above/left of the
        # primary one. Clamping against tkinter's winfo_screenwidth/height
        # (which only reports the *primary* monitor) would misplace the
        # popup onto the wrong monitor entirely — this is what caused the
        # very first version of this to render invisibly off-screen.
        #
        # Always place it ABOVE the field, not below: compose boxes (Discord,
        # Gmail, Slack) sit near the bottom of their window/screen, so
        # "below" routinely pushed the popup past the screen edge — which is
        # what made it look like it rendered "under the application."
        _left, top, right, _bottom = anchor_rect
        x = right - width
        y = top - height - 8
    else:
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        x = screen_w - width - SCREEN_MARGIN
        y = screen_h - height - SCREEN_MARGIN - 40

    root.geometry(f"{width}x{height}+{int(x)}+{int(y)}")


def _show_popup(build_content, anchor_rect, auto_dismiss_ms):
    try:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.97)
        except tk.TclError:
            pass

        frame = tk.Frame(root, bg=BG, padx=14, pady=12)
        frame.pack(fill="both", expand=True)

        def dismiss(_event=None):
            root.destroy()

        build_content(frame, dismiss)
        root.bind("<Button-1>", dismiss)

        _position(root, anchor_rect)
        root.after(auto_dismiss_ms, dismiss)
        root.mainloop()
    except Exception:
        log.exception("notifier popup failed")


CONCERN_RED = "#f28b82"
CONCERN_YELLOW = "#fdd663"
OK_GREEN = "#81c995"

_SEVERITY_STYLE = {
    "error": {"icon": "⛔", "color": CONCERN_RED},
    "warning": {"icon": "⚠", "color": CONCERN_YELLOW},
}


def _build_concerns(items):
    """items: list of (message: str, severity: "error" | "warning"). The
    popup's title reflects the worst severity present; each bullet is
    styled with its own — e.g. an unprofessional-only result shows a
    yellow warning throughout, but adding one impolite result turns the
    title red even though the unprofessional bullet stays yellow."""

    def build(frame, dismiss):
        overall = "error" if any(sev == "error" for _msg, sev in items) else "warning"
        overall_style = _SEVERITY_STYLE[overall]

        tk.Label(
            frame,
            text=f"{overall_style['icon']} Before you send — Jev noticed:",
            fg=overall_style["color"],
            bg=BG,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            justify="left",
            wraplength=WIDTH - 28,
        ).pack(fill="x")

        for message, severity in items:
            style = _SEVERITY_STYLE[severity]
            tk.Label(
                frame,
                text=f"{style['icon']} {message}",
                fg=style["color"],
                bg=BG,
                font=("Segoe UI", 9),
                anchor="w",
                justify="left",
                wraplength=WIDTH - 28,
            ).pack(fill="x", pady=(4, 0))

        dismiss_label = tk.Label(
            frame,
            text="Dismiss",
            fg="#8ab4f8",
            bg=BG,
            font=("Segoe UI", 9, "underline"),
            cursor="hand2",
        )
        dismiss_label.pack(anchor="e", pady=(8, 0))
        dismiss_label.bind("<Button-1>", dismiss)

    return build


def _build_ok():
    def build(frame, _dismiss):
        tk.Label(
            frame,
            text="✓ Looks good",
            fg=OK_GREEN,
            bg=BG,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x")

    return build


def notify(items, anchor_rect=None):
    """Fire-and-forget: shows the concerns popup in a background thread so
    the watch loop keeps polling while it's up. `items` is a list of
    (message, severity) tuples, severity being "error" or "warning".
    `anchor_rect` is the focused control's (left, top, right, bottom) in
    screen coordinates."""
    thread = threading.Thread(
        target=_show_popup,
        args=(_build_concerns(items), anchor_rect, 8000),
        daemon=True,
    )
    thread.start()


def notify_ok(anchor_rect=None):
    """Brief green checkmark confirming a check ran and found nothing —
    the "clean" counterpart to notify(), so silence never has to be
    interpreted as either outcome."""
    thread = threading.Thread(
        target=_show_popup,
        args=(_build_ok(), anchor_rect, 2500),
        daemon=True,
    )
    thread.start()
