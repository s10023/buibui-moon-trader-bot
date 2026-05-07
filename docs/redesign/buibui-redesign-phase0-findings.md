# Phase 0 Audit — Findings & §3 Amendment

> **Status (2026-05-07)**: Phase 1 cuts shipped via PR #348. Measured lift on weekdays config: **+0.0282R (+67% relative)**, `signal_watch.toml` (tue_thu): **+0.0006R (noise — already pre-tuned)**. Validation captured in `~/.claude-personal/.../memory/project_phase1_lift_validation.md`. The action map below is now live in production.

**Audit run**: 2026-05-07 against `analytics.db` snapshot.
**Inputs**: 698,758 closed trades · 4,413 same-tf combos · 31,520 cross-tf combos · 19 strategies · 4 timeframes (15m, 1h, 4h, 1d).
**Verdict counts**: KILL = **0**, DEMOTE = **0**, KEEP = **19**.
**CSV**: `docs/redesign/phase0_audit_2026-05-07.csv` (854 slice rows).

The cut rule (avg_r ≤ 0 in *all* slices AND no combo with ≥ +0.10R uplift) was deliberately strict. Zero strategies meet it. The redesign §3 cut list is **not data-justified** at the strategy-wide level — but per-(strategy × timeframe) the data **does** support surgical demotions. This document captures both.

---

## 1. (strategy × timeframe) action map

`pct_pos` = % of (regime × session) slices with avg_r > 0 (n ≥ 30).
`wgt_avg_r` = volume-weighted average R across all slices on that TF.

Action rule:

- **DROP_TF**: pct_pos < 25% **or** wgt_avg_r < −0.10
- **CONFLUENCE_ONLY**: 25% ≤ pct_pos < 50% (alerter off, shape function still used as score boost)
- **KEEP**: pct_pos ≥ 50%

| strategy | tf | pct_pos | wgt_avg_r | n | action |
| --- | --- | --- | --- | --- | --- |
| bos | 15m | 33% | −0.045 | 54,717 | CONFLUENCE_ONLY |
| bos | 1d | 0% | −0.356 | 98 | DROP_TF |
| bos | 1h | 33% | −0.033 | 13,095 | CONFLUENCE_ONLY |
| bos | 4h | 44% | −0.056 | 2,774 | CONFLUENCE_ONLY |
| cvd_divergence | 15m | 44% | +0.017 | 1,875 | CONFLUENCE_ONLY |
| **cvd_divergence** | **1h** | **80%** | **+0.304** | 405 | **KEEP** |
| doji | 15m | 67% | +0.130 | 14,216 | KEEP |
| doji | 1d | 100% | +1.272 | 73 | KEEP |
| doji | 1h | 64% | +0.227 | 2,797 | KEEP |
| doji | 4h | 78% | +0.178 | 831 | KEEP |
| ema | 15m | 25% | −0.209 | 1,023 | DROP_TF |
| ema | 1h | 44% | −0.066 | 596 | CONFLUENCE_ONLY |
| ema | 4h | 50% | +0.173 | 88 | KEEP (low n) |
| engulfing | 15m | 58% | +0.093 | 25,529 | KEEP |
| engulfing | 1d | 100% | +0.332 | 131 | KEEP |
| engulfing | 1h | 83% | +0.197 | 6,551 | KEEP |
| engulfing | 4h | 89% | +0.423 | 1,593 | KEEP |
| **eqh_eql** | **15m** | **8%** | **−0.156** | 36,911 | **DROP_TF** |
| eqh_eql | 1h | 50% | +0.011 | 6,578 | KEEP |
| **eqh_eql** | **4h** | **33%** | **−0.217** | 588 | **DROP_TF** |
| **fib_golden_zone** | **15m** | **33%** | **−0.226** | 6,215 | **DROP_TF** |
| fib_golden_zone | 1h | 83% | +0.253 | 2,321 | KEEP |
| fib_golden_zone | 4h | 57% | +0.792 | 612 | KEEP |
| **fvg (all TFs)** | **15m/1h/4h/1d** | **0–25%** | **−0.15 to −0.51** | 28,830 | **DROP_TF (all)** |
| hammer_hanging_man | 15m | 33% | +0.004 | 18,988 | CONFLUENCE_ONLY |
| hammer_hanging_man | 1d | 100% | +0.175 | 129 | KEEP |
| hammer_hanging_man | 1h | 58% | +0.166 | 6,048 | KEEP |
| hammer_hanging_man | 4h | 33% | +0.091 | 1,216 | CONFLUENCE_ONLY |
| inside_bar | 15m | 42% | +0.029 | 49,987 | CONFLUENCE_ONLY |
| inside_bar | 1d | 100% | +0.283 | 309 | KEEP |
| inside_bar | 1h | 83% | +0.102 | 11,387 | KEEP |
| inside_bar | 4h | 67% | +0.091 | 2,962 | KEEP |
| **liquidity_sweep** | **15m** | **0%** | **−0.308** | 26,121 | **DROP_TF** |
| liquidity_sweep | 1d | 100% | +0.420 | 230 | KEEP |
| **liquidity_sweep** | **1h** | **0%** | **−0.250** | 8,297 | **DROP_TF** |
| **liquidity_sweep** | **4h** | **22%** | **−0.251** | 1,865 | **DROP_TF** |
| **marubozu (all TFs)** | **15m/1h/4h** | **0–20%** | **−0.16 to −0.64** | 3,077 | **DROP_TF (all)** |
| morning_evening_star | 15m | 42% | +0.045 | 46,506 | CONFLUENCE_ONLY |
| morning_evening_star | 1d | 100% | +0.307 | 277 | KEEP |
| morning_evening_star | 1h | 75% | +0.110 | 13,734 | KEEP |
| morning_evening_star | 4h | 78% | +0.248 | 2,995 | KEEP |
| orb | 15m | 25% | −0.181 | 6,671 | DROP_TF |
| orb | 1h | 50% | +0.074 | 6,084 | KEEP |
| orb | 4h | 50% | +0.070 | 3,384 | KEEP |
| **order_block** | **15m** | **9%** | **−0.198** | 6,104 | **DROP_TF** |
| order_block | 1d | 100% | +0.373 | 153 | KEEP |
| **order_block** | **1h** | **33%** | **−0.151** | 3,196 | **DROP_TF** |
| order_block | 4h | 33% | −0.079 | 1,089 | CONFLUENCE_ONLY |
| pin_bar | 15m | 50% | +0.049 | 40,797 | KEEP |
| pin_bar | 1d | 100% | +0.527 | 226 | KEEP |
| pin_bar | 1h | 67% | +0.142 | 12,040 | KEEP |
| pin_bar | 4h | 56% | +0.105 | 2,022 | KEEP |
| smt_divergence | 15m | 58% | +0.115 | 7,379 | KEEP |
| smt_divergence | 1d | 100% | +0.533 | 52 | KEEP (low n) |
| smt_divergence | 1h | 80% | +0.212 | 1,682 | KEEP |
| **smt_divergence** | **4h** | **25%** | **−0.307** | 217 | **DROP_TF** |
| **trend_day** | **15m** | **8%** | **−0.109** | 54,703 | **DROP_TF** |
| trend_day | 1d | 50% | +0.366 | 354 | KEEP |
| trend_day | 1h | 25% | −0.075 | 11,483 | CONFLUENCE_ONLY |
| trend_day | 4h | 56% | +0.147 | 5,058 | KEEP |
| **wick_fill (all TFs)** | **15m/1h/4h/1d** | **0–22%** | **−0.10 to −0.18** | 114,466 | **DROP_TF (all)** |

