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
   names/bundle IDs. Originally console-only (`manage.py add/list/remove`);
   a system tray icon with a `tkinter` Settings window (`tray_app.py`,
   `core/settings_window.py`) was added afterward as the friendlier
   day-to-day entrypoint — most people won't run a console tool. `manage.py`
   still exists for scripting/headless setup and both read/write the same
   config file, so they're interchangeable.
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

- Exact idle threshold: lowered from the original 1s guess's sibling
  (`MIN_LENGTH`, not idle itself) after real usage — see `core/pre_filter.py`.
  The 1s idle window itself hasn't needed adjustment yet.
- ~~Whether `plyer` covers both OSes' notification needs~~ — resolved:
  `plyer` didn't reliably surface a visible toast on Windows, so the nudge
  became a custom `tkinter` popup anchored to the field's bounding rect
  instead (see `core/notifier.py`). No dependency needed either way.
- The v1 browser-extension JS code has been removed (2026-09-17) — v2 fully
  superseded it once the Windows backend was validated end-to-end, so there
  was nothing left to fall back to. The v1 write-up below is kept purely as
  historical record of the design that came before.
- **Slack desktop (Electron) not yet spiked on either OS** — Discord came
  back positive on Windows (see below), but each Electron app's
  accessibility tree can differ; verify Slack the same way before assuming
  coverage, and worth checking again once macOS is validated too.
- **macOS: not yet run against a live session.** `platform_backends/macos.py`
  is built to the same interface as `windows.py` (see Milestone 1b below)
  but is unverified — needs the same "spike and validate" pass Windows got.
  One macOS-specific gotcha to expect: the process running the agent needs
  Accessibility permission (System Settings → Privacy & Security →
  Accessibility) granted explicitly, or `get_focused_control()` will just
  return `None` forever with no error — that's the first thing to check if
  `manage.py add` times out with nothing detected.

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

Also built and validated end-to-end beyond the spike itself: the allowlist
(`manage.py add/list/remove`, `core/config.py`), idle debounce
(`core/idle_watcher.py`), ported pre-filter/Jev-call (`core/pre_filter.py`,
`core/jev_client.py`), and the anchored popup notifier (`core/notifier.py`).
Real issues hit and fixed along the way, worth knowing about before
touching this code:

- **Latency**: individual Jev calls were taking 14-18s on the test Windows
  machine despite a 4s per-phase timeout — not a timeout bug, since each
  phase (DNS/connect/TLS/request) can individually stay under its own
  4s budget while still summing to something much larger, because
  `httpx`'s `timeout=` is a per-operation budget, not a whole-request one.
  Root cause on that machine: IPv6 "happy eyeballs" (DNS returning an
  unreachable IPv6 address that has to time out before falling back to
  IPv4) and/or WPAD/PAC proxy auto-detection. Fixed in `jev_client.py` via
  `local_address="0.0.0.0"` (forces IPv4 — this is `httpcore`'s documented
  mechanism for it) and `trust_env=False` (skips proxy auto-detection).
- **Popup positioning**: the focused control's `BoundingRectangle` can have
  negative coordinates on a multi-monitor setup where a display sits
  above/left of the primary one. Clamping against `tkinter`'s
  `winfo_screenwidth/height()` (which only reports the *primary* monitor)
  silently misplaced the popup onto the wrong monitor. Fix: trust the
  accessibility API's raw coordinates, and always place the popup *above*
  the field (compose boxes sit near the bottom of their window/screen, so
  "below" routinely pushed it past the screen edge).
- **Startup speed**: `keyring`'s default backend selection scans installed
  packages' entry points to pick a backend, which is a known source of a
  slow first call. Fixed via explicit backend selection
  (`WinVaultKeyring`/macOS `Keyring`) in `core/api_key.py`.
- **Silence is ambiguous feedback**: given Jev calls can take several
  seconds, showing nothing on a clean result is easy to misread as "still
  working" or "broken." `notifier.notify_ok()` shows a brief green
  checkmark on a genuine clean result; a failed/timed-out call (fail-open)
  stays silent rather than falsely claiming "looks good."

### Milestone 1b (macOS) — built, not yet validated

`platform_backends/macos.py` mirrors `windows.py`'s exact function
interface (`get_focused_control`, `get_control_text`, `is_password_field`,
`get_bounding_rect`, `safe_runtime_id`, `run_add_flow`, etc.) using
`pyobjc`'s `ApplicationServices` bindings (`AXUIElementCopyAttributeValue`,
`AXValueGetValue` for position/size) instead of UI Automation. Same
fallback shape as Windows (direct value, then a child-walk over
`kAXChildrenAttribute` for rich editors that don't aggregate text on the
parent control) and same password hard-skip (checked via
`kAXRoleAttribute`/`kAXSubroleAttribute` containing `SecureTextField`).

