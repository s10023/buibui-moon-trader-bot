# Exit Improvement (the 40%-expiry leak) — spec

**Date:** 2026-06-05 · **Status:** design / discussion (no code yet) · **Parent:** `docs/redesign/2026-06-05-top-tier-quant-redesign.md` (L4/L9) · **Sibling of:** `docs/redesign/2026-06-05-p1-sizing-spec.md` (jointly evaluated — see §5).

## 0. Goal

~40% of live alerts **expire** (hit neither SL nor TP within `max_hold_bars`; resolved at mark-to-market R at the last bar). The exit logic is a **flat fixed-SL / fixed-TP / time-expiry policy everywhere** — verified 2026-06-05 in both `analytics/backtest/engine.py:1043-1051` and `analytics/signal/outcome_backfill.py::_scan_forward`. No trailing, no breakeven, no partials, no adaptive hold. That makes the **exit side the single largest *untouched* realized-R lever** in the system: a huge fraction of trades are decided by an arbitrary time cutoff and a fixed cap, not by the market.

This task introduces a **pluggable exit-policy layer**, validates candidate policies by **re-simulating exits over OHLCV on the real alert ledger** (same entries, only the exit varies → OOS-honest), and assigns an exit policy **per edge-type** (location vs momentum — they want opposite exits). Near-term output is **alert exit-guidance + recalibrated tp_r/hold**; full automated exit management is P5 (execution).

## 1. Framing — exits are edge-specific (and this is mostly research, not live automation yet)

- **Location / value book** (your winning core: liquidity_sweep, eqh_eql, fib_golden_zone — mean-reversion-flavored) wants **fast, tight** exits: the reversion edge decays quickly, so take profit near the level, cut a short time-stop. Letting these run *hurts*.
- **Momentum / trend book** (ema, trend_day now; EWMAC in P2) wants **let-it-run** exits: wide/trailing stop, raised or removed cap, longer hold — capture the fat tail. Capping these at a fixed tp_r is the classic momentum mistake.

A single global exit policy will help one book and hurt the other. So the deliverable is a **policy library + a per-edge (later per-cell) assignment**, validated by data — never a global assumption. The live bot still only *alerts* today, so the immediate use is **exit guidance in the alert** (move to BE at +1R, trail 1.5×ATR, scale 50% at 1R) plus **recalibrated tp_r/max_hold**; automated management lands in P5.

## 2. Diagnostic first — MFE/MAE study (is this an exit problem or an entry problem?)

**Do not sweep exit policies before this step.** For every alert, walk its window `[candle_ts_ms → candle_ts_ms + max_hold_bars]` over OHLCV and record, in R units (÷ `|entry − sl|`):

- **MFE_R** — max favorable excursion (best unrealized R reached).
- **MAE_R** — max adverse excursion (worst unrealized R reached).

Aggregate per cohort (win / loss / **expired**) × cell (strategy × tf × direction). The distribution **decides whether exits can even help**:

| Pattern | Interpretation | Action |
| --- | --- | --- |
| Expired cohort **often reaches ≥1R** but tp_r set higher | TP is unreachably far | Lower tp_r / add partial at 1R → **exit fix, big win** |
| Expired cohort **rarely reaches +0.5R** | Weak entries, not an exit problem | **Don't paper over with exits** — prune/down-weight (signal issue); maybe shorten time-stop to free capital |
| Loss cohort with **high MFE before SL** | Went +1R then reversed to stop | Breakeven / trail converts to scratch/small win → **exit fix** |
| Loss cohort with **low MFE, fast MAE** | Just wrong | Exit can't help; entry/SL issue |

This MFE/MAE table is the **go/no-go gate and the parameter oracle** (it tells you the *reachable* trail/TP levels) for the whole task. It also cleanly separates exit-fixable trades from entry-quality problems — preventing us from masking bad signals with exit tuning.

## 3. Exit-policy library (candidates)

All share the **same entry + same `sl_price`** the alert was issued with (apples-to-apples). Policy #0 is today's baseline.

| # | Policy | Params | Best for |
| --- | --- | --- | --- |
| 0 | **Fixed** (baseline) | `tp_r`, `max_hold_bars` | — |
| 1 | **Time-stop sweep** | `max_hold_bars` per cell | both (frees capital) |
| 2 | **Breakeven** | move SL→entry after `+b`R (b∈{0.5,1}) | both |
| 3 | **R-multiple trail** | arm at `+a`R, trail by `d`R | momentum |
| 4 | **ATR trail** | arm, then trail `k×ATR14` | momentum |
| 5 | **Structure trail** | trail behind last swing (reuse `zones_lib`) | momentum |
| 6 | **Partial scale-out** | take `f₁` at `r₁`R; runner → trail or `r₂`R | location (lock) + momentum (runner) |
| 7 | **Cap removal** | no fixed TP; exit on trail/trend-break only | momentum (P2) |

v1 scope recommendation: **#1 time-stop, #2 breakeven, one of #3/#4 trail, #6 partial.** Defer #5 structure-trail and #7 cap-removal to the momentum book (P2) — they have the most DOF and the least current data.

## 4. Exit-replay engine

Generalize `_scan_forward` into a **pluggable policy evaluator** — `replay_exits(alert, forward_bars, policy) -> (outcome, R, exit_ts)`:

