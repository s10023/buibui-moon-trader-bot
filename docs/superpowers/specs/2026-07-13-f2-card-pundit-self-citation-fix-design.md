# F2 fix — pundit board must not cite the AI card as an external pundit

**Date:** 2026-07-13
**Status:** approved (approach B)
**Scope:** one-file behavioural fix in `analytics/brief/pundit.py` + tests + one doc line.

## Problem

TRADE cards dual-write a `{source: "ai-card", author: "buibui_card", …}` row to
`docs/plans/pundit-calls.jsonl` so `tools/pundit_score.py` can score the AI
(the intended kill-test). But `analytics/brief/pundit.py::build_board` reads the
same file and includes **every** author, so the next `buibui card` run — and the
standalone `buibui brief` — see the card's own prior rows as external pundit
confluence.

Confirmed live in the 2026-07-13 F2 smoke: the card listed "four short pundit
calls", one of which was `buibui_card` (its own previous call), inflating the
confluence count. It did not flip direction here, but the contamination worsens
as the ledger grows.

All of `docs/plans/` is gitignored, so this is purely a read-side logic issue —
there is no git-hygiene or file-provenance dimension.

## Decision (approach B — board-level exclusion)

Concept: **the pundit board shows external pundits; the AI card is never an
external pundit to itself.** Keep the single `pundit-calls.jsonl` file (the
scorer wants the AI rows scored alongside humans), and exclude the AI at the
board only. Rejected approach A (physically split into `ai-card-calls.jsonl` +
teach the scorer to read both + migrate existing rows): larger diff, buys
nothing now that both files are gitignored, and would still need the same
board-side priors exclusion for completeness.

No config knob: there is exactly one AI author and it is a structural fact, not
a tunable (YAGNI). If a second machine author ever appears, generalise then.

## Change

`analytics/brief/pundit.py`:

- Module constants `_AI_CARD_SOURCE = "ai-card"`, `_AI_CARD_AUTHOR = "buibui_card"`.
- In `build_board`'s ledger loop, after the line is counted into `ledger_total`
  and confirmed to be a dict, `continue` when `raw.get("source") == _AI_CARD_SOURCE`.
  This is an **exclusion** (like the existing future-dated / too-old case), not a
  malformed **skip**: `ledger_total` still counts the line, `ledger_skipped` does
  not increment. Keying off the stable `source` machine field (not the author
  name) makes it robust and automatically covers the rows already in the file.
- Build the priors-derived `authors` output list from `authors_by_name` values
  excluding `_AI_CARD_AUTHOR`, so the AI's own scored track record never shows on
  the board. (`authors_by_name` itself is left intact for the per-call `prior`
  attachment on external authors — a no-op for the now-excluded AI rows.)

Nothing in `card/`, the scorer, or config changes. No data migration.

## Consumers unaffected

- `tools/pundit_score.py` reads the ledger directly (not via `build_board`) and
  keeps scoring `buibui_card` — the kill-test is preserved.
- `buibui brief` shares `build_board`, so it too stops citing the AI card — the
  desired behaviour (the brief is an external-pundit view).

## Tests (TDD)

1. Ledger with mixed human + `source:"ai-card"` rows → `recent_calls` contains
   only the human rows; `ledger_total` counts all lines; `ledger_skipped` is
   unchanged by the AI rows.
2. Priors JSON containing a `buibui_card` author → board `authors` omits it while
   retaining human authors.
3. Existing pundit-board tests stay green (no external-author regression).

## DoD

`make lint-py` · `make typecheck` · `make test` green ·
`make test-regression` goldens unmoved (pure read-side, no golden surface).
