// Discord (web) adapter.
//
// Assumptions (best-effort, not verified in a live browser from this
// environment — see PLAN.md's "DOM fragility" note):
// - The message box is a Slate.js-based editor exposing
//   `div[role="textbox"][data-slate-editor="true"]`, a combination widely
//   relied on by Discord userscripts/extensions.
// - Unlike Gmail, Discord sends on a bare Enter; Shift+Enter inserts a
//   newline instead. There's no separate Send button to intercept in the
//   normal desktop-web flow.
(function () {
  const BOX_SELECTOR = 'div[role="textbox"][data-slate-editor="true"]';

  let bypass = false;

  function isSendKeydown(e) {
    return e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey;
  }

  function getText(el) {
    return el ? el.innerText || el.textContent || "" : "";
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
})();
