// Shared "check, maybe nudge, then maybe proceed" flow used by every site
// adapter, so each adapter only has to handle its own DOM/send-gesture
// wiring, not this logic.
window.JevGuard = window.JevGuard || {};

(function () {
  /**
   * @param {string} text - the draft text
   * @param {() => void} doRealSend - called (synchronously) when the send
   *   should actually go through, whether because nothing was flagged or
   *   because the user clicked "Send anyway."
   */
  window.JevGuard.runCheck = async function runCheck(text, doRealSend) {
    if (!window.JevGuard.shouldCheck(text)) {
      doRealSend();
      return;
    }

    const apiKey = await window.JevGuard.getApiKey();
    const result = await window.JevGuard.checkDraft(apiKey, text);

    if (!result || (!result.curt && !result.missingAsk)) {
      doRealSend();
      return;
    }

    const messages = [];
    if (result.curt) messages.push("This might read as curt or blunt.");
    if (result.missingAsk) messages.push("Doesn't seem to have a clear ask.");

    const proceed = await window.JevGuard.showNudge(messages);
    if (proceed) doRealSend();
  };
})();
