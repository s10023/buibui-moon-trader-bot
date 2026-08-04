# H8 — M1 Indicator-State Conditioning Audit — Verdict (2026-07-24, amended 2026-08-04)

> **AMENDED 2026-08-04.** The verdict published on 2026-07-24 was produced by a
> build in which the **AVOID verdict was structurally unreachable** and one
> pre-registered gate leg was never implemented. PR #546 fixed four defects in
> `analytics/indicator_condition.py`; this document now carries the **re-run**,
> not the original conclusion. The headline claim *"every gate-grade cell those
> axes produce is short-side"* is **withdrawn** — see
> [What is withdrawn](#what-is-withdrawn-from-the-2026-07-24-text). The original
> text remains in git history at `bf646af~`.

**Question.** Do the M1 brief-indicator states — EMA stack / slope, regime,
Bollinger squeeze / %B, anchored-VWAP distance, volume-profile value-area,
price-action character, Monday-range — separate winning trades from losers in
the historical ledger? Or does the standing `conditional-edge = NO` verdict
still hold on these fresh axes?

**Tool.** `tools/indicator_condition_audit.py` (read-only) over
`analytics/indicator_condition.py`. Each trade is tagged with the M1 state that
held **as of its own entry** (causal, look-ahead mutation-guarded, full causal
slice), then a **four-leg** pre-committed gate runs per `(axis-state ×
direction)`:

1. `audit_guard` block-bootstrap CI clearing the ±0.05R bar **and** a
   Holm-adjusted `p < α` over the pre-registered family (sign-inverted:
   `DISABLE → BUILD`, `ENABLE → AVOID`);
2. a two-sample with-vs-without lift CI excluding 0, same sign as leg 1;
3. `DSR ≥ 0.95 ∧ PBO ≤ 0.5` over the axis family;
4. **`n_with ≥ MinTRL(0.95)`** — design-doc §7's fourth leg, absent from the code
   until PR #546.

`bar = ±0.05R`, `alpha = 0.05`, `min_n = 30`, `n_boot = 2000`, seed 12345.
**Run one tier at a time** (`--timeframes`): pooling tiers merges cells *and*
enlarges the shared Holm family, changing every other tier's haircut.

**Substrate.** `backtest_trades` = primary (gate-deciding); `signal_alert_outcomes`
(live) = corroboration only. 3 symbols (BTC / ETH / SOL). Tiers run: 1d (940
trades), 4h (7,720), 1h (30,405), live (4,034). **The three backtest tier sizes
are identical to the 2026-07-24 run**, so the comparison below is like-for-like —
the verdict changed because the gate changed, not because the data did. The live
ledger grew 3,481 → 4,034 rows over the intervening 11 days. The 15m tier
(121,849 trades) remains deliberately deferred.

---

## What changed, and why the original could not report this

Four defects, fixed in PR #546. Each was only visible once the previous was
fixed.

| # | Defect | Effect on the published verdict |
| --- | --- | --- |
| 1 | `_family_dsr` fed the **signed** Sharpe to `deflated_sharpe_ratio` | A reliably-negative cell scored DSR **0.0000**; its mirror-image positive cell **0.9980**. AVOID requires `dsr ≥ 0.95` ⇒ **AVOID was essentially unreachable** |
| 2 | `_map_verdict` collapsed both of `audit_guard`'s INSUFFICIENT branches | Lost the distinction between the two under-powered causes |
| 3 | `evaluate_conditions:391` short-circuited before `_map_verdict` | Published `lift = 0.000` / CI `[0, 0]` for powered cells that **do** have a measured lift. The branch the prior handoff named was **dead code** |
| 4 | The spec-registered `n ≥ MinTRL(0.95)` leg was **never implemented** | Every published BUILD cell cleared a gate missing a pre-committed condition. **Runs opposite to 1–3: those made AVOID unreachable, this made BUILD too easy** |

**Magnitude Sharpe is load-bearing in two places.** `deflated_sharpe_ratio` and
`min_track_record_length` both answer *"is this **positive** performance
credible"* — MinTRL of a negative Sharpe against target 0 is `inf` (−0.35 → `inf`
signed, `24.4` folded). Had defect 4 been fixed without folding to magnitude, the
new leg would have re-blocked AVOID through a different door.

**Disclosed cost of the fold:** folding to magnitude shrinks trial dispersion in
a mixed-sign family, so the gate becomes marginally **more permissive** than the
signed form. The bias runs toward more passes, never fewer.

---

## Verdict (amended)

**On indicator *character*, `conditional-edge = NO` still holds.** EMA stack /
slope, regime, Bollinger squeeze and price-action character do not carry
orthogonal information — see the restraint section for why their cells are
direction dispersion rather than state.

**On price *location*, the axis is real, powered, and — this is the correction —
two-sided:**

> **Signals continue in the direction price is already extended.** Shorts
> entered *below* their reference levels win and shorts entered *above* them
> lose; longs entered *below* those same levels lose. It is **one continuation
> effect**, and its long half was structurally unreportable in the original
> build.

