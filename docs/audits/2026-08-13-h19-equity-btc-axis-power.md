# H19 — equity→BTC transmission: POWER PRICED, NO-GO

**Date:** 2026-08-13.
**Thesis:** `docs/plans/thesis-inbox.md` — the @tiabtc cross-asset cluster (2026-08-03 equity-vol drains crypto, 08-05 gold leads BTC, 08-08 SPX/NDX strength transmits).
**Panel:** equity day `t` -> BTCUSDT vol-normalised daily return `t+1`, n=1,720 (2019-10 -> 2026-08).
**Sibling:** H18 (leveraged-long -> liquidation), parked 2026-08-13 for a different reason -- see below.

**Verdict: PARK. Do not design it.** Priced before any design, per
[[price-the-power-before-designing]]. Cost: one sitting, no branch, no code landed.

Candidate: the @tiabtc cross-asset cluster — 08-03 (US-equity vol drains capital out
of crypto), 08-05 (gold leads BTC), 08-08 (SPX/NDX strength transmits into BTC). It
was the **last free unrun D1 axis**, since H18 (leveraged-long → liquidation) was
priced and parked 08-13c.

## The numbers

Panel: equity day `t` → BTCUSDT vol-normalised daily return `t+1`. The `t+1` offset is
causal, not stylistic — the US close lands ~21:00 UTC, *inside* the UTC day-`t` BTC
bar, so a same-bar test measures contemporaneous overlap rather than prediction.

- **n = 1,720 paired observations**, 2019-10-09 → 2026-08-13 (BTC holds 2,501 daily
  bars; 781 have no prior equity session). Panel skew −0.895, kurtosis 16.65, std 1.139.
- **Data cost is genuinely zero, and this was tested not assumed:** `^GSPC` / `^NDX` /
  `^VIX` all fetched clean (1,913/1,913/1,915 closes) through the keyless Yahoo chart
  path `analytics/venue_fetch.py::fetch_yahoo_daily` that H15 already uses. No new
  dependency, no provider, no yfinance.

Required per-day effect to clear **DSR ≥ 0.95**, against H15's pre-registered economic
floor for this exact panel shape (`BAR_VOL = 0.02` vol-normalised units ≡ 0.382
annualised Sharpe):

| trials | mean shift needed | ann. Sharpe | vs the 0.02 economic bar |
| --- | --- | --- | --- |
| 1 | 0.0462 | 0.774 | **2.31×** |
| 2 | 0.0608 | 1.019 | 3.04× |
| 5 | 0.0798 | 1.338 | 3.99× |
| 20 | 0.0998 | 1.674 | 4.99× |

**MDE (two-sided 95%, no deflation) = 0.0538** — an effect worth trading is **invisible
by 2.69×**.

## Why this is a NO-GO, and why it is NOT H18's reason

H18 failed on *accrual*: 84 book-days, needing 3.48 Sharpe, fixable by waiting to
~2027. **This one is not fixable by waiting.** Holding `sr_variance = 1/n` (trials that
are pure noise still differ by sampling error), both the deflation benchmark and the
standard error scale as `1/√n`, giving a closed form for the n at which the
statistical bar finally reaches the economic bar:

```text
target_sr = BAR_VOL / panel_sd = 0.02 / 1.1394 = 0.01755
var       = 1 - skew*sr + ((kurt-1)/4)*sr^2          # = 1.0175 at target_sr
n         = ( [c(k) + Z*sqrt(var)] / target_sr )^2 + 1,  c(k) = expected_max_sharpe(k, 1.0)
```

| trials | n needed | in years |
| --- | --- | --- |
| 1 | 8,931 | **24** |
| 2 | 15,404 | 42 |
| 5 | 26,387 | 72 |
| 20 | 41,121 | 113 |

We have 4.7 years. **A single pre-registered trial needs 24 years of paired history**;
BTC does not have 24 years of existence, let alone of liquid perp history.

The dead band is the whole finding: an effect must exceed **0.774 annualised Sharpe**
to be *visible*, but only needs **0.382** to be *worth trading*. Anything landing
between those is undecidable here, forever. And the honest prior puts equity→BTC
squarely in that band — the relationship is real, publicly documented, and weak. If it
were a 0.77-Sharpe standalone signal it would already be the second strong edge.

