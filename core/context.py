"""Best-effort conversation context for a draft, read from the same
accessibility tree the draft itself comes from (UI Automation on Windows,
AX on macOS) — no browser extension or per-app integration needed.

Starting at the focused text box, walk *up* its ancestors. At each level,
look at the siblings that come before the input's branch, nearest first,
for a list (role "list") whose items sit above the input in the same
column. Chat apps render their message history that way: Discord's
"Messages in #channel" list sits directly above its composer, and a Slack
or Discord thread panel has its own list above its own reply box. The
"same column" test (horizontal overlap with the input) is what keeps the
channel sidebar, member list, or — in a thread panel — the main channel's
messages out. The last few items become `previous_messages`.

At a level with no such list (e.g. a Google Docs comment thread, whose
comments are plain groups above the reply box), the text blocks above the
input are used instead — split into one message per stacked child — but
only at ancestors still about as narrow as the input, so the walk never
reaches other panes.

`place` is the first named ancestor (Discord: "general (channel)"), else
the list's own name. `window_title` is always sent too when available: it
often carries what the tree doesn't (Discord labels a DM "Name (channel)",
but its title is "@Name - Discord").

Everything is bounded (nodes visited, wall-clock time, characters), and
any failure means "no context" — the draft is still checked as before.

The backend must provide context_parent(node), context_children(node),
context_info(node) -> (role, name, rect) with role normalized to "list",
"listitem", "text", "link", "edit", "button" or "other" and rect as
(left, top, right, bottom) or None, context_key(node) -> a hashable
identity, and get_window_title(node).
"""

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("jev-send-guard")

MAX_ANCESTORS = 14
MAX_MESSAGES = 5
MAX_MESSAGE_CHARS = 500
# Elements this many pixels wide/high or less are screen-reader-only copies.
HIDDEN_MAX_PX = 2
# Shorter list items are separators ("new", unread markers), not messages.
MIN_MESSAGE_CHARS = 4
MAX_TOTAL_CHARS = 2000
MAX_NODES = 800
TIME_BUDGET_SEC = 0.5
# A list (or fallback block) must overlap at least this fraction of the
# input's width to count as being in the input's column.
MIN_HORIZONTAL_OVERLAP = 0.5
# Pixels of slack when deciding whether something is above the input.
VERTICAL_SLACK = 4
# The text fallback stops at ancestors this much wider than the input: by
# then the preceding siblings are other panes (sidebars, toolbars).
MAX_FALLBACK_WIDTH_RATIO = 2.0
# The whole walk stops there: near the window level everything on screen
# is some ancestor's sibling (Google Docs' floating "On this page" outline
# sits right above a new comment box). Discord's message list is found at
# an ancestor ~1.4x the composer's width.
MAX_WALK_WIDTH_RATIO = 3.0
MAX_TEXT_DEPTH = 8
TEXT_ROLES = {"text", "link"}

REQUIREMENT_KEYS = ("place", "window_title", "previous_messages")


@dataclass
class ConversationContext:
    place: str | None = None
    window_title: str | None = None
    previous_messages: list = field(default_factory=list)
    source: str = "none"  # "list", "text" or "none"
    nodes_visited: int = 0
    elapsed_ms: int = 0

    def to_state(self):
        """The JSON sent to the model next to `draft`. Empty fields are
        omitted so a question's `requires` can test for presence."""
        state = {}
        if self.place:
            state["place"] = self.place
        if self.window_title:
            state["window_title"] = self.window_title
        if self.previous_messages:
            state["previous_messages"] = list(self.previous_messages)
        return state

    def available(self):
        return set(self.to_state())


class _BudgetExceeded(Exception):
    pass


class _Walker:
    """Wraps the backend so every call is counted, time-bounded, and can
    never raise anything but _BudgetExceeded."""

    def __init__(self, backend):
        self.backend = backend
        self.deadline = time.perf_counter() + TIME_BUDGET_SEC
        self.visited = 0

    def _tick(self):
        self.visited += 1
        if self.visited > MAX_NODES or time.perf_counter() > self.deadline:
            raise _BudgetExceeded

    def parent(self, node):
        self._tick()
        try:
            return self.backend.context_parent(node)
        except Exception:
            return None

    def children(self, node):
        self._tick()
        try:
            return list(self.backend.context_children(node) or [])
        except Exception:
            return []

    def info(self, node):
        role, name, rect, _hidden = self.info_hidden(node)
        return role, name, rect

    def info_hidden(self, node):
        """info() plus whether the node is laid out but not visible: zero-
        size, or a few pixels wide/high — the "visually hidden" duplicates
        apps keep for screen readers (Discord's full-date timestamp)."""
        self._tick()
        try:
            role, name, rect = self.backend.context_info(node)
        except Exception:
            return "other", "", None, False
        hidden = False
        if rect is not None:
            width, height = rect[2] - rect[0], rect[3] - rect[1]
            hidden = width <= HIDDEN_MAX_PX or height <= HIDDEN_MAX_PX
            if width <= 0 or height <= 0:
                rect = None  # zero-size: offscreen or not laid out, position unknown
        return role or "other", (name or "").strip(), rect, hidden

    def key(self, node):
        try:
            return self.backend.context_key(node)
        except Exception:
            return None


