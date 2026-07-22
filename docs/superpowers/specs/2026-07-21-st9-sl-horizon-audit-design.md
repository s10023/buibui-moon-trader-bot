# ST9 / H11 — SL-horizon audit (design)

**Status:** design approved 2026-07-21, not yet implemented
**Hypothesis:** H11 (memory `flat-sl-pct-defect`)
**Priority:** MEDIUM-HIGH — a dimensional defect, so explicitly *not* covered by the
"tuning existing detectors is dead" verdict in `finetune-vs-xs-challenge`; capped
because XS-solo remains the deploy core.

## 1. The question

Six detectors — `doji`, `engulfing`, `hammer_hanging_man`, `inside_bar`,
`morning_evening_star`, `pin_bar` — hard-code `sl_pct = 0.02` and compute
`sl = entry * (1 ± sl_pct)` at **every** timeframe. Every other active detector
derives a structural SL (swing / zone edge / wick), which is TF-adaptive by
construction. No config overrides the 2%: `min_sl_pct = 0.005` only floors, and
`atr_sl_multiplier` is configured for exactly one unrelated cell
(`liquidity_sweep` 1h).

The configs are full of kills for this family (`pin_bar` 1h, `doji` 4h/1d,
`morning_evening_star` 1d, `hammer_hanging_man` 1h/4h, `inside_bar` 4h long).

**If the stop was dimensionally wrong, those verdicts killed a mis-parametrized
strategy rather than a strategy** — the false-negative class that
`feedback_data_driven_strategy_cuts` exists to prevent.

**Success metric.** A pre-committed, de-biased verdict per (strategy × TF):
SUSPECT (the graveyard reopens) / CONFIRMED-BAD (it closes for good) /
NO-DIFFERENCE / INSUFFICIENT.

## 2. What the scoping queries already established

These are measured, not assumed. They are recorded here because they changed the
design and any implementer should be able to reproduce them.

**The flat stop is real and the overrides are inert.** SL is exactly 2.000% in
both substrates (`backtest_trades` range 1.976–2.053%; live mean 2.0% at every
TF). Neither `min_sl_pct` nor the ATR floor ever fires for this family.

**The horizon decoupling is directly observable.** Median bars from entry to
resolution in `backtest_trades`:

| TF | bars → loss | bars → win |
| --- | --- | --- |
| 15m | 67 | 204 |
| 1h | 17 | 50 |
| 4h | 4 | 13 |
| 1d | 0 | 1 |

A 15-minute pattern is held ~51 hours to win. A daily pattern resolves *inside
its entry candle* — stop and target both sit within one bar, so the outcome is
decided by the engine's adverse-first intrabar rule rather than by the pattern.
Both ends are broken, in opposite directions, exactly as the dimensional
argument predicts.

**A large live OOS substrate exists.** 2,629 resolved `signal_alert_outcomes`
rows for these six — **76% of the entire live ledger**. It disagrees with
backtest at every timeframe:

| TF | backtest avg_r | live avg_r | live expiry rate |
| --- | --- | --- | --- |
| 15m | +0.011 … +0.067 | −0.016 | 55% |
| 1h | +0.020 … +0.135 | −0.197 | 29% |
| 4h | +0.016 … +0.180 | −0.159 | 21% |
| 1d | +0.195 … +0.670 | −0.170 | 0% |

The backtest engine has **no expiry at all**; live applies `max_hold_bars`. So
the positive backtest number is bought by holding 15m trades for two days, and
live refuses to. That gap *is* the defect, measured. Live 15m expired trades
average **+0.586R** — the target is unreachable within the horizon, so the family
banks partial winners as expiries while paying full −1R losses.

**The counterfactual needs no engine change.** `engine.py:990` takes the
per-signal `sl_price` branch, where ATR can only *widen* (F9 floor semantics).
`engine.py:1017` — `elif atr_sl_multiplier is not None` — makes ATR a full
*replacement*. Omitting the `sl_price` column reaches it. This design does not
rely on that branch (see §4), but it confirms the family's SL is fully
substitutable.

**Data coverage far exceeds the stored runs.**

| TF | symbols with OHLCV | history | stored `backtest_trades` |
| --- | --- | --- | --- |
| 1h / 4h / 1d | 25 (N3 universe) | 2019-09 → 2026-07 | 3 symbols, 2025-09 → 2026-07 |
| 15m | 3 (majors) | 2019-09 → 2026-07 | 3 symbols, ~10 months |

The 1d cell has n=294 in stored trades — unpowered, and distorted by same-bar
resolution. Re-detecting over full history takes it past n≈2,500.

## 3. Scope decisions (locked)

| Decision | Choice | Why |
| --- | --- | --- |
| Depth | Descriptive **and** counterfactual, both substrates | Descriptive alone cannot say what the right stop is, guaranteeing a second session |
| `tp_r` | **Pinned** to each cell's live-config value, never swept | Tests the dimensional hypothesis exactly; keeps one free axis; `tp_r` was already swept via `/wfo-sweep`, and re-spending budget there invites the DSR/PBO refusal that killed EMA, `ote_entry` and F9-joint |
| Population | Re-detect over full history: universe at 1h/4h/1d, majors at 15m | Makes 1d testable; yields a free majors-vs-universe breadth contrast |