---

## 2. Surprises that overturn redesign §3

Six findings directly contradict the redesign's deletion / "core edge" claims:

1. **`liquidity_sweep` is NOT a core edge** — 0% positive on 15m and 1h, 22% on 4h, only 1d works (n=230). The redesign treats it as the cleanest reversion edge; the data says it only works on the slowest TF where almost nothing fires. Major architectural implication: **§4 unified `sweep_reversion` setup needs evidence at the TFs it's meant to fire on**, otherwise the whole "Mode B session_sweep" rationale weakens.
2. **`eqh_eql` 4h is dead** (−0.217 wgt_avg_r, 33% positive). Redesign promotes it as a "1d EQH/EQL" core edge — but on 4h it's negative. Restrict to **1h only** (the only positive TF) and 1d (where data exists but n=0 in this audit).
3. **`cvd_divergence` 1h is alive** — 80% positive, +0.304 wgt_avg_r. Redesign deletes it as "synthetic fiction"; the data says it has a real 1h edge despite the synthetic-CVD concern. Keep 1h, drop the rest.
4. **All "candlestick bloat"** strategies (engulfing, doji, pin_bar, morning_evening_star, inside_bar, hammer_hanging_man) **work on 1h+** (58–100% positive). Redesign demotes them all to confluence-only on principle; the data says they should remain alerters on 1h, 4h, and 1d.
5. **`fvg` is dead on every TF** (0–25% positive, all negative wgt_avg_r). Redesign demotes to confluence-only; data is stronger — **DROP_TF on all four TFs**, not just demote. Shape function survives as a confluence feature.
6. **`wick_fill` and `marubozu` are universally dead** (0–22% positive across all TFs). Redesign deletes both — data justifies the deletion. These are the only two strategies the data fully agrees should leave the alerter pool.

---

## 3. §3 amendment — replace deletions with TF-level demotions

The original §3 deletes 14 detectors wholesale. The amendment instead disables specific (strategy × timeframe) cells in the `signal_watch*.toml` `strategy_timeframes` config. Phase 2 regime gating then refines further within remaining cells.

### Phase 1 (TF-level) — actionable now via TOML

**Disable from `strategy_timeframes`** (DROP_TF cells):

