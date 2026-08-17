# Give-back / heat-and-run — pre-registration

**Date:** 2026-08-17 · **Status:** pre-registered, not yet run · **Source:** SoT ST17
(Stream B mechanic, harvested 2026-08-14c) · **Scope:** live outcome ledger only

Fixed **before** any number was computed. The commit timestamp is the evidence; that is why
this file is tracked rather than living in gitignored `docs/plans/scratch/`.

## Question

Does the live signal book hand back open profit before it exits?

## This is DESCRIPTIVE, and that decides its cost

It measures a property of the existing record — the same class as "41.6% of trades expired".
It claims no edge, searches no construction, and therefore carries **no trial count and no
DSR/PBO/boot_lo gate**.

**The gate binds the moment a rule is proposed** ("trail at 1R", "move to breakeven at X"),
because that is a new construction fitted to the same data. A rule change is a separate
pre-registration and inherits the full three-leg gate.

The single licensable per-era comparison (bar +0.079R, `2026-08-14-st17-era-study-power-price.md`)
is **deliberately NOT spent here** — a descriptive stat does not need it, and there is only one.

## Definitions, fixed now

Population: the `signal_alert_outcomes` rows with a non-null `outcome_r` (5,170 at
pre-registration).

- `R_unit = abs(entry_price - sl_price)` — per row, so stop width stays explicit rather than
  hiding in a pooled average.
- `MFE_R` = maximum favourable excursion between `candle_ts_ms` and `outcome_filled_at_ms`,
  in `R_unit`, from OHLCV bars on the row's own `tf`. Long → `max(high)`; short →
  `min(low)`. The ledger carries **no MFE column**, so this is computed by join.
- `giveback_R = MFE_R - outcome_r_restated`.
- **Pre-registered threshold X = 1.0R.** Chosen from doctrine, not from data: 1R is where
  "move to breakeven" is the standard operator action and is the level the harvested Stream B
  mechanics speak to ("trim rather than widen", "don't move a stop lower to avoid a floating
  loss").

Primary reported quantity — the **conditional** stat, over rows with `MFE_R >= 1.0`:

- share of those rows finishing at `outcome_r <= 0`
- median and IQR of `giveback_R`
- median `outcome_r / MFE_R` (the capture ratio)

Reported beside it, never as the headline: the same three over the whole population.

## Three traps this design defuses

1. **R-units inherit stop width by construction.** 78% of the ledger runs the hardcoded flat
   2% SL across 6 detectors, and modelled drag `= 2(fee+slip)·entry/risk` scales inversely
   with stop width — so any cross-cell comparison in R carries a bias, not just a level
   shift. ⇒ **Stratify by stop width; publish no pooled cross-detector give-back average as
   a headline.**
2. **`outcome_r` changes BASIS mid-ledger** at `e5d92bb` (#432, gross → net). `MFE_R` is a
   raw price quantity, so a naive `MFE_R - outcome_r` subtracts a mixed-basis number from a
   raw one. ⇒ use `restate_cost_basis=True`, and say so in the output.
3. **Losers that never went green have `giveback_R = 0` by construction**, so a population
   mean understates give-back among trades that actually went favourable. ⇒ that is exactly
   why the primary stat is conditional on `MFE_R >= X`, with X fixed above.

Plus one label, not a trap: the ledger straddles **46 signal-path rule changes** (largest
single-era sub-sample 32%), so every figure is an average ACROSS rule changes. Print
`straddle_report` beside the result rather than leaving the reader to assume one regime.

## Known limits, stated up front

- **Costs are MODELLED, not realised** — raw stays exactly −1.0 = declared risk, so no figure
  here expresses gap risk, and each is an optimistic bound whose error runs one way.
- `MFE_R` is read from **bar extremes**, so it is the excursion an omniscient exit would have
  caught. It is an upper bound on what any real rule could capture, and must never be quoted
  as forgone profit.
- Intrabar path is unknown: on a bar touching both stop and target, this cannot say which came
  first. Rows where that happens are counted and reported, not silently resolved.

## Decision log — the observable that REVERSES this

| Decision | Reverses if |
| --- | --- |
| Descriptive, no gate | Anyone proposes a stop/trail rule from the output — then it needs the full three-leg gate as a new construction |
| X = 1.0R | A capture-ratio curve shows 1.0R sits on a cliff rather than a plateau, making the choice load-bearing rather than incidental. Re-pre-register; do NOT re-pick after seeing it |
| Era slot banked | A give-back difference across eras becomes the actual question — then re-derive the bar, since +0.079R was priced for an era-Sharpe question, not this one |
| Ledger scope only | Backtest scope is NOT priced (90 boundaries, ~5.29× duplication factor needing dedup on `(symbol, timeframe, strategy, direction, entry_time)` first) |

⚠ **"Descriptive, no gate" licenses a REPORT, never a NEGATIVE VERDICT — and the null
direction is the likely one here.** If the measurement comes back small, writing "the book
does not give back meaningful profit" is a positive claim about absence and needs
**CI containment** (`analytics.audit_guard.powered_null`: `ci_lo > -bar` and `ci_hi < bar`) —
a small median, a wide IQR, a p-value or a failure to clear anything are NOT power. Six sites
in this repo asserted power from one of those, each spelling it differently, and ST26/27/28
then dropped 41-of-170, 22-of-30 and 3-of-3 such claims. **The honest small-result output is
"give-back is X ± CI, INSUFFICIENT to rule out an effect up to ±bar", not "no give-back".**

## Not settled by this

`why-trades-expire` answered a **time-stop** question and is adjacent, not overlapping —
give-back is about profit surrendered before exit, whichever exit fired.
