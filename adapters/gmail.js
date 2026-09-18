// Gmail adapter.
//
// Assumptions (best-effort, not verified in a live browser from this
// environment — see PLAN.md's "DOM fragility" note):
// - The compose body is `div[aria-label="Message Body"][contenteditable="true"]`.
//   This aria-label has been stable in Gmail's web UI for a long time and is
//   the selector most third-party Gmail extensions rely on.
// - Send is triggered either by Ctrl+Enter (Cmd+Enter on macOS) while
//   focused in that body, or by clicking the Send button, which exposes a
//   `data-tooltip` starting with "Send".
(function () {
  const BODY_SELECTOR = 'div[aria-label="Message Body"][contenteditable="true"]';
  const SEND_BUTTON_SELECTOR = 'div[role="button"][data-tooltip^="Send"]';

  let bypass = false;

  function isSendKeydown(e) {
    return e.key === "Enter" && (e.ctrlKey || e.metaKey);
  }

  function getText(el) {
    return el ? el.innerText || el.textContent || "" : "";
  }

  function findComposeContainer(el) {
    // The compose window/dialog wrapping both the body and the Send button.
    return el.closest('[role="dialog"], table.M9, div.AD');
  }

  document.addEventListener(
    "keydown",
    (e) => {
      if (bypass) return;
      if (!isSendKeydown(e)) return;
      const body = e.target.closest(BODY_SELECTOR);
      if (!body) return;

      const text = getText(body);
      // Let the untouched case fall through with zero interception.
      if (!window.JevGuard.shouldCheck(text)) return;

      e.preventDefault();
      e.stopImmediatePropagation();

      window.JevGuard.runCheck(text, () => {
        bypass = true;
        body.dispatchEvent(
          new KeyboardEvent("keydown", {
            key: "Enter",
            ctrlKey: true,
            bubbles: true,
            cancelable: true,
          })
        );
        setTimeout(() => {
          bypass = false;
        }, 0);
      });
    },
    true
  );

  document.addEventListener(
    "click",
    (e) => {
      if (bypass) return;
      const button = e.target.closest(SEND_BUTTON_SELECTOR);
      if (!button) return;

      const container = findComposeContainer(button);
      const body = container ? container.querySelector(BODY_SELECTOR) : null;
      const text = getText(body);
      if (!window.JevGuard.shouldCheck(text)) return;

      e.preventDefault();
      e.stopImmediatePropagation();

      window.JevGuard.runCheck(text, () => {
        bypass = true;
        button.click();
        setTimeout(() => {
          bypass = false;
        }, 0);
      });
    },
    true
  );
})();