def _overlaps_column(rect, input_rect):
    if rect is None:
        return True  # unknown position: don't prune on it
    width = input_rect[2] - input_rect[0]
    overlap = min(rect[2], input_rect[2]) - max(rect[0], input_rect[0])
    return width > 0 and overlap >= width * MIN_HORIZONTAL_OVERLAP


def _same_column(rect, input_rect):
    """Overlap in both directions: the candidate covers most of the
    input's width *and* the input covers most of the candidate's. A wide
    pane that merely contains the input's column fails the second half —
    e.g. a Google Docs body next to its comment sidebar, which overlaps
    half the reply box but is three times wider."""
    if rect is None:
        return True
    input_width = input_rect[2] - input_rect[0]
    width = rect[2] - rect[0]
    overlap = min(rect[2], input_rect[2]) - max(rect[0], input_rect[0])
    return (
        input_width > 0
        and width > 0
        and overlap >= input_width * MIN_HORIZONTAL_OVERLAP
        and overlap >= width * MIN_HORIZONTAL_OVERLAP
    )


def _starts_above(rect, input_rect):
    return rect is None or rect[1] < input_rect[1] + VERTICAL_SLACK


def _ends_above(rect, input_rect):
    return rect is None or rect[3] <= input_rect[1] + VERTICAL_SLACK


def _clean(text):
    text = " ".join(text.split())
    for ch in "﻿​‌‍⁠":
        text = text.replace(ch, "")
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS] + "…"
    return text.strip()


def _gather_text(walker, node, depth=0, seen=None, pieces=None):
    """The visible text under `node`, in order: a named text/link node
    counts as a whole (so a message header reads "author tag time"
    without descending into it). Buttons are skipped with their subtree —
    reactions, "Reply", "Remove all embeds", an image repeating its embed
    title — and so are visually hidden screen-reader duplicates. Text
    already gathered for this node isn't repeated. `pieces`, if given,
    collects (is_text_role, text) for each piece kept."""
    seen = set() if seen is None else seen
    pieces = [] if pieces is None else pieces
    role, name, _rect, hidden = walker.info_hidden(node)
    if hidden or role in ("button", "edit"):
        return ""
    if role in TEXT_ROLES and name:
        if name in seen:
            return ""
        seen.add(name)
        pieces.append((True, name))
        return name
    if depth >= MAX_TEXT_DEPTH:
        return ""
    children = walker.children(node)
    if not children:
        if not name or name in seen:
            return ""
        seen.add(name)
        pieces.append((False, name))
        return name
    texts = [_gather_text(walker, child, depth + 1, seen, pieces) for child in children]
    return " ".join(text for text in texts if text)


def _has_text(walker, node, depth=0):
    """Whether any named text/link node sits under `node` outside
    buttons: a labeled row with none is a divider (Slack's "Today" date
    picker), not a message. Visibility isn't checked — a row scrolled
    partly out of view still holds a message."""
    role, name, _rect = walker.info(node)
    if role == "button":
        return False
    if role in TEXT_ROLES and name:
        return True
    if depth >= MAX_TEXT_DEPTH:
        return False
    return any(_has_text(walker, child, depth + 1) for child in walker.children(node))


def _find_list(walker, node, input_rect):
    """Breadth-first search under `node` for a list in the input's column
    starting above it. Subtrees outside the column or entirely below the
    input are pruned without being entered. A list only a pixel or two in
    size (Slack's channel history on macOS reports 1x1 at its bottom-left
    corner) has an unknown position, like a zero-size one: its items'
    own rects still decide what is above the input."""
    queue = [node]
    while queue:
        current = queue.pop(0)
        role, _name, rect, hidden = walker.info_hidden(current)
        if role == "list" and hidden:
            rect = None
        if not _overlaps_column(rect, input_rect) or not _starts_above(rect, input_rect):
            continue
        if role == "list" and not _same_column(rect, input_rect):
            continue  # another pane's list (sidebar, document body)
        children = walker.children(current)
        if role == "list" and children:
            return current, children
        queue.extend(children)
    return None


