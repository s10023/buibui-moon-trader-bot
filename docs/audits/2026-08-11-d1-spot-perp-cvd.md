# D1 — Spot-perp CVD divergence sleeve: VERDICT

**Date:** 2026-08-11.
**Spec (pre-registered):** `docs/superpowers/specs/2026-08-11-d1-spot-perp-cvd-design.md`
**Plan:** `docs/superpowers/plans/2026-08-11-d1-spot-perp-cvd.md`
**Driver:** `tools/cvd_audit.py`. **Sleeve:** `analytics/cvd/`.

## Verdict: SHELVED — no trial clears the gate

**All 10 pre-registered trials FAIL** the three-leg gate
(`DSR >= 0.95 AND PBO <= 0.5 AND boot_lo > 0`). This lands on §8's third row:
*"no trial clears → SHELVED, verdict doc written, do not rebuild."*

Venue-split order flow, as constructed here, is **not** a second edge. It is also not
the *wrong-signed* edge — see "Direction" below. This is a powered null on the primary
construction, not an underpowered one: 2,530 book-days across 22 instruments.

**A shelved verdict is a result, not a failure**, and it is recorded with the same
weight as a pass so that nobody rebuilds this. Prior expectation was explicitly low
(`project_spot_perp_cvd_data_axis.md`); the value of running it was to convert a filed
hypothesis into a decided one, and that is what happened.

## Headline

| | Shape A — cross-sectional | Shape B — time-series |
| --- | --- | --- |
| Sharpe (ann) | −0.147 | +0.183 |
| max drawdown | −0.760 | −0.412 |
| n_obs (book-days) | 2,530 | 2,530 |
| **DSR** | 0.2797 | 0.5318 |
| **PBO** | 0.8488 | 0.3164 |
| **boot CI** | [−0.975, +0.636] | [−0.642, +1.005] |
| **GATE (3 legs)** | **FAIL** | **FAIL** |
| MinTRL *(stamp)* | inf | inf |
| `corr_to_xsmom` *(stamp)* | −0.043 | −0.082 |

**Benchmark, same 22 symbols, same window: XS-momentum +1.117.**

Shape A fails all three legs, and its **PBO of 0.8488 is the clearest single number
here** — the cross-sectional CVD book is overfit far past the 0.5 line. Shape B is the
more interesting failure: it *passes* PBO at 0.3164, but DSR 0.5318 and a bootstrap
lower bound of −0.642 both fail. A positive point estimate whose CI comfortably spans
zero is the textbook shape of noise.

## Every trial, gated individually

§8's wording says "**a trial** clears all three legs" while §5 defines a *trial* as a
member of the DSR/PBO family and a *shape* as a book construction. The tool gates the
two combined books. To remove the ambiguity rather than argue it, every family member
was gated separately:

| trial | Sharpe (ann) | DSR | PBO | boot_lo | gate |
| --- | --- | --- | --- | --- | --- |
| XS span8 | −0.219 | 0.2197 | 0.8488 | −1.024 | FAIL |
| XS span16 | −0.099 | 0.3242 | 0.8488 | −0.904 | FAIL |
| XS span32 | −0.087 | 0.3354 | 0.8488 | −0.930 | FAIL |
| XS span64 | −0.215 | 0.2228 | 0.8488 | −1.098 | FAIL |
| XS combined | −0.147 | 0.2797 | 0.8488 | −0.975 | FAIL |
| **TS span8** | **+0.304** | 0.6546 | 0.3164 | −0.516 | FAIL |
| TS span16 | +0.302 | 0.6526 | 0.3164 | −0.502 | FAIL |
| TS span32 | +0.158 | 0.5063 | 0.3164 | −0.653 | FAIL |
| TS span64 | −0.007 | 0.3369 | 0.3164 | −0.812 | FAIL |
| TS combined | +0.183 | 0.5318 | 0.3164 | −0.642 | FAIL |

**The verdict is identical under either reading of §8.** The best single trial is TS
span8 at +0.304 — well short on both DSR and the bootstrap. `boot_lo` is negative in
all ten.

## Direction: not an inverted edge either

Shape A is negative, but this is **not** a wrong-sign finding. Its DSR is 0.22–0.34
across trials and PBO is 0.85: the negative Sharpe is not credible in either direction.
`--invert` was **not** used to produce any number in this document. Both shapes were
evaluated under §4's pre-registered sign — long when spot leads perp — exactly as
committed before the data was seen.

## What was declared before any result

