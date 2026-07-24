# H8 — M1 Indicator-State Conditioning Audit — Verdict (2026-07-24)

**Question.** Do the new M1 brief-indicator states — EMA stack / slope, regime,
Bollinger squeeze / %B, anchored-VWAP distance, volume-profile value-area,
price-action character, Monday-range — separate winning trades from losers in
the historical ledger? Or does the standing `conditional-edge = NO` verdict
still hold on these fresh axes?

**Tool.** `tools/indicator_condition_audit.py` (read-only) over
`analytics/indicator_condition.py`. Each trade is tagged with the M1 state that
held **as of its own entry** (causal, look-ahead mutation-guarded, full causal
slice), then a pre-committed gate is run per `(axis-state × direction)`:
`audit_guard` bootstrap-CI + Holm haircut (sign-inverted: `DISABLE → BUILD`),
a two-sample with-vs-without lift CI, and a DSR ≥ 0.95 ∧ PBO ≤ 0.5 family stamp.
`bar = ±0.05R`, `alpha = 0.05`, `min_n = 30`, seed 12345.

**Substrate.** `backtest_trades` = primary (gate-deciding); `signal_alert_outcomes`
(live) = corroboration only. 3 symbols (BTC / ETH / SOL). Tiers run: 1d (940
trades), 4h (7,720), 1h (30,405), live (3,481). The 15m tier (121,849 trades)
was deliberately deferred — daily M1 state stamped on an intraday entry is the
lowest-fidelity slice and it is the graveyard timeframe.

---

## Verdict

**`conditional-edge = NO` holds on the orthogonal indicator-*character* axes.**
Conditioning on EMA stack / slope, regime, or price-action character does **not**
add information beyond the trade's direction — every gate-grade cell those axes
produce is short-side and re-expresses the short base rate, not the state.

**One real, powered, cross-timeframe, live-corroborated refinement surfaced —
but it is DIRECTION × price-location, not indicator character:**

> **Short signals continue when price is already extended *below* its reference
> levels, and lose when they fire *above* them.**

This is a **live-gate hypothesis (REFINE), not a BUILD**. It is not a new
detector and must not be deployed as-is — see the skeptical read below.

---

## The one real signal — short × below-reference (continuation)

`vwap_weekly = below`, direction `short`. `with` = shorts entered while price is
below the weekly anchored VWAP; `without` = shorts entered while price is *above*
it (same direction, same population, different state):

| tier | n (with) | avg_r with | avg_r without (short, above VWAP) | lift [95% CI] |
| --- | --- | --- | --- | --- |
| 1d | 276 | +0.797 | −0.280 | +1.077 [+0.77, +1.38] |
| 4h | 2,299 | +0.485 | −0.425 | +0.910 [+0.80, +1.02] |
| 1h | 8,701 | +0.445 | −0.397 | +0.843 [+0.79, +0.90] |
| live | 1,358 | +0.381 | −0.342 | +0.723 [+0.63, +0.81] |

The same effect repeats across **five collinear axes**, every one short-side:
`vwap_monthly/below`, `vp_value_area/below`, `monday_range/below`, `bb_pctb/low`
(price near/below lower band). All BUILD on 4h + 1h; the VWAP pair also BUILD
live. These are ~**one signal measured five ways**.

**Money translation.** A short below weekly VWAP realizes ≈ **+0.4 to +0.5R**;
the same strategy shorting *above* weekly VWAP realizes ≈ **−0.4R** — a ~**0.8–0.9R
swing per trade** from a pure price-location gate on the short side. On the **live
ledger** (real fired alerts, out-of-sample) the below-VWAP short cohort ran
**+0.38R/alert vs −0.34R/alert** above it. The swing is real OOS, not a backtest
artifact.

**Not tautological.** "Price below its VWAP at entry" is a causal, as-of-entry
state; a short's R is positive only if price *keeps* falling. The state does not
mechanically fix the outcome — this is an empirical **continuation / momentum**
effect, the same family as the deployed XS-solo cross-sectional momentum edge.

---

## Why this is NOT a new edge (the skeptical read)

1. **Collinearity.** below-weekly-VWAP ≈ below-monthly-VWAP ≈ below-value-area ≈
   below-Monday-range ≈ low-%B — all encode "price extended down." The per-axis
   `DSR ≈ 1.0 / PBO = 0.0` stamps treat them as independent trials and therefore
   **overstate** the evidence. Effective independent findings ≈ 1, not 5–6.
2. **It's DIRECTION, not character.** The trend/character axes
   (`ema_stack/bullish/short`, `ema_slope/rising/short`, `regime/high_vol/short`,
   `pa_char/grind_down/short`) fire BUILD **only** on the short side, and their
   `without` slice (other short states) is **still positive** (e.g. 4h
   `ema_stack/bullish/short` +0.571 vs without +0.094) — so they mostly re-flag
   the favorable short base rate, not orthogonal information. The tell: at 4h,
   *both* "short into a bullish EMA stack" *and* "short below references" register
   BUILD — mutually exclusive setups. What the gate is really catching is the
   dispersion **within the short direction**, which almost any partition exposes.
3. **Non-iid, concentrated.** 3 correlated majors with overlapping holds →
   effective N ≪ nominal N; the DSR / PBO stamps are optimistic.
4. **Long side is empty.** No long axis-state clears the gate (a couple of
   tiny-n per-strategy-type diagnostic cells aside) — confirming the lever is the
   short asymmetry, not the indicator panel.

---

## Relationship to prior verdicts

- **Confirms** `project_conditional_edge_test` (no new orthogonal axis; the only
  robust conditioning axis is DIRECTION) and **sharpens**
  `project_direction_axis_hard_flip` — it says *when* shorts work: below reference.
- **Coheres with the deploy core (XS-solo):** short-below-reference continuation
  is a momentum expression, the same edge family the cross-sectional sleeve harvests.
- **Refines** the ST1 reference-level proximity audit: there the underpowered-
  positive was *long* sweep-reclaim at PDL/PWL; here the powered leg is *short*
  continuation below reference — the mirror side.

---

## Caveats

- Backtest is gate-deciding; live corroborates the **one** lever but is thin and
  irregularly sampled (the live ledger is hand-run, not a clean OOS clock — see
  `project_live_system_dark_since_2026-06-24`).
- 15m tier deferred; the effect is unverified on the highest-frequency slice.
- M1 state is re-derived from the **full** causal OHLCV history up to entry, which
  differs slightly from production's bounded brief fetch (EMA-200 warmup) — a
  second-order fidelity gap, noted not resolved.
- Collinear axis family → the reported DSR / PBO are optimistic.
- Unconditional entry population (no combo / quality / regime pre-filter).

---

## Next step

File a **live-gate hypothesis** (a suppression lever like F8 / ADR, *not* a
detector): *on short signals, prefer / require price below weekly VWAP (or below
value-area); suppress shorts firing above it.* Before any deploy it must be
(a) collapsed to a **single** price-location axis, (b) re-tested on an **OOS
holdout**, and (c) gated on the live golden-signal loop reaching n ≥ 30 per cell
— which is blocked on the dark live ledger (ST3 / VPS). Until then it is a
**descriptive prior only**.