Two things this couldn't inherit from the Windows validation and need a
real macOS run to confirm:
- Whether `AXUIElementCopyAttributeValue`'s out-parameter tuple convention
  (`(err, value)`) matches this pyobjc version exactly — written from
  documented patterns, not tested against a live call.
- Whether Electron apps' AX tree on macOS needs the same child-walk
  fallback Discord needed on Windows, or aggregates text differently.

`safe_runtime_id()` also can't use a real stable element ID the way
Windows' `RuntimeId` works — there's no macOS AX equivalent — so it builds
a fingerprint from pid + role + subrole + bounding rect instead. Good
enough for "did focus change," not a true identity check; worth
revisiting if it turns out to misfire on some app.

### Milestone 2 (tray app) — built, not yet validated

`tray_app.py` + `core/settings_window.py` replace the console (`manage.py`)
as the primary day-to-day interface: a `pystray` tray/menu-bar icon with
Settings, Pause/Resume, and Quit, and a `tkinter` window for adding/
removing watched apps and setting the API key (same detection flow as
`manage.py add`, just triggered by a button instead of typed at a prompt).

The watch loop itself was extracted from `agent.py` into
`core/watch_loop.py` so both entrypoints share it — `agent.py` now just
calls `watch_loop.run()` directly on the main thread (unchanged headless
behavior), while `tray_app.py` runs it in a background thread controlled
by `stop_event`/`pause_event`. The allowlist is re-read from disk every
poll iteration specifically so changes made in the Settings window take
effect immediately without restarting the loop.

One real threading assumption here that couldn't be validated without a
real GUI session: `on_settings()` spawns a background thread that builds
and runs its own `tk.Tk()` + `mainloop()` (the same isolated-Tk-per-thread
pattern `core/notifier.py`'s popups already use successfully) in response
to a `pystray` menu click. This is a well-established pattern for
combining the two libraries, but macOS in particular is stricter about
which thread owns the GUI run loop than Windows is — if the Settings
window doesn't open, or opens but behaves oddly, this is the first thing
to suspect. A `_settings_lock` prevents two Settings windows (and two
competing Tk mainloops) from opening at once, but doesn't address the
main-thread question if that turns out to matter on macOS.

### Milestone 3 (four questions + severity) — 2026-09-17

v1's scope decision #4 ("two `noul` questions max, not a kitchen sink" —
see the v1 write-up below) was expanded to four:
`unprofessional` (warning/yellow) and `impolite` (error/red) joined the
original `curt`/`missing_ask` (both error/red). All four are framed the
same way (true = concern flagged) and live in one place —
`core/jev_client.py`'s `QUESTIONS` dict — with each question's severity
and popup message alongside it, so `check_draft()`/`watch_loop.py` score
and display them generically instead of hardcoding four near-identical
if-blocks. Verified against the live API: an insult flags
`curt`/`unprofessional`/`impolite` together; a casual-but-harmless
message ("yo bro...") flags only `unprofessional`; a polite explicit
request comes back fully clean.

`core/notifier.py`'s popup now takes a list of `(message, severity)`
tuples rather than plain strings: each bullet is colored/iconed by its
own severity (⚠ yellow for warning, ⛔ red for error), and the title uses
the *worst* severity present — so an unprofessional-only result shows
yellow throughout, but adding any error-severity concern turns the title
red while the unprofessional bullet stays yellow. Precision is worth
watching now more than with two questions: four independent 0.7-threshold
judgments on the same short draft means more chances for an occasional
wrong flag, particularly `unprofessional` given how casual most personal
chat is by default (Discord especially) — if it over-fires, narrowing
its instructions or raising just its own threshold is the fix, not
lowering `NOUL_THRESHOLD` globally.

### Popup lifecycle bug (2026-09-17): stale popups not replaced

Reported bug: fixing a flagged message and getting a clean result showed
the new "Looks good" checkmark *alongside* the old curt/impolite warning
rather than replacing it — because the original `core/notifier.py` design
gave every `notify()`/`notify_ok()` call its own throwaway `Tk()`
interpreter on its own thread, with no tracking of what was already on
screen.

Rewritten to a single persistent Tk interpreter on one dedicated
background thread for the process's lifetime, with `notify()`/`notify_ok()`
just enqueueing a request onto a `queue.Queue()` that thread's mainloop
polls every 50ms; each new request destroys whatever `Toplevel` is
currently up before showing the new one. This is the standard correct
pattern for driving a GUI toolkit from other threads (one thread owns the
mainloop, everyone else talks to it via a thread-safe queue) — not just a
fix for the reported bug, but a strictly more correct design than the
original one-interpreter-per-popup approach regardless.

