# Jev Send Guard — Plan

## What this is

A browser extension that watches whatever text field currently has focus
(Gmail compose, initially) and, right before you send, runs one or two fast
`noul` questions against TypeSafe AI's Jev model — "does this read as
curt/aggressive," "is there a clear, explicit ask" — and shows a small,
dismissible nudge if something looks off. It never hard-blocks sending.

## Why this, and why Jev specifically

Most everyday computer actions (typing, saving, clicking) don't benefit
from an AI check that takes a couple of seconds — the wait is worse than
the value. Sending a message is one of the few moments people already
pause on for a half-second anyway, and Jev's `noul`/`choice`/`score`
primitives are built to answer a single structured question fast, not
generate prose. The test for whether this idea is worth building at all:
**it must be worse with a normal 2-3s LLM call than with nothing.** If the
check ever feels like "waiting on AI," the product has failed regardless
of how accurate it is.

## Non-goals for v1

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

## Scope decisions for v1 (from the brainstorm this plan is based on)

1. **Surface: Gmail compose only.** Not Slack, not Google Docs comments,
   not a desktop app. One well-understood DOM to integrate against, one
   set of send-gesture semantics (Ctrl+Enter, or clicking Send). Slack
   and Docs are architecturally the same pattern (a content script per
   site) but each needs its own selectors/send-gesture handling — treat
   them as v2+, not part of getting the core loop right.
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

## Architecture sketch

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

## Open questions to resolve while building (not blockers to starting)

- Exact wording/threshold for "does this cross the line" — starts as a
  guess, needs real usage to tune. Expect to adjust the `noul` threshold
  (e.g. 0.7+) after a week of actual use, not before.
- Gmail's compose DOM is not officially documented and changes
  periodically — expect the selector logic to need occasional
  maintenance. Not solvable up front, just something to accept.
- Whether the pre-filter heuristic should be configurable (a simple
  settings toggle) or fixed for v1 — leaning fixed, revisit once it's
  actually being used.

## Rough milestones

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

## Context for whoever (or whatever) picks this up next

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