**Breadth does not rescue it.** Pooling the 25-perp universe buys 1.73×, not 5× (n_eff
= 2.92, mean pairwise corr 0.315), bringing the 1-trial bar to 1.34× the economic
floor — still short, and pooling alts *changes the hypothesis*: H10 found the same
universe > majors > BTC gradient and judged it very likely XS momentum re-measured. A
pooled equity test risks re-finding the deploy core and calling it a new axis.

## The live door (unpriced, and not research)

Nothing above forbids the axis as **descriptive context** — the Brief or the F2 card
saying "SPX at ATH, BTC lagging" carries no alpha claim, no gate, and no trial cost.
Same slot ST20 was routed into. That is a product decision, not a study, and it should
never be pitched as an edge.

## ⚠ Secondary finding — H15's "powered null" is not supported as written

H15's verdict justifies **powered** solely by *"every cell cleared `MIN_N` by a wide
margin (smallest cell n=200, more than 6× the floor)"* — a **sample-size floor, not a
power calculation against the effect size it was gating on**. At H15's own n=2,055 the
MDE is ~0.049 against its 0.02 bar, so a true effect sitting exactly at the economic
bar was indistinguishable from zero. Same defect family as the `bar`-units trap and
H8's missing gate leg: a number that reads as power and isn't.

**H15's verdict direction survives** — its cells landed NO-EDGE and its two axes
disagreed in sign, which is independent evidence. What does not survive is the strength
of the claim that the axis is *"genuinely closed, not merely untested for lack of
data"*. The door is open; it needs decades, which is practically closed but is a
different sentence — and a future session quoting "powered null" as precedent for a
similar daily cross-asset panel being decisive would be misled.

Worth a tracked `CLAUDE.md` line when something else is landing anyway: **`n >> MIN_N`
is not power. Invert the bar at the real n.**

## ⚠⚠ H14 CHECKED — same conflation, MORE severe, encoded as a DECISION RULE

H14 does not merely word it loosely; it ships the conflation as a verdict mapping:

| `audit_guard` decision | H14 verdict | H14's stated meaning |
| --- | --- | --- |
| `INSUFFICIENT` (n < 30 days) | INSUFFICIENT | "genuinely underpowered" |
| `INSUFFICIENT` (**n ≥ 30 days**) | **NO-EDGE** | **"powered, but clears neither side"** |

So "powered" is *defined* as `n ≥ 30 days`. H14's test is a bootstrap CI that must clear
**±0.05R** to call a cell, so a genuinely powered null needs the CI to sit **inside**
±0.05 — that is what rules out a tradeable effect. Checked across all 10 primary
`prem_adj` cells:

- **0 of 10 CIs exclude the bar.** Every cell is statistically consistent with an effect
  exactly the size H14 was hunting.
- **Median CI half-width 0.274R = 5.5× the bar** (range 4.0×–8.2×) — resolution ~5×
  coarser than its own economic threshold, worse than H15's 2.4×.
- **8 of 10 point estimates exceed the bar**, up to 0.270R = 5.4×. `change/falling/long`
  reads −0.226R at n=148 days and prints NO-EDGE, i.e. "powered, nothing here".

**The honest label for most of these cells is INSUFFICIENT** — exactly the mapping H14
deliberately overrode, citing the ST9/H8 inversion as precedent. The override is right in
principle (a powered null must not read as "no data") and rests on a criterion that cannot
tell the two apart.

**Separately, the propagated n is the wrong n.** `CLAUDE.md` and the SoT carry H14 as
NO-EDGE *"over 849,445 trades"*. The verdict doc is honest internally — *"n in the tens to
low hundreds of days, not trades"* — but the trade count is what reached the always-paid
tier, making the negative read orders of magnitude stronger than the test's real
resolution of 44–190 days.

**Does H14's verdict direction survive? Probably, but not for the filed reason.** Three
independent supports: nothing was significant; the peg-confound decomposition (3 series)
is genuine design strength; and **the long/short sign symmetry inside a single state**
(`level/depressed` −0.227 long vs +0.244 short; `change/falling` −0.226 vs +0.270) is the
signature of the **direction** axis — already the one OOS-robust axis here — not of a
premium mechanism. What does **not** survive is "NO-EDGE on 10 cells ⇒ the axis is closed".

