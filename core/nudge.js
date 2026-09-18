// A small, dismissible toast — never a modal, never blocks anything by
// itself. Adapters call showNudge() and await the result before deciding
// whether to actually trigger the underlying send.
window.JevGuard = window.JevGuard || {};

(function () {
  const CONTAINER_ID = "jev-send-guard-nudge";

  function removeExisting() {
    const existing = document.getElementById(CONTAINER_ID);
    if (existing) existing.remove();
  }

  /**
   * @param {string[]} messages - one or more short concern descriptions
   * @returns {Promise<boolean>} true = send anyway, false = go edit it
   */
  window.JevGuard.showNudge = function showNudge(messages) {
    removeExisting();

    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.id = CONTAINER_ID;
      box.style.cssText = [
        "position: fixed",
        "right: 20px",
        "bottom: 20px",
        "z-index: 2147483647",
        "background: #202124",
        "color: #fff",
        "font-family: system-ui, sans-serif",
        "font-size: 13px",
        "line-height: 1.4",
        "padding: 12px 14px",
        "border-radius: 8px",
        "box-shadow: 0 2px 10px rgba(0,0,0,0.3)",
        "max-width: 280px",
      ].join(";");

      const title = document.createElement("div");
      title.textContent = "Before you send — Jev noticed:";
      title.style.cssText = "font-weight: 600; margin-bottom: 6px;";
      box.appendChild(title);

      const list = document.createElement("ul");
      list.style.cssText = "margin: 0 0 10px 18px; padding: 0;";
      for (const m of messages) {
        const li = document.createElement("li");
        li.textContent = m;
        list.appendChild(li);
      }
      box.appendChild(list);

      const buttons = document.createElement("div");
      buttons.style.cssText = "display: flex; gap: 8px; justify-content: flex-end;";

      const editBtn = document.createElement("button");
      editBtn.textContent = "Let me edit";
      editBtn.style.cssText =
        "background: #fff; color: #202124; border: none; border-radius: 4px; padding: 5px 10px; cursor: pointer;";
      editBtn.addEventListener("click", () => {
        removeExisting();
        resolve(false);
      });

      const sendBtn = document.createElement("button");
      sendBtn.textContent = "Send anyway";
      sendBtn.style.cssText =
        "background: transparent; color: #8ab4f8; border: 1px solid #5f6368; border-radius: 4px; padding: 5px 10px; cursor: pointer;";
      sendBtn.addEventListener("click", () => {
        removeExisting();
        resolve(true);
      });

      buttons.appendChild(editBtn);
      buttons.appendChild(sendBtn);
      box.appendChild(buttons);

      document.body.appendChild(box);
    });
  };
})();
