"""core/context.py's heuristic against fake accessibility trees shaped like
the real ones (the Discord layout here mirrors a live UI Automation dump:
sidebar | message list over composer | member list)."""

import pytest

from core import context


class Node:
    def __init__(self, role="other", name="", rect=None, children=()):
        self.role = role
        self.name = name
        self.rect = rect
        self.parent = None
        self.children = list(children)
        for child in self.children:
            child.parent = self


class FakeBackend:
    def __init__(self, window_title=None):
        self.window_title = window_title
        self.calls = 0

    def context_parent(self, node):
        self.calls += 1
        return node.parent

    def context_children(self, node):
        self.calls += 1
        return node.children

    def context_info(self, node):
        return node.role, node.name, node.rect

    def context_key(self, node):
        return id(node)

    def get_window_title(self, _node):
        return self.window_title


def item(text, top, left=400, right=1400):
    return Node("listitem", text, (left, top, right, top + 50))


def discord_like(messages, input_rect=(475, 900, 1200, 950)):
    """Returns (window, input). Sidebar and member list are lists too, but
    in other columns, so they must never be picked."""
    sidebar = Node("list", "Channels", (0, 0, 390, 1000), [
        item("general", 10, 0, 390), item("random", 60, 0, 390),
    ])
    members = Node("list", "Members", (1420, 0, 1700, 1000), [
        item("alex", 10, 1420, 1700),
    ])
    message_list = Node("list", "Messages in general", (400, -5000, 1400, 880), [
        item(text, 100 + i * 60) for i, text in enumerate(messages)
    ])
    textbox = Node("edit", "Message #general", input_rect)
    composer = Node("other", "", (410, 895, 1410, 955), [Node("button", "Upload"), textbox])
    chat = Node("other", "general (channel)", (400, 0, 1420, 960), [
        Node("text", "general chat"), Node("other", "", (400, 0, 1400, 880), [message_list]), composer,
    ])
    content = Node("other", "", (400, 0, 1700, 960), [chat, members])
    window = Node("other", "#general | Server - Discord", (0, 0, 1700, 1000), [sidebar, content])
    return window, textbox


def test_reads_last_messages_from_the_list_above_the_input():
    _window, textbox = discord_like([f"alex: message {i}" for i in range(8)])
    ctx = context.extract(FakeBackend(), textbox)
    assert ctx.source == "list"
    assert ctx.place == "general (channel)"
    assert ctx.previous_messages == [f"alex: message {i}" for i in range(3, 8)]
    assert ctx.to_state() == {
        "place": "general (channel)",
        "previous_messages": [f"alex: message {i}" for i in range(3, 8)],
    }


def test_skips_separators_and_items_below_the_input():
    _window, textbox = discord_like(["sam: can you review my PR?", "new"])
    message_list = textbox.parent.parent.children[1].children[0]
    message_list.children.append(item("sam: rendered below the input", 990))
    ctx = context.extract(FakeBackend(), textbox)
    assert ctx.previous_messages == ["sam: can you review my PR?"]


def test_respects_the_total_character_budget():
    long = "x" * 450
    _window, textbox = discord_like([long, long, long, long, long, long])
    ctx = context.extract(FakeBackend(), textbox)
    assert sum(len(m) for m in ctx.previous_messages) <= context.MAX_TOTAL_CHARS + 500
    assert len(ctx.previous_messages) < 6


def test_thread_panel_uses_its_own_list_not_the_channel():
    channel_list = Node("list", "Messages in general", (0, 0, 800, 880), [
        item("channel message", 100, 0, 800),
    ])
    thread_list = Node("list", "Thread", (820, 0, 1400, 880), [
        item("thread root: anyone free?", 100, 820, 1400),
        item("thread reply", 160, 820, 1400),
    ])
    textbox = Node("edit", "Reply", (830, 900, 1390, 950))
    thread_panel = Node("other", "Thread", (820, 0, 1400, 960), [thread_list, textbox])
    channel = Node("other", "general (channel)", (0, 0, 800, 960), [channel_list])
    Node("other", "", (0, 0, 1400, 960), [channel, thread_panel])

    ctx = context.extract(FakeBackend(), textbox)
    assert ctx.previous_messages == ["thread root: anyone free?", "thread reply"]
    assert ctx.place == "Thread"