def _list_messages(walker, children, input_rect):
    """The last few items' text. When the list has real list items, other
    children (date dividers, "new messages" bars) are skipped. An item's
    text is gathered from its parts rather than its own name: on Windows,
    Chromium names an item by concatenating *everything* inside it,
    buttons and hidden duplicates included.

    A backend whose item names are only ever app-provided labels
    (CONTEXT_ITEM_LABELS: macOS, where Chromium gives a list row a title
    only from aria-label/labelledby) uses them instead when the list has
    any: Slack labels each message "Author: text. time. 2 replies.",
    keeping the author its tree only has as a button. Unlabeled rows are
    then dividers ("Yesterday", "1 reply") and are skipped."""
    infos = [walker.info(item) for item in children]
    has_items = any(role == "listitem" for role, _name, _rect in infos)
    use_labels = bool(getattr(walker.backend, "CONTEXT_ITEM_LABELS", False)) and any(
        name for _role, name, _rect in infos
    )
    messages = []
    total = 0
    try:
        for item, (role, name, rect) in reversed(list(zip(children, infos))):
            if role == "edit" or (has_items and not use_labels and role != "listitem"):
                continue
            if use_labels and (not name or not _has_text(walker, item)):
                continue
            if not _ends_above(rect, input_rect):
                continue
            if use_labels:
                text = _clean(name)
            else:
                text = _clean(_gather_text(walker, item) or name)
            if len(text) < MIN_MESSAGE_CHARS:
                continue
            messages.append(text)
            total += len(text)
            if len(messages) >= MAX_MESSAGES or total >= MAX_TOTAL_CHARS:
                break
    except _BudgetExceeded:
        # Keep the most recent messages already read rather than nothing.
        if not messages:
            raise
    messages.reverse()
    return messages


def extract(backend, control):
    """ConversationContext for the focused `control`, or None if the
    backend doesn't support context or nothing usable was found."""
    if not hasattr(backend, "context_parent"):
        return None
    start = time.perf_counter()
    walker = _Walker(backend)
    result = ConversationContext()
    # The first named ancestor, used as `place` only if the level where the
    # conversation is found has no name of its own: a wrapper right around
    # the input (Slack names its composer "composer") says less than the
    # conversation's container or list ("ros-ai-sharing (channel)").
    inner_name = None
    try:
        _role, _name, input_rect = walker.info(control)
        if input_rect is None:
            return None
        input_width = input_rect[2] - input_rect[0]
        node = control
        for _ in range(MAX_ANCESTORS):
            parent = walker.parent(node)
            if parent is None:
                break
            parent_role, parent_name, parent_rect = walker.info(parent)
            if parent_rect is not None and (
                parent_rect[2] - parent_rect[0] > input_width * MAX_WALK_WIDTH_RATIO
            ):
                break
            siblings = walker.children(parent)
            node_key = walker.key(node)
            index = next(
                (i for i, sibling in enumerate(siblings) if walker.key(sibling) == node_key),
                None,
            )
            if index is None:
                # Identity didn't match (untested on some platforms): fall
                # back to the first sibling that looks the same.
                fingerprint = walker.info(node)
                index = next(
                    (i for i, sibling in enumerate(siblings) if walker.info(sibling) == fingerprint),
                    None,
                )
            if index is None:
                break
            preceding = list(reversed(siblings[:index]))
            level_name = parent_name if parent_role not in ("edit", "list") else ""
            # The input is a row of the conversation's own list (Slack's
            # thread panel ends with its reply box): the rows before it are
            # the messages. Only for a list laid out in the input's column —
            # Google Docs keeps all comment threads in a zero-size list, and
            # the rows before a new comment box are *other* threads.
            if parent_role == "list" and parent_rect is not None and _same_column(
                parent_rect, input_rect
            ):
                messages = _list_messages(walker, siblings[:index], input_rect)
                if messages:
                    result.previous_messages = messages
                    result.source = "list"
                    result.place = parent_name or inner_name
                    return _finish(result, walker, backend, control, start)
            for sibling in preceding:
                found = _find_list(walker, sibling, input_rect)
                if found is None:
                    continue
                list_node, items = found
                messages = _list_messages(walker, items, input_rect)
                if messages:
                    result.previous_messages = messages
                    result.source = "list"
                    result.place = level_name or walker.info(list_node)[1] or inner_name
                    return _finish(result, walker, backend, control, start)
            # No list at this level: use text blocks just above the input,
            # but only while still close to it — wider ancestors' siblings
            # are other panes (sidebars, toolbars, a document body).
            # An ancestor of unknown size can't be shown to be close (Docs
            # nests comment threads in a zero-size list: its other items are
            # other threads), so it gets no text fallback.
            if parent_rect is not None and (
                parent_rect[2] - parent_rect[0] <= input_width * MAX_FALLBACK_WIDTH_RATIO
            ):
                messages = _text_messages(walker, preceding, input_rect)
                if messages:
                    result.previous_messages = messages
                    result.source = "text"
                    result.place = level_name or inner_name
                    return _finish(result, walker, backend, control, start)
            if inner_name is None and level_name:
                inner_name = level_name
            node = parent
    except _BudgetExceeded:
        log.debug("context: budget exceeded after %d nodes", walker.visited)
    except Exception:
        # Never let a context bug stop the watch loop or the check itself.
        log.exception("context: extraction failed")
        return None
    if result.place is None:
        result.place = inner_name
    return _finish(result, walker, backend, control, start)


