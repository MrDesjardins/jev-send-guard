# Jev Send Guard

A small background agent that watches whatever text field currently has OS
focus — but only in an explicit allowlist of apps you configure — and,
after you stop typing for about a second, runs two fast `noul` questions
against [TypeSafe AI's Jev model](https://docs.typesafe.ai) on the current
draft: "does this read as curt," "is there a clear ask." A small popup
appears anchored right next to the field: red with a warning if something
looks off, green with a checkmark if it's clean. It never blocks anything —
there's no send gesture to hook into by design. See `PLAN.md` for the full
design rationale, including the v1 Chrome-extension approach this replaced
and why.

## Status

- **Windows**: built and validated end-to-end against real apps (Discord
  desktop, Firefox/Gmail). See `PLAN.md`'s "Milestone 1" for what was
  tested and the real bugs hit and fixed along the way (latency, popup
  positioning, startup speed).
- **macOS**: built to the same interface as Windows (`platform_backends/
  macos.py`) but not yet run against a live session — see `PLAN.md`'s
  "Milestone 1b" for what specifically needs validating.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
# Windows
uv sync --extra windows

# macOS
uv sync --extra macos
```

Store your TypeSafe API key in the OS credential store (or set
`TYPESAFE_API_KEY` in the environment for local testing instead):

```bash
uv run manage.py set-key
```

Add at least one app to watch — this is interactive: it asks you to switch
to the target app and type a couple of words there, detects which process
that was, then asks for a short label. Nothing is watched until you do
this; the default allowlist is empty.

```bash
uv run manage.py add
uv run manage.py list
uv run manage.py remove "Discord messages"
```

On macOS, the agent needs Accessibility permission (System Settings →
Privacy & Security → Accessibility) granted to whatever process is running
Python, or it'll silently see nothing — no error, `add` will just time out.

## Run it

```bash
uv run agent.py
```

Ctrl+C to stop. A detailed trace always goes to
`~/.jev-send-guard/agent.log`; set `JEV_DEBUG=1` to also mirror it to the
console.

## Project layout

```
core/                        Pure Python, OS-agnostic
  pre_filter.py               Local heuristic: should this draft even be checked?
  jev_client.py                 Calls Jev with two noul questions, fails open on any error
  idle_watcher.py                 Debounce: "you stopped typing, evaluate now"
  notifier.py                       The popup, anchored to the field's bounding rect
  api_key.py                          OS credential store via `keyring`
  config.py                             The watched-app allowlist (~/.jev-send-guard/config.toml)

platform_backends/
  windows.py                  UI Automation backend (validated)
  macos.py                     Accessibility API (AX*) backend (unvalidated — see PLAN.md)
  windows_spike.py               Throwaway script used to validate the Windows approach

agent.py                     Entrypoint: the watch loop
manage.py                    CLI: add/list/remove watched apps, set the API key
```
