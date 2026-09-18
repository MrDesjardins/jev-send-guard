// Google Docs (comments) adapter.
//
// Assumptions — the LEAST reliable of the three adapters, flagged
// explicitly in PLAN.md. Google Docs renders most of the page on canvas,
// and the comment UI's class names are internal/obfuscated and change
// across releases. Best-effort selectors, combining a couple of known
// patterns (the `.docos-*` class prefix historically used for the comment
// overlay, and aria-label-based fallbacks) — expect to need real
// inspection once actually tested in a browser.
// - Comment/reply input: a contenteditable box, either `.docos-input` or
//   one exposing `aria-label="Comment"`/`aria-label="Reply"`.
// - Submit: Ctrl+Enter in that box, or a button labeled "Comment"/"Reply".
(function () {
  const BOX_SELECTOR = [
    '.docos-input[contenteditable="true"]',
    'div[aria-label="Comment"][contenteditable="true"]',
    'div[aria-label="Reply"][contenteditable="true"]',
  ].join(", ");

  const BUTTON_SELECTOR = [
    'div[aria-label="Comment"][role="button"]',
    'div[aria-label="Reply"][role="button"]',
    ".docos-input-postbutton",
    ".docos-replyview-postbutton",
  ].join(", ");

  let bypass = false;

  function isSendKeydown(e) {
    return e.key === "Enter" && (e.ctrlKey || e.metaKey);
  }

  function getText(el) {
    return el ? el.innerText || el.textContent || "" : "";
  }

  function findCommentContainer(el) {
    return el.closest(".docos-replyview, .docos-anchoreddocoview, [role=\"dialog\"]");
  }

  document.addEventListener(
    "keydown",
    (e) => {
      if (bypass) return;
      if (!isSendKeydown(e)) return;
      const box = e.target.closest(BOX_SELECTOR);
      if (!box) return;

      const text = getText(box);
      if (!window.JevGuard.shouldCheck(text)) return;

      e.preventDefault();
      e.stopImmediatePropagation();

      window.JevGuard.runCheck(text, () => {
        bypass = true;
        box.dispatchEvent(
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
      const button = e.target.closest(BUTTON_SELECTOR);
      if (!button) return;

      const container = findCommentContainer(button);
      const box = container ? container.querySelector(BOX_SELECTOR) : null;
      const text = getText(box);
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