Testing note, in the interest of not overclaiming: while building this,
an attempt to verify it under Xvfb on this Linux dev box hit a real,
reproducible crash (an Xlib/xcb assertion failure) — but further isolation
showed the crash was inconsistent even for trivial single-threaded Tk
code with no relation to this module's design (a bare recurring
`root.after()` loop on the main thread passed once, then a slightly more
complex main-thread-only Toplevel test crashed the same way). That points
to this sandbox's particular Xvfb/libxcb setup being generally unreliable
for validating `tkinter` here, not a specific finding about this code —
Windows (GDI) and macOS (Cocoa) don't share Xlib's threading model at all,
so this Linux-specific instability may not even apply to them. **Real
verification of the popup-replacement behavior still needs to happen on
an actual Windows or macOS session** — trigger two evaluations in a row
(one flagged, one clean, or vice versa) and confirm only the latest popup
is ever on screen.

### Windows browser-host bug (2026-09-18): browser-scoped apps never matched

A later change (outside this conversation's history — see the "Update for
MacOS" commit) added website-scoping for browsers: a browser process
(Chrome/Edge/Firefox/Chromium) can only be watched for one explicit host
(e.g. `docs.google.com`), read via `backend.get_active_browser_host
(process_name)`, checked every poll in `core/watch_loop.py`. macOS
implements this via AppleScript (`get_active_browser_host` in
`platform_backends/macos.py`, asking the named browser app for its active
tab's URL). **Windows never got the equivalent function at all** — the
attribute didn't exist on the module, so `getattr(backend,
"get_active_browser_host", None)` silently returned `None` every time,
which meant `active_host` was always `None` and no browser-scoped rule
could ever match. Reported symptom: a Google Docs rule (Firefox +
`docs.google.com`) configured via Settings, but the log showed
`host=None watched=False` on every Firefox focus event, forever.

Fixed by adding `get_active_browser_host` to `platform_backends/windows.py`.
There's no AppleScript-equivalent tab-URL API on Windows, so it reads the
address bar's actual on-screen text via the same UI Automation mechanism
already used for every other text field: breadth-first search the
foreground window (bounded to 400 nodes / depth 6, so a large page's
accessibility tree can't stall the 300ms poll loop) for an Edit/ComboBox
control whose text parses as a URL, then keep only the hostname. The found
control is cached per foreground window handle so repeated polls just
re-read its text instead of re-walking the tree every 300ms. Verified the
hostname-parsing logic in isolation (rejects search-query text containing
spaces, handles bare-host and full-URL forms, case-insensitive) — the
UI Automation tree search itself still needs a real Windows run to confirm
it actually locates the address bar reliably across Chrome/Edge/Firefox,
the same "built from the documented API shape, not yet run live" caveat
as the rest of this project's Windows-specific accessibility code.

### Milestone 4 (tests, tuning, stats, snooze, login install) — 2026-09-18

A batch of improvements picked from a review of the whole system:

- **Automated tests** (`tests/`, `pytest`): everything OS-independent now
  has real coverage — pre-filter, idle debounce, config (allowlist, domain
  normalization, question overrides), the Jev client's question-merging
  and scoring (network mocked), and `watch_loop.py`'s actual decision
  logic (allowlist matching, browser-host gating, password/read-only
  skipping, pause) exercised via a new fake backend,
  `platform_backends/mock.py`, implementing the same interface as
  `windows.py`/`macos.py`. This is what would have caught the Windows
  browser-host bug (above) automatically, before it ever shipped. 49
  tests, all passing; run with `uv sync --extra dev && uv run pytest`.
- **Per-app question tuning**: `core/config.py`'s `set_app_disabled_questions`
  lets a specific app skip specific questions (e.g. turn off
  `unprofessional` for a casual Discord server) without a global change.
  Exposed via Settings' new "Edit selected" button.
- **Global prompt configuration**: `core/jev_client.py`'s `QUESTIONS` are
  now defaults, not the final word — `get_question_defs()` merges in
  user overrides from `config.toml` (instructions, message, severity,
  enabled/disabled), and `check_draft()` only asks Jev the
  enabled-and-not-per-app-disabled questions, skipping the network call
  entirely if none are active. Exposed via Settings' new "Configure
  checks..." dialog, with a per-question "Reset to default." `QUESTIONS`
  itself is never mutated, so "default" always means the original wording.
- **Local usage stats** (`core/stats.py`, `~/.jev-send-guard/stats.json`):
  counts real checks (pre-filter skips and failed/timed-out calls don't
  count — they say nothing about threshold quality) and which question
  flagged how often, surfaced as a summary in Settings. Purely local,
  never sent anywhere; exists so "is `unprofessional` over-firing on
  Discord" (a concern raised back in Milestone 3) becomes a number you
  can check instead of a guess.
- **Snooze**: the tray's Pause was previously indefinite-only; a
  Snooze submenu (15/30/60 min, auto-resume via `threading.Timer`) was
  added alongside it. Manually toggling Pause cancels any pending snooze
  so a stale timer can't re-pause after a manual resume.
- **Login-item installers** (`install/install.py`): a `schtasks`-based
  Windows Task Scheduler logon task and a macOS launchd user agent plist,
  so the tray app can actually run automatically instead of being
  launched by hand every session. The plist-generation logic was verified
  (valid XML, correct paths) with `subprocess.run` mocked out, but neither
  installer has been run for real — same "needs an actual OS session"
  caveat as everything else platform-specific here. Check
  `schtasks /query /tn JevSendGuardTray` or `launchctl list | grep
  jevsendguard` after installing, and confirm the tray icon actually
  appears after a real logout/login, not just "no error was printed."

### Milestone 5 (custom questions, shared window icon) — 2026-09-18

Two follow-ups from actually using Milestone 4's Settings additions:

- **Every Tk window showed Tk's default icon (a feather on Windows)
  instead of matching the tray icon.** `core/icon.py` is now the single
  source of truth for the app's icon (a generated circle, no external
  asset file): `tray_app.py`'s `ICON_RUNNING`/`ICON_PAUSED` are built from
  it, and `core/settings_window.py` calls `icon.set_window_icon()` on the
  main Settings window and both of its dialogs. A `PhotoImage` reference
  is kept on the widget itself (`_icon_photo_ref`) since Tk doesn't retain
  one internally — a garbage-collected `PhotoImage` silently blanks the
  icon back out, a easy-to-miss gotcha with this API.
- **"Configure checks" could only edit the four built-in questions — no
  way to add a new one or remove one entirely.** Added `core/config.py`'s
  `custom_questions` table (separate from `questions`, the built-in
  overrides table, since a custom question has no code-level default to
  fall back to) with `add_custom_question`/`update_custom_question`/
  `remove_custom_question`/`list_custom_questions`.
  `jev_client.get_question_defs()` merges these in alongside the four
  built-ins, so `check_draft()`, per-app disabling, and the popup styling
  all handle a custom question identically to a built-in one with zero
  additional code — verified end-to-end against the live API (a custom
  "overpromising" question was correctly scored `True` for "I guarantee
  this will be 100% bug-free... no matter what"). Settings' "Configure
  checks" dialog gained "Add new check..." (validates the internal name:
  lowercase/digits/underscores only) and "Delete" (built-ins reject
  deletion with a message pointing at disabling instead).

Test suite grew from 49 to 54 to cover the custom-question CRUD and its
merge/scoring behavior; still all pure-logic, no OS session needed.

### Milestone 6 (three bugs from a code review) — 2026-09-18

- **macOS's `is_writable_text_control` would have silently broken Discord/
  Slack.** It required the focused control's AX role to be exactly
  `AXTextArea`/`AXTextField`/`AXComboBox` — but this same file's
  `run_add_flow` already documented that Electron apps can expose their
  compose box as `AXGroup`/`AXWebArea` instead. That role whitelist would
  have rejected exactly those controls before ever reading their text,
  meaning Discord/Slack messages — the whole reason v2 exists — would
  never be evaluated on macOS at all. Removed the whitelist; now mirrors
  `windows.py`'s already-validated approach (read-only + plausible size
  only, not control type). `get_focused_control()` only ever returns
  whatever currently holds keyboard focus, and non-interactive static
  content normally can't, so this isn't as loose as dropping a whitelist
  might sound.
- **A corrupted `config.toml` would silently and permanently kill
  monitoring.** `load_config()` had no exception handling around parsing
  it. Since `watch_loop.py` calls `list_apps()` on every ~300ms poll, a
  bad manual edit, a crash mid-write, or a race between two processes
  saving around the same time (`tray_app.py` + `settings_app.py`) would
  raise on the very next poll and propagate straight up. In `tray_app.py`
  this runs inside a daemon thread with no handling around it either:
  Python prints a traceback to stderr and silently kills just that
  thread, while the tray icon keeps showing "running" — nothing is ever
  checked again for the rest of the session, no visible symptom. Fixed at
  the source: `load_config()` now catches `TOMLDecodeError`/`OSError` and
  fails closed to an empty config (logged), matching the project's
  existing "default empty" philosophy. Also added a try/except around
  `run_watch_loop()` itself in `tray_app.py` as a backstop against any
  *other* unexpected exception, not just this one.
- **`notification_app.py`'s (macOS native popup) title was hardcoded
  red**, regardless of which severities were actually present — an
  `unprofessional`-only (warning) result would still show a red title
  implying an error-level concern, even though each bullet's own color
  was already correct. `core/notifier.py`'s Windows popup already
  computes the title from the worst severity actually present; the
  macOS one now matches it (and gained the same title icon Windows has,
  which it was missing entirely).

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