This remains a **live-gate hypothesis (REFINE), not a deployable BUILD.**

### Gate-grade cell counts (pooled, gate-deciding)

| tier | AVOID | BUILD | NO-EDGE | INSUFFICIENT | cells |
| --- | --- | --- | --- | --- | --- |
| 1d | 1 (1 long) | 6 | 47 | 0 | 54 |
| 4h | 11 (8 long) | 12 | 31 | 0 | 54 |
| 1h | 13 (8 long) | 12 | 29 | 0 | 54 |
| **backtest total** | **25 (17 long)** | **30 (3 long)** | **107** | **0** | **162** |
| live | 10 (4 long) | 4 | 36 | 0 | 50 |
| *published 2026-07-24* | **0** | 6 | — | — | — |

**The published table had zero AVOID cells at every tier.** The re-run finds
**25** across the backtest tiers, **17 of them long-side** — the side the
original concluded was empty. It also finds 3 long-side BUILD cells
(`bb_squeeze/squeeze/long` +0.286 @4h and +0.227 @1h, `regime/range/long` +0.269
@1h) where the original reported none.

`INSUFFICIENT = 0` everywhere: at `min_n = 30` every pooled cell is powered, so
defect 2's fix changes no cell in this table. It was still worth fixing (it
governs future runs at tighter `min_n`), and saying so is more honest than
implying all four fixes moved the result.

### The four quadrants — `vwap_weekly`, the cleanest axis

The original saw only the top row of this table and read it as a short-side
finding. All four quadrants tell one story.

| tier | below / short | below / long | above / short | above / long |
| --- | --- | --- | --- | --- |
| 1d | **BUILD** +0.797 | **AVOID** −0.334 | NO-EDGE −0.280 | NO-EDGE +0.149 |
| 4h | **BUILD** +0.485 | **AVOID** −0.439 | **AVOID** −0.425 | NO-EDGE +0.111 |
| 1h | **BUILD** +0.445 | **AVOID** −0.439 | **AVOID** −0.397 | NO-EDGE +0.145 |
| live | **BUILD** +0.286 | **AVOID** −0.654 | **AVOID** −0.322 | NO-EDGE +0.066 |

The `above/long` quadrant is **positive at every tier but never gate-grade** — it
fails DSR (+0.701 / +0.117 / +0.497 / +0.000) and MinTRL (370 / 705 / 477 / 1,264
observations required).
That asymmetry is itself informative: the effect is *strong* where price is
extended down and *weak* where it is extended up.

### Money translation

**Headline:** a pure price-location filter is worth roughly a **0.9R swing per
trade** on the short side and a **0.55R swing** on the long side, at 4h.

**Metric:** at 4h a short below weekly VWAP realizes **+0.485R** versus
**−0.425R** above it (lift +0.910, CI [+0.802, +1.018]). A long below weekly
VWAP realizes **−0.439R** versus **+0.111R** above it (lift −0.550, CI [−0.647,
−0.451]).

**Money:** on a 1%-risk-per-trade sizing, that short-side swing is ≈ **0.9% of
equity per trade** separating the two states — which is the entire realistic
edge budget for a strategy of this class.

**Backtest vs live:** live reproduces **the same cells, same signs**, and more
extremely on the long side (`vwap_weekly/below/long` **−0.654** live vs −0.439
backtest at 4h; `vwap_weekly/above/short` **−0.322** live vs −0.425). Live is
corroboration, not independent confirmation — see restraint point 3.

---

## Why this is still NOT a new edge (restraint)

The original's skeptical read was right, and the re-run makes one of its points
**stronger** and adds a new one. Nothing here should be read as "H8 found an
edge after all."