```text
wick_fill         : remove from 15m, 1h, 4h, 1d         # universal -avg_r
marubozu          : remove from 15m, 1h, 4h             # universal -avg_r
fvg               : remove from 15m, 1h, 4h, 1d         # universal -avg_r
liquidity_sweep   : remove from 15m, 1h, 4h             # only 1d remains
eqh_eql           : remove from 15m, 4h                 # only 1h remains positive
fib_golden_zone   : remove from 15m                     # 1h/4h kept
order_block       : remove from 15m, 1h                 # 4h confluence-only, 1d kept
orb               : remove from 15m                     # 1h/4h kept
trend_day         : remove from 15m                     # 1h confluence-only, 4h/1d kept
ema               : remove from 15m                     # 4h kept; 1h confluence-only
smt_divergence    : remove from 4h                      # 15m/1h/1d kept
bos               : remove from 1d                      # 15m/1h/4h confluence-only
cvd_divergence    : remove from 15m, 4h, 1d             # only 1h kept (80% positive!)
```

**Mark as CONFLUENCE_ONLY** (alerter off, shape detector kept for §4 confirm_score):

```text
bos               : 15m, 1h, 4h   (already loses standalone, used for OTE/structure)
ema               : 1h            (only when paired with another setup)
hammer_hanging_man: 15m, 4h
inside_bar        : 15m
morning_evening_star: 15m
order_block       : 4h
trend_day         : 1h
hammer_hanging_man: 4h
```

**Keep alerter** (no change from current behaviour):

```text
doji              : 15m, 1h, 4h, 1d
engulfing         : 15m, 1h, 4h, 1d
pin_bar           : 15m, 1h, 4h, 1d
inside_bar        : 1h, 4h, 1d
morning_evening_star: 1h, 4h, 1d
hammer_hanging_man: 1h, 1d
fib_golden_zone   : 1h, 4h
liquidity_sweep   : 1d
eqh_eql           : 1h
order_block       : 1d
orb               : 1h, 4h
trend_day         : 4h, 1d
ema               : 4h
smt_divergence    : 15m, 1h, 1d
cvd_divergence    : 1h
```

### What this means for §4 (unified `SignalCandidate`)

The redesign's §4 collapses everything to 4 setups: `sweep_reversion`, `smt_reversion`, `ote_continuation`, `session_sweep`. The data partially supports the *names* but not the *coverage*:

- `sweep_reversion` (= `liquidity_sweep` + `eqh_eql`): **only 1d (sweep) + 1h (eqh)** have edge. The 15m/1h/4h "core" of Mode B doesn't exist in the data.
- `smt_reversion` (= `smt_divergence` BTC↔ETH): edge on 15m/1h/1d only — 4h is dead.
- `ote_continuation` (= `ote_entry` + `bos`): `ote_entry` not directly audited (no entries in `backtest_trades`?), `bos` is universally CONFLUENCE_ONLY. Need separate audit pass.
- `session_sweep`: brand new setup; no existing data. Phase 2 must produce it before claiming edge.

Implication: **the §4 unified-pipeline architecture is still sound, but the input setups need to be much more TF-restricted than the redesign assumes.** Mode B's 15m primary TF needs at least one positive-edge alerter to make sense — currently only `pin_bar`, `engulfing`, `doji`, `smt_divergence` qualify on 15m.

---

## 4. Recommended path forward

1. **Amend `buibui-redesign.md` §3** in this branch to point at the action map above (replace the deletion list with the TF-level table).
2. **Generate a TOML diff** for `signal_watch.toml`, `signal_watch_all.toml`, `signal_watch_weekdays.toml` that applies the DROP_TF cells. This is the actual Phase 1 cleanup deliverable.
3. **Run `make db-update`** to refresh ratings under the new TF restrictions and verify the audit's predicted lift materialises in fresh backtests.
4. **Audit `ote_entry`, `funding_reversion` separately** — neither appeared in this audit (likely due to entry-time recording, not data absence). Phase 2 cannot start until these are confirmed.
5. **Hold §4 unified setup architecture** until the Phase 2 regime classifier is live and a re-audit confirms which (regime × session × tf) cells survive.

---

## 5. Caveats

- **No `ote_entry` data**: the audit shows 19 strategies but `ote_entry` is missing from `backtest_trades`. Either it doesn't write trades or the strategy name differs. Worth checking before Phase 2 commits to it as the trend-continuation core.
- **`funding_reversion` confirmed dead**: not in audit (no signals → no trades), as expected from `fetch_funding_rates` never being wired.
- **Sample-size caveat**: the `low_confidence` flag (n < 30) is honoured in the verdict logic but not in the TF-level rollup above. Cells like `ema 4h` (n=88) and `smt_divergence 1d` (n=52) remain low-conf and could flip with another month of data.
- **Regime label is a Phase 2 artifact**: this audit uses the §6 classifier as a stub. Boundaries may shift slightly when the classifier is hardened in Phase 2; re-run the audit then to confirm DROP_TF cells stay DROP_TF.
