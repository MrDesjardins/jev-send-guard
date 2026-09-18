# Jev Send Guard — Plan

## What this is (v2: native agent, not a browser extension)

A small background agent, one per OS (Windows + macOS), that watches
whatever text field currently has OS focus **in an explicit allowlist of
apps** and, after you stop typing for a short idle window, reads the
field's current text via the OS accessibility API and runs one or two fast
`noul` questions against TypeSafe AI's Jev model — "does this read as
curt/aggressive," "is there a clear, explicit ask" — surfacing a
dismissible native OS notification if something looks off. It never
blocks anything.

This supersedes the v1 design below the fold: a Chrome-only extension
watching Gmail/Discord/Docs DOM at send time. That code stays in
`adapters/`/`manifest.json` as a working reference for the shared
pre-filter/Jev-call logic, but the active direction is the native agent.

## Why the pivot

v1 only worked in Chrome, and only on three specific sites, because it
hooked each site's DOM and send gesture directly. The actual goal —
catching a curt or ask-less message before it goes out — isn't
browser-specific or Gmail-specific. The accessibility APIs both OS vendors
ship for screen readers (macOS `AX*`, Windows UI Automation) expose the
live text of *any* focused control in *any* accessibility-compliant app,
including Firefox, Slack desktop, Outlook, and Discord's desktop client —
without needing an extension per browser or an adapter per site.

## Why idle-based, not gesture-based

v1 hooked a specific "send" action (Ctrl+Enter, a Send button) per site.
There's no generic, reliable "send" hook across arbitrary apps at the OS
level. So the trigger changes from "right before you send" to "you paused
typing for N ms" — close enough in practice (most people pause briefly
before sending anyway) but an explicit behavior change worth naming: this
can no longer guarantee the nudge appears before the message is gone: if
you type and hit Enter immediately with no pause, the agent may not have
evaluated it yet. That tradeoff is accepted in exchange for going from 3
supported surfaces to effectively all of them.

## Non-goals (updated for v2)

- **Not all-apps by default.** The agent only reads focus/text-change
  events while the foreground app matches an explicit, user-configured
  allowlist (by process name / bundle ID on macOS, executable name on
  Windows). Default allowlist is empty — nothing is watched until the
  user adds an app. This is the important boundary: without it, this is
  a generalized cross-app keystroke-content reader, which is not
  something to build casually.
- **Not a keylogger.** The agent never records individual keystrokes and
  never persists captured text anywhere. It holds the current field value
  in memory only long enough to run the pre-filter and, if needed, one
  Jev call, then discards it. No history, no logs of message content.
- **Not a hard gate.** Still never blocks or delays sending — it can't,
  by construction, since it isn't hooked to a send action anymore.
- **Not a grammar/typo checker or writing assistant.** Same as v1 — tone
  and "is there a clear ask," nothing else. No rewriting, no suggestions.
- **Not a password/secret reader.** Fields exposed via accessibility as
  secure/masked (e.g. `AXSecureTextField` on macOS, password-flagged
  controls in UI Automation) must be explicitly skipped regardless of
  allowlist — this is a hard rule, not a config option.

## Scope decisions for v2

1. **Language: Python, one shared codebase, platform backends behind a
   common interface.** Both OS accessibility APIs have mature Python
   bindings — `pyobjc` (`ApplicationServices`/`AXUIElement`) on macOS,
   `comtypes` + the UI Automation COM interfaces (or the `uiautomation`
   package) on Windows. A `platform_backends/mac.py` and `platform_backends/windows.py`
   each implement the same small interface (watch focus changes, watch
   text-changed events on the focused element, read current value, check
   whether a field is a secure/password field) so the shared core
   (pre-filter, Jev client, idle debounce, notification) is pure Python
   and OS-agnostic.
2. **Per-app allowlist, configured explicitly.** A local config file
   (e.g. `~/.jev-send-guard/config.toml`) lists watched process
   names/bundle IDs. No UI needed for v2.1 — hand-edited config is fine
   to start; a small tray/menu-bar settings UI is a later nice-to-have,
   not required to ship.