- **Sign convention** (§4): long when spot leads perp. Never changed.
- **10 trials**: 4 single spans (8/16/32/64) + combined, per shape, times 2 shapes.
- **Zero parameters swept.** `vol_span`, `fdm`, `cap`, `vol_target_annual`, the
  governor, and the cost model are all inherited from `ForecastConfig`.
- **Decision rule** (§8): the three-row table this verdict lands in.

## Disclosures

These are recorded because a verdict that hides its own caveats is worth less than one
that states them.

1. **The panel is 22 symbols, not the 23 the spec states in four places.** 25 universe
   − 2 with no spot pair (`HYPEUSDT`, `VVVUSDT`) − 1 whose spot market is halted
   (`TONUSDT`, exchangeInfo status `BREAK` while its klines still return HTTP 200,
   probed 2026-08-11). The backfill's TRADING filter excluded it and said so. The
   benchmark ran on the same 22, so the comparison is like-for-like.
2. **The XS-momentum benchmark is +1.117 on these 22, against its published +1.375 on
   25.** Quote the 1.117 for this comparison; the difference is the universe, not a
   change in that sleeve.
3. **Window: 2019-09-08 → 2026-08-11, 2,530 book-days.** Both shapes and the benchmark
   ran on byte-identical indices (verified, not assumed), so no shape was advantaged by
   a longer sample.
4. **The DSR haircut is computed per shape over that shape's 5 trials**, while the tool
   prints "10 trials declared". A pooled 10-trial haircut would be marginally stricter
   (measured elsewhere at ~0.006 DSR on a matched target). It changes nothing here —
   every trial fails by a wide margin — but the printed line should not be read as the
   haircut basis.
5. **`FORECAST_SCALAR = 10.0` is a real parameter and is absent from §7's
   "everything is inherited" table.** It was fixed a priori, never fitted. Its one
   material effect is where the ±20 cap binds: **7.6% of bars on span8 rising to 30.6%
   on span64**, so the slow legs of the family are substantially more saturated than the
   fast ones and are not strictly scale-comparable to each other.
6. **Daily bars are information-complete, and this is now measured rather than argued.**
   §3's identity — a day's taker-buy field equals the sum of that day's intraday
   taker-buy fields — was verified against a real captured BTCUSDT symbol-week (672×15m
   vs 7×1d bars, `tests/fixtures/cvd_daily_completeness_btcusdt_spot.json`).
   **Residual: exactly 0.0, bit-exact float64**, for volume, taker-buy volume, and the
   imbalance. The cheap daily ingestion path is sound; do not re-open it as a resolution
   question.
7. **Intraday sequencing is invisible to this design, so this NO does not rule it out.**
   Daily taker imbalance is a coarse flow measure. If the real information is spot
   selling *into* a perp-led rally *within* a session, nothing here could see it. That
   remains untested, and it is the one honest re-entry point for this axis.
8. **Spec §8 was amended on 2026-08-11, after the run**, to require negating returns
   rather than folding Sharpes to `abs()`. The original wording reached DSR and MinTRL
   but not the bootstrap leg, leaving the inverted-finding branch structurally
   unreachable. **The amendment did not change this verdict** — the flag was never used
   here. Details and the measured before/after are in the spec's §8 note.

## What this says about the standing constraint

The binding constraint is **unchanged**: the next edge needs genuinely new data, and
this was a genuinely new data axis that did not deliver one.

Worth recording precisely, because it is the useful part: **the two CVD books are
essentially uncorrelated with the deploy core** (−0.043 and −0.082). Had either cleared
the gate, it would have been a real second edge rather than a restatement — the
decorrelation the combine socket needs was there. What was missing was the edge itself.
So the failure is one of *signal*, not of *redundancy*, and that is a different and
more informative negative than H14's or H15's.

Order-flow-at-daily-resolution now joins the tested-and-clean-NO list. Three cross-asset
or cross-venue axes have now been tested — H14 Coinbase premium, H15 USD/JPY carry,
D1 spot-perp CVD — and all three came back negative.

## Reproduce

```bash
poetry run python -m tools.cvd_audit --db analytics.db backfill   # 45,793 rows / 22 symbols
poetry run python -m tools.cvd_audit --db analytics.db run
```

**Run `backfill` off the quarter-hour.** It takes a write lock on `analytics.db`, which
the 15-minute `buibui-signal-watch` timer also owns at `:01/:16/:31/:46`; an overlap
fails one of the two, and a failed scan is an N8-shaped ledger gap. The 2026-08-11 run
took 16 seconds at 05:21 UTC.
