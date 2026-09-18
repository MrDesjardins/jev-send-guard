"""OS-agnostic idle debounce: given a stream of "this is the field's current
text right now" observations, decide when the user has paused long enough
that it's worth evaluating the draft. Kept separate from any platform
backend so it can be unit-tested without a real accessibility API.
"""

import time

# A short pause feels responsive while still avoiding a request on every
# keystroke. Polling occurs every 0.3 seconds, so this is effectively 0.6–0.9s.
IDLE_SECONDS = 0.6


class IdleWatcher:
    def __init__(self, idle_seconds=IDLE_SECONDS, now=time.monotonic):
        self._idle_seconds = idle_seconds
        self._now = now
        self._last_text = None
        self._last_change_at = None
        self._last_evaluated_text = None

    def observe(self, text):
        """Call on every poll with the focused field's current text (or
        None if focus moved off the watched field / field is unreadable)."""
        if text != self._last_text:
            self._last_text = text
            self._last_change_at = self._now()

    def reset(self):
        """Call when focus leaves the watched field entirely, so a later
        refocus doesn't fire on stale state."""
        self._last_text = None
        self._last_change_at = None
        self._last_evaluated_text = None

    def due(self):
        """Returns the text to evaluate if the idle window has elapsed
        since the last change and this text hasn't been evaluated yet;
        otherwise None."""
        if self._last_text is None or self._last_change_at is None:
            return None
        if self._last_text == self._last_evaluated_text:
            return None
        if self._now() - self._last_change_at < self._idle_seconds:
            return None
        self._last_evaluated_text = self._last_text
        return self._last_text
