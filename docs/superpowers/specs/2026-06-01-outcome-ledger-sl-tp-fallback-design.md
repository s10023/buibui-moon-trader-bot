# Outcome-Ledger SL/TP Fallback — Design

**Status:** Draft (2026-06-01)
**Owner:** s10023
**Related:** T2 outcome tracking ([[todo-master]]), `analytics/signal/outcome_backfill.py`, `signals/alert_formatter.py`

## Problem

The live outcome ledger (`signal_alert_outcomes`) can only score **11%** of fired
alerts. A read of the table on 2026-06-01 (`tools/live_outcomes_report.py`):

| metric | value |
| --- | --- |
| total rows | 2,407 |
| resolved (win/loss/expired) | 266 |
| **open, `tp_price IS NULL`** | **2,141 (89%)** |
| `has tp_price` | 266 (= resolved, exactly) |

`has_tp == resolved == 266` and `no_tp == open == 2,141` is a 1:1 correspondence:
**a row resolves iff it was written with a `tp_price`.** 89% of alerts persist
`tp_price = NULL` and are therefore *unscoreable forever* — the backfill worker
filters them out (`outcome_backfill.py:116`, `AND tp_price IS NOT NULL`).

Consequences:

- **`bos` — 87% of backtest volume — has 0 of 256 live alerts resolved.** The single
  most important strategy is invisible in live outcome data.
- The 266 "resolved" rows are a **biased slice** (only signals that happened to carry
  a valid structural SL), so the live edge read is not representative.
- Every soft→hard gate flip (F8, T2b bos-regime, T2c bos-suppress, regime) is supposed
  to be decided on this data; right now it is reading 11% of a biased sample.

## Root cause — the ledger is stricter than the alert

Two code paths compute SL/TP from the same `SignalEvent`s, but only one has a fallback.

**Alert formatter — always produces an SL/TP** (`signals/alert_formatter.py:306-309`,
`_widest_sl`):

```python
valid = [e.sl_price for e in events if 0 < e.sl_price < price]
return min(valid) if valid else price * (1 - sl_pct)   # ← pct fallback when no structural SL
```

It then floors by `min_sl_pct` (`_apply_min_sl_floor`) and derives TP from
`sl_dist * tp_r`. So **every alert the user sees has a concrete SL + TP.**

**Outcome-ledger writer — NO fallback** (`analytics/signal/scanner.py:918-940`):

```python
ev_sl = None; ev_tp = None
if direction == "long" and 0 < e.sl_price < entry:   # structural SL only
    sl_dist = max(entry - e.sl_price, entry * min_sl_pct); ev_sl = ...; ev_tp = ...
elif direction == "short" and e.sl_price > entry:
    ...
# else → ev_sl/ev_tp stay None → row persisted with NULL sl_price+tp_price → never scored
```

`SignalEvent.sl_price` defaults to `0.0` (= "use sl_pct fallback", `types.py:66`).
Whenever a detector emits no structural SL — or one on the wrong side of the **live**
fire price `entry = e.price` — the formatter falls back to `entry*(1±sl_pct)` and shows
a target, while the writer writes NULL. The comment at `scanner.py:916-917` documents
this as "by design," but that design defeats the purpose of outcome tracking.

It is also **per-event**, not per-alert: the formatter takes the widest SL across the
whole confluence group, but the writer checks each event's own `sl_price`, so even more
rows NULL out than the alert would suggest.

## Goals

1. **Forward fix:** the writer scores the *same* SL/TP the user saw — when an event's
   structural `sl_price` is invalid, fall back to `entry*(1±eff_sl_pct)` floored by
   `min_sl_pct`, then `ev_tp = entry ± sl_dist * eff_alert_tp_r`. Closes the 89% hole
   for all future alerts; makes `bos` measurable live.
2. **Retroactive migration (follow-up):** reconstruct SL/TP for the existing 2,141 NULL
   rows from stored `entry_price` + per-(strategy,symbol,tf,direction) `eff_sl_pct`/`tp_r`,
   then run the existing forward-walk resolver — recovering weeks of already-fired data
   instead of waiting.

## Non-goals

- No change to the **alert** path or what the user sees — alerts already render correctly.
- No change to detector SL emission — detectors keep emitting `0.0` when they have no
  structural level; the fallback handles it.
- No change to backtest (`backtest_trades`) scoring — this is purely the live ledger.
- No retuning of `sl_pct` / `tp_r` / `min_sl_pct` values — parity only.

## Design

### Forward fix (`scanner.py:918-940`)