3. **Idle debounce, not per-keystroke.** Reset a timer on every
   text-changed event for the focused, allowlisted field; when the timer
   fires (default ~1s of no changes) without the field losing focus,
   read the current value and hand it to the existing pre-filter.
4. **Reuse the existing pre-filter and Jev-call logic, ported to
   Python.** The heuristic (`MIN_LENGTH`, the acknowledgement-pattern
   skip list) and the two-`noul`-question Jev call are proven logic from
   v1 — port `core/preFilter.js` and `core/jevClient.js` to Python
   as-is, don't redesign them.
5. **Nudge is a small borderless popup anchored to the actual textbox**,
   not a generic OS notification-center toast (tried `plyer` first; a
   corner toast is too easy to miss and not obviously tied to what you're
   typing) and not an in-page banner (no DOM to inject into anymore). Built
   on `tkinter` (stdlib, ships with Python on both Windows and macOS) —
   positioned using the focused control's screen-coordinate bounding rect
   (`BoundingRectangle` via UI Automation on Windows, the `AXPosition`/
   `AXSize` attributes via `AX*` on macOS), same visual style as v1's
   in-page banner. No "Send anyway"/"let me edit" choice — v2 isn't hooked
   to a send gesture, so there's nothing for a button to gate; it's
   purely informational and auto-dismisses. Being stdlib-based, this one
   module (`core/notifier.py`) may end up working unchanged on macOS too,
   rather than needing a separate per-OS implementation.
6. **API key storage: OS credential store, via the `keyring` package**
   (Keychain on macOS, Credential Manager on Windows) — same principle as
   v1's `chrome.storage.local`, just the OS-native equivalent for a
   background process with no browser storage available.
7. **Runs as a background/login-item process**, not something the user
   launches manually each session — a macOS `launchd` user agent
   (`~/Library/LaunchAgents`) and a Windows Startup/Task Scheduler entry,
   set up by a small install script rather than requiring manual setup
   each boot.

## Architecture sketch

```
core/                        (pure Python, OS-agnostic)
  pre_filter.py               Ported from preFilter.js — unchanged logic
  jev_client.py                 Ported from jevClient.js — unchanged logic
  idle_watcher.py                 Debounce: text-changed events -> "evaluate now"
  notifier.py                       Cross-platform notification dispatch
  config.py                           Load/save allowlist + settings

platform_backends/
  mac.py                      pyobjc: AXObserver focus + value-changed
                              notifications, AXUIElementCopyAttributeValue
                              for current text, secure-field detection
  windows.py                  comtypes/uiautomation: focus-changed +
                              TextChanged event handlers, TextPattern/
                              ValuePattern for current text, IsPassword
                              property check

agent.py                     Entrypoint: picks the platform backend, wires
                              it to idle_watcher -> pre_filter -> jev_client
                              -> notifier, loads config.py for the allowlist

install/
  mac_launchd.plist           Login-item registration for macOS
  windows_task.xml              Login-item registration for Windows
  install.py                      Writes the above + registers them
```

## Open questions to resolve while building

- Exact idle threshold (starting guess: 1s) — same "tune after real
  usage" posture as v1's Jev threshold.
- Whether `plyer` covers both OSes' notification needs well enough, or
  whether per-OS native notification code is needed from the start —
  decide once actually testing on both.
- Whether to keep the v1 browser-extension code around as a fallback for
  apps whose accessibility tree doesn't expose useful text (leaning: keep
  it working but not invest further in it while v2 is unproven).
- **Slack desktop (Electron) not yet spiked on Windows** — Discord came
  back positive (see below), but each Electron app's accessibility tree
  can differ; verify Slack the same way before assuming coverage.

### Milestone 1 (Windows) — validated 2026-09-17

`platform_backends/windows_spike.py`, polling `GetFocusedControl()` + trying
`TextPattern` → `ValuePattern` → legacy `IAccessible` → child-walk in that
order, confirmed real text reads on all three target surfaces:

