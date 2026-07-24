# H8 — M1 indicator-state conditioning audit (design)

**Date:** 2026-07-24
**Status:** design approved (operator, 2026-07-24; "proceed, can defer changes")
**Kind:** read-only research audit — new pure library + driver, no engine/schema/golden change
**Owner of execution:** deferred to a sonnet subagent (this doc + its plan are the brief)

## 1. Goal & success metric

**Goal (one line):** Does conditioning on any of the M1 *market-indicator states*
(computed features that did **not** exist when the 2026-06-04 "conditional edge =
NO" verdict was reached) separate winning trades from losing ones in
`backtest_trades` with an edge that survives the de-biased gate — or does
conditional-edge = NO still hold on these fresh axes?

**Success metric:** a pre-committed per-(axis-state × direction) verdict table —
**BUILD** / **AVOID** / **NO-EDGE** / **INSUFFICIENT** — gated on
`audit_guard` (block-bootstrap CI clearing the ±0.05R economic bar **and**
Holm-adjusted p < α over the pre-registered family) **plus** a two-sample
with-vs-without lift CI excluding 0, **plus** n ≥ MinTRL and DSR/PBO over the
axis family. **A NO is a valid, publishable result — demote, don't hide.**

This is NOT a fishing expedition: the axis list in §4 is pre-registered here so
the Holm family is fixed before any split is looked at.

## 2. The historical defect this re-tests (name it in the dispatch)

The 2026-06-04 conditional-edge test (`project_conditional_edge_test`) found that
in-sample positive conditional splits **decay out-of-sample**; the only
OOS-robust axis was DIRECTION (short). H8's ONLY justification for revisiting is
that the M1 brief-indicator states (EMA stack/slope, Bollinger squeeze/%B,
anchored-VWAP distance, volume-profile value-area position, price-action
character, Monday-range, regime) were **not computed features** at that time.
They are unexplored, not exhausted. If they too decay, that is the expected
result and it closes the axis for good.

**Two guardrails carried from ST9/H10 (do not repeat):**

- **`audit_guard`'s decisions are sign-inverted** relative to the mean:
  `DISABLE ⇔ reliably positive`, `ENABLE ⇔ reliably negative`. The harness MUST
  map `DISABLE → BUILD` and `ENABLE → AVOID`. The intuitive map inverts every
  verdict — it bit ST9. This is load-bearing.
- **Prove each guard by mutation.** Any test asserting "the look-ahead
  perturbation goes red" must actually go red when the strictly-before-entry
  slice is removed. A test that cannot fail for its stated reason is a defect.

## 3. Substrate & population

- **Primary + gate:** `backtest_trades` (broad, de-biased, and H8's definition).
  Rows `WHERE pnl_r IS NOT NULL`, **deduped across saved runs** by sorting on
  `run_id` (stable) then `drop_duplicates` on the trade key
  (`symbol, timeframe, strategy, direction, entry_time`) — reuse
  `warning_value_audit.normalize_backtest` verbatim.
- **Corroboration only:** `signal_alert_outcomes` (live), reported where a cell
  reaches n but never gate-deciding — it is thin and irregularly sampled.
- **Columns available** (`analytics/store/schema.py`): `symbol`, `timeframe`,
  `strategy`, `direction`, `entry_time` (BIGINT ms), `exit_time`, `pnl_r`,
  `run_id`. **`regime` is NOT stored** — it is re-derived per trade (§6), which
  is what makes regime a legitimate as-of-entry axis.
- **Universe:** all symbols present in `backtest_trades` (the saved-run set).
  OHLCV loaded per distinct `(symbol, timeframe)` as in `warning_value_audit`.

## 4. Pre-registered conditioning axes (the Holm family)

Each axis is tested as a **within-direction** split. States come from the M1
`IndicatorState` sub-blocks (`analytics/brief/indicators.py`). Pre-registered:

| Axis | States | Source sub-block |
| --- | --- | --- |
| EMA stack | bullish / bearish / mixed | `EmaState.stack` |
| EMA 200-slope | rising / falling | `EmaState.slope_200` |
| Regime | trend / range / high_vol | `regime_series_1d` (re-derived) |
| Bollinger squeeze | true / false | `BbState.squeeze` |
| Bollinger %B | low (<0.2) / mid / high (>0.8) | `BbState.pct_b` |
| Anchored-VWAP (weekly) | above / below | `VwapState.weekly_dist_atr` sign |
| Anchored-VWAP (monthly) | above / below | `VwapState.monthly_dist_atr` sign |
| Volume-profile value area | above VAH / inside / below VAL | `ProfileState` |
| Price-action character | grind / impulse / chop | `PaState.label` |
| Monday range | above / inside / below | `MondayState.state` |

**Excluded:** candle-pattern hits (`CandleHit`) — those *are* the candle
detectors, already audited via H9 (`warning_value_audit`); conditioning a candle
strategy on its own pattern is circular.

The family size is ~10 axes × ~2–3 states × 2 directions ≈ 40–60 cells —
manageable under Holm when pooled (§5). Bucket `%B` and the two VWAP distances
into signs/coarse bins a-priori to avoid a continuous-cutoff fishing surface.

Note this partly re-tests the "do business at important levels" thesis
(volume-profile value area + anchored-VWAP distance) that ST1
(`reference_level_proximity_audit`) left **underpowered-positive**; H8 approaches
it via indicator-state rather than calendar level.

## 5. Granularity

- **Gate (headline):** **pooled across strategies** — one split per
  (axis-state × direction) over ALL trades. Maximises n and keeps the Holm
  family small. Answers "does this indicator state carry edge on average?".
- **Diagnostic color (non-gating):** per **strategy-TYPE** (the registry's
  `KNOWN_STRATEGY_TYPES`: reversal / continuation / breakout / flow / trend),
  NOT per individual strategy. Catches heterogeneity a pooled mean hides while
  staying powered and interpretable. Never enters the gate.

## 6. Per-trade re-derivation (the causal core)

For each deduped trade at `entry_time = t`:

1. Slice `completed_1d` / `completed_1h` OHLCV to bars whose close is **strictly
   before `t`** (no look-ahead — the bar the signal fired on is the last
   completed bar as-of `t`).
2. `ref_close` = last completed close before `t`; `atr14` via the engine's
   `_compute_atr14` (audit and live cannot disagree on ATR); `regime_series_1d`
   via `analytics/regime.py` over the pre-`t` slice.
3. `state, _notes = build_indicator_state(completed_1d, completed_1h,
   regime_series_1d, ref_close, atr14, as_of_ms=t)`.
4. Extract each axis's state enum from the sub-blocks; a `None` sub-block →
   the trade is `unknown` for that axis and excluded from that axis's split
   only (never dropped globally).

**Look-ahead is the load-bearing invariant.** A perturbation test asserts that a
trade's tagged state is unchanged when a *future* bar (close > t) is mutated, and
CHANGES when the strictly-before-entry guard is removed (mutation-proof, per §2).

## 7. Gate (de-biased, pre-committed)

Two legs, **both must hold**, mirroring `reference_level_proximity_audit`:

- **Absolute leg** via `audit_guard.evaluate_audit_cells(cells, *, bar=0.05,
  alpha=0.05, min_n=30)`: the with-state slice's mean R clears the ±bar AND its
  Holm-adjusted p < α over the pre-registered family. Map the inverted verdict:
  - `DISABLE` (with-state reliably **positive**) → **BUILD** (enter/boost in
    this state).
  - `ENABLE` (with-state reliably **negative**) → **AVOID** (suppress entries
    in this state — also an actionable lever).
  - `CONCENTRATE` → **NO-EDGE** (the kept-vs-suppressed concentration branch is
    not meaningful for a symmetric with/without split).
  - `INSUFFICIENT` (n < min_n) → **INSUFFICIENT**.
  - Cell shape: build each `audit_guard` cell with **`supp_r` = the with-state
    slice's R** (the slice whose mean the verdict statistic tests) and
    **`kept_r` = the without-state slice's R**.
- **Lift leg:** a seeded two-sample bootstrap CI on (with-state mean −
  without-state mean) excluding 0, same sign as the absolute leg — implemented as
  in `reference_level_proximity_audit`'s near-vs-far lift leg. This is what makes
  it a *conditioning* result (edge ADDED beyond the base rate), not just a
  restatement of the base edge.
- **Family stamps:** DSR (Deflated Sharpe) and PBO (probability of backtest
  overfit, CSCV) computed over the per-axis-state trial family and reported on
  the verdict; a cell is only BUILD/AVOID if DSR ≥ 0.95 ∧ PBO ≤ 0.5 as well.
- `n ≥ MinTRL(0.95)` on the with-state slice.

Anything not clearing every condition is **NO-EDGE** (resolvable, n ≥ min_n) or
**INSUFFICIENT** (n < min_n).

## 8. Harness (modules)

Mirror the `warning_value_audit` shape exactly:

- **`analytics/indicator_condition.py`** — pure, read-only, no I/O beyond a
  passed DuckDB conn. Functions:
  - `tag_trades(entries, market_by_pair) -> pd.DataFrame` — adds one column per
    axis (the as-of-entry state enum) via §6.
  - `build_condition_cells(tagged, *, grain) -> list[Cell]` — per
    (axis-state × direction) [× strategy-type for the diagnostic grain].
  - `evaluate_conditions(cells, cfg) -> list[ConditionVerdict]` — the §7 gate,
    with the inverted-verdict mapping baked in and unit-tested.
  - Frozen `IndicatorConditionConfig` (bar/alpha/min_n/n_boot/seed a-priori).
- **`tools/indicator_condition_audit.py`** — DB front door. Reuses
  `warning_value_audit`'s `_load_entries` / `normalize_backtest` /
  `normalize_live` / `_load_market`. Prints the pooled verdict table + the
  strategy-type diagnostic + family stamps. Read-only
  (`duckdb.connect(..., read_only=True)`).
- **`make buibui-indicator-condition-audit`** (add `.PHONY` + target + wrap the
  CLI invocation, `--source both|backtest|live`, `--min-n`, `--timeframes`,
  `--db`, `--out`).

No engine change, no schema change, no golden change (read-only over existing
tables). Deterministic (seeded bootstrap).

## 9. Non-goals / caveats (state in the verdict doc)

- **Backtest-gated, not live-confirmed** unless a cell independently reaches n on
  `signal_alert_outcomes`. The live ledger is thin/irregular (operator runs the
  daemon by hand ~daily).
- **Trades are non-iid** within a symbol; the block-bootstrap addresses serial
  correlation but DSR/PBO ≈ 1.000/0.000 should be read as "well-powered under the
  block model," not "risk-free."
- **Unconditional entry** — H8 conditions the EXISTING trade population; it does
  not propose a new detector. A BUILD/AVOID result is a *gating* lever for the
  live daemon (a follow-up), not a strategy.
- The M1 states are re-derived from committed bars, so unlike the catch-up
  backfill path there is **no as-of-now gating approximation** — the state is
  honestly as-of-entry.

## 10. Deliverable

- The two modules + make target + tests (TDD, mutation-proof look-ahead test).
- A verdict doc `docs/audits/2026-07-XX-h8-m1-indicator-conditioning.md` with the
  plain-English glossary framing (headline → metric → money → backtest-vs-live)
  and the pre-committed verdict table.
- Update `project_conditional_edge_test` / `project_strategy_overhaul_revisit`
  memory + the SoT with the verdict.