## 4. Architecture

Two new modules, following the established pure-lib + thin-driver split
(`structural_touch.py` + `tools/structural_touch_decay_audit.py`;
`warning_audit.py` + `tools/warning_value_audit.py`).

### `analytics/sl_horizon.py` — pure, no DB, no IO

- `SLGridConfig` — frozen dataclass. `multipliers` (a-priori, see §5),
  `baseline_pct = 0.02`, `max_hold_bars_by_tf`, `fee_pct`, `slippage_bps`,
  `bar = 0.05`, `alpha = 0.05`, `min_n = 30`, `n_boot = 2000`, `seed`.
  `max_hold_bars_by_tf` defaults to `outcome_backfill.DEFAULT_MAX_HOLD_BARS`
  (`15m: 96`, `1h: 48`, `4h: 30`, `1d: 14`) — imported, not redefined, so the
  audit horizon cannot drift from the live one.
- `counterfactual_levels(entry, direction, atr, k, tp_r)` → `(sl_price, tp_price)`.
- `describe_horizon(trades)` → the §2 descriptive table generalized: effective SL
  in ATR14 units, median bars-to-resolution, expiry rate, per (strategy × TF).
- `build_paired_table(signals, arms)` → per-signal net R under every arm, aligned
  on one row per signal so arms are directly subtractable.
- `evaluate_sl_grid(cells)` → `list[SLVerdict]` per the §6 rule.

### `tools/sl_horizon_audit.py` — driver

DB front door (`duckdb.connect(..., read_only=True)`), markdown report, wrapped
by `make buibui-sl-horizon-audit`. Read-only: no DB writes, no config edits, no
engine change.

### Data flow

```text
ohlcv (read-only)
  ├─ detect: 6 detectors × [25 sym × 1h/4h/1d] + [3 majors × 15m], full history
  │    → signal set + each detector's own 2% sl_price (= the baseline arm)
  ├─ enrich: ATR14 at the signal bar (engine._compute_atr14 — the primitive live uses)
  ├─ arms:  baseline 2% flat  ‖  k × ATR14 for k in the a-priori grid
  │         tp_r PINNED per (strategy, tf) from the live config
  ├─ resolve: exits.replay.replay_exits over a max_hold_bars-truncated window
  │           (adverse-first ties; expiry falls out of window exhaustion)
  ├─ cost:   net R = raw − fee − slippage − funding
  ├─ FIDELITY CHECK (§7) ← acceptance gate
  └─ verdict: audit_guard.evaluate_audit_cells + DSR/PBO over the k-grid family
```

The live leg is identical with the signal set drawn from the 2,629 resolved
`signal_alert_outcomes` rows, ATR14 recomputed at `candle_ts_ms`.
**Live gates the verdict; backtest corroborates** — the ST1 / H9 precedent.

### Reuse, not reimplementation

`replay_exits(highs, lows, closes, *, direction, entry, sl_price, policy)`
already derives TP from `policy.tp_r × risk` and resolves adverse-first, so
passing a counterfactual `sl_price` with a pinned `tp_r` is sufficient — no new
exit policy, no second resolver. Expiry comes from truncating the window to
`max_hold_bars`; `ExitPolicyConfig.time_stop_bars` stays `None`.

## 5. The `k` grid — a-priori, never tuned

`k ∈ {0.5, 1.0, 1.5, 2.0, 3.0}` × ATR14.

Current effective ratios (2% expressed in median-true-range units) are ≈7.5 at
15m, ≈3.6 at 1h, ≈1.7 at 4h, ≈0.6 at 1d. The grid brackets the current value on
both sides at 1h, 4h and 1d. At 15m it sits entirely below — every arm is a
tightening, which *is* the hypothesis. The 0.5–3.0 span is the conventional
a-priori band for ATR stops; it is fixed before any run and is not widened,
narrowed or re-centred in response to results.

## 6. Pre-committed verdict rule

Per (strategy × TF), fixed before the first run:

| Verdict | Condition |
| --- | --- |
| **SUSPECT** | some `k` beats baseline by ≥ `bar` (0.05R), paired block-bootstrap CI excludes the bar, Holm-adjusted across the tested family, n ≥ `min_n`, **and** the winning `k` survives DSR ≥ 0.95 ∧ PBO ≤ 0.5 over the k-grid |
| | *Winning `k`* = the largest mean paired lift among the `k` that clear the CI test. Ties break toward the **larger** `k` (the wider, cheaper-to-trade stop). Fixed here so the choice is not made after seeing results. |
| **CONFIRMED-BAD** | no `k` clears the bar **and** baseline avg_r ≤ 0 — the 2% is cosmetic; close the cell permanently |
| **NO-DIFFERENCE** | no `k` clears the bar, baseline not clearly negative — stop width is not the binding constraint |
| **INSUFFICIENT** | n < `min_n` (excluded from the Holm family, per `audit_guard` semantics) |

