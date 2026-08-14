# Multi-regime detector validation — pre-registered design

**Date:** 2026-08-12.
**Verdict:** `docs/audits/2026-08-12-multi-regime-validation.md` — **NO detectable
regime dependence.**
**Drivers (gitignored, on disk, re-runnable, covered by `LEDGER_DIRS`):**
`docs/plans/scratch/multi_regime_coverage.py` (panel coverage),
`docs/plans/scratch/multi_regime_power.py` (DSR inversion),
`docs/plans/scratch/multi_regime_deflator.py` (t-deflator + paired MDE).

**This document was written before the study ran, and after the power was
priced.** Every number below is measured, not assumed; each section names the
script that produced it.

## Question

Every live result this system holds comes from one regime. The signal book was
swept, rated and deployed inside a single market state, so nothing distinguishes
"this detector has an edge" from "this detector suits 2025–26".

**Does a detector's edge persist across market regimes?**

Note what this is *not* asking. It is not asking which detectors have positive
edge — that question is already answered, negatively, and re-asking it is the
trap §3 exists to prevent.

## Success metric — the number that decides

**A paired regime difference of |Δ avg_r| ≥ +0.195R on the 15m panel**, where
Δ = (mean `pnl_r` in the bull leg) − (mean `pnl_r` in the bear leg) for one
pre-registered cell.

That is the 80%-power, two-sided-95% minimum detectable effect at the measured
per-leg n, after the correlation correction recomputed in §5. Per grain:

| grain | n / leg | SE (naive) | deflator | SE (corrected) | **MDE** |
| --- | --- | --- | --- | --- | --- |
| **15m** | 3,741 | 0.0428R | ×1.63 | 0.0696R | **+0.195R** |
| 1h | 4,394 | 0.0395R | ×3.33 | 0.1315R | +0.368R |
| 4h | 1,141 | 0.0775R | ×3.33 | 0.2580R | +0.723R |
| 1d | 176 | 0.1972R | ×3.33 | 0.6570R | +1.841R |

**15m is the primary panel. 1h is secondary. 4h and 1d are reported but cannot
decide anything** — 1d's MDE (+1.841R) exceeds the best cell in the entire
corpus (+1.196R), so a 1d null is uninformative by construction and must be
reported as INSUFFICIENT, never as "no regime dependence".

**The unit is load-bearing.** Δ is a difference of two per-trade `pnl_r` means.
It is not an alert-level avg_r, not a book-day excess, and not comparable to the
ensemble spec's +0.139 R/day bar. Substituting one for another is the H15
`bar`-units trap.

## The defect this design exists to avoid

**The obvious design — score every cell in every regime leg against DSR ≥ 0.95 —
is structurally unreachable, and would produce a confident-looking verdict that
no data could ever have changed.** This is the H8 failure mode: a gate leg that
cannot fire, published as if it had been tested.

Measured (`multi_regime_power.py`). Deflating against the trial family, the
`avg_r` a cell must post to clear DSR ≥ 0.95:

| trials | 15m (n=3,741) | 1h (n=4,393) | 4h (n=1,141) | 1d (n=175) |
| --- | --- | --- | --- | --- |
| 1 | +0.049R | +0.045R | +0.087R | +0.212R |
| 4 | +0.404R | +0.401R | +0.438R | +0.546R |
| 16 | +0.657R | +0.655R | +0.687R | +0.785R |
| 80 | +0.878R | +0.875R | +0.905R | +0.994R |
| 320 | +1.035R | +1.033R | +1.060R | +1.143R |

A full scan is 20 strategies × 4 timeframes × 2 directions × 2 legs ≈ **320
trials**, demanding **+1.03R**. The best cell in the whole corpus is **+1.196R**
and the 90th percentile is **+0.434R** (122 scoreable cells, n ≥ 30). So a scan
could return at most one pass, and it would be the in-sample maximum — precisely
the cell the deflation exists to disbelieve.

**Trial count dominates sample size, and it is not close.** Across a 21× range
of n (175 → 4,393) the bar moves 10%; across trial counts (1 → 320) it moves
**21×**. Sample size is therefore not the lever this study should be designed
around. **Trial count is.**

For the same reason, the standing gate is not applied per leg here. Today, with
no regime split at all, **0 of 156 production cells clear DSR ≥ 0.95** (max
0.7289, median 0.0000, via `recalibrate_lib.compute_dsr_ratings`). Splitting by
regime only lowers n and raises trial count. A per-leg DSR scan cannot return a
pass, so it must not be run as if it could.

## Population and coverage — measured, not assumed

From `multi_regime_coverage.py` against `analytics.db`:

