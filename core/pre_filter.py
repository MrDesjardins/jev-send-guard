"""Local, zero-latency heuristic deciding whether a draft is even worth
sending to Jev.

MIN_LENGTH is lower here than v1's original 40: v1 gated a *synchronous,
pre-send* check, so a high threshold minimized how often you'd feel Jev's
latency before hitting send. v2's check runs during idle time in the
background, not blocking anything, so there's far less cost to checking
more short messages — and a short message can absolutely still be curt
("You are working way too slow" is 29 chars). The ACK_PATTERN skip list
still covers genuinely trivial short replies regardless of length.
"""

import re

MIN_LENGTH = 15

# Very short acknowledgement-style messages skip the check even if they
# happen to clear MIN_LENGTH (e.g. "Sounds good, thanks so much for the
# quick turnaround on this!").
ACK_PATTERN = re.compile(
    r"^(ok(ay)?|sounds good|thanks?( you)?|np|got it|will do|sure|yep|yes"
    r"|no problem|perfect|great|awesome)[.!\s]*$",
    re.IGNORECASE,
)


def should_check(raw_text):
    text = (raw_text or "").strip()
    if len(text) < MIN_LENGTH:
        return False
    if ACK_PATTERN.match(text):
        return False
    return True
