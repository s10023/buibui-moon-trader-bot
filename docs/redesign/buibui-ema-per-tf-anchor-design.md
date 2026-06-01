# Per-TF / multi-anchor EMA for trend detection — design draft

> **Superseded (2026-06-01)** by
> `docs/superpowers/specs/2026-06-01-asymmetric-f8-htf-ema-gate-design.md`. The
> ablation this draft deferred (now `tools/htf_ema_gate_replay.py`) showed F8 is
> directionally inverted — the win is a direction-/family-scoped gate, not anchor
> laddering. Dials A/B below are retained as the deferred layer-on. Kept for the
> reasoning trail.
>
> Status: **DRAFT — no code yet.** Exploration of "use a different EMA per
> timeframe instead of one fixed EMA-50." Captures current state, the gap, the
> dials, hard caveats, and a deferred validation plan. Pairs with
> `buibui-redesign.md` §6 (regime) + the F8 HTF EMA gate.

## The question being explored

Today the EMA used for trend/bias is effectively **fixed-period (EMA-50)** with
only a per-*strategy* anchor timeframe (4h or 1d). Should the EMA **period**
and/or **anchor TF** scale with the **signal's own timeframe** instead of being
flat?

Intuition: a 4h EMA-50 is "fast/twitchy" relative to a 1d swing signal but
"slow/laggy" relative to a 15m scalp. The trend reference arguably should be
*proportional to the signal horizon*, not one global setting.

## Current state (what the code actually does — verified 2026-05-26)

1. **F8 HTF EMA bias gate** — `[bias.htf_ema]` in `config/strategy_params.toml`,
   logic in `analytics/signal/gates.py::_apply_htf_ema_gate`, resolver
   `analytics/signal_config.py::BiasConfig.htf_ema_anchor(strategy)`:
   - **Default anchor**: `tf=4h, period=50, slope_lookback=10`, deadband 0.3%,
     **mode=hard** (drops opposing-direction signals live).
   - **Per-strategy 1d overrides**: `ema, smt_divergence, cvd_divergence, orb,
     eqh_eql, marubozu` → `tf=1d, period=50`.
   - **Anchor is keyed by strategy ONLY.** No signal-TF dimension. A 15m and a
     4h signal for the same strategy check the identical HTF EMA. **Period is
     always 50; slope_lookback always 10.**

2. **Regime classifier** — `analytics/regime.py::classify_series`,
   `[bias.regime]`: EMA-**50** hardcoded (`compute_ema(close, 50)`), classified
   off **4h** candles (`htf_tf="4h"`), `_SLOPE_LOOKBACK=10`. Volatility axis uses
   ATR-14 percentile (`atr_p80`). Fully fixed periods.

3. **EMA pullback detector** — `analytics/strategies/ema.py`: `fast=20/slow=50`.
   **Disabled / evidence-killed** (WFO falsified 2026-05-06; both v2 rescue
   hypotheses falsified). Context-indicator only per `buibui-redesign.md:77`. Out
   of scope here — this draft is about EMA-as-trend-context, not reviving it.

## The gap — two independent dials

**Dial A — Anchor-TF ladder.** Make the HTF anchor a function of the *signal's*
TF: e.g. 15m→1h, 1h→4h, 4h→1d, 1d→1w. The bias reference scales with the signal
horizon. Plugs in by extending `htf_ema_anchor(strategy)` →
`htf_ema_anchor(strategy, signal_tf)` plus a `[bias.htf_ema.per_tf]` ladder table;
same idea for `classify_series` per classification TF.

**Dial B — Period / cross variation.** Vary the EMA *period* per TF (e.g.
20/50/200 ladder), **or** use golden/death **cross** (fast vs slow EMA crossover)
as trend-direction instead of single-EMA slope sign. `HtfEmaAnchor` already
carries `period`; add per-TF periods. Cross logic is new (today F8 uses slope
sign, not a fast/slow cross).

A and B are orthogonal — can ship A alone, B alone, or both.

## Hard caveats (why this is measure-before-build, not a quick win)

1. **The regime type→regime mapping is empirically INVERTED.** 2026-05-10 replay
   (`tools/regime_gate_replay.py`, ~708K trades): `bos`/`ema`/`fib_golden_zone`
   all perform *better* in `range` than `trend`. Adding more EMA-trend nuance on
   top of a mapping known to be wrong risks polishing a broken axis. **Resolve
   the mapping question (or scope A/B to F8 only, which is direction not
   type-routing) before trusting any trend refinement.**
2. **F8 is in hard mode.** Any anchor/period change directly moves live alert
   volume + direction. **Ship soft (log-only) first; observe ≥2 weeks.**
3. **Period sweeps are exactly what WFO punished the EMA detector for.** Use WFO
   (IS/OOS split), not full-dataset sweep, to trust any per-TF period — otherwise
   it's overfitting.

## Deferred validation plan (no code now — for the build session)

1. **Instrument (soft):** for each signal, log what the F8 anchor *would* say
   under candidate ladders A/B — zero behavior change, just observability.
2. **Replay:** backtest the suppressed/flipped subset per strategy × TF — does a
   TF-proportional anchor improve avg_r vs the flat per-strategy anchor?
3. **Promote only** anchors where OOS avg_r improves AND the regime-mapping
   question is settled.

## Open questions for next session

- **Win condition:** is the goal *fewer counter-trend false signals* (F8 gate
  refinement) or *a new trend-direction feature* (golden/death cross as a
  signal/context score)? These lead to different builds.
- **Scope A vs B:** together or separately? (A is lower-risk: pure anchor remap,
  no new logic. B/cross is a new computation.)
- **Sequence vs regime mapping:** do we resolve the inverted-mapping question
  first, or constrain this to F8-direction-only (which sidesteps type-routing)?
- **Which TFs ladder to which anchors?** Needs a first-pass table to backtest,
  e.g. `15m→1h, 1h→4h, 4h→1d, 1d→1w` (proportional) vs `*→1d` (current-ish).
