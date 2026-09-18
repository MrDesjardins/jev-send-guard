# Jev Send Guard

A small background agent that watches whatever text field currently has OS
focus — but only in an explicit allowlist of apps you configure — and,
after you stop typing for about a second, runs four fast `noul` questions
against [TypeSafe AI's Jev model](https://docs.typesafe.ai) on the current
draft: curt/blunt, missing a clear ask, unprofessional, impolite. A small
popup appears anchored right next to the field: red for an error-severity
concern (curt, missing ask, impolite), yellow for a warning (unprofessional),
green with a checkmark if it's clean. It never blocks anything — there's no
send gesture to hook into by design. See `PLAN.md` for the full design
rationale, including the v1 Chrome-extension approach this replaced and why.

## Status

- **Windows**: built and validated end-to-end against real apps (Discord
  desktop, Firefox/Gmail). See `PLAN.md`'s "Milestone 1" for what was
  tested and the real bugs hit and fixed along the way (latency, popup
  positioning, startup speed, browser-host detection).
- **macOS**: built to the same interface as Windows (`platform_backends/
  macos.py`) but not yet run against a live session — see `PLAN.md`'s
  "Milestone 1b" for what specifically needs validating.
- **Tray app** (`tray_app.py`): runs Settings separately on macOS because
  Cocoa and Tk cannot safely own one interpreter's GUI event loop. Draft
  results use native non-activating popups near the editor, preserving focus.
- **Login-item installers** (`install/install.py`): written but not yet run
  against a real session — see PLAN.md.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
# Windows
uv sync --extra windows

# macOS
uv sync --extra macos
```

## Run it

The tray app is the recommended way to run this day to day — a tray
(Windows) / menu-bar (macOS) icon with a Settings window, no console
needed:

```bash
uv run tray_app.py
```

Click the icon for:
- **Settings...** — add/remove watched apps, edit which of the four checks
  run for a specific app, configure the checks themselves (wording,
  severity, on/off) globally, set the API key, and see local usage stats
  (how many checks ran, how many were flagged, broken down by check).
  Nothing is watched until you add at least one app; the default allowlist
  is empty.
- **Paused** — indefinite toggle.
- **Snooze** — 15/30/60 minutes, auto-resumes.
- **Quit**.

To run it automatically at login instead of launching it by hand each
session:

```bash
uv run install/install.py install      # registers it (Task Scheduler / launchd)
uv run install/install.py uninstall    # removes it
```

### Tuning the checks

Settings' **Configure checks...** lets you edit, per question: whether
it's enabled at all, its severity (error/red vs. warning/yellow), the
popup message, and the exact instructions sent to Jev. "Reset to default"
always available — edits are stored as overrides in `config.toml`, never
by changing the built-in defaults in code.

**Edit selected** (on a watched app) lets you turn off specific checks for
just that app — e.g. disable "unprofessional" for a casual Discord server
without affecting anything else.

### Browsers are website-scoped

When Settings detects Chrome, Firefox, Edge, or Chromium, it asks for
one exact website host (for example `docs.google.com`). A browser is never
watched across every site: while `roblox.glean.com` is active, a
`docs.google.com` rule is inactive. Re-add the same browser to add another
explicit host; its host rules are merged.

On macOS, the guard asks the system for Automation permission to read the
active tab URL from the browser, immediately discarding everything except its
hostname. On Windows, the same hostname is read directly from the address
bar's on-screen text via UI Automation (there's no AppleScript-equivalent
tab API). No browser extension is installed and no browser page content,
title, path, or query string is read for this purpose. If host detection is
unavailable or denied, browser checks stay disabled rather than falling back
to app-wide monitoring.

A headless/console alternative also exists, for scripting or if you'd
rather not have a tray icon:

```bash
uv run manage.py set-key
uv run manage.py add        # interactive: switch to the target app, type a couple of words
uv run manage.py list
uv run manage.py remove "Discord messages"
uv run agent.py              # Ctrl+C to stop
```

Both share the same config file, so mixing them is fine — e.g. `manage.py
add` from a script, then run the tray app day to day.

On macOS, the agent needs Accessibility permission (System Settings →
Privacy & Security → Accessibility) granted to whatever process is running
Python, or it'll silently see nothing — no error, adding an app will just
time out.

A detailed trace always goes to `~/.jev-send-guard/agent.log`; set
`JEV_DEBUG=1` to also mirror it to the console. Local-only usage stats
(never sent anywhere) live at `~/.jev-send-guard/stats.json`, visible from
Settings.

## Tests

```bash
uv sync --extra dev
uv run pytest
```

Covers everything OS-independent: the pre-filter heuristic, idle debounce,
config (allowlist, domain normalization, per-app/global question
overrides), the Jev client's question-merging and scoring logic (network
calls mocked), and `watch_loop.py`'s actual decision logic exercised via
`platform_backends/mock.py` — a fake backend implementing the same
interface as `windows.py`/`macos.py`, so allowlist matching, browser-host
gating, and idle-triggered evaluation are all tested without a live OS
session. Nothing OS-specific (the real accessibility backends, the Tk/AppKit
popups, the tray icon) is covered — those still need a real Windows/macOS
run, same as everything else this project has needed to validate live.

## Project layout

```
core/                        Pure Python, OS-agnostic
  pre_filter.py               Local heuristic: should this draft even be checked?
  jev_client.py                 Calls Jev with four noul questions, fails open on any error
  idle_watcher.py                 Debounce: "you stopped typing, evaluate now"
  watch_loop.py                     The loop itself: shared by agent.py and tray_app.py
  notifier.py                          The popup, anchored to the field's bounding rect
  settings_window.py                     Tkinter Settings window, opened from the tray icon
  api_key.py                                OS credential store via `keyring`
  config.py                                   Allowlist + question overrides (~/.jev-send-guard/config.toml)
  stats.py                                      Local-only usage stats (~/.jev-send-guard/stats.json)
  logging_setup.py                                Shared logging config (console + file)

platform_backends/
  windows.py                  UI Automation backend (validated)
  macos.py                     Accessibility API (AX*) backend (unvalidated — see PLAN.md)
  mock.py                       Fake backend for tests — no real OS/GUI needed
  windows_spike.py               Throwaway script used to validate the Windows approach

install/
  install.py                  Login-item install/uninstall (Task Scheduler / launchd)

tests/                        pytest suite for everything OS-independent

tray_app.py                  Recommended entrypoint: tray icon + Settings window
agent.py                     Headless CLI entrypoint (no tray/pause control)
manage.py                    Console CLI: add/list/remove watched apps, set the API key
```
