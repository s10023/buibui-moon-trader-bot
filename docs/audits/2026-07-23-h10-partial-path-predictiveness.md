# H10 — Partial-path predictiveness (verdict)

**Date:** 2026-07-23
**Spec:** `docs/superpowers/specs/2026-07-23-h10-partial-path-predictiveness-design.md`
**Tool:** `make buibui-weekly-path-audit` (`tools/weekly_path_audit.py` over
`analytics/weekly_path.py`, read-only)
**Verdict: NO-EDGE across all 15 gated cells → ST6 and ST7 close (spec §8).**
A robust but sub-bar cross-sectional effect is recorded below and filed as a new
hypothesis; it is **not** ST6/ST7 reborn.

## The question

The weekly cone is conditional ON OUTCOME — a "bull week" is defined by its own
close, so the bull band sits above the unconditional one by construction and
carries zero predictive information. H10 asks the non-tautological version: does
the week's AWR-normalized path observable at hour `h` predict the return from
`h` to the week's close, **beyond drift**? The statistic is `v = sign(path[h]) ×
(remaining − causal_expanding_mean)`, collapsed to one observation per calendar
week (spec §2, §5). Gate at `h ∈ {24, 48, 72, 96, 120}`, bar 0.05 AWR/week,
Holm over the 5-hour family, early/late sign agreement (spec §6).

## Result

All 15 cells (5 hours × 3 cohorts) → **NO-EDGE**. But the cells are not a flat
null — there is a real, robust, positive effect in the universe cohort that
fails **only** the pre-committed economic bar.

### Universe (25-perp research set, n = 321 weeks)

| hour | verdict | mean v | CI | Holm p | early | late |
| --- | --- | --- | --- | --- | --- | --- |
| h24 | NO-EDGE | 0.004 | [−0.032, 0.042] | 1.000 | 0.026 | −0.017 |
| h48 | NO-EDGE | 0.011 | [−0.026, 0.048] | 1.000 | 0.020 | 0.002 |
| h72 | NO-EDGE | 0.028 | [−0.004, 0.058] | 0.286 | 0.019 | 0.036 |
| **h96** | **NO-EDGE** | **0.056** | **[0.032, 0.082]** | **0.000** | 0.065 | 0.047 |
| h120 | NO-EDGE | 0.037 | [0.019, 0.056] | 0.001 | 0.038 | 0.036 |

Family stamps: best h96 · Sharpe 0.228 · **DSR 0.972** · **PBO 0.119** ·
MinTRL 54.2 (n = 321).

### Majors (BTC/ETH/SOL, n = 312) and BTC-only (n = 292)

| cohort | h96 mean v | CI | Holm p | DSR | PBO |
| --- | --- | --- | --- | --- | --- |
| majors | 0.029 | [0.003, 0.056] | 0.394 | 0.841 | 0.502 |
| BTC only | 0.023 | [−0.015, 0.064] | 1.000 | 0.545 | 0.823 |

## Reading it

**h96 (end of Thursday) is NO-EDGE by one criterion only:** its bootstrap CI
lower bound (0.032) does not clear the 0.05 bar. On every other axis it is a
strong result — CI excludes zero, Holm p ≈ 0, DSR 0.972, PBO 0.119, both time
halves positive. Against this repo's **house** gate (DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧
n ≥ MinTRL ∧ boot_lo > 0) the universe h96 cell **passes all four**. It fails
only H10's own stricter economic bar, which was set at ~2.5× round-trip cost
a-priori. This is exactly the case spec §11 anticipated: "a NO-EDGE at this bar
does not exclude a genuine effect below ~0.10 AWR."

**In money:** at AWR ≈ 8% of price, +0.056 AWR ≈ 0.45%/week gross, ~0.25%/week
net of a 0.2% round trip (~13%/yr); at the CI lower bound, ~0.06%/week (~3%/yr).
Real, but thin — which is precisely what the bar exists to catch.

**The effect is cross-sectional, and its breadth gradient matches XS momentum.**
Universe (+0.056, robust) → majors (+0.029, not significant) → BTC-only (+0.023,
p = 1.000). Alt breadth pays; the single-instrument view is nothing. That is the
same P3 signature that made XS-solo the deploy core, so this is very likely the
**same alpha seen on a weekly horizon**, not a new edge.

**The hour curve's peak is weakly identified — do not over-read it.** The raw
argmax is h88 and the scale-normalized argmax is h115; h96 is simply the gated
hour nearest a broad, noisy hump spanning ~h84–120. The curve's terminal decay
to 0.000 at h168 is structural (remaining ≡ 0 there by construction, and the
remaining-window scale shrinks toward the week's end). The **rise** into the
hump, which runs against that shrinking scale, is the informative part; the
peak's exact location is not established.

## Decision (spec §8, pre-committed before any result)

**All five gated hours are NO-EDGE, so ST6 and ST7 close permanently.**

- **ST6** ("is this a bullish week?" framing) closes. The Brief's weekly
  language stays descriptive — it may say where the forming week sits inside the
  cone, never what that implies for its close.
- **ST7** (idea generation + invalidation) closes. The F2 card gains no
  predictive weekly framing.

This is the right call **on the merits, not merely by the rule.** ST6 and ST7
are per-symbol Brief/card products. The measured effect is cross-sectional and
vanishes on BTC alone, so it cannot support a per-symbol "is BTC a bullish week"
signal; the peak hour is not pinned; and the effect points the same direction as
XS momentum, which already harvests it. The pre-commitment and the evidence
agree.

## What is NOT discarded — one new hypothesis

The universe h96/h120 measurement is real and is recorded here rather than
thrown away. Filed to the hypothesis inbox, **low priority**, correctly scoped:

> **Weekly cross-sectional continuation — is there anything here XS-solo does
> not already capture?** A robust, sub-bar (+0.056 AWR/week, DSR 0.972, PBO
> 0.119) weekly-horizon continuation effect exists in the alt cross-section,
> with a breadth gradient identical to XS momentum. Before treating it as a new
> edge, gate it behind a correlation check against the XS book's returns; the
> honest prior is that it is the same alpha on a different horizon. This is
> **not** ST6/ST7 (which asked a per-symbol predictive-framing question and are
> closed); it is a portfolio-research question about whether the weekly horizon
> leaves XS money on the table.

## Method integrity notes

- **Read-only, additive.** The driver opens DuckDB `read_only=True`; no schema
  change; `analytics/audit_guard.py`, `analytics/research_guards/`,
  `analytics/strategies/`, `analytics/backtest/`, `analytics/signal/` untouched.
  Regression goldens unmoved.
- **No look-ahead**, verified end to end by the whole-branch review: AWR14 uses
  strictly-prior weeks, the expanding baseline advances only after a week is
  emitted, and the driver threads `now_ms` so no incomplete week enters.
- **Deterministic** (seed 12345); the result reproduced byte-identically on an
  independent re-run.
- The result is unaffected by the live-delivery state of the system — H10 runs
  on completed-week OHLCV only.

---

## ⚠ AMENDED 2026-08-13 — RE-RUN COMPLETE, 11 of 15 `NO-EDGE` WITHDRAWN

The `NO-EDGE` labels in this document were produced by a verdict map that treated
`n >= min_n` as evidence of statistical power. **A sample-size floor is not power**
— it cannot distinguish "the effect is smaller than the bar" from "the CI is
several times the bar and we cannot tell". Corrected in code on 2026-08-13: a
powered null now requires the bootstrap CI to sit strictly inside ±`bar`
(`audit_guard.CellVerdict.powered_null`).

**Re-run 2026-08-13 (SoT ST26) — verdict:
`docs/audits/2026-08-13-st26-powered-null-rerun.md`.** 11 of 15 cells move to
`INSUFFICIENT`; median CI width among movers **1.6× bar**. **Four survive as genuine
powered nulls** — universe h24 and h48, majors h120, BTC-only h120 — making H10 the
only one of the three re-run audits with any survivors.

**⚠ THE `h96` ROW BELOW IS THE IMPORTANT CORRECTION.** It is filed as `NO-EDGE` while
carrying Holm p = **0.000**, CI **[0.032, 0.082]** entirely above zero, DSR **0.972**
and PBO **0.013** — it clears all three legs of the published research gate. The old
criterion stamped "no effect worth acting on" onto the strongest positive result in
the family.

Its corrected label is `INSUFFICIENT`, and the reason matters: the CI straddles the
0.05 actionability bar, so **the effect is real and its size against the bar is
unresolved**. That is not the same statement as "we ruled an effect out", and the two
point at opposite next actions. Anyone re-opening H10 should start here.
