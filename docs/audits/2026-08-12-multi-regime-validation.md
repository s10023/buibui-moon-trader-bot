# Multi-regime detector validation — verdict: NO detectable regime dependence

**Date:** 2026-08-12.
**Spec:** `docs/superpowers/specs/2026-08-12-multi-regime-validation-design.md`
(written before the study ran, after the power was priced).
**Drivers:** `tools/multi_regime_{power,deflator,study}.py` (promoted out of
`docs/plans/scratch/` 2026-08-14) and `docs/plans/scratch/multi_regime_coverage.py`.

> ⚠ **CORRECTED 2026-08-14 (ST28) — the three "powered null" labels below are
> WITHDRAWN and read INSUFFICIENT.** The study declared a powered null from
> `|Δ| < MDE`, and since `MDE = 2.802 × SE` that is a significance test wearing a
> power label: a criterion computed from the data's own noise can report that an
> effect was not *seen*, never that one was *ruled out*. Under the corrected
> criterion (`analytics.audit_guard.powered_null` — CI containment inside
> ±0.05R) **0 of 3 survive**; CI half-widths are 2.1× / 3.8× / 7.1× the bar.
> **The verdict direction is unchanged** — no cell was significant then and none
> is now — but the claim weakens from *ruled out* to *not ruled out*. This is the
> sixth site of the family closed at five others by ST26/ST27, and it looks
> different again from all five. Tables below are left as the dated record with
> the corrected column added. Verdict:
> `docs/audits/2026-08-14-st28-multi-regime-powered-null.md`.

## Verdict

**No pre-registered cell shows regime dependence that survives correction for the
four tests performed.** The signal book is not regime-flattered: it performs about
as poorly in a 2021 bull leg and a 2022 bear leg as it does in the 2025–26 window
every star rating was fitted on.

Three of the four cells were filed as **powered nulls** with tight bounds; that
label is withdrawn (see the correction above) and reads **INSUFFICIENT** — the
bounds are 2.1–7.1× the effect-size bar, so no effect worth acting on was ruled
out. The fourth is a nominal hit that does not survive multiplicity. **The
headline NO stands on the absence of a surviving hit, which is untouched.**

## What had to be built first

**The entire backtest corpus was one regime.** Before this study,
`backtest_trades` spanned **2025-09-12 → 2026-07-07** — 857,740 trades inside a
single 10-month window. So it was never only the *live* ledger that came from one
market state: the star ratings, the decay review's 66 cells and the `min_avg_r`
gate were all fitted there too.

The legs were therefore generated, not queried: 15m × BTCUSDT/ETHUSDT/SOLUSDT
since 2021-01-01, 57 combos (3 `smt_divergence` skipped — no `smt_pair` on 15m),
`sweep_id=89440765-864b-4c01-841b-80ee503eae20`, yielding **147,881 trades in
2021 and 143,128 in 2022**.

**Run against an isolated 321MB copy of `analytics.db`**, from a scratch working
directory — `DEFAULT_DB_PATH` is a *relative* `Path("analytics.db")`, which is
the lever. This was not fastidiousness: a long-running reader would have blocked
the 15-minute signal-watch writer, and the 08-13 soak verdict reads trailing
weekday coverage. That is the same contamination risk that defers the
`backtest_runs` heal. The live DB was untouched.

## Pre-registered cells (Phase 1 — selected on pooled avg_r, blind to the split)

38 candidate cells, all eligible (n ≥ 30 per leg); none dropped on dispersion.

| # | cell | pooled avg_r | n bull | n bear |
| --- | --- | --- | --- | --- |
| 1 | `cvd_divergence` / 15m / short | +0.1303 | 549 | 250 |
| 2 | `eqh_eql` / 15m / short | +0.1065 | 1,985 | 3,138 |
| 3 | `engulfing` / 15m / short | +0.0741 | 4,320 | 3,231 |
| 4 | `fib_golden_zone` / 15m / long | +0.0738 | 1,164 | 1,162 |

Ranked 5–8, recorded and **not** tested: `hammer_hanging_man`/short (+0.0514),
`inside_bar`/short (+0.0416), `orb`/long (+0.0406),
`morning_evening_star`/short (+0.0336).

## Phase 2 — paired regime difference

Δ = bull avg_r − bear avg_r. SE is the cell's pooled per-trade std times
`sqrt(1/n_bull + 1/n_bear)`, then multiplied by the recomputed 15m deflator
**×1.628** (3 symbols, n_eff 1.13–1.42).

