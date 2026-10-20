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

from core import config

log = logging.getLogger("jev-send-guard")

# The progress/result card has one labelled row per check, plus one score
# column per provider.  Give their concise verdicts enough room to stay on
# one line rather than truncating them at the editor's edge.
WIDTH = 440
SCORE_COLUMN_WIDTH = 70
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


def _join_names(provider_names):
    return " + ".join(provider_names)


_STATE_STYLE = {
    "error": {"icon": "⛔", "color": CONCERN_RED},
    "warning": {"icon": "⚠", "color": CONCERN_YELLOW},
    "ok": {"icon": "✓", "color": OK_GREEN},
    "checking": {"icon": "◌", "color": CHECKING_GRAY},
    "none": {"icon": "", "color": CHECKING_GRAY},
}

SCORE_HEADER = "Chance of a problem"


def score_caption():
    from core.jev_client import NOUL_THRESHOLD

    return f"Lower is better · {round(NOUL_THRESHOLD * 100)}%+ is flagged"


def _verb(names):
    return "is" if len(names) == 1 else "are"


def table_view(rows, columns, context_note=None):
    """Everything the checking/results card shows, computed once for both
    the Tk (Windows) and AppKit (macOS, notification_app.py) renderers so
    the two can't drift, and so a loading card, a half-answered "Both"
    card and the final one share one layout: same title line, same
    provider columns, one row per check.

    ``rows``: ``(label, concern, message, severity, scores)``, ``scores``
    holding one probability (or None) per column. ``columns``:
    ``(provider_name, elapsed_ms)`` with ``elapsed_ms`` None when that
    call failed, or ``(provider_name, elapsed_ms, pending)`` with
    ``pending`` True while that provider hasn't answered yet.

    States are "error", "warning", "ok", "checking" (still waiting) or,
    for a cell only, "none" (that provider gave no score). A row with no
    concern yet is "checking" until every provider has answered, since a
    later one could still flag it; a flagged row is final as soon as one
    provider flags it.
    """
    from core.jev_client import is_concern

    columns = [tuple(column) + (False,) * (3 - len(column)) for column in columns]
    names = [name for name, _ms, _pending in columns]
    waiting = [name for name, _ms, pending in columns if pending]

    view_rows = []
    for label, concern, message, severity, scores in rows:
        severity = severity if severity in ("error", "warning") else "warning"
        if concern:
            state, text = severity, f"{label}: {message}"
        else:
            state, text = ("checking" if waiting else "ok"), label
        cells = []
        for (_name, _ms, pending), score in zip(columns, scores):
            if pending:
                cells.append({"text": "…", "state": "checking"})
            elif score is None:
                cells.append({"text": "—", "state": "none"})
            else:
                cells.append({
                    "text": f"{round(score * 100)}%",
                    "state": severity if is_concern(score) else "ok",
                })
        view_rows.append({"state": state, "text": text, "cells": cells})

    flagged = [row for row in view_rows if row["state"] in ("error", "warning")]
    if flagged:
        state = "error" if any(row["state"] == "error" for row in flagged) else "warning"
        title = f"Before you send — {' + '.join(names)} noticed:"
    elif waiting:
        state = "checking"
        if len(waiting) == len(names):
            title = f"{' + '.join(names)} {_verb(names)} checking your draft"
        else:
            title = f"{' + '.join(waiting)} {_verb(waiting)} still checking…"
    else:
        state = "ok"
        title = f"{' + '.join(names)} checked your draft"

    headers = []
    for name, elapsed_ms, pending in columns:
        timing = "checking…" if pending else ("failed" if elapsed_ms is None else f"{elapsed_ms} ms")
        headers.append(f"{name}\n{timing}")

    return {
        "state": state,
        "title": title,
        "score_header": SCORE_HEADER,
        "headers": headers,
        "rows": view_rows,
        "context_note": context_note,
        "caption": score_caption(),
    }


def pending_rows(labels, column_count):
    """Rows for a card on which no provider has answered yet."""
    return [(label, False, "", "warning", [None] * column_count) for label in labels]


