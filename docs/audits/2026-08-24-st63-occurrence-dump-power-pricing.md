# ST63 — pricing the occurrence dump's gated variant

**Date:** 2026-08-24
**Tool:** `tools/distil_power.py` (G3 power gate), inputs measured from
`tools/occurrence_dump.py` output, not estimated
**Substrate:** `backtest_trades` (deduped), 1d + 4h, 8,887 fires, 19 strategies, 3 symbols,
span 2025-09-12 → 2026-08-17

## Verdict

The gated variant of ST63 is **UNREACHABLE, and this is now measured rather than inferred**.
A search over the occurrence dump's per-`(tf, strategy, direction, axis, state)` cells cannot
clear `DSR >= 0.95` at any trial count the dump actually produces, because **the best cell in
the whole family sits below the bar that family imposes**. Do not build a gated variant, and do
not re-open it on the strength of a large top-of-table `delta`. The dump remains a legitimate
diagnostic; only the search over it is closed.

The decisive comparison, at the honest trial count after a dispersion floor:

| | value |
| --- | --- |
| required Sharpe (702 trials) | **1.2589** |
| required Sharpe (1,766 raw trials) | **1.3504** |
| **best observed cell Sharpe in the family** | **1.1569** (`4h smt_divergence short ema_stack=mixed`, n=55) |
| shortfall of the best cell | **+0.1020** floored · **+0.1935** raw |

The required *effect* is **+2.1660 R per trade** at 702 trials and **+2.3233 R** at 1,766,
against a pooled live book of **−0.1665R**. That is more than an order of magnitude in the
opposite direction, so the conclusion does not depend on the floor choice.

⚠ **The tool printed `REACHABLE` and that word must not be quoted as the result.** Reachability
is decided against `corpus best`, which is the XS deploy core — a different sleeve. `AGENTS.md`
already carries this warning from the CVD G3 pricing; this run is its second confirmed sighting.
**Read the required Sharpe, never the verdict word.**

## Method

Cells were rebuilt from the dump CSV exactly as `occurrence_dump.summarize` builds them, then
each cell's per-trade Sharpe was computed as `mean(pnl_r) / sd(pnl_r)`. `sr_variance` is the
variance of those Sharpes across the trial family — the quantity DSR deflates by.

Inputs passed to `distil_power.py`: `--units per_trade --n-obs 78 --sr-variance 0.102192
--sd 1.7205`, with `--n-trials` at 1, 702 and 1,766. `n_obs` is the median cell n after the
floor; `sd` is the pooled `pnl_r` standard deviation. No deflator was applied — see Limits.

## Side finding: the missing dispersion floor is not latent at this scale

`AGENTS.md` records that `MIN_DSR_TRADES` **gates count, not dispersion** — `_sharpe` rejects
only `sd == 0.0` exactly — and files it as *"a latent fragility rather than a cause"*. At 1,766
cells it is not latent:

- **105 of 1,766 cells (5.9%)** have `sd < 0.05` at `n >= 2`.
- Un-floored, the trial family's `sr_variance` is **9.79e26**. Floored at `n>=30, sd>=0.05` it
  is **0.1022** — twenty-seven orders of magnitude apart. Any DSR computed over the raw family
  is meaningless, not merely noisy.
- The floor is insensitive between `sd>=0.05` and `sd>=0.10` (both leave 702 cells), so the
  choice is not doing the work.

**This is an actionable finding and it is the one thing here worth acting on:** a dispersion
floor beside the count floor. It was A/B'd against production before and did not move production
DSR, which is why it was left out — but that test was run on a small family, and the effect
scales with family size.

## The `bos/1d/long` cell is now VERIFIED post-fix

`AGENTS.md` names `bos/1d/long` (36 trades, all ≈ −1.0076R, sd 0.0022, Sharpe −461) as the worked
example of the dispersion defect, and explicitly flags that **its numbers pre-date the 2026-08-18
`bos` causality fix and nothing has re-run them**, so the named cell was unverified.

This run re-derives it on post-fix data. The signature survives:

| cell | n | mean | sd | Sharpe |
| --- | --- | --- | --- | --- |
| `1d bos long bb_squeeze=no_squeeze` | 33 | −1.00748 | 0.00227 | −444.70 |
| `1d bos long vwap_monthly=above` | 30 | −1.00780 | 0.00214 | −470.57 |
| `1d bos long regime=trend` | 29 | −1.00742 | 0.00199 | −505.72 |

The mechanism is unchanged and the magnitude bracket (−445 to −506) contains the filed −461.
**That claim can stop being labelled unverified.**

## Do not carry the trial-count multiplier between families

`AGENTS.md` quotes 1 → 320 trials moving the bar **21×**, measured on the multi-regime panel.
Measured *here*, 1 → 1,766 trials moves the required effect **+0.3254R → +2.3233R = 7.14×**.
Both are correct on their own family and neither transfers: the multiplier depends on
`sr_variance`, which is a property of the family being searched. This is the same class as the
n_eff/deflator crossing that `AGENTS.md` corrected on 2026-08-21 — **run the tool, do not quote a
sibling's number.**

## Limits

- **No deflator was applied.** Cells pool 3 symbols, whose returns are correlated, so the true
  effective n is below the declared n and the real bar is therefore **higher** than reported.
  The verdict is unreachability, so this omission is conservative — it can only strengthen the
  conclusion, never reverse it. A deflated re-run would need `effective_independent_series` on
  this panel, never a deflator quoted from another.
- **15m and 1h are not in this panel.** 15m costs ~4h on this tagging path and was deferred.
  Adding tiers raises the trial count, which moves the bar the same direction — so the verdict
  is not at risk from the missing tiers, though the exact numbers would change.
- `n_obs = 78` is a median; cells range from 30 to 891 after the floor. The bar falls slowly in
  n and steeply in trial count, which is the whole point.