- Walks bars from `entry_idx`; at each bar checks (in conservative order): SL/trail-stop hit → TP/partial level hit → arm/advance trail → time expiry.
- **Conservative intrabar convention (critical anti-bias):** keep `_scan_forward`'s adverse-first tie rule — on any same-candle ambiguity (did the trail-stop or the next target trigger first?), assume the **adverse** event. No assuming you captured the trail before the wick that would have stopped you.
- **No look-ahead in trailing:** a trail level effective at bar *i* may use only information through bar *i−1* close / bar *i* extremes under the conservative rule.
- **Partials** accumulate a position-weighted R across legs (Σ legᵢ_fraction × legᵢ_R).
- Returns a **new R *and* a new `exit_ts`** per alert — both feed P1 (§5).

## 5. Evaluation — judge on risk-adjusted, jointly with sizing

Raw avg_r is **not** the metric: exit policies trade mean for variance (a trail can lower avg_r but cut drawdown). And exits change the **holding interval** (`exit_ts`) → change concurrency → change how many positions the P1 caps admit → change portfolio vol. **Exits and sizing must be evaluated jointly.**

Method:

1. Run candidate policy through the exit-replay (§4) → `(new_R, new_exit_ts)` per alert.
2. Feed that into the **P1 paper book** → portfolio equity curve.
3. **Headline metric = portfolio Sharpe / max-DD via P1**, not per-trade avg_r.
4. Guards (same as everywhere): **OOS split**, **n-floor (≥30/cell)**, and **P0 multiple-testing correction (deflated Sharpe / PBO)** — exits have many DOF, so this is mandatory, not optional.
5. **A/B vs policy #0** on identical entries; emit per-cell **ENABLE / HOLD / INSUFFICIENT** verdicts (mirrors `gate_audit.py` / `adr_threshold_audit.py`).
6. Secondary reporting: expiry-rate reduction, win-rate shift, MFE-capture ratio, avg hold-time (shorter holds → more concurrency capacity in P1).

## 6. Edge-type policy assignment

Start coarse to limit DOF: assign one default policy **per book/edge-type**, refine to per-cell only where n supports.

- **Location/value book** → default **#6 partial (lock 50% at ~1R) + #2 breakeven + short time-stop (#1)**. Fast realization of a decaying edge.
- **Momentum/trend book** → default **#3/#4 trail + raised or removed cap (#7) + longer hold**. Capture the tail; this is also where disciplined forecast-driven pyramiding (P2) adds size.

This pre-builds the exit library the momentum book (P2) will need, while improving the current strategies now.

## 7. Module structure (matches the audit-tool pattern)

```text
analytics/exits/
  __init__.py
  policies.py   ExitPolicy protocol + Fixed/Breakeven/RTrail/AtrTrail/StructureTrail/Partial/CapRemoval; ExitPolicyConfig
  replay.py     replay_exits(alert, forward_bars, policy) -> (outcome, R, exit_ts)   # generalizes _scan_forward
  mfe_mae.py    per-trade MFE_R/MAE_R + cohort aggregation (the §2 diagnostic)
tools/exit_audit.py   diagnose (MFE/MAE) + sweep policies×params per cell, OOS + n-floor + deflated-Sharpe,
                      feed P1 book, emit ENABLE/HOLD verdicts.  PYTHONPATH=. poetry run python tools/exit_audit.py ...
tests/  test_exit_policies.py · test_exit_replay.py · test_mfe_mae.py
```

Tool-first (like `gate_audit.py`, `adr_threshold_audit.py`) is the cheapest OOS-honest path. Only **after** a clear OOS winner do we **graduate it into `engine.py` + `_scan_forward` + the alert** (see §8) — mirroring how live-parity gates were ported.

## 8. Live integration path

1. **Alert exit-guidance (near-term, read-only):** add an "Exit plan" line to the Telegram alert (`alert_formatter`) driven by the per-cell winning policy — e.g. *"BE at +1R · trail 1.5×ATR · take 50% at 1R."* Human still executes.
2. **Backtest parity (behavioral change):** port the winning policy into `engine.py`'s exit loop **and** `_scan_forward`, so `backtest_runs` / recalibrate / star ratings reflect the *real* exit instead of fixed-TP. This **moves regression goldens** → requires `make db-update`. Keep engine and `_scan_forward` in sync (existing live-parity discipline).
3. **Automated exit management (P5):** the execution layer applies the trail/partial/BE in live orders. Gated on the L8 risk overlay.

## 9. Risks / anti-goals

- **Overfitting (the big one):** exits have huge DOF. Prefer simple robust policies (BE, single trail); enforce P0 multiple-testing + OOS + n-floor; reject marginal winners.
- **Don't fix entry problems with exits:** if the MFE diagnostic shows a cell's expired trades never reach +0.5R, that's a *signal* problem — prune/down-weight it, don't tune its exit.
- **Variance illusion:** never promote on avg_r alone; judge risk-adjusted via the P1 book.
- **Intrabar optimism:** conservative adverse-first convention, no look-ahead in trail levels.
- **One-size-fits-none:** a global exit helps one book and hurts the other — assign per edge-type.
- **Parity drift:** once ported, engine ↔ `_scan_forward` ↔ alert must stay consistent.

## 10. Open decisions

1. **Tool-first vs engine-first** — recommend tool-first (`tools/exit_audit.py`), port the winner after.
2. **Metric** — pure Sharpe-via-P1, or a blend (avg_r + max-DD)? Recommend Sharpe-via-P1 headline, avg_r/expiry/win-rate as secondary.
3. **v1 policy scope** — #1/#2/#3-or-#4/#6 (defer #5/#7 to P2)? Recommend yes.
4. **Granularity** — per-edge-type first, per-cell only where n≥30? Recommend yes (DOF control).
5. **Port trigger** — gate engine/alert changes on a clear OOS winner (triggers `db-update` + goldens).
6. **Diagnostic-only first PR?** — ship §2 MFE/MAE alone first; it may show some cells are entry-problems (route to prune) before any exit code is written.