def _build_table(view):
    """Tk renderer for table_view()."""
    def build(frame, _dismiss):
        style = _STATE_STYLE[view["state"]]
        content = _build_card(frame, style["color"])
        _row(content, style["icon"], view["title"], style["color"], font_size=10, bold=True)

        table = tk.Frame(content, bg=BG)
        table.pack(fill="x")
        table.columnconfigure(1, weight=1)
        tk.Label(
            table, text=view["score_header"], fg=CHECKING_GRAY, bg=BG, font=("Segoe UI", 8),
            anchor="sw", justify="left",
        ).grid(row=0, column=1, sticky="sw", padx=(6, 0), pady=(0, 4))
        for col, header in enumerate(view["headers"]):
            tk.Label(
                table, text=header, fg=CHECKING_GRAY, bg=BG,
                font=("Segoe UI", 8), justify="right",
            ).grid(row=0, column=2 + col, sticky="e", padx=(10, 0), pady=(0, 4))

        text_wrap = WIDTH - ACCENT_WIDTH - 28 - 26 - SCORE_COLUMN_WIDTH * len(view["headers"])
        for index, row in enumerate(view["rows"], start=1):
            row_style = _STATE_STYLE[row["state"]]
            tk.Label(table, text=row_style["icon"], fg=row_style["color"], bg=BG, font=("Segoe UI", 13)).grid(
                row=index, column=0, sticky="nw"
            )
            tk.Label(
                table, text=row["text"], fg=row_style["color"], bg=BG, font=("Segoe UI", 9),
                anchor="w", justify="left", wraplength=text_wrap,
            ).grid(row=index, column=1, sticky="w", padx=(6, 0), pady=(0, 5))
            for col, cell in enumerate(row["cells"]):
                tk.Label(
                    table, text=cell["text"], fg=_STATE_STYLE[cell["state"]]["color"], bg=BG,
                    font=("Segoe UI", 9, "bold"),
                ).grid(row=index, column=2 + col, sticky="ne", padx=(10, 0))

        if view["context_note"]:
            tk.Label(
                content, text=view["context_note"], fg=CHECKING_GRAY, bg=BG, font=("Segoe UI", 8),
                anchor="w", justify="left", wraplength=WIDTH - ACCENT_WIDTH - 28,
            ).pack(anchor="w", fill="x", pady=(4, 0))
        tk.Label(
            content, text=view["caption"], fg=CHECKING_GRAY, bg=BG, font=("Segoe UI", 8),
        ).pack(anchor="e", pady=(2, 0))

    return build


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


def _build_unavailable(labels, provider_names):
    """Explain that a batch timed out instead of silently removing progress."""
    def build(frame, _dismiss):
        style = _SEVERITY_STYLE["warning"]
        content = _build_card(frame, style["color"])
        _row(
            content,
            style["icon"],
            f"{_join_names(provider_names)} couldn't finish checking",
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


CHECKING_DISMISS_MS = 10000


def _results_dismiss_ms():
    """The user's "Keep results open for" setting, in ms. Read on each
    popup so a change in Settings applies to the next one."""
    return int(config.get_popup_seconds() * 1000)


def notify(items, anchor_rect=None):
    """Fire-and-forget: enqueues the concerns popup for the UI thread to
    show, replacing whatever's currently up. `items` is a list of
    (message, severity) tuples, severity being "error" or "warning".
    `anchor_rect` is the focused control's (left, top, right, bottom) in
    screen coordinates."""
    if sys.platform == "darwin":
        _show_macos_panel("concerns", items, anchor_rect, _results_dismiss_ms())
    else:
        _enqueue(_build_concerns(items), anchor_rect, _results_dismiss_ms())


def _show_table(view, anchor_rect, auto_dismiss_ms):
    if sys.platform == "darwin":
        _show_macos_panel("table", view, anchor_rect, auto_dismiss_ms)
    else:
        _enqueue(_build_table(view), anchor_rect, auto_dismiss_ms)


def notify_checking(labels, anchor_rect=None, provider_names=("Jev",), context_note=None):
    """Show the results card before any answer: every check's row, every
    provider's column, "…" in each score cell."""
    provider_names = list(provider_names)
    columns = [(name, None, True) for name in provider_names]
    view = table_view(pending_rows(labels, len(columns)), columns, context_note)
    _show_table(view, anchor_rect, CHECKING_DISMISS_MS)


def notify_progress(rows, columns, anchor_rect=None, context_note=None):
    """Some providers answered, others are still pending (``columns`` as
    in table_view, with ``pending`` set): fill in what's known. Stays up
    as long as the checking card would, since more is coming."""
    _show_table(table_view(rows, columns, context_note), anchor_rect, CHECKING_DISMISS_MS)


def notify_results(rows, columns, anchor_rect=None, context_note=None):
    """Replace the progress card with the complete, stable result. See
    table_view for the shape of ``rows`` and ``columns``."""
    _show_table(table_view(rows, columns, context_note), anchor_rect, _results_dismiss_ms())


def notify_unavailable(labels, anchor_rect=None, provider_names=("Jev",)):
    """Replace progress with an explicit, non-judgmental request failure."""
    provider_names = list(provider_names)
    if sys.platform == "darwin":
        _show_macos_panel(
            "unavailable", {"labels": labels, "providers": provider_names}, anchor_rect, 4500
        )
    else:
        _enqueue(_build_unavailable(labels, provider_names), anchor_rect, 4500)


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
