"""The nudge popup: a small, dismissible, always-on-top window anchored
near the actual textbox (via the focused control's screen bounding rect),
rather than a generic OS notification-center toast in a corner you might
not even glance at. Visually the same idea as v1's in-page banner.

Unlike v1, there's no "Send anyway"/"let me edit" choice — v2 isn't hooked
to a send gesture at all (see PLAN.md's "why idle-based, not gesture-based"),
so there's nothing for a button to gate. It's purely informational: it
shows up, you glance at it, it auto-dismisses (or click Dismiss to close
early).

Every actual Jev check first shows a fixed progress panel with one row per
question.  It is then replaced by the complete stable result, including
clean rows as well as concerns.  Given the call can take several seconds
(see jev_client.py's latency warning), the immediate neutral feedback makes
the guard feel responsive without streaming jumpy verdicts.

Only one popup is ever shown at a time: each new request replaces whatever
is currently up — otherwise a fixed "curt/impolite" warning could stay on
screen indefinitely while a later clean result's checkmark shows up
alongside it.

Card look (accent bar + rounded corners) is shared conceptually with
notification_app.py's native macOS panel — same colors, same icons, same
"colored strip + icon + text" layout — even though the two are built with
completely different toolkits (this file: tkinter; macOS: AppKit), since
a plain Tk window can't host AppKit's non-activating panel and vice versa.

Architecture note: Windows uses one persistent Tk interpreter on a dedicated
UI thread, and each new request replaces the current popup. macOS uses a
separate helper containing a native non-activating AppKit panel: a Tk popup
helper becomes the active app and steals keyboard focus from the message being
composed.
"""

import json
import logging
import queue
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk

log = logging.getLogger("jev-send-guard")

# The progress/result card has four labelled rows.  Give their concise
# verdicts enough room to stay on one line rather than truncating them at the
# editor's edge.
WIDTH = 380
ACCENT_WIDTH = 5
SCREEN_MARGIN = 16
BG = "#26282b"  # a slightly lifted "card" surface, not flat black
BORDER = "#3c4043"  # 1px outline standing in for the drop shadow Tk can't give a plain window

CONCERN_RED = "#f28b82"
CONCERN_YELLOW = "#fdd663"
OK_GREEN = "#81c995"
CHECKING_GRAY = "#bdc1c6"

_SEVERITY_STYLE = {
    "error": {"icon": "⛔", "color": CONCERN_RED},
    "warning": {"icon": "⚠", "color": CONCERN_YELLOW},
}

_request_queue = queue.Queue()
_start_lock = threading.Lock()
_started = False
_popup_process = None
_popup_process_lock = threading.Lock()


def _position(win, anchor_rect):
    win.update_idletasks()
    width = win.winfo_width() or WIDTH
    height = win.winfo_height()

    if anchor_rect:
        # Trust the accessibility API's coordinates as-is — they're already
        # in real virtual-desktop space, which can include negative values
        # on a multi-monitor setup where a display sits above/left of the
        # primary one. Clamping against tkinter's winfo_screenwidth/height
        # (which only reports the *primary* monitor) would misplace the
        # popup onto the wrong monitor entirely — this is what caused an
        # earlier version of this to render invisibly off-screen.
        #
        # Always place it ABOVE the field, not below: compose boxes (Discord,
        # Gmail, Slack) sit near the bottom of their window/screen, so
        # "below" routinely pushed the popup past the screen edge — which is
        # what made it look like it rendered "under the application."
        _left, top, right, _bottom = anchor_rect
        x = right - width
        y = top - height - 8
    else:
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        x = screen_w - width - SCREEN_MARGIN
        y = screen_h - height - SCREEN_MARGIN - 40

    win.geometry(f"{width}x{height}+{int(x)}+{int(y)}")