| cell | bull | bear | Δ | SE | t | MDE | filed verdict | **corrected (95% CI vs ±0.05R)** |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `cvd_divergence`/short | +0.1406 | +0.1076 | +0.0331 | 0.1805 | +0.18 | ±0.506 | powered null | **INSUFFICIENT** — [−0.321, +0.387], 7.1× the bar |
| `eqh_eql`/short | +0.2031 | +0.0454 | **+0.1577** | 0.0676 | **+2.33** | ±0.189 | nominal hit | nominal hit, dies on multiplicity (unchanged) |
| `engulfing`/short | +0.0734 | +0.0751 | −0.0017 | 0.0544 | −0.03 | ±0.152 | powered null | **INSUFFICIENT** — [−0.108, +0.105], 2.1× the bar |
| `fib_golden_zone`/long | +0.0329 | +0.1148 | −0.0819 | 0.0971 | −0.84 | ±0.272 | powered null | **INSUFFICIENT** — [−0.272, +0.108], 3.8× the bar |

**MDE is a scale stamp, not a criterion.** It is a function of `SE` alone, so it
says how large an effect would have been visible and never whether a small one
was ruled out. The `MDE` column is retained for scale; the verdict column is what
changed.

## The nominal hit does not survive multiplicity

The spec's kill rule tested each cell at two-sided 95% but **did not pre-register
a correction across the four tests**. Applying one:

| cell | p | p × 4 (Bonferroni) | survives? |
| --- | --- | --- | --- |
| `eqh_eql`/short | 0.0198 | **0.0792** | **no** |
| `fib_golden_zone`/long | 0.4009 | 1.0000 | no |
| `cvd_divergence`/short | 0.8572 | 1.0000 | no |
| `engulfing`/short | 0.9761 | 1.0000 | no |

Four tests need |t| ≥ **2.498**; `eqh_eql` reaches **2.33**. Reported as **not
significant**, which is the conservative reading and the one consistent with why
this system deflates for multiplicity everywhere else. Its Δ = +0.158R is a
*candidate* worth one pre-registered re-test on an independent leg (2023–24),
never a finding to act on.

**This is an amendment to how the spec's kill rule should have read**, recorded
rather than quietly applied — see the spec's amendment log.

## Exploratory — not pre-registered, labelled as such

Across all 38 15m cells in the 2021–22 legs: median avg_r **−0.0431R**, 14 of 38
positive. The 2025–26 corpus reads median **−0.0474R**, 52 of 122 positive.

**The book is about equally unprofitable in a bull+bear pair as in the current
regime.** That is the study's most useful output: it argues the detectors'
weakness is structural, not a regime artifact, and it removes "our results are
regime-flattered" as an explanation for the negative live ledger.

## What this does not establish

- **Nothing about profitability.** 0 of 156 production cells clear DSR ≥ 0.95
  today, and this design never tested that.
- **Nothing past BTC/ETH/SOL.** Three symbols, n_eff 1.42 — the 15m panel was
  chosen because it is the best *powered*, not because it generalises.
- **Nothing on 1h/4h/1d.** Not run; their MDEs (+0.368R / +0.723R / +1.841R)
  would not have decided anything at 4h or 1d in any case.
- **⚠ Nothing on 15m either, at the size that matters.** Added 2026-08-14: the
  three withdrawn cells leave regime dependence up to ±0.11R–±0.39R untested on
  the panel this study called its best-powered. That is 2–8× the bar, so an
  effect worth acting on could sit inside every one of them.
- **Nothing about 2023–24 or 2025–26 as legs.** Two legs only; more legs multiply
  trials, which the spec's §3 shows is the binding cost.
- **No causal claim.** This measures persistence, not mechanism.

## Consequences

1. **Do not re-open "the detectors might work in a different regime."** Measured
   across a bull and a bear leg on the best-powered panel: no cell differs
   significantly, and the level is the same mediocre one. ⚠ **Corrected
   2026-08-14 — this consequence now rests on the exploratory level comparison
   below, not on the four gated cells**, whose corrected verdicts rule nothing
   out. It is a weaker foundation than filed, and it is the one that survives.
2. **The one live re-entry** is `eqh_eql`/15m/short re-tested on 2023–24 as a
   single pre-registered trial. Re-running the same four cells on a longer
   window is not independent evidence.
3. **The binding constraint is unchanged** — the next edge needs genuinely new
   data. This is one more axis that came back a NO. ⚠ **Not a "clean, powered"
   one** (corrected 2026-08-14): the NO rests on no cell surviving multiplicity,
   which is a failure to detect, not a demonstration that nothing is there.