- Firefox address bar — `ValuePattern`.
- Gmail compose in Firefox (contenteditable) — `ValuePattern`.
- Discord message box (Electron/Slate.js) — `ValuePattern`/plain
  `TextPattern` only return a zero-width-space placeholder while the box
  is *empty*; once real text exists, `TextPattern`'s `DocumentRange.GetText`
  reflects it correctly, live, character by character. This was the
  biggest unknown motivating the pivot (whether Electron rich editors
  expose real content via accessibility) and it resolved positively.

The child-walk-children fallback (concatenating leaf node `Name`s) was
added as a safety net for editors that don't aggregate text at all on the
parent control, but wasn't needed for Discord specifically — worth keeping
for Slack or other apps that may behave differently.

## Rough milestones

1. **Spike accessibility read on both OSes** — a throwaway script per
   platform that logs the focused element's text on value-change, no
   allowlist/idle/Jev logic yet. This is the step that validates or kills
   the whole approach; do it before anything else.
2. **Allowlist + idle debounce**, still just logging to console — prove
   the trigger logic (including secure-field skipping) before wiring Jev.
3. **Port `pre_filter.py`/`jev_client.py`** and wire them into the
   debounce trigger — confirm end-to-end latency from "stop typing" to
   "notification" is acceptable.
4. **Native notifications**, both OSes, with a working dismiss.
5. **Login-item install scripts** for both OSes.
6. **Dogfood across whatever apps get allowlisted first** (likely: Chrome
   + Discord desktop + Slack desktop), tune the idle threshold and
   pre-filter based on real false positives/negatives.

## Context for whoever (or whatever) picks this up next

v1 (this repo's original scope) is documented below for reference — it's
a working Chrome MV3 extension with verified core logic (see README) but
DOM adapters never tested in a live browser. It's being superseded because
its reach was capped at three sites in one browser, and the actual value
(catching curt/ask-less messages) generalizes better as an OS-level
service than as a per-site DOM integration. The core evaluation logic
(pre-filter heuristic, two-`noul`-question Jev call) carries forward
unchanged into v2 — only the capture mechanism and delivery UI change.

---

## v1 plan (superseded — kept for reference)

### What v1 was

A browser extension that watches whatever text field currently has focus
(Gmail compose, initially) and, right before you send, runs one or two fast
`noul` questions against TypeSafe AI's Jev model — "does this read as
curt/aggressive," "is there a clear, explicit ask" — and shows a small,
dismissible nudge if something looks off. It never hard-blocks sending.

### Why this, and why Jev specifically

Most everyday computer actions (typing, saving, clicking) don't benefit
from an AI check that takes a couple of seconds — the wait is worse than
the value. Sending a message is one of the few moments people already
pause on for a half-second anyway, and Jev's `noul`/`choice`/`score`
primitives are built to answer a single structured question fast, not
generate prose. The test for whether this idea is worth building at all:
**it must be worse with a normal 2-3s LLM call than with nothing.** If the
check ever feels like "waiting on AI," the product has failed regardless
of how accurate it is.

### Non-goals for v1

- **Not a Slack bot.** No workspace integration, no bot permissions
  needed — this is a client-side browser extension watching the DOM of
  pages already open in your own browser. Slack/Docs support can come
  later; see "Scope decisions" below for why they're deliberately
  deferred.
- **Not a hard gate.** It never prevents sending. Worst case, it's a
  nudge you dismiss in one click.
- **Not a grammar/typo checker.** That's a solved problem (native
  spellcheck). This only judges tone and "did you actually ask for what
  you need," which nothing currently automates.
- **Not a general-purpose writing assistant.** No rewriting, no
  suggested replacement text, no generated explanations — matching the
  "structured judgment, not generated text" philosophy the sibling
  project (`jevrealtimecodecheck`) already established.

### Scope decisions for v1 (from the brainstorm this plan is based on)

1. **Surface: Gmail, Discord (web), and Google Docs comments.** Not
   Slack (no bot needed here either, but not in scope yet), not a
   desktop app. Each site gets its own thin adapter (DOM selectors + its
   own send-gesture) on top of one shared core (pre-filter, Jev call,
   nudge UI) — the adapter is the only part that's site-specific. Gmail
   sends on Ctrl+Enter or a Send-button click; Discord sends on Enter
   (Shift+Enter for a newline, the opposite convention from Gmail);
   Google Docs' comment box sends on Ctrl+Enter or a Comment-button
   click. Selectors for all three are best-effort from known DOM
   patterns, not verified against a live browser session from this
   environment — expect to need adjustment once actually tested in
   Chrome.