Replace the "structural-only, else NULL" branch with "structural-if-valid, else pct
fallback" — mirroring the formatter. The exact fallback pattern **already exists**
locally at `scanner.py:973-986` (the CME rough-TP block), so reuse it:

```python
eff_sl_pct = _resolve_sl_pct(strategy_params, e.strategy, symbol, tf, sl_pct)
if direction == "long":
    structural = e.sl_price if 0 < e.sl_price < entry else entry * (1 - eff_sl_pct)
    sl_dist = max(entry - structural, entry * min_sl_pct)
    ev_sl = entry - sl_dist
    ev_tp = e.tp_price if e.tp_price > entry else entry + sl_dist * eff_alert_tp_r
else:  # short
    structural = e.sl_price if e.sl_price > entry else entry * (1 + eff_sl_pct)
    sl_dist = max(structural - entry, entry * min_sl_pct)
    ev_sl = entry + sl_dist
    ev_tp = e.tp_price if 0 < e.tp_price < entry else entry - sl_dist * eff_alert_tp_r
```

`ev_sl`/`ev_tp` are now always set → `rr_ratio = eff_alert_tp_r` always set → every row
is scoreable. `_resolve_sl_pct` is already imported (`scanner.py:60`).

### Parity caveats (call out in the plan, verify in tests)

- **`eff_sl_pct` per strategy** matches the alert's `_resolve_sl_pct` at `scanner.py:534`.
  Using bare `sl_pct` would diverge for strategies with an `sl_pct` override.
- **`min_sl_pct` source.** The writer runs *outside* the `if backtest_cfg:` guard, so it
  uses the function-param `min_sl_pct`; the formatter (inside the guard) uses
  `backtest_cfg.min_sl_pct`. These are normally the same value, but the plan must verify
  they don't diverge, or the ledger floor won't match the alert floor. (Pre-existing — the
  current structural path at line 926 already uses param `min_sl_pct`.)
- **Per-event vs per-group SL.** The ledger stays per-event (correct grain for
  per-(strategy,tf,direction) stats); it will not be byte-identical to the alert's
  group-widest SL. This is intentional and acceptable.

### Retroactive migration (follow-up, separate task/PR)

One-shot script (`tools/backfill_null_tp_outcomes.py`, read-then-write, opt-in `--apply`):

1. Select `signal_alert_outcomes` rows where `tp_price IS NULL AND outcome IS NULL`.
2. For each, recompute `eff_sl_pct` + `eff_tp_r` from `strategy_params`/coins config keyed
   on the stored `(strategy, symbol, tf, direction)`; derive `ev_sl = entry*(1∓eff_sl_pct)`
   floored, `ev_tp = entry ± sl_dist*eff_tp_r`. (Structural SL is unrecoverable — not
   stored — so all reconstructed rows use the pct fallback. This matches the per-event
   forward fix.)
3. `UPDATE` the row's `sl_price`/`tp_price`/`rr_ratio`, then let the next
   `backfill_outcomes()` cycle resolve them (or call it directly in the script).
4. Dry-run prints counts + a sample; `--apply` writes. Idempotent.

Caveat: rows whose *event* had a valid structural SL (alert showed structural, not pct)
will be reconstructed with the pct fallback, so their retroactive R may differ slightly
from what the alert displayed. Forward rows are exact; retro rows are best-effort.

## Risks

- **Live-signal-path change.** Must be TDD'd against in-memory DuckDB + mocked events;
  **no smoke-run on the real daemon** (prior incident: real Telegram + real DB writes —
  see [[feedback-live-daemon-smoke-test-danger]]).
- **More rows now resolve as `loss`.** The 89% were silently dropped; many are likely
  losers (live win-rate among resolved is already low). Expect the *measured* aggregate
  to fall once the hole closes — that is the ledger becoming honest, not a regression.
- **No golden/regression movement** — `signal_alert_outcomes` is not in the backtest
  golden pipeline, so `make db-update` is not required (verify goldens unmoved).

## Validation

- Unit: an event with `sl_price=0` now yields non-NULL `ev_sl`/`ev_tp` matching
  `entry*(1±eff_sl_pct)` (long & short); structural-SL events unchanged (regression).
- Unit: written `tp_price`/`rr_ratio` are never NULL for a fired event.
- Integration: scan a synthetic frame with a no-structural-SL detector → row resolvable
  by `backfill_outcomes()`.
- Post-merge: re-run `tools/live_outcomes_report.py` after a few daemon cycles → `no_tp`
  share drops toward 0; `bos` begins resolving.
- `make lint-py`, `make typecheck`, `make test` green; regression goldens unmoved.
