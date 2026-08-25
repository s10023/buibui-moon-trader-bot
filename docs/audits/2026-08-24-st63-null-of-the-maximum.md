# ST63 — the null of the maximum: why the top cell is not a lead

**Date:** 2026-08-24
**Tool:** permutation, read-only, reproducing `tools/occurrence_dump.py::summarize` exactly
**Substrate:** `docs/plans/scratch/st63-occurrence-dump-1d-4h-2026-08-24.csv` — 8,887 fires,
1,766 cells at all `n`, 704 at `n >= 30`
**Companion:** `2026-08-24-st63-occurrence-dump-power-pricing.md`, which closed the same
variant on power. This one replaces its stated REASON, not its answer.

## Verdict

The gated variant of ST63 **stays closed, and the reason it was closed for is wrong.** The
to-do row said the top cells are "what a null produces at this cell count". That is false
under the null it implies: permuting `pnl_r` within each `(tf, strategy, direction)` group —
which holds every base rate and every cell's `n` fixed — puts the maximum of 704 cells at
**+0.7353 on average and +1.0239 at p95**, against an observed **+1.2431**. That is
**p = 0.003**. The top cell is not what an i.i.d. null produces.

What actually closes it is **temporal clustering**, which the row never names. Under a
circular block permutation in `entry_time` order, `p` crosses 0.05 between a three- and a
five-trade block, and the empirical block length in the top cell's own group is ~5. So the
result turns entirely on whether three to five consecutive fires of one strategy share
outcome structure — on 4h bars in a persistent price-action state they plainly do, and the
top cell is unremarkable once that is admitted. **Do not build a gated variant, and do not
re-open it on the strength of a large top-of-table `delta`.**

**The correction matters more than the verdict.** A future session that re-derives the filed
cell-count argument *correctly* — permuting, matching the trial count, doing exactly what the
row asks — lands on p=0.003 and concludes the OPPOSITE. The careful move gets the wrong
answer because the filed reason names the wrong mechanism.

## The measurement

Observed maximum: **+1.2431 R**, at `4h · morning_evening_star · short · pa_char=grind_down`,
`n = 61`.

| block L | 1 | 3 | 5 | 10 | 20 | 40 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| p(observed) | 0.003 | 0.040 | 0.084 | 0.178 | 0.250 | 0.288 |

`L = 1` is the i.i.d. null the row implies. Every larger `L` admits that consecutive fires
are not independent draws.

## The block length is measured, not assumed

In the top cell's own group (`4h morning_evening_star short`, `n = 299`, 61 in-state) the
indicator forms **25 runs against 98.1 expected** under random arrangement —
Wald-Wolfowitz **z = −13.06** — and the **mean in-state run length is 5.08 trades**.

So the empirical `L` is ~5, which is where `p` crosses out of significance. It was not tuned
until the answer changed: the run statistic is a property of the indicator series and was
computed before the p-vs-L curve was read.

## Every axis is persistent, so the i.i.d. null is wrong for the WHOLE dump

`P(next fire in the same state)`:

| axis | p | axis | p |
| --- | ---: | --- | ---: |
| `ema_slope` | 0.966 | `vp_value_area` | 0.776 |
| `ema_stack` | 0.885 | `pa_char` | 0.775 |
| `bb_squeeze` | 0.866 | `vwap_weekly` | 0.757 |
| `vwap_monthly` | 0.842 | `bb_pctb` | 0.751 |
| `regime` | 0.778 | `monday_range` | 0.628 |

Outcome autocorrelation on `pnl_r` is **+0.354 at lag 1**, decaying to +0.087 by lag 5.

⇒ **Any t-stat or DSR computed per-cell on this table as if fires were independent is
inflated.** This is the deflator `AGENTS.md` already carries for the cross-section — pooling
symbol-days across 25 perps inflates every t ~2.92× because they carry `n_eff` 2.92 — running
along the TIME axis instead. The cross-sectional form is written down; this one was not.

## Limits

- **5.08 is an average over one cell** and the p-vs-L curve is steep (0.040 → 0.178 between
  `L=3` and `L=10`). Read this as "not significant once clustering is admitted", never as
  "p = 0.084" to three decimals.
- **The statistic tested is the MAXIMUM.** That is the right null for "is the top cell a
  lead" and the wrong one for any specific pre-registered cell, which faces its own null plus
  the full three-leg gate.
- **Nothing here licenses a construction.** A cell surviving a clustering-aware null would
  still owe `DSR >= 0.95 ∧ PBO <= 0.5 ∧ boot_lo > 0`, and the companion audit prices that as
  unreachable at this dump's trial count.
- The permutation reproduces `summarize` exactly (1,766 cells at all `n`, 704 at `n >= 30`),
  so the cell population is the shipped one rather than a re-derivation of it.

Full working: `docs/plans/scratch/2026-08-24-st63-null-of-the-maximum.md` (gitignored).