def test_falls_back_to_text_blocks_without_a_list():
    """Comment-thread shape: entries are plain groups, not list items."""
    def comment(author, text, top):
        return Node("other", "", (1000, top, 1300, top + 80), [
            Node("text", author, (1000, top, 1300, top + 20)),
            Node("text", text, (1000, top + 20, 1300, top + 80)),
            Node("button", "Resolve", (1250, top, 1300, top + 20)),
        ])

    textbox = Node("edit", "Reply", (1000, 400, 1300, 440))
    card = Node("other", "", (990, 100, 1310, 460), [
        comment("Ana", "Should this be Q3 or Q4?", 100),
        comment("Ben", "I think Q4.", 200),
        Node("other", "", (1000, 390, 1300, 450), [textbox]),
    ])
    doc = Node("other", "", (0, 0, 1400, 1000), [
        Node("other", "", (0, 0, 980, 1000), [Node("text", "Document body text", (0, 0, 980, 1000))]),
        card,
    ])
    Node("other", "Q3 Planning - Google Docs", (0, 0, 1400, 1000), [doc])

    ctx = context.extract(FakeBackend(window_title="Q3 Planning - Google Docs - Google Chrome"), textbox)
    assert ctx.source == "text"
    assert ctx.previous_messages == ["Ana Should this be Q3 or Q4?", "Ben I think Q4."]
    assert "Document body text" not in " ".join(ctx.previous_messages)


def test_window_title_alone_is_still_context():
    textbox = Node("edit", "", (0, 100, 300, 140))
    Node("other", "", (0, 0, 300, 200), [Node("button", "x"), textbox])
    ctx = context.extract(FakeBackend(window_title="Notes - App"), textbox)
    assert ctx.place is None
    assert ctx.previous_messages == []
    assert ctx.to_state() == {"window_title": "Notes - App"}


def test_window_title_is_sent_next_to_the_place():
    """Discord names a DM "Name (channel)"; only its title says DM."""
    _window, textbox = discord_like(["kuz: do u still play r6?"])
    ctx = context.extract(FakeBackend(window_title="@kuz - Discord"), textbox)
    assert ctx.to_state() == {
        "place": "general (channel)",
        "window_title": "@kuz - Discord",
        "previous_messages": ["kuz: do u still play r6?"],
    }


def test_nothing_found_returns_none():
    textbox = Node("edit", "", (0, 100, 300, 140))
    Node("other", "", (0, 0, 300, 200), [textbox])
    assert context.extract(FakeBackend(), textbox) is None


def test_unsupported_backend_returns_none():
    assert context.extract(object(), Node("edit", "", (0, 0, 10, 10))) is None


def test_node_budget_bounds_the_walk(monkeypatch):
    monkeypatch.setattr(context, "MAX_NODES", 5)
    _window, textbox = discord_like(["a message here"])
    backend = FakeBackend()
    # Must give up quietly, never raise.
    ctx = context.extract(backend, textbox)
    assert ctx is None or ctx.nodes_visited <= 6


def test_backend_errors_mean_no_context():
    class Broken(FakeBackend):
        def context_info(self, node):
            raise RuntimeError("COM error")

    assert context.extract(Broken(), Node("edit", "", (0, 0, 10, 10))) is None


def test_message_text_skips_buttons_hidden_copies_and_duplicates():
    """Shaped like a real Discord message item."""
    message = Node("listitem", "everything concatenated, buttons and all", (400, 100, 1400, 300), [
        Node("other", "", (400, 100, 1400, 300), [
            Node("text", "Sam 12:03", (400, 100, 600, 120), [
                Node("button", "Sam", (400, 100, 450, 120)),
            ]),
            Node("other", "", (400, 100, 402, 101), [
                Node("text", "Thursday, August 13, 2026 12:03", (400, 100, 402, 101)),
            ]),
            Node("text", "Can you review my PR?", (400, 120, 900, 140)),
            Node("link", "https://example.com/pr/1", (400, 140, 900, 160)),
            Node("text", "Can you review my PR?", (400, 160, 900, 180)),
            Node("text", "⁠", (0, 0, 0, 0)),
            Node("button", "Click to react", (400, 200, 450, 220)),
            Node("button", "Reply", (1300, 100, 1350, 120)),
        ]),
    ])
    divider = Node("other", "August 13, 2026", (400, 300, 1400, 320))
    message_list = Node("list", "Messages", (400, 0, 1400, 880), [message, divider])
    textbox = Node("edit", "Message", (475, 900, 1200, 950))
    Node("other", "", (400, 0, 1400, 960), [message_list, textbox])

    ctx = context.extract(FakeBackend(), textbox)
    assert ctx.previous_messages == ["Sam 12:03 Can you review my PR? https://example.com/pr/1"]