| grain | symbols (now) | bull 2021 | bear 2022 | span |
| --- | --- | --- | --- | --- |
| 15m | **3** | 3 | 3 | 2019-09-08 → 2026-08-12 |
| 1h | 25 | **14** | **15** | 2019-09-08 → 2026-08-12 |
| 4h | 25 | **14** | **15** | 2019-09-08 → 2026-08-12 |
| 1d | 25 | **14** | **15** | 2019-09-08 → 2026-08-12 |

15m is **BTCUSDT, ETHUSDT, SOLUSDT only** — but all three span the full history,
so 15m covers every regime leg. `1w` stops at 2026-06-08 and is excluded: nothing
reads it, and that is by design, not a bug to fix.

**Regime legs are fixed by calendar, pre-registered here, and never fitted:**
**bull = 2021-01-01 → 2021-12-31**, **bear = 2022-01-01 → 2022-12-31** (UTC).
Deriving regime labels from an indicator fitted on the same price series would
be look-ahead in the *labelling*, which is the subtlest way this study could
fool itself.

## The correlation correction — recomputed, never reused

CLAUDE.md's filed **2.92×** belongs to the 25-symbol forecast panel. These panels
are 14, 15 and 3 symbols, so the deflator is recomputed
(`multi_regime_deflator.py`, via `analytics.forecast.effective_independent_series`):

| panel | k | n_eff | t_deflator |
| --- | --- | --- | --- |
| 15m bull 2021 | 3 | 1.42 | **1.452×** |
| 15m bear 2022 | 3 | 1.13 | **1.628×** |
| 1h/4h/1d bull 2021 | 14 | 1.97 | **2.668×** |
| 1h/4h/1d bear 2022 | 15 | 1.35 | **3.331×** |

**This is the finding that inverts the received framing.** The 3-symbol 15m
panel was filed as the study's weakness. Measured, it is the **best-powered**
panel by a wide margin, for two compounding reasons:

1. 15m fires ~136.7 trades/symbol-day, so 3 symbols still yield **3,741
   trades/cell/leg** — more than 4h and 1d get from 14.
2. Fourteen crypto perps carry **n_eff = 1.97** — they are worth about *two*
   independent bets, barely more than three symbols' 1.42. Breadth buys almost
   nothing here because the cross-section is nearly one asset.

So "15m has only 3 symbols" is not a concession to disclose. It is the reason to
run 15m **first**. What the 3-symbol panel genuinely costs is **generalisation**
— a 15m result speaks for BTC/ETH/SOL, not for the universe — and that
limitation belongs in the verdict, not in the power budget.

## Trial budget — counted, not free

**At most FOUR pre-registered cells**, fixed in writing before any regime-split
number is computed. Four is not arbitrary: at 4 trials the DSR-reachable bar
(+0.40R) sits below the observed 90th percentile (+0.434R); at 16 it does not.

**Selection rule, and why it does not contaminate the test.** Cells are ranked by
**full-sample pooled `avg_r`** — over both legs together — and the top 4 with
n ≥ 30 per leg are taken. Selecting on the pooled *level* is orthogonal to the
bull−bear *contrast* under exchangeability, so this does not bias Δ. **Selecting
on anything that sees the split — best bull leg, biggest apparent gap — would
bias it, and is forbidden by this pre-registration.**

Record the 4 cells, with their pooled avg_r and per-leg n, in the verdict doc
*before* reporting any Δ.

## Observation unit

**One TRADE**: a row of `backtest_trades`, statistic `pnl_r`. This matches
`recalibrate_lib._scope_dsr`, so the numbers are commensurable with the star
ratings and the decay review.

`backtest_trades` carries a **5.29× duplication factor** (857,740 rows →
162,263 distinct) because repeated runs re-store identical trades. **Deduplicate
on `(symbol, timeframe, strategy, direction, entry_time)` before computing
anything.** Skipping this inflates every n ~5× and every t-stat ~2.3×.

Measured on the deduplicated pool (n = 160,914): mean −0.0542R, **std 1.8511**,
skew +1.415, kurtosis 3.527 (non-excess). **1 unit of per-trade Sharpe = 1.851R**
— that is the conversion every bar in this document uses.

⚠ **Do not read these trades from `backtest_runs` aggregates.** 415 of those rows
were overwritten by the live gate and 331 disagree with their own trades. The
trade rows themselves are sound; the aggregates are not.

## Construction

1. Deduplicate `backtest_trades` as above.
2. Restrict to the two calendar legs by `entry_time` (UTC epoch-ms bounds
   computed in Python — DuckDB's session TimeZone here is `Asia/Kuala_Lumpur`
   and `date_trunc` would bucket in MYT).
3. Rank cells by pooled `avg_r`, take the top 4 with n ≥ 30 per leg, **write them
   down**.
4. For each: Δ = mean(bull `pnl_r`) − mean(bear `pnl_r`); SE =
   `std × sqrt(1/n_bull + 1/n_bear) × deflator` for that grain.
