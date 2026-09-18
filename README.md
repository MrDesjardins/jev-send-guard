# Jev Send Guard

A Chrome extension that runs two fast `noul` questions against
[TypeSafe AI's Jev model](https://docs.typesafe.ai) on your draft message —
"does this read as curt," "is there a clear ask" — right before you send,
on Gmail, Discord (web), and Google Docs comments. Never blocks; worst case
is a one-click-dismissible nudge. See `PLAN.md` for the full design
rationale and scope decisions.

## Status

Core logic (the pre-filter heuristic and the live Jev call) is built and
**verified against the real API** — see the smoke-test commands below.
The three site adapters (Gmail/Discord/Google Docs DOM integration) are
written but **not yet verified in an actual browser** — this was built in
an environment with no GUI/Chrome available. The DOM selectors are
best-effort based on known patterns and will likely need adjustment once
loaded for real. Treat "load it and see what breaks" as the actual next
step, not a formality.

## Load it locally

1. `chrome://extensions` → enable **Developer mode** (top right).
2. **Load unpacked** → select this directory.
3. Click the extension's **Details** → **Extension options** → paste your
   TypeSafe API key (the same one in `../jevrealtimecodecheck/.env`) →
   **Save**.
4. Open Gmail, compose a message, and try sending something long and curt
   ("No. Figure it out yourself.") — expect a nudge. Try something short
   ("thanks!") — expect nothing, no delay.
5. Do the same on `discord.com/app` (a message box) and a Google Doc's
   comment panel.

If a site doesn't trigger at all, the adapter's selectors are almost
certainly stale for that site's current DOM — open dev tools, inspect the
actual compose/message box, and update the `*_SELECTOR` constants at the
top of the relevant file in `adapters/`.

## Verify the core logic without a browser

The pre-filter and Jev-calling logic have no DOM dependency and can be
exercised directly with Node (needs `TYPESAFE_API_KEY` in the environment):

```bash
TYPESAFE_API_KEY=... node -e "
global.window = global;
eval(require('fs').readFileSync('core/preFilter.js', 'utf8'));
eval(require('fs').readFileSync('core/jevClient.js', 'utf8'));
(async () => {
  console.log(await window.JevGuard.checkDraft(process.env.TYPESAFE_API_KEY,
    'No. Not doing that. Figure it out yourself.'));
})();
"
```

## Project layout

```
manifest.json           Chrome MV3 manifest, one content_scripts entry per site
options.html/.js        API key settings page
core/
  preFilter.js           Local heuristic: should this draft even be checked?
  jevClient.js            Calls Jev with two noul questions, fails open on any error
  nudge.js                 The dismissible toast UI
  guard.js                  Shared check -> maybe nudge -> maybe send flow
adapters/
  gmail.js                Gmail-specific DOM selectors + send-gesture interception
  discord.js               Discord (web) equivalent
  googleDocs.js              Google Docs comments equivalent (least reliable selectors)
```
