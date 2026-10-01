# ST113 — task/skill completion push to Telegram (design)

**Date:** 2026-08-29 · **Status:** RULED 2026-10-01 (all three recommendations accepted, #828) · IMPLEMENTED: v1 skill pushes; long-task pushes are v2 · **SoT:** ST113
**Owner surface:** `.claude/settings.json` (Stop hook) + a renderer under `tools/` +
`utils/telegram.py` (unchanged)

## Goal and success metric

When a skill or long task finishes, the phone gets a PROPER message — not raw stdout. Success:
a `/card` batch or `/ingest-charts` round ends with one readable push; a lint pass ends with
none; and no day sees the channel flooded.

## The mechanism is a hook, and that is settled

A "notify me when done" contract cannot be carried by prose or memory — this repo measured
the split: prose cadence rules ran **0-for-15** while the marker check ran **15-for-15**
(`daily_check.py`'s own comment, ST108). So the trigger is a **`Stop` hook in
`.claude/settings.json`** — the `update-config` skill owns that surface — never a `CLAUDE.md`
sentence. The precondition is CLEARED: ST114 (#716) proved the per-medium renderer pattern,
so new traffic no longer routes through the fold-destroyed format.

## The three open decisions, with recommendations

1. **Which events push — an allowlist file, opt-in per skill.** A gitignored
   `docs/plans/telegram-notify.toml` the hook reads: `skills = ["card", "ingest-charts",
   "brief", ...]`. A `/card` verdict earns a push; a lint pass does not. This mirrors the two
   existing precedents exactly: `TELEGRAM_ALWAYS=1` and `TG_TAIL_LINES` are both opt-in
   per job so the 15-minute signal-watch cannot inherit them. Long-task pushes (trigger 2)
   are **deferred to v2** — measuring elapsed time from inside a Stop hook is the one open
   mechanism question, and shipping the skill-completion half first loses nothing.
2. **One generic renderer, not per-skill.** Skill name · outcome line · one fact per line ·
   short labels, no fixed-width label column · **≤46 columns emitted at the source** (the
   ST114 lesson: the fold is then a no-op) · body through `tg_send`'s existing
   escape-and-cap contract. A skill that already owns a Telegram layout (`card/telegram.py`,
   the daily check) keeps its own; the generic renderer is for everything else. Never pipe
   terminal stdout.
3. **A mode the operator toggles, not always-on.** `/telegram-mode` edits the allowlist file
   (add/remove a skill, or `off`). The file being gitignored keeps it out of CI and in the
   backup's `docs/plans/*` glob automatically.

## Constraints inherited from the delivery path

- `tg_send` folds at 46 columns BEFORE the 3400-byte cap, HTML-escapes, wraps in `<pre>` —
  both legs live there so success and failure paths cannot drift. The renderer emits within
  those bounds rather than special-casing them.
- The hook must be fast and silent when disabled: read the allowlist first, exit 0 in
  milliseconds. A hook that costs a second on every Stop taxes every session for a feature
  most stops don't use.
- Push failures must not fail the session — fire-and-forget with a one-line stderr note.

## Failure modes designed against

- **Flood:** allowlist + one push per skill completion. The 96-messages-a-day shape
  (signal-watch inheriting `TELEGRAM_ALWAYS`) is the named anti-goal.
- **Unreadable push:** any push arriving folded means a >46-column line escaped the
  renderer — that is a renderer bug, fixed at the source, never by widening the fold.
- **Double-push** on session resume/compaction re-firing Stop: open question for
  implementation; a cheap dedup (last-push marker keyed on transcript id) is the candidate.

## Decision Log

- **D1:** measured pushes exceed ~10/day in normal operation → the design has failed its
  anti-flood goal; tighten the allowlist or add the min-duration filter before adding
  anything else.
- **D2:** if the Stop hook cannot see which skill ran (hook payload lacks it), the design
  falls back to skill-side opt-in (each allowlisted SKILL.md's final step calls the push
  tool directly) — same renderer, same allowlist, no hook. Decide at implementation, not by
  re-litigating this doc.
  **Decided at implementation (2026-10-01): hook-side, from the transcript.** The payload
  carries `session_id` / `transcript_path` / `cwd` but no skill name, so `last_turn` reads the
  transcript: `Skill` tool_use `input.skill` plus typed `<command-name>/x</command-name>` rows,
  scoped to the newest turn. The skill-side fallback was not taken because it relies on the
  model remembering a final step, which is the prose route this design rejects.
- **Double-push:** solved with a per-session marker keyed on the turn's opening-row uuid
  (`docs/plans/.telegram-notify-state.json`).

## Implementation sketch (for the executing session, not begun here)

Hook script beside the existing guards in `.claude/hooks/`; renderer + send in one small
`tools/notify_telegram.py` (bootstraps `sys.path`, gains `test_bare_invocation_works` the
moment it imports anything from the repo); width contract pinned by a test the way
`card/telegram.py`'s omissions are. `/telegram-mode` SKILL.md is a thin toggle over the
allowlist file.