2. **Personal use only, not work email.** Sending draft message text to
   a third-party API before you've hit send is a real thing to be
   deliberate about, especially for anything under an employer's data
   policies. v1 targets a personal Gmail account. Revisit explicitly,
   don't assume, before ever pointing this at work communication.
3. **A cheap local pre-filter before ever calling Jev.** Most sent
   messages are short and fine ("ok", "sounds good", "thanks!"). Calling
   Jev on every single one adds latency-that-doesn't-matter at best and
   trains the user to ignore it at worst. A local heuristic (message
   length above some threshold, presence of certain words, absence of a
   question mark on what reads like a request) decides whether to call
   Jev at all. Most sends should never touch the network.
4. **Two `noul` questions max, not a kitchen sink.** "Does this read as
   curt/aggressive" and "is there a clear, explicit ask if one seems
   needed." More questions than that risks both added latency and a
   noisier, less trustworthy signal. Precision matters more than
   coverage — a checker that's wrong too often gets disabled.

### Architecture sketch (v1)

- **Chrome extension (Manifest V3), content script only** — no
  background service worker doing anything beyond holding the API key
  in extension storage. No server component for v1: the content script
  calls the Jev API directly.
- **Content script responsibilities:**
  1. Detect Gmail's compose box (there can be multiple open at once —
     reply, forward, new compose).
  2. Intercept the send gesture (capture-phase keydown listener for
     Ctrl+Enter, plus a click listener on the Send button) before Gmail's
     own handler fires.
  3. Run the local pre-filter. If it says "skip," let the native send
     proceed immediately — no delay, no visible extension behavior at
     all.
  4. If the pre-filter says "check it," call Jev with the draft body as
     `state` and the two `noul` questions. Show a lightweight inline
     banner only if either answer crosses a threshold; otherwise let the
     send proceed exactly as if the extension weren't there.
  5. The banner has one clear dismiss/override action ("Send anyway") and
     never blocks a second attempt.
- **API key storage:** `chrome.storage.local`, entered once via the
  extension's options page — same SecretStorage-equivalent posture as
  the VS Code extension, never hardcoded, never logged.
- **No telemetry, no message content persisted anywhere** — the draft
  text is sent to Jev for the single request and not stored by the
  extension itself.

### Rough milestones (v1)

1. **Scaffold the extension** — manifest, options page for the API key,
   a content script that can detect Gmail's compose box and log when
   Ctrl+Enter/Send is pressed (no Jev call yet, just proving the
   intercept works without breaking normal sending).
2. **Wire the local pre-filter** — decide skip vs. check, still no Jev
   call, just prove the gating logic on real drafts.
3. **Wire the Jev call** — the two `noul` questions, real API key,
   confirm latency is acceptable end to end on a real Gmail compose box.
4. **The nudge UI** — the inline banner, dismiss/override, and confirm it
   never blocks a second send.
5. **Dogfood on personal email for a week**, tune thresholds and the
   pre-filter based on real false positives/negatives before considering
   any additional surface (Slack, Docs) or scope expansion.

This plan came out of a longer brainstorm session about what to build
around TypeSafe AI's Jev model, alongside the existing
`jevrealtimecodecheck` project (a VS Code extension + GitHub Action that
reviews code diffs against Markdown-defined rules using the same `choice`/
`score`/`noul` primitives). Several other ideas were considered and set
aside for this one specifically because it's the only one where Jev's
*speed* is load-bearing rather than a nice-to-have — see the "Why this"
section above. If priorities change, the other ideas discussed were: a
smart clipboard dispatcher, a live meeting-notes auto-tagger, a "Jev Print
Check" for 3D-printer slicer profiles (reusing the code-review pattern),
a live OnShape parameter-tuning loop, and a real-estate listing red-flag
scanner (blocked mainly by Zillow/Redfin having no public API).