def _build_card(frame, accent_color):
    """Turns the bare Toplevel frame into a bordered card with a colored
    severity accent bar down the left edge, and returns the padded content
    frame everything else should be packed into."""
    frame.configure(bg=BORDER)
    inner = tk.Frame(frame, bg=BORDER)
    inner.pack(fill="both", expand=True, padx=1, pady=1)  # the 1px border showing through

    accent = tk.Frame(inner, bg=accent_color, width=ACCENT_WIDTH)
    accent.pack(side="left", fill="y")

    content = tk.Frame(inner, bg=BG, padx=14, pady=12)
    content.pack(side="left", fill="both", expand=True)
    return content


def _row(parent, icon_char, text, color, font_size=9, bold=False):
    """An icon + text pair on one line, the icon rendered larger and given
    its own column so it reads as a real glyph rather than being crammed
    into the message string."""
    row = tk.Frame(parent, bg=BG)
    row.pack(fill="x", pady=(0, 5))
    tk.Label(row, text=icon_char, fg=color, bg=BG, font=("Segoe UI", font_size + 4)).pack(
        side="left", anchor="n"
    )
    tk.Label(
        row,
        text=text,
        fg=color,
        bg=BG,
        font=("Segoe UI", font_size, "bold" if bold else "normal"),
        anchor="w",
        justify="left",
        wraplength=WIDTH - ACCENT_WIDTH - 28 - 26,
    ).pack(side="left", fill="x", expand=True, padx=(6, 0))


def _build_concerns(items):
    """items: list of (message: str, severity: "error" | "warning"). The
    popup's title reflects the worst severity present; each bullet is
    styled with its own — e.g. an unprofessional-only result shows a
    yellow warning throughout, but adding one impolite result turns the
    title red even though the unprofessional bullet stays yellow."""

    def build(frame, dismiss):
        overall = "error" if any(sev == "error" for _msg, sev in items) else "warning"
        overall_style = _SEVERITY_STYLE[overall]
        content = _build_card(frame, overall_style["color"])

        _row(
            content,
            overall_style["icon"],
            "Before you send — Jev noticed:",
            overall_style["color"],
            font_size=10,
            bold=True,
        )

        for message, severity in items:
            # Defense in depth: jev_client.get_question_defs() already
            # clamps severity to a known value, but this lookup must never
            # KeyError on unexpected data reaching it some other way — that
            # would raise inside a tkinter callback Tk swallows silently,
            # meaning the concern gets counted but the popup never appears.
            style = _SEVERITY_STYLE.get(severity, _SEVERITY_STYLE["warning"])
            _row(content, style["icon"], message, style["color"])

        dismiss_label = tk.Label(
            content,
            text="Dismiss",
            fg="#8ab4f8",
            bg=BG,
            font=("Segoe UI", 9, "underline"),
            cursor="hand2",
        )
        dismiss_label.pack(anchor="e", pady=(4, 0))
        dismiss_label.bind("<Button-1>", dismiss)

    return build


def _build_ok():
    def build(frame, _dismiss):
        content = _build_card(frame, OK_GREEN)
        _row(content, "✓", "Looks good", OK_GREEN, font_size=10, bold=True)

    return build


def _build_checking(labels):
    """A fixed-size progress panel, shown while one batched Jev call runs."""
    def build(frame, _dismiss):
        content = _build_card(frame, CHECKING_GRAY)
        _row(content, "◌", "Jev is checking your draft", CHECKING_GRAY, font_size=10, bold=True)
        for label in labels:
            _row(content, "◌", f"{label}: Checking…", CHECKING_GRAY)

    return build


def _build_results(rows):
    """Render every check in a stable layout after a progress panel.

    Each row is ``(label, concern, message, severity)``.  Keeping the same
    title-plus-one-row-per-question layout as `_build_checking` makes the
    transition legible rather than a sequence of popups that move or grow.
    """
    def build(frame, _dismiss):
        flagged = [row for row in rows if row[1]]
        overall = "error" if any(row[3] == "error" for row in flagged) else "warning"
        accent = (
            _SEVERITY_STYLE[overall]["color"] if flagged else OK_GREEN
        )
        content = _build_card(frame, accent)
        title = "Before you send — Jev noticed:" if flagged else "Jev checked your draft"
        title_icon = _SEVERITY_STYLE[overall]["icon"] if flagged else "✓"
        _row(content, title_icon, title, accent, font_size=10, bold=True)
        for label, concern, message, severity in rows:
            if concern:
                style = _SEVERITY_STYLE.get(severity, _SEVERITY_STYLE["warning"])
                _row(content, style["icon"], f"{label}: {message}", style["color"])
            else:
                _row(content, "✓", f"{label}: Looks good", OK_GREEN)

    return build


