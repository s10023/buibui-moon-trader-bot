# Spec — API-assisted `/journal-trade` v2

**Date:** 2026-06-21
**Status:** Approved (design), pending implementation plan
**Topic:** Replace manual trade keying in the `/journal-trade` skill with a Binance-API-backed fetch → pick → pre-fill workflow, and auto-run the signal-replay cross-check.

## Problem

Today `/journal-trade` requires the user to hand-key every trade detail (entry, adds, exit,
fees, realized PnL) before any reasoning is captured. The "Signal replay" cross-check that
recent entries carry (e.g. `2026-06-18-btc-short.md`) is also run by hand via
`buibui signal test`. Both are mechanical reconstructions the system can do from data the
user's own Binance account already holds.

The goal: the user should only supply **judgement** — thesis, soft-stop invalidation, intent,
tags, retrospective — while the mechanical facts (fills, $ PnL, fees, funding, exchange SL/TP,
and the detected-signal context) are fetched and pre-filled.

### Premise correction

A premise in the original idea ("Binance API doesn't return SL") is only half true.
`monitor/position_lib.py::get_stop_loss_for_symbol` already reads exchange-placed
`STOP_MARKET`/`TAKE_PROFIT` orders via `futures_get_open_orders`. So:

- **Exchange-placed** SL/TP **are** retrievable → auto-fill them.
- **Mental/soft** stops are **not** retrievable → stay a manual blank.

This matters because the journal's R-scoring convention scores `r_realized` against the
**soft** stop (the real invalidation), so the soft stop remains a human input regardless.

## Success criteria

- Running `/journal-trade` with no pasted details surfaces a pickable list of recent trades
  reconstructed from the Binance account (closed round-trips + currently-open positions).
- Picking one or more produces per-symbol journal file(s) with the mechanical frontmatter
  fields pre-filled and the human fields left blank.
- The "Signal replay" section is drafted automatically from `buibui signal test`.
- The tool is read-only (never places/cancels orders), local-only, and has no network calls
  in tests.
- DoD gates green: `make lint-py`, `make typecheck`, `make test`, `make lint-md`. No
  regression/golden movement (the tool is isolated; it does not touch the backtest pipeline).

## Architecture & data flow

```text
/journal-trade  ──▶  tools/journal_fetch.py  ──▶  JSON list of "trade candidates"
   (skill)            (read-only, local, create_client())
       │
       ├─ present numbered, pickable list (closed round-trips + open positions)
       │
   user picks #2, #3
       │
       ├─ per pick: pre-fill frontmatter from fetched data
       ├─ per leg: auto-run `buibui signal test --symbol <S> --at <leg_ts>` → Signal replay
       └─ Write per-symbol file(s), cross-linked, leave human blanks
```

Two artifacts change:

1. **New** `tools/journal_fetch.py` — read-only reconstructor.
2. **Rewritten** `.claude/skills/journal-trade/SKILL.md` — fetch → pick → pre-fill workflow.

No schema change, no Binance writes, no `analytics.db` change (signal replay reuses the
existing `buibui signal test` path).

## Component 1 — `tools/journal_fetch.py`

A `tools/`-pattern one-shot script (sibling of `tools/live_outcomes_report.py`): read-only,
local, uses the existing authenticated `utils.binance_client.create_client()`.

### Invocation

```bash
poetry run python tools/journal_fetch.py [--days 7] [--symbol BTCUSDT ...] \
    [--json] [--include-journaled]
```

- `--days N` — lookback window for closed round-trips (default 7). Open positions are always
  shown regardless of age.
- `--symbol …` — narrow to specific symbols; default = the `coins.json` traded set ∪ any
  symbol with a currently-open position. (Binance `futures_account_trades` is **per-symbol**,
  so a symbol set is required; `coins.json` is the practical traded universe.)
- `--json` — emit machine-parseable JSON to stdout (the skill always passes this). Default is
  a compact human table.
- `--include-journaled` — include trades already matched to an existing journal file (default
  hides them, but still flags them in JSON via `already_journaled`).

### Data sources (all read-only)

| Source | Binance call | Used for |
| ------ | ------------ | -------- |
| Fills | `futures_account_trades(symbol=…)` | entries / adds / exits, price, qty, realizedPnl, commission, time |
| Open positions | `futures_position_information()` | currently-open trades, entryPrice, markPrice |
| Orders (incl. stops) | `futures_get_all_orders` / `futures_get_open_orders` | exchange SL/TP levels |
| Funding / fees | `futures_income_history()` | funding accrual + commission totals over the hold |

### Grouping algorithm (the core, unit-tested)

Per `(symbol, positionSide)` — keyed by `positionSide` so hedge-mode LONG/SHORT on the same
symbol stay independent (mirrors `position_lib._fetch_all_tpsl_prices`):

1. Walk fills time-ascending, tracking signed net qty.
2. `0 → nonzero` opens a trade; that fill's role = `entry`.
3. Same-side increase while open → role `add`.
4. Opposing fill that reduces toward 0 → role `partial_exit`, or `exit` when it reaches 0.
5. Net returns to `0` → close the trade and emit it.
6. A flip through zero (net crosses 0 in one fill) → close the current trade, open a new one
   with the remainder.
