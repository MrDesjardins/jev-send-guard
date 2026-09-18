// Local, zero-latency heuristic that decides whether a draft is even worth
// sending to Jev. Most messages ("ok", "sounds good", "thanks!") are short
// enough that a check would only add latency without adding value — the
// whole point of this tool is that it must be invisible on the common case.
window.JevGuard = window.JevGuard || {};

(function () {
  const MIN_LENGTH = 40;

  // Very short acknowledgement-style messages skip the check even if they
  // happen to clear MIN_LENGTH (e.g. "Sounds good, thanks so much for the
  // quick turnaround on this!").
  const ACK_PATTERN = /^(ok(ay)?|sounds good|thanks?( you)?|np|got it|will do|sure|yep|yes|no problem|perfect|great|awesome)[.!\s]*$/i;

  window.JevGuard.shouldCheck = function shouldCheck(rawText) {
    const text = (rawText || "").trim();
    if (text.length < MIN_LENGTH) return false;
    if (ACK_PATTERN.test(text)) return false;
    return true;
  };
})();