def _build_unavailable(labels):
    """Explain that a batch timed out instead of silently removing progress."""
    def build(frame, _dismiss):
        style = _SEVERITY_STYLE["warning"]
        content = _build_card(frame, style["color"])
        _row(
            content,
            style["icon"],
            "Jev couldn't finish checking",
            style["color"],
            font_size=10,
            bold=True,
        )
        for label in labels:
            _row(content, "—", f"{label}: Not checked", CHECKING_GRAY)

    return build


def _build_service_issue(message):
    def build(frame, _dismiss):
        style = _SEVERITY_STYLE["warning"]
        content = _build_card(frame, style["color"])
        _row(content, style["icon"], "TypeSafe Jev backend issue", style["color"], font_size=10, bold=True)
        _row(content, style["icon"], f"{message}. Your draft wasn't checked.", style["color"])

    return build


def _run_ui_thread():
    root = tk.Tk()
    root.withdraw()  # hidden root; only the Toplevel popups are ever shown

    state = {"popup": None}

    def dismiss_popup():
        if state["popup"] is not None:
            try:
                state["popup"].destroy()
            except tk.TclError:
                pass
            state["popup"] = None

    def replace_popup(build_content, anchor_rect, auto_dismiss_ms):
        dismiss_popup()

        win = tk.Toplevel(root)
        # Configure the native window before mapping it, so Windows does not
        # activate the editor popup and steal the typing caret.
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.98)
        except tk.TclError:
            pass

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True)

        def dismiss(_event=None):
            if state["popup"] is win:
                state["popup"] = None
            try:
                win.destroy()
            except tk.TclError:
                pass

        build_content(frame, dismiss)
        win.bind("<Button-1>", dismiss)

        _position(win, anchor_rect)
        _make_non_activating_on_windows(win)
        _round_corners_on_windows(win)
        win.deiconify()
        win.after(auto_dismiss_ms, dismiss)

        state["popup"] = win

    def poll_queue():
        try:
            while True:
                build_content, anchor_rect, auto_dismiss_ms = _request_queue.get_nowait()
                if build_content is None:
                    dismiss_popup()
                else:
                    replace_popup(build_content, anchor_rect, auto_dismiss_ms)
        except queue.Empty:
            pass
        root.after(50, poll_queue)

    root.after(50, poll_queue)
    root.mainloop()


def _ensure_started():
    global _started
    with _start_lock:
        if not _started:
            threading.Thread(target=_run_ui_thread, daemon=True).start()
            _started = True


def _make_non_activating_on_windows(win):
    """Give a Tk popup WS_EX_NOACTIVATE so it never takes editor focus."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        user32 = ctypes.windll.user32
        hwnd = win.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020,
        )  # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
    except Exception:
        log.exception("could not make Windows popup non-activating")


def _round_corners_on_windows(win):
    """Windows 11's DWM can round a plain Tk window's corners for a more
    modern "card" look. A pure cosmetic best-effort: silently does nothing
    on Windows 10 (the attribute isn't supported there — DwmSetWindowAttribute
    just returns a failure HRESULT, not an exception) or if anything else
    about this call goes wrong. Never allowed to affect whether the popup
    itself shows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        hwnd = win.winfo_id()
        preference = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(preference), ctypes.sizeof(preference)
        )
    except Exception:
        pass


def _enqueue(build_content, anchor_rect, auto_dismiss_ms):
    try:
        _ensure_started()
        _request_queue.put((build_content, anchor_rect, auto_dismiss_ms))
    except Exception:
        log.exception("notifier enqueue failed")