5. Report Δ, SE, the corrected t, and whether |Δ| ≥ the grain's MDE.
6. Exclude any cell with per-leg dispersion `std_r < 0.05` and **say so** — one
   such cell exists (`bos/1d/long`: 36 trades all ≈ −1.0076R, std 0.0022,
   Sharpe −461) and it inflates a trial-family variance 50,000× if admitted.

## Kill rule and how a null is reported

> ⚠ **AMENDED 2026-08-14 (ST28) — the rule below is WRONG and is superseded by
> the three bullets after it.** `MDE = 2.802 × SE`, so `|Δ| < MDE` is `|t| <
> 2.802` — a significance test, not a power test. It is computed from the data's
> own noise and can never rule an effect out. Struck through rather than deleted
> because the published table was produced under it.

- ~~**|Δ| ≥ MDE** on 15m or 1h ⇒ regime dependence detected.~~
- ~~**|Δ| < MDE on 15m** ⇒ a **powered null**.~~
- ~~**|Δ| < MDE on 4h or 1d** ⇒ **INSUFFICIENT**.~~

The corrected rule, on any grain:

- **|t| ≥ the Bonferroni threshold for the pre-registered family** ⇒ regime
  dependence detected; report direction and size. This does **not** mean the cell
  is tradeable — no cell clears the gate.
- **CI contained strictly inside ±`bar`** (`bar = 0.05R`, per-trade units) ⇒ a
  **powered null**: an effect worth acting on has been ruled out. Computed by
  `analytics.audit_guard.powered_null`; never restate the comparison inline.
- **Otherwise** ⇒ **INSUFFICIENT**, never "no effect". This now covers cells the
  old rule called powered on 15m: failing to detect and ruling out are different
  outcomes, and only the interval separates them.

`MDE` is still reported, as a **scale stamp only** — it says how large an effect
would have been visible, which is a planning number, not a verdict.

The H15 discipline applies: a powered null and an underpowered null are different
verdicts and must never be reported in the same words.

## What this study cannot answer

- **Whether any detector is profitable.** It cannot; 0 of 156 cells clear the
  gate today, and this design does not test that.
- **Whether 15m results generalise past BTC/ETH/SOL.** Three symbols, n_eff 1.42.
- **Regime dependence on 4h/1d below +0.723R / +1.841R.**
- **Anything about 2023–24 or 2025–26.** Two legs only; adding legs multiplies
  trials, and §3 is why that is not free.
- **Why** an edge does or does not persist. This measures persistence, not cause.

## Amendment log

- 2026-08-12 — written. Power priced before the design, which changed it twice:
  the per-leg DSR scan was dropped as structurally unreachable (§3), and 15m was
  promoted from "caveat to disclose" to primary panel (§5).
- 2026-08-12 (post-run) — **§Kill rule was under-specified and is amended.** It
  tested each of the 4 pre-registered cells at two-sided 95% without correcting
  across the family of 4. Testing 4 cells at α=0.05 each carries a ~19% chance of
  one false positive, and the study returned exactly one nominal hit
  (`eqh_eql`/15m/short, t=+2.33, p=0.0198) which fails the 4-test Bonferroni
  threshold of |t| ≥ 2.498. **A pre-registered cell family must carry a
  pre-registered multiplicity correction** — deflating for trial count is the one
  thing this whole design was built around, and the kill rule omitted it at the
  last step. The verdict applies the correction and reports the hit as not
  significant. Recorded here rather than silently applied.
- 2026-08-12 (post-run) — §Population understated the work. `backtest_trades`
  held **only 2025-09-12 → 2026-07-07**, so both legs had to be generated by
  re-running backtests, not queried. The spec read as though the trades existed.
- 2026-08-14 (ST28) — **§Kill rule was WRONG, not merely under-specified, and is
  amended above.** `|Δ| < MDE ⇒ powered null` is `|t| < 2.802`: a significance
  test wearing a power label. Re-scored on CI containment inside ±0.05R, **0 of
  the 3 filed powered nulls survive** (half-widths 2.1× / 3.8× / 7.1× the bar).
  The verdict direction is unchanged — no cell survived multiplicity then or now
  — but the cell-level claim weakens from *ruled out* to *not ruled out*.
  Verdict: `docs/audits/2026-08-14-st28-multi-regime-powered-null.md`.
- 2026-08-14 (ST28) — **the spec and its implementation diverged on DETECTION and
  neither noticed.** This spec detects at `|Δ| ≥ MDE` (|t| ≥ 2.802); the driver
  detected at `|t| ≥ 1.96`. Under this spec as written, `eqh_eql`/15m/short
  (|t| = 2.33) was never a hit, and the §Consequences re-entry recommendation
  built on it had no basis. The published table follows the code. Recorded
  because no gate can catch this class — both halves are internally consistent,
  and only reading one against the other finds it.
