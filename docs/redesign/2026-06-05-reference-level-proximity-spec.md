# Reference-level proximity audit — scope

**Date:** 2026-06-05
**Status:** scoped, not built
**Memory:** `project_reference_level_triggers.md` ([[reference-level-triggers]])
**Origin:** 2026-06-05 BTC PDL-sweep long (journal `2026-06-05-btc-long.md`) won, but `liquidity_sweep`
did not fire (needs fib-1.27 overshoot → misses shallow named-level sweep+reclaim). User thesis: "do
business only at important levels."

## Question

Does **proximity to a calendar/structural reference level at entry** carry edge (avg_r lift)? Measure on
existing ground-truth data **before** building any `reference_level` detector. This is framing #2 (modulator)
from the memory note — the cheap measurement that gates framing #1 (a new trigger).

## Why measure first, not build

- Prior evidence is supportive but not specific: the conditional-edge test ([[conditional-edge-test]]) found
  **location is the only OOS-robust axis** (liquidity-locations win, imbalance loses). Reference levels ARE
  liquidity locations — but "PDL within 0.5 ATR" has never been isolated and scored.
- Building a detector first risks the candlestick trap: in-sample positive → OOS decay. Tag-and-split is
  ~half a day, read-only, and answers go/no-go.

## Reference-level set (per symbol, look-ahead-safe)

Anchored to each entry's UTC `open_time`; every level uses only **completed prior periods**.

| Level | Definition | Source |
| --- | --- | --- |
| PDL / PDH | previous UTC day low / high | 1d OHLCV, day before entry |
| PWL / PWH | previous ISO-week low / high | weekly resample |
| PML / PMH | previous calendar-month low / high | monthly resample |
| DO / WO / MO | current day / week / month **open** | period open at/before entry |

v2 (optional): prior swing pivots from `zones_lib.py`, session H/L, round numbers.

## Metric

- For each entry `(symbol, tf, entry_price, entry_ts, direction)`: **min distance to nearest level**,
  normalized by **ATR(14) at entry** (scale-free; primary) and raw `%` (secondary).
- Proximity buckets: `≤0.25 ATR`, `≤0.5`, `≤1.0`, `>1.0` (the far cohort = control).
- **Directional sharpening (v1.5, key):** distance alone conflates *support bounce* vs *resistance
  rejection*. Add a **sweep flag** = did price wick **beyond** the level within the last N candles and
  reclaim (long) / reject (short)? The journal trade is "near PDL **from below**, swept + reclaimed" —
  that is the cell we actually want to isolate, not generic nearness.

## Data sources

1. **`signal_alert_outcomes`** (live ledger, OOS — the de-biased truth). Per-row realized R derived from
   `outcome` + `rr_ratio` (win = +rr, loss = −1), consistent with the `live_outcomes` roll-up. **Primary
   verdict source.** Use only scoreable rows (post-TP-hole-fix; see [[outcome-ledger-tp-hole]]).
2. **`backtest_trades`** (in-sample, large n). Corroborating cross-check only. **IS-positive + OOS-negative
   = decay = do not build.**

## Split + verdict

Table per `(level_type × proximity_bucket × direction [× sweep_flag])`: `n, avg_r_live, avg_r_IS, win%`.

- **Decision bar (mirror `gate_audit.py` / conditional-edge):** near-level cohort beats far cohort by
  **≥ +0.05R at n ≥ 30 on the LIVE ledger** → justifies building. Below bar / IS-only = INSUFFICIENT/DECAY.
- Verdicts: **ENABLE / DISABLE / INSUFFICIENT** per cell.

## Rigor (where P0a guards plug in)

- **Look-ahead:** levels strictly from completed prior periods; ATR from candles ≤ entry.
- **Multiple testing:** ~4 level-families × 4 buckets × 2 directions × 2 sweep-states = many cells →
  exactly the overfit surface for spurious positives. **Run the avg_r lifts through P0a-1's deflated-Sharpe
  / PBO / Harvey-Liu haircut** (`analytics/research_guards/`) once it lands — this audit is the guards'
  first real consumer. That is the argument for sequencing this **after** P0a-1.
- **Costs:** `r_realized` excludes funding + slippage (P0b) — same caveat as every other audit.

## Implementation

- Read-only `tools/reference_level_proximity_audit.py` (pattern: `tools/strategy_edge_audit.py`,
  `tools/gate_audit.py`). No DB writes. `PYTHONPATH=. poetry run python tools/reference_level_proximity_audit.py [--days N] [--min-n 30] [--source live|backtest|both]`.
- Reads `analytics.db`: outcomes/trades + OHLCV (level computation + ATR). Pure pandas/duckdb.
- Output: per-cell verdict table + headline ENABLE/DISABLE/INSUFFICIENT, written to `docs/audits/`.

## Effort / priority

- ~half a day, read-only, **goldens unmoved**.
- **Not the next task.** Peer of the Group C combo test (interleave). Sequence **after P0a-1** so the
  multi-bucket test goes through the deflated-Sharpe/PBO guard. Feeds the `g_location` modulator in
  [[top-tier-redesign]] (P2+); do not let it pull the alpha pivot forward.