**⚠ The scoreboard consequence, stated carefully.** "Conditioning axes are
6-for-6-plus-one-amended" and "the binding constraint is confirmed N times" partly rest on
H14/H15 being *powered* nulls. On the evidence they are **undecidable at available n**,
not proven dead. **This does NOT reopen them as work** — the pricing above shows these
panel shapes cannot resolve an economically meaningful effect anyway, the same practical
conclusion by a more honest route. But "we tested it and there is nothing there" should
read "we ran a test that could not have seen it", and a session quoting the scoreboard as
settled evidence is being over-served.

## ⚠⚠⚠ IT IS IN PRODUCTION CODE — FOUR MODULES (checked 08-13d)

**`analytics/audit_guard.py` is CLEAN and makes no power claim.** It returns one
`INSUFFICIENT` decision with an accurate *reason* string (`n {n} < min_n {min_n}` at
`:186`, `CI [...] does not clear ±bar` at `:214`). The gap is that it **cannot express a
powered null**: the `:212` condition `ci_hi > -bar and ci_lo < bar` is true both for a
wide CI that straddles everything (cannot tell) and a narrow CI sitting inside the bar
(effect genuinely ruled out). No containment test exists anywhere in the module.

**Four consumers filled that gap with the same wrong proxy — `n ≥ a sample-size floor`
⇒ a positive null claim:**

| module | line | rule | hypothesis |
| --- | --- | --- | --- |
| `analytics/state_audit.py` | 314 | `n_supp < MIN_N (30) ? INSUFFICIENT : NO_EDGE` | H14 |
| `analytics/indicator_condition.py` | 121 | `n_supp < cfg.min_n (30) ? INSUFFICIENT : NO-EDGE` | H8 |
| `analytics/warning_audit.py` | ~232 | both cohorts `>= min_n` ⇒ **COSMETIC** | H9 |
| `analytics/weekly_path.py` | 237 | `n >= cfg.min_n (52 wks)` ⇒ the null verdict | H10 |

**Pinned by green tests that assert the wrong semantics** — `tests/test_state_audit.py:54`
(*"Underpowered is INSUFFICIENT; powered-but-null is NO-EDGE"*, asserting
`map_verdict(INSUFFICIENT, n_supp=MIN_N+1) == NO_EDGE`),
`tests/test_indicator_condition.py:325`, `tests/test_weekly_path.py:259`. A passing test
locking in a defect → [[vacuous-guard-fixture-trap]].

**The motivation was sound and is documented in each docstring:** collapsing both branches
made NO-EDGE *unreachable*, the mirror of H8's real defect where AVOID could never fire.
Right problem, wrong criterion — "never fires" was fixed by making it **always** fire.

**The fix is one predicate:** a powered null requires the CI **inside** the bar,
`ci_lo > -bar and ci_hi < bar`. Everything else that fails to clear is honestly
INSUFFICIENT. **The change direction is safe — it only ever weakens NEGATIVE claims and
can never promote a cell to BUILD/AVOID**, so H8's 25 AVOID cells and H9's `w6` REVERSE
are untouched. Measured consequence on H14: **10 NO-EDGE → 10 INSUFFICIENT**. H8/H9/H10
would each need a re-run to see which cells flip.

Not in the regression trigger set (`analytics/backtest/`, `analytics/strategies/`,
`signal_config.py`, configs, fixtures, lockfile), so `make test-regression` would not be
required — but it is production code, so it needs a branch and the public flip.

## Reproducibility

Deliberately no script is filed. The pricing ran from the session scratchpad and the
closed form above reproduces every number in ~10 lines against
`analytics.research_guards.expected_max_sharpe` — filing a gitignored script that
imports production libs and is then cited by a verdict is the exact defect recorded in
[[scratch-dir-is-for-output-not-code]]. Constants needed: n=1720, skew=−0.895,
kurt=16.652, panel_sd=1.1394, BAR_VOL=0.02, Z=Φ⁻¹(0.95).

**The pricing was blind by construction** — it computes n, the required effect and the
MDE, and never a conditional mean by equity state. Looking at the outcome before the
trial family is pre-committed is the data-mining trap the exercise exists to prevent.