def _show_macos_panel(kind, items, anchor_rect, auto_dismiss_ms):
    """Show a native, non-activating popup near the focused control."""
    global _popup_process
    payload = json.dumps(
        {
            "kind": kind,
            "items": items,
            "anchor_rect": anchor_rect,
            "auto_dismiss_ms": auto_dismiss_ms,
        }
    )
    helper = Path(__file__).resolve().parents[1] / "notification_app.py"
    try:
        with _popup_process_lock:
            if _popup_process is not None and _popup_process.poll() is None:
                _popup_process.terminate()
                # A checking panel and its result use the same non-activating
                # AppKit panel class.  Starting the replacement while macOS
                # is still tearing down the old helper can leave only the
                # close animation visible.  Wait for the short-lived helper
                # to exit before creating the next one.
                try:
                    _popup_process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    _popup_process.kill()
                    _popup_process.wait()
            _popup_process = subprocess.Popen(
                [sys.executable, str(helper), payload],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.debug("started macOS popup helper pid=%s", _popup_process.pid)
    except OSError:
        log.exception("macOS popup helper failed")


def dismiss():
    """Remove the current popup as soon as the user resumes typing."""
    global _popup_process
    if sys.platform != "darwin":
        # The Windows UI thread owns Tk, so request destruction through its
        # queue instead of touching its windows across threads.
        if _started:
            _request_queue.put((None, None, None))
        return
    with _popup_process_lock:
        if _popup_process is not None and _popup_process.poll() is None:
            _popup_process.terminate()
        _popup_process = None


def notify(items, anchor_rect=None):
    """Fire-and-forget: enqueues the concerns popup for the UI thread to
    show, replacing whatever's currently up. `items` is a list of
    (message, severity) tuples, severity being "error" or "warning".
    `anchor_rect` is the focused control's (left, top, right, bottom) in
    screen coordinates."""
    if sys.platform == "darwin":
        _show_macos_panel("concerns", items, anchor_rect, 8000)
    else:
        _enqueue(_build_concerns(items), anchor_rect, 8000)


def notify_checking(labels, anchor_rect=None):
    """Show immediate, neutral progress for every question in a batch."""
    if sys.platform == "darwin":
        _show_macos_panel("checking", labels, anchor_rect, 10000)
    else:
        _enqueue(_build_checking(labels), anchor_rect, 10000)


def notify_results(rows, anchor_rect=None):
    """Replace the progress panel with the complete, stable batch result."""
    if sys.platform == "darwin":
        # JSON turns tuples into lists, which notification_app.py handles
        # identically.  Use dictionaries here so the payload stays explicit.
        payload_rows = [
            {"label": label, "concern": concern, "message": message, "severity": severity}
            for label, concern, message, severity in rows
        ]
        _show_macos_panel("results", payload_rows, anchor_rect, 6000)
    else:
        _enqueue(_build_results(rows), anchor_rect, 6000)


def notify_unavailable(labels, anchor_rect=None):
    """Replace progress with an explicit, non-judgmental request failure."""
    if sys.platform == "darwin":
        _show_macos_panel("unavailable", labels, anchor_rect, 4500)
    else:
        _enqueue(_build_unavailable(labels), anchor_rect, 4500)


def notify_service_issue(message, anchor_rect=None):
    """Explain a failed check when TypeSafe itself reports an API incident."""
    if sys.platform == "darwin":
        _show_macos_panel("service_issue", [message], anchor_rect, 7000)
    else:
        _enqueue(_build_service_issue(message), anchor_rect, 7000)


def notify_ok(anchor_rect=None):
    """Brief green checkmark confirming a check ran and found nothing —
    the "clean" counterpart to notify(), so silence never has to be
    interpreted as either outcome. Also replaces whatever's currently up."""
    if sys.platform == "darwin":
        _show_macos_panel("ok", [], anchor_rect, 2500)
    else:
        _enqueue(_build_ok(), anchor_rect, 2500)