1. **Mirror double-counting (NEW — the amended count's biggest caveat).** Four
   axes are **binary**: `vwap_weekly`, `vwap_monthly`, `bb_squeeze`,
   `ema_slope`. For a binary axis the with/without split is *symmetric* —
   `below/long` and `above/long` are the **same two-sample test with the sign
   flipped**. At 1d, `below/long` reads with −0.334 / without +0.149 and
   `above/long` reads with +0.149 / without −0.334; the lift is exactly negated
   (−0.483 vs +0.483). **7 of the 25 backtest AVOID cells are exact mirrors of a
   BUILD cell on the same axis and direction** (0 at 1d, 3 at 4h, 4 at 1h), as
   are **2 of the 10 live** ones. Two of the three long-side BUILD cells are the
   same thing in reverse (`bb_squeeze/squeeze/long` is the mirror of
   `bb_squeeze/no_squeeze/long` AVOID at both 4h and 1h), leaving
   `regime/range/long` as the only non-mirror long BUILD in the whole run. The
   raw cell count therefore overstates the number of findings, and any future
   report of this table must say so.
2. **Collinearity.** below-weekly-VWAP ≈ below-monthly-VWAP ≈ below-value-area ≈
   below-Monday-range ≈ low-%B — all encode "price extended down." The per-axis
   `DSR ≈ 1.0 / PBO = 0.0` stamps treat them as independent trials and therefore
   **overstate** the evidence. Effective independent findings ≈ **1**, not 25.
3. **Live is not an independent sample.** Live alerts come from the same
   detectors that were backtested, so live agreement is *expected*, not
   confirmatory. The live ledger is additionally the known session-skewed
   ~35%-delivery sample (hand-run daemon, no cron). **One effect, corroborated on
   a biased substrate — not a set of independent OOS wins.**
4. **Character axes still look like direction dispersion.** The tell survives the
   fix and is now sharper: at 4h *both* `ema_stack/bullish/short` (**BUILD**,
   +0.571) and `vwap_weekly/below/short` (**BUILD**, +0.485) clear the gate —
   **mutually exclusive setups**. What the gate catches is the dispersion *within*
   a direction, which almost any partition exposes.
5. **Non-iid, concentrated.** 3 correlated majors with overlapping holds →
   effective N ≪ nominal N; the DSR / PBO stamps are optimistic.
6. **`PBO = +0.000` nearly everywhere** is as suspiciously clean as the original
   flagged. Read it as "well-powered under the block model," not "risk-free."

---

## What is withdrawn from the 2026-07-24 text

| Original claim | Status | Evidence |
| --- | --- | --- |
| *"every gate-grade cell those axes produce is short-side"* | **WITHDRAWN** | 17 long-side AVOID cells across the backtest tiers, plus 3 long-side BUILD cells |
| *"Long side is empty. No long axis-state clears the gate"* | **WITHDRAWN** | Same as above; the long side was unreachable, not empty |
| *"the only robust conditioning axis is DIRECTION"* | **NARROWED** | Price-location conditions **within** both directions; direction alone does not express it |
| *"It's DIRECTION, not character"* (skeptical read #2) | **RE-ARGUED, same conclusion** | The original inferred this from character axes firing only short-side — that premise is gone. The conclusion now rests on point 4 above (mutually exclusive BUILD cells) |
| Verdict framing as a one-sided short refinement | **REPLACED** | One two-sided continuation effect |
| Collinearity / non-iid / optimistic-stamp caveats | **STAND, strengthened** | Mirror double-counting added |

---

## What still stands

- **`conditional-edge = NO` on indicator character.** Unchanged.
- **This is not deployable as-is.** Still a suppression-lever hypothesis, not a
  detector, and still requires collapsing to a single price-location axis.
- **The 15m tier is unverified.**
- Every caveat in the Caveats section below.

---

## Relationship to prior verdicts

- **Amends the conditioning scoreboard.** The "conditioning axes are 0-for-6"
  count was carried on the strength of H8's original NO. H8's BUILD half stands;
  its negative-direction half now reports a real, two-sided price-location
  effect. The count reads **5-for-5-plus-one-amended** — and the amended one is
  *not* a clean win, for all six restraint reasons above.
- **Sharpens** `project_direction_axis_hard_flip`: it says *when* shorts work
  (below reference) **and** when longs fail (also below reference).
- **Coheres with the deploy core (XS-solo):** continuation in the direction of
  existing extension is the same edge family the cross-sectional sleeve harvests.
- **Refines** the ST1 reference-level proximity audit: there the underpowered
  positive was *long* sweep-reclaim at PDL/PWL; here the powered legs are short
  continuation below reference and long *deterioration* below it.

---

## Caveats

- Backtest is gate-deciding; live corroborates but is thin, irregularly sampled,
  and **not independent of the backtest** (`project_live_system_dark_since_2026-06-24`).
- 15m tier deferred; the effect is unverified on the highest-frequency slice.
- M1 state is re-derived from the **full** causal OHLCV history up to entry, which
  differs slightly from production's bounded brief fetch (EMA-200 warmup) — a
  second-order fidelity gap, noted not resolved.
- Collinear axis family + binary-axis mirroring → the reported cell counts and
  DSR / PBO are optimistic.
- Unconditional entry population (no combo / quality / regime pre-filter).
- The DSR/MinTRL magnitude fold makes the gate marginally more permissive.

---

## Next step

File a **live-gate hypothesis** (a suppression lever like F8 / ADR, *not* a
detector): *prefer shorts below weekly VWAP; suppress shorts firing above it;
suppress longs firing below it.* Before any deploy it must be (a) collapsed to a
**single** price-location axis, (b) re-tested on an **OOS holdout**, and (c)
gated on the live golden-signal loop reaching n ≥ 30 per cell — which is blocked
on the dark live ledger (ST3 / VPS). Until then it is a **descriptive prior
only**.

**Reproduce:** run one tier at a time, never pooled.

```bash
poetry run python tools/indicator_condition_audit.py --source backtest --timeframes 4h
poetry run python tools/indicator_condition_audit.py --source live
```