def test_budget_hit_while_reading_keeps_the_messages_already_read(monkeypatch):
    _window, textbox = discord_like([f"alex: message {i}" for i in range(8)])
    full = context.extract(FakeBackend(), textbox)
    # Run out two nodes before the full read finishes: mid-list.
    monkeypatch.setattr(context, "MAX_NODES", full.nodes_visited - 2)
    ctx = context.extract(FakeBackend(), textbox)
    assert ctx is not None
    assert 0 < len(ctx.previous_messages) < 5
    assert ctx.previous_messages[-1] == "alex: message 7"


def test_google_docs_comment_thread_beside_the_document_body():
    """Mirrors a live Google Docs dump: comments are plain groups above
    the reply box, and the document body to the left overlaps half the
    reply box's width and contains its own list — which must not be
    taken for the conversation."""
    def comment(text, top):
        return Node("other", "", (3134, top, 3551, top + 90), [
            Node("other", "Patrick", (3150, top + 10, 3182, top + 42)),  # avatar image
            Node("other", "", (3184, top + 8, 3459, top + 46), [
                Node("text", "Patrick", (3194, top + 10, 3315, top + 28)),
                Node("text", "7:30 PM Today", (3194, top + 28, 3275, top + 44)),
            ]),
            Node("button", "More options", (3500, top + 8, 3534, top + 42)),
            Node("other", "", (3145, top + 54, 3535, top + 80), [
                Node("text", text, (3150, top + 58, 3215, top + 76)),
            ]),
        ])

    textbox = Node("other", "Reply", (3150, -943, 3535, -905))
    reply_row = Node("other", "", (3134, -943, 3551, -845), [
        textbox, Node("button", "Reply to comment", (3464, -897, 3535, -861)),
    ])
    thread = Node("other", "", (3134, -1228, 3551, -845), [
        Node("other", "", (3134, -1228, 3551, -943), [
            comment("Message1", -1228), comment("Message2", -1138), comment("Message3", -1048),
        ]),
        reply_row,
    ])
    comments = Node("list", "", None, [Node("listitem", "Comments dialog", (3134, -1228, 3551, -845), [thread])])
    body = Node("other", "", (2287, -1344, 3351, -226), [
        Node("list", "", (2300, -1300, 3340, -1200), [
            Node("listitem", "Reasoning effort levels", (2300, -1300, 3340, -1250)),
        ]),
    ])
    Node("other", "", (1991, -1344, 3675, -226), [body, comments])

    ctx = context.extract(FakeBackend(window_title="Doc - Google Docs"), textbox)
    assert ctx.source == "text"
    assert ctx.previous_messages == [
        "Patrick 7:30 PM Today Message1",
        "Patrick 7:30 PM Today Message2",
        "Patrick 7:30 PM Today Message3",
    ]