def _text_units(walker, node):
    """The message blocks inside `node`: its children, when they are
    containers stacked one under another (a comment thread's comments),
    else `node` itself. Single-child wrappers are looked through. Text
    children (a header over a body) are parts of one message, not
    separate messages, so they never split a block."""
    for _ in range(MAX_TEXT_DEPTH):
        visible = []
        for child in walker.children(node):
            role, _name, rect, hidden = walker.info_hidden(child)
            if not hidden and role != "button":
                visible.append((child, role, rect))
        if len(visible) != 1:
            break
        node = visible[0][0]
    if len(visible) < 2 or any(
        rect is None or role in TEXT_ROLES for _child, role, rect in visible
    ):
        return [node]
    for (_a, _ra, above), (_b, _rb, below) in zip(visible, visible[1:]):
        if below[1] < above[3] - VERTICAL_SLACK:
            return [node]  # side by side: parts of one block
    return [child for child, _role, _rect in visible]


def _text_messages(walker, preceding, input_rect):
    """Messages from the text blocks among `preceding` (nearest first)
    that sit above the input in its column, oldest first."""
    messages = []
    total = 0
    for sibling in preceding:
        _role, _name, rect = walker.info(sibling)
        if not _same_column(rect, input_rect) or not _ends_above(rect, input_rect):
            continue
        for unit in reversed(_text_units(walker, sibling)):
            pieces = []
            text = _clean(_gather_text(walker, unit, pieces=pieces))
            if len(text) < MIN_MESSAGE_CHARS:
                continue
            if not any(is_text for is_text, _piece in pieces):
                # Only an image's name — e.g. the new-comment card's own
                # header (avatar + the same name): no message in it.
                continue
            messages.append(text)
            total += len(text)
            if len(messages) >= MAX_MESSAGES or total >= MAX_TOTAL_CHARS:
                messages.reverse()
                return messages
    messages.reverse()
    return messages


def describe_tree(backend, control, max_ancestors=MAX_ANCESTORS, max_children=30):
    """Diagnostic dump for tuning this heuristic on a new app (see
    manage.py inspect-context): each ancestor of `control` and its direct
    children, with role, rect and name (names truncated). Local only."""

    def short(text, limit=60):
        text = " ".join((text or "").split())
        return text if len(text) <= limit else text[:limit] + "…"

    def describe(node):
        try:
            role, name, rect = backend.context_info(node)
        except Exception as exc:
            return f"<unreadable: {exc}>"
        raw_role = getattr(backend, "context_raw_role", None)
        if raw_role is not None:
            try:
                role = f"{role}/{raw_role(node)}"
            except Exception:
                pass
        try:
            kids = len(backend.context_children(node) or [])
        except Exception:
            kids = "?"
        return f"{role:<24} rect={rect} kids={kids} name={short(name)!r}"

    lines = [f"input: {describe(control)}"]
    node = control
    for level in range(1, max_ancestors + 1):
        try:
            parent = backend.context_parent(node)
        except Exception:
            parent = None
        if parent is None:
            break
        lines.append(f"\nancestor {level}: {describe(parent)}")
        try:
            children = list(backend.context_children(parent) or [])
        except Exception:
            children = []
        node_key = backend.context_key(node)
        for child in children[:max_children]:
            marker = "   <== input branch" if backend.context_key(child) == node_key else ""
            lines.append(f"    - {describe(child)}{marker}")
        if len(children) > max_children:
            lines.append(f"    ... {len(children) - max_children} more")
        node = parent
    return lines


def _finish(result, walker, backend, control, start):
    try:
        result.window_title = (backend.get_window_title(control) or "").strip() or None
    except Exception:
        pass
    result.nodes_visited = walker.visited
    result.elapsed_ms = round((time.perf_counter() - start) * 1000)
    if not result.to_state():
        return None
    return result