7. Any symbol with nonzero net qty after all fills → `status: open` (merge with
   `futures_position_information` for entry/mark price and pull exchange SL/TP).

### Output schema (per candidate)

```jsonc
{
  "index": 1,
  "symbol": "BTCUSDT",
  "direction": "short",              // from positionSide / net-qty sign
  "status": "closed",                // "closed" | "open"
  "position_side": "SHORT",          // SHORT | LONG | BOTH (one-way)
  "opened_ts_utc": "2026-06-18T00:57:00Z",
  "closed_ts_utc": "2026-06-18T11:28:00Z",  // null when open
  "legs": [
    { "ts_utc": "...", "side": "SELL", "price": 64448, "qty": 0.01,
      "role": "entry", "realized_pnl": 0.0, "commission": 0.26 }
    // ... adds / partial_exit / exit
  ],
  "avg_entry": 64161.5,
  "avg_exit": 64034,                 // null when open
  "qty_total": 0.02,
  "realized_pnl_usd": 2.41,          // Σ realizedPnl
  "fees_usd": 1.04,                  // Σ commission
  "funding_usd": -0.18,              // from income_history over [open, close]
  "exchange_sl": 65737,              // null if no exchange stop order
  "exchange_tp": null,
  "already_journaled": false,        // matched against existing journal frontmatter
  "suggested_filename": "2026-06-18-btc-short.md"
}
```

`already_journaled` is computed by reading existing `docs/plans/journal/*.md` frontmatter and
matching on symbol + direction + entry date so the user does not double-log.

All timestamps emitted in **UTC** (Binance epoch ms → UTC ISO). The skill converts to MYT
(UTC+8) for the MYT frontmatter fields.

## Component 2 — rewritten `SKILL.md`

New default flow when invoked without pasted details:

1. **Fetch** — run `poetry run python tools/journal_fetch.py --json` (optionally `--days N`),
   parse the JSON.
2. **Present** a numbered list: `#`, symbol, direction, status, avg entry → avg exit, $ PnL,
   and an "already journaled" marker.
3. **Pick** — user names one or more (`journal #2 and #3`). Several picks are drafted in one
   pass and cross-linked (`[[2026-06-18-eth-short]]`), keeping the per-symbol filename
   convention.
4. **Pre-fill** the frontmatter from the candidate: entry/adds/exit, avg entry/exit,
   `r_realized` left blank pending soft stop (see below), fees/funding noted in Outcome,
   exchange SL/TP into `sl_*` when present.
5. **Signal replay** — auto-run `buibui signal test --symbol <SYM> --at <leg_ts>` once per leg
   (entry + each add), **raw detector** (no cooldown / min_avg_r / combo / F8 live gates —
   matching how existing entries were built). Draft `strategies_seen` + the Signal replay
   section (detected-vs-missed, direction alignment). Prose stays human-editable.
6. **Leave human blanks** — thesis, intent, soft-stop invalidation, `thesis_tags`,
   retrospective, `self_rating`.

The legacy paste-the-details path is retained for trades not on Binance (manual/other venue)
and for filling an outcome into an existing entry.

## SL & R handling

- **Auto-filled:** entries/adds/exit, $ PnL, fees, funding, **exchange-placed SL/TP**.
- **Manual blank:** the soft/mental invalidation — the API cannot know it, and the scoring
  convention scores against it.
- The tool reports `$ PnL`, PnL as `%` of notional, and (when an exchange stop existed) an
  R-against-exchange-SL. The canonical soft-stop `r_realized` is computed once the user
  supplies the invalidation, preserving the existing soft-stop scoring convention.

## Testing

Unit tests for the grouping function with a `MagicMock` client (no network, per repo
convention — `futures_account_trades` / `futures_position_information` /
`futures_get_open_orders` / `futures_income_history` all mocked):

- entry + add + full close (single round-trip)
- partial exits before full close
- hedge-mode: independent LONG and SHORT on one symbol
- flip through zero (long → short in one closing-and-reversing fill set)
- open position (nonzero net at end → `status: open`, merged with position info)
- `already_journaled` matching against a fixture journal file

## Out of scope (YAGNI)

- No order placement or cancellation — strictly read-only.
- No combined multi-symbol "session" file — per-symbol files stay the unit.
- No auto-computed soft-stop R — requires the user's invalidation.
- No new DB tables / no schema change / no `analytics.db` writes.
- No auto-filled thesis / tags / retrospective — judgement stays human.
- No new `buibui` subcommand — journaling is a personal one-shot, so it lives in `tools/`.

## Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ (mypy strict, full annotations) · `make test` ✓
  (new grouping tests green) · `make lint-md` ✓ (skill markdown).
- `make test-regression` goldens unmoved (tool is isolated from the backtest pipeline).
- `SKILL.md` description/table updated; `CLAUDE.md` `tools/` list gains `journal_fetch.py`;
  memory `project_trade_journal_procedure.md` note updated to describe the API-assisted flow.