def test_new_comment_box_finds_no_conversation():
    """Mirrors a live Google Docs new-comment card: the only thing above
    the box is the card's own header (avatar + the same name), the other
    comment threads live in a zero-size list, and the floating "On this
    page" outline list sits above in the same column — but only as a
    sibling of a far wider ancestor."""
    textbox = Node("edit", "Comment draft", (3150, -1088, 3535, -1050))
    card = Node("other", "", (3134, -1150, 3551, -990), [
        Node("other", "", (3134, -1150, 3551, -1088), [
            Node("other", "Patrick Desjardins", (3150, -1140, 3182, -1108)),  # avatar
            Node("text", "Patrick Desjardins", (3194, -1138, 3315, -1120)),
        ]),
        Node("other", "", (3134, -1088, 3551, -990), [
            textbox, Node("button", "Post Comment", (3437, -1042, 3535, -1006)),
        ]),
    ])
    other_thread = Node("listitem", "Comments dialog. Open comment.", (3134, -1445, 3551, -1160), [
        Node("text", "Patrick 7:30 PM Message1", (3150, -1400, 3500, -1380)),
    ])
    threads = Node("list", "", None, [other_thread, Node("other", "", (3134, -1150, 3551, -990), [card])])
    outline = Node("other", "On this page", (3301, -1329, 3565, -1273), [
        Node("list", "", (3301, -1305, 3565, -1273), [
            Node("listitem", "Reasoning effort levels", (3301, -1305, 3565, -1273)),
        ]),
    ])
    Node("other", "", (1991, -1344, 3675, -226), [outline, threads])

    ctx = context.extract(FakeBackend(window_title="Doc - Google Docs"), textbox)
    assert ctx.to_state() == {"window_title": "Doc - Google Docs"}


def test_identity_mismatch_falls_back_to_matching_by_look():
    """If a platform's element identity doesn't compare equal across
    lookups, matching by role/name/rect still finds the input's branch."""
    class NoIdentity(FakeBackend):
        def context_key(self, node):
            return object()  # never equal

    _window, textbox = discord_like(["alex: ready?"])
    ctx = context.extract(NoIdentity(), textbox)
    assert ctx.previous_messages == ["alex: ready?"]


def test_describe_tree_includes_raw_roles():
    class WithRaw(FakeBackend):
        def context_raw_role(self, node):
            return "AXGroup"

    _window, textbox = discord_like(["alex: ready?"])
    lines = context.describe_tree(WithRaw(), textbox, max_ancestors=2)
    assert lines[0].startswith("input: edit/AXGroup")
    assert any("<== input branch" in line for line in lines)


class LabelBackend(FakeBackend):
    """macOS: list rows are named only by app-provided labels."""
    CONTEXT_ITEM_LABELS = True


def slack_message(label, author, text, top, left=967, right=1482, height=52):
    """A Slack row as on macOS: titled "Author: text. time.", author as a
    button, a "1 reply" button, and the text runs as static text."""
    body = Node("other", "", (left, top, right, top + height), [
        Node("button", author, (left + 64, top + 4, left + 180, top + 26)),
        Node("link", "Yesterday at 1:52:56 PM", (left + 190, top + 8, left + 236, top + 24)),
        Node("other", "", (left + 64, top + 26, right - 20, top + 48), [
            Node("text", text, (left + 64, top + 28, right - 40, top + 46)),
        ]),
        Node("button", "1 reply", (left + 64, top + 48, left + 110, top + 50)),
    ])
    return Node("other", label, (left, top, right, top + height), [body])


def slack_divider(label, top, left=967, right=1482):
    return Node("other", label, (left, top, right, top + 24), [
        Node("other", "", (left, top, right, top + 24), [
            Node("other", "Jump to date", (left + 400, top, left + 500, top + 24)),
        ]),
    ])


def slack_composer(textbox):
    """Slack wraps its editor in a group named "composer"."""
    left, top, right, bottom = textbox.rect
    return Node("other", "composer", (left - 1, top - 39, right + 1, bottom + 41), [
        Node("other", "Formatting", (left, top - 38, right, top)),
        Node("other", "", textbox.rect, [textbox]),
        Node("other", "Composer actions", (left, bottom, right, bottom + 40)),
    ])