A SUSPECT verdict produces a **recommendation**, never a TOML edit. Applying it
is a separate, separately-gated decision.

### The statistic is a PAIRED lift

Both arms run on the identical signal set, so the statistic is the per-signal
difference `R_k − R_baseline`, tested against ±`bar`. This is far better powered
than comparing two independent slices, and it is immune to the signal
population's own quality — which matters because these detectors are marginal to
begin with.

It reuses `audit_guard.evaluate_audit_cells` **unchanged**: feeding the
difference series as `supp_r` makes the existing ±bar + Holm machinery a paired
test. No change to `analytics/audit_guard.py`.

**The Holm family** is every `(strategy × TF × k)` cell tested in one run of one
substrate — one family per substrate, never pooled across substrates, matching
`warning_value_audit`'s "one family per source". Cells below `min_n` are
`INSUFFICIENT` and excluded from the family denominator, per `audit_guard`
semantics.

## 7. Acceptance gate — the fidelity check

Two independent checks, both run before any verdict is computed. Each fails
loudly; neither is a result.

**7a — backtest leg.** The baseline arm, run with **no** time stop over the
majors slice that overlaps the stored runs (2025-09 → 2026-07), must reproduce
`backtest_trades.pnl_r`. Tolerance, fixed in advance: per (strategy × TF),
`|avg_r_replayed − avg_r_stored| ≤ 0.02R` **and** matched-trade outcome
agreement ≥ 95% on trades joinable by
`(symbol, tf, strategy, direction, signal_time)`. The two conditions together
catch both a systematic shift and offsetting per-trade errors that a mean alone
would hide.

**7b — live leg.** The live baseline arm must be **re-resolved through the same
harness**, not read from stored `outcome_r` — otherwise the paired difference
would compare two different resolvers. The stored `outcome_r` then becomes a
free second fidelity check: re-resolved baseline vs stored must agree to the
same 0.02R / 95% tolerance.

If the harness cannot reproduce the production path on the one arm where they
must agree, every downstream number is drift and the audit is void.

## 8. Reporting requirements

- **Cost as a fraction of R, per (TF, k).** Round-trip cost is
  `2 × fee_pct (0.0005) + 2 × slippage (2 bps)` = **0.14%**. At 15m, `k = 1.0`
  gives a ≈0.27% stop on BTC — so cost is **≈0.52R per trade**. Tight stops at
  15m may be cost-destroyed regardless of edge. The audit must surface this
  plainly rather than letting a gross-of-cost lift look like an edge.
- **Majors-only column at every TF.** 15m has 3 symbols; 1h/4h/1d have 25.
  Cross-TF comparison on mixed populations would confound stop width with
  universe composition. Universe powers within-TF verdicts; the majors-only
  column makes cross-TF reads like-for-like.
- **Funding included.** Net R = fee + slippage + funding, matching
  `outcome_backfill`'s P0b honest-cost stance, with graceful 0.0 when absent.
  15m trades currently run ~50 hours, crossing ~6 funding windows — excluding it
  would flatter the baseline arm specifically.
- **Survivorship note.** Both arms share an identical signal set, so the *lift*
  is survivorship-robust even where the absolute level is not. State this;
  do not claim it for the levels.
- Written to `docs/audits/2026-07-21-st9-sl-horizon.md`, markdownlint-conformant.

## 9. Testing

- `counterfactual_levels` — long/short symmetry; TP tracks `tp_r × sl_dist`.
- Paired-difference construction — arms stay row-aligned; a signal missing from
  any arm is dropped from all arms, never silently zero-filled.
- **Fidelity test** (§7) as an explicit test, not just a runtime check.
- Verdict-rule tests over synthetic cells covering all four outcomes, including
  the CONFIRMED-BAD vs NO-DIFFERENCE boundary at `baseline avg_r = 0`.
- Cost accounting — net R is strictly below gross R for a non-zero-cost config.
- No network. DuckDB `:memory:` wherever a connection is needed.

## 10. Out of scope (YAGNI)

- No `tp_r` sweep.
- No new exit policies — breakeven and partial scale-outs belong to the exits
  sub-project, and mixing them in would confound the stop-width question.
- No engine, detector, or config change. The audit is read-only.
- No live wiring. Acting on a SUSPECT verdict is a later, separately-gated slice.

## 11. Risks

- **A tight stop at 15m loses to costs, not to the hypothesis.** Mitigated by
  reporting cost-as-fraction-of-R per cell so the two causes stay separable.
- **Re-detected signals will not exactly match stored runs** (different config
  filtering, wider date range). This is intended — but it means the audit's
  absolute levels are not comparable to the graveyard numbers, only its lifts.
  The fidelity check bounds how far the harness itself drifts.
- **First-touch signals are non-iid**, so as in the structural entry-sim audit a
  DSR near 1.0 / PBO near 0.0 should be read as *well-powered*, not *risk-free*.
- **A SUSPECT verdict is backtest-plus-live, not live-confirmed-forward.** Any
  resulting config change stays OOS-gated.