def test_slack_channel_list_with_a_one_pixel_rect_and_labeled_rows():
    """Mirrors a live macOS Slack dump: the history list reports a 1x1
    rect at its corner, rows are labeled, dividers are labeled too but
    hold no text, and the composer is a named wrapper."""
    history = Node("list", "ros-ai-sharing (channel)", (967, 1189, 968, 1190), [
        slack_divider("Yesterday Press enter to select a date to jump to.", 300),
        slack_message("Jack Buckley: Also mentions our assessments. 12:21 PM. 8 replies.",
                      "Jack Buckley", "Also mentions our assessments", 324),
        slack_divider("Today Press enter to select a date to jump to.", 376),
        slack_message("Patrick Desjardins: Diffusion LLMs are fast. 1:52 PM. 1 reply.",
                      "Patrick Desjardins", "Diffusion LLMs are fast", 400),
        Node("other", "", (967, 1166, 1482, 1190)),
    ])
    textbox = Node("edit", "Message to ros-ai-sharing", (988, 1221, 1461, 1259))
    pane = Node("other", "", (967, 167, 1483, 1324), [
        Node("other", "", (967, 167, 1483, 1190), [Node("other", "", (967, 159, 1483, 1190), [history])]),
        Node("other", "", (967, 1182, 1483, 1324), [slack_composer(textbox)]),
    ])
    Node("other", "ros-ai-sharing App", (967, 80, 1483, 1324), [
        Node("other", "Tabs", (983, 129, 1471, 167), [Node("text", "Messages", (983, 129, 1100, 167))]),
        pane,
    ])

    ctx = context.extract(LabelBackend(), textbox)
    assert ctx.source == "list"
    assert ctx.place == "ros-ai-sharing (channel)"
    assert ctx.previous_messages == [
        "Jack Buckley: Also mentions our assessments. 12:21 PM. 8 replies.",
        "Patrick Desjardins: Diffusion LLMs are fast. 1:52 PM. 1 reply.",
    ]


def slack_thread(backend_labels=True):
    """Mirrors a live macOS Slack thread panel: the reply box is the last
    row of the thread's own list, next to the channel's list."""
    textbox = Node("edit", "Reply to thread in ros-ai-sharing", (1500, 770, 2036, 808))
    thread = Node("list", "Thread in ros-ai-sharing (channel, 1 reply)", (1483, 129, 2052, 899), [
        slack_message("Patrick Desjardins: Diffusion LLMs are fast. Yesterday at 1:52 PM.",
                      "Patrick Desjardins", "Diffusion LLMs are fast", 129, 1483, 2052, 484),
        Node("other", "", (1483, 613, 2052, 637), [Node("text", "1 reply", (1500, 613, 1560, 637))]),
        slack_message("Patrick Desjardins: Ill give it a shot tonight. Yesterday at 1:54 PM.",
                      "Patrick Desjardins", "Ill give it a shot tonight", 637, 1483, 2052, 82),
        Node("other", "", (1483, 719, 2052, 899), [
            Node("other", "", (1483, 719, 2052, 899), [slack_composer(textbox)]),
        ]),
    ])
    channel = Node("list", "ros-ai-sharing (channel)", (967, 1189, 968, 1190), [
        slack_message("Jack Buckley: channel message. 12:21 PM.", "Jack Buckley", "channel message", 400),
    ])
    Node("other", "", (967, 80, 2052, 1324), [
        Node("other", "", (967, 80, 1483, 1324), [channel]),
        Node("other", "Thread", (1483, 80, 2052, 1324), [thread]),
    ])
    return textbox


def test_slack_thread_reply_box_inside_the_thread_list():
    ctx = context.extract(LabelBackend(), slack_thread())
    assert ctx.source == "list"
    assert ctx.place == "Thread in ros-ai-sharing (channel, 1 reply)"
    assert ctx.previous_messages == [
        "Patrick Desjardins: Diffusion LLMs are fast. Yesterday at 1:52 PM.",
        "Patrick Desjardins: Ill give it a shot tonight. Yesterday at 1:54 PM.",
    ]


def test_slack_thread_without_item_labels_gathers_row_text():
    """A backend whose row names aren't curated labels (Windows) still
    reads the thread from its list, from the rows' own text."""
    ctx = context.extract(FakeBackend(), slack_thread())
    assert ctx.source == "list"
    assert ctx.place == "Thread in ros-ai-sharing (channel, 1 reply)"
    assert ctx.previous_messages[0] == "Yesterday at 1:52:56 PM Diffusion LLMs are fast"
    assert ctx.previous_messages[-1] == "Yesterday at 1:52:56 PM Ill give it a shot tonight"
    assert "channel message" not in " ".join(ctx.previous_messages)
