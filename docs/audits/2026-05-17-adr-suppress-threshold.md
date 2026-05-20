# T6 Phase A — `adr_suppress_threshold` Audit Findings (2026-05-17)

**Gate**: `bias.adr_suppress_threshold` (global float, default `0.80`). Drives `analytics/signal/gates.py::_filter_signals_by_adr`, which suppresses signals where today's range has already consumed ≥ threshold of the 14-day ADR **in the chasing direction**. Strategies with `adr_exempt = true` bypass the gate entirely (see PR #380).

**Schema**: defined in `BiasConfig.adr_suppress_threshold` (`analytics/signal_config.py:281`). No per-strategy schema exists, but per-config override via TOML `extends` merge is supported — each `signal_watch_*.toml` can declare its own `[bias] adr_suppress_threshold = X` overriding the base in `strategy_params.toml`.

**Tool**: `tools/adr_threshold_audit.py` (new this PR). Sweeps candidate thresholds {0.60, 0.65, 0.70, 0.75} against `backtest_trades`, masks chasing-direction trades whose consumed_ratio falls in `[candidate, current_threshold)`, and reports per-(strategy, tf, direction) + aggregate verdicts. Mirrors `_filter_signals_by_adr` bit-for-bit for parity with the live gate.

**Decision rule** (per cell, `n_supp ≥ --min-n=30`):

- `supp_avg_r ≤ −0.05R` → **ENABLE** this candidate (tighten — late-ADR chasing trades in `[candidate, 0.80)` are losers we should drop).
- `supp_avg_r ≥ +0.05R` → **DISABLE** this candidate (keep current — tightening would suppress winners).
- else → INSUFFICIENT.

The threshold is global per config, so the **aggregate** view (across non-exempt strategies, all tfs, both directions) is the primary signal. Per-(strategy, tf, direction) view is used only as a cross-check that the aggregate isn't masking clobbered high-star cells.

## Replay-only caveat (direction note)

Trades currently in `backtest_trades` are those that passed the live gate at `T_now = 0.80` (or whose strategy was adr_exempt, or non-chasing direction). So:

- **Stricter** thresholds `T_cand < 0.80` (e.g. 0.60–0.75): the audit can mask trades with consumed_ratio in `[T_cand, 0.80)` in chasing direction — those trades **are** in the data. **Measurable.**
- **Relaxed** thresholds `T_cand > 0.80`: trades with consumed_ratio in `[0.80, T_cand)` were already dropped by the live gate — they're **not** in the data. **Not measurable** without a permissive-baseline run (T6 engine work).

The next-conversation prompt for PR #381 had this reversed ("only test higher thresholds"). The correct direction is what `tools/adr_threshold_audit.py` implements: candidates must be strictly less than the current threshold.

## Outcome — 3 per-config tightenings

| Config | day_filter | Current | New | Aggregate at new (n_supp / supp_avg_r) |
| --- | --- | --- | --- | --- |
| `signal_watch.toml` | `tue_thu` | 0.80 | **0.75** | 782 / −0.067R (marginal ENABLE) |
| `signal_watch_weekdays.toml` | `mon_fri` | 0.80 | **0.65** | 1,249 / −0.104R (clean ENABLE) |
| `signal_watch_all.toml` | `weekend` | 0.80 | **0.70** | 534 / −0.423R (strongest signal) |

Base `strategy_params.toml` keeps `[bias] adr_suppress_threshold = 0.80` so any future child config that doesn't override gets the conservative default.

## Audit scope

| Config | day_filter | Sweep run_ids | Rows | Exempt strategies (skipped) |
| --- | --- | --- | --- | --- |
| `signal_watch.toml` | `tue_thu` | 192 | 28,013 | `bos`, `cvd_divergence`, `eqh_eql`, `fib_golden_zone`, `smt_divergence` |
| `signal_watch_weekdays.toml` | `mon_fri` | 192 | 14,259 | (none) |
| `signal_watch_all.toml` | `weekend` | 214 | 16,275 | (none) |

Sweep IDs resolved by `tools/gate_audit.py::_resolve_config_run_ids` (re-used). Exempt sets read from each config's resolved `StrategyOverride.adr_exempt` map.

## Per-config aggregate sweeps

### Tue_thu (`signal_watch.toml`) — marginal ENABLE at 0.75 only

```text
candidate  n_supp  supp_avg_r  n_kept  kept_avg_r      verdict
     0.60    2984     +0.054   20937     +0.034      DISABLE
     0.65    2122     +0.039   21799     +0.036 INSUFFICIENT
     0.70    1467     +0.016   22454     +0.037 INSUFFICIENT
     0.75     782     −0.067   23139     +0.040       ENABLE
```

Monotonic trend: the deeper into late-ADR (0.75–0.80 band), the worse. Below 0.75 the suppressed slice flips to neutral-positive — going stricter than 0.75 would actively suppress winners. **Decision: 0.80 → 0.75** (captures the worst 5%-band only; expected lift ≈ +52R aggregate).

Per-(strategy, tf, direction) at candidate 0.75 ENABLE cells:

| strategy | tf | direction | n_kept | kept_avg_r | n_supp | supp_avg_r | CR (config=signal_watch) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `inside_bar` | 15m | long | 1565 | −0.119 | 63 | **−0.320** | 1★ −0.121R |
| `inside_bar` | 15m | short | 1399 | +0.339 | 31 | **−0.115** | 3★ +0.336R (drop loser slice from winning cell) |
| `morning_evening_star` | 15m | long | 1439 | −0.056 | 35 | **−0.521** | 1★ −0.061R |
| `pin_bar` | 15m | long | 1231 | −0.013 | 32 | **−0.300** | 1★ −0.021R |
| `trend_day` | 15m | long | 2340 | −0.194 | 103 | **−0.355** | 1★ −0.287R |

All ENABLE cells either are 1★ losing cells (tightening helps) or, in the case of `inside_bar 15m short` (3★ winning), surgically remove the late-ADR losing slice while leaving the cell profitable (kept_avg_r stays at +0.339R).

5★ tue_thu cells (`inside_bar 1d short`, `smt_divergence 1h short`, `cvd_divergence 1h short`, `doji 1h short`, `order_block 1d short`, `liquidity_sweep 1d short`, `orb 1h short`, `engulfing 1d combined`, `doji 1d short`, `smt_divergence 1h combined`) are either HTF (1d/4h — ratio computation low impact) or exempt (`smt_divergence`, `cvd_divergence`). The audit data confirms `orb 1h short` n_supp=6 at candidate 0.75 — too small to meaningfully suppress its +1.11R edge. **Safe.**

### Mon_fri (`signal_watch_weekdays.toml`) — clean ENABLE at 0.65

```text
candidate  n_supp  supp_avg_r  n_kept  kept_avg_r      verdict
     0.60    1790     −0.112   12353     −0.025       ENABLE
     0.65    1249     −0.104   12894     −0.029       ENABLE
     0.70     877     −0.030   13266     −0.036 INSUFFICIENT
     0.75     451     +0.184   13692     −0.043      DISABLE
```

The `[0.75, 0.80)` band flips DISABLE (+0.184R winners). Tightening to 0.70 catches that winner-band and dilutes the verdict. Tightening to 0.65 strikes the cleanest balance — captures both the `[0.65, 0.70)` and `[0.60, 0.65)` loser-bands while still excluding the `[0.75, 0.80)` winner-band. **Decision: 0.80 → 0.65** (expected lift ≈ +130R aggregate).

Going further to 0.60 would add 541 more suppressed trades at marginal extra avg (-0.130R per the implied band). Conservative pick is 0.65 — less throughput restriction for nearly the same verdict.

Cross-check of live-affecting ENABLE cells at 0.65:

| strategy | tf | direction | n_kept | kept_avg_r | n_supp | supp_avg_r | CR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `inside_bar` | 15m | short | 890 | +0.034 | 70 | **−0.479** | 2★ +0.009R |
| `inside_bar` | 1h | long | 224 | +0.169 | 32 | **−0.284** | 2★ +0.113R |
| `morning_evening_star` | 15m | long | 788 | +0.035 | 98 | **−0.086** | 2★ +0.022R |
| `morning_evening_star` | 15m | short | 781 | −0.065 | 62 | **−0.324** | 1★ −0.088R |

(`engulfing 15m short` and `trend_day 15m long` also ENABLE but their TFs are cut from mon_fri `strategy_timeframes` per PRs #377 / #379 — not live, no impact.)

5★/4★ mon_fri winners (`fib_golden_zone 4h long`, `orb 4h long`, `cvd_divergence 15m short`, `bos 4h short`, `ema 4h long`, etc.) are mostly HTF or low-n in the audit's chasing-direction pool:

- `cvd_divergence 15m short` (5★ +1.26R): n_supp=1 at 0.65 — untouched.
- `fib_golden_zone 4h long` (5★ +2.19R): n_supp=0 — untouched.
- `orb 4h long` (5★ +1.35R): n_supp=13 at 0.65 supp_avg=+0.79R — would lose ~10R if these get clobbered. Below min_n=30 so doesn't trigger DISABLE, but it's a real contra-effect.
- `bos 15m long/short` DISABLE at 0.65 (n=49 +0.073R / n=32 +0.187R): bos cells are losing in aggregate (kept −0.23R) but tightening removes their few profitable late-ADR trades. Net contra-effect ≈ 10R loss across both bos directions.

Total contra-effect ≈ 20R vs the +130R aggregate gain. Net strongly positive. **Safe.**

### Weekend (`signal_watch_all.toml`) — strongest signal, ENABLE at every candidate

```text
candidate  n_supp  supp_avg_r  n_kept  kept_avg_r verdict
     0.60    1066     −0.376   15132     −0.041  ENABLE
     0.65     787     −0.435   15411     −0.044  ENABLE
     0.70     534     −0.423   15664     −0.051  ENABLE
     0.75     261     −0.510   15937     −0.056  ENABLE
```

Late-ADR chasing on weekends is **catastrophic** — every band from 0.60 to 0.80 averages −0.38R to −0.51R per trade. The widest, most uniform ENABLE signal in the entire Phase A audit set.

Pick choice: aggressive (0.60, n=1066, ~+401R gain) vs conservative (0.70, n=534, ~+226R gain) vs extreme (0.75, n=261, ~+133R gain).

**Decision: 0.80 → 0.70.** Picks the best signal-to-noise — n_supp meaningful (534), supp_avg_r most clearly negative (−0.423R), and avoids over-restricting throughput on the lower bands where per-cell verdicts are mostly INSUFFICIENT (small per-cell n's) even though the aggregate is clear.

Cross-check at 0.70: only `morning_evening_star 15m short` shows a per-cell ENABLE (n_supp=42 supp_avg=−0.943R), confirming a 1★ −0.065R losing cell where late-ADR is even worse. No 5★/4★ weekend cell is affected:

- `cvd_divergence 15m long` (4★ +0.58R): n_supp=0 — untouched.
- `smt_divergence 15m short` (4★ +0.71R): n_supp=1 — untouched.
- `morning_evening_star 1d short` (5★ +1.95R), `wick_fill 4h` cells, `pin_bar 1d short` (5★ +1.20R), `trend_day 4h short` (5★ +1.05R), `fib_golden_zone 1d long` (5★ +0.99R), `inside_bar 1d long` (5★ +0.95R), `hammer_hanging_man 1d long` (5★ +0.95R): all HTF or n_supp ≤ 5 — untouched.

The aggregate signal is real and uniform across cells; the per-cell view doesn't surface individual ENABLEs because each cell's chasing-direction late-ADR pool is small. Tightening to 0.70 captures the cumulative loss uniformly. **Safe.**

## TOML edits

Each config file gains a `[bias]` block at the end overriding only `adr_suppress_threshold`:

```toml
# signal_watch.toml — tue_thu — 0.80 → 0.75
[bias]
adr_suppress_threshold = 0.75   # T6 Phase A audit 2026-05-17: ...

# signal_watch_weekdays.toml — mon_fri — 0.80 → 0.65
[bias]
adr_suppress_threshold = 0.65   # T6 Phase A audit 2026-05-17: ...

# signal_watch_all.toml — weekend — 0.80 → 0.70
[bias]
adr_suppress_threshold = 0.70   # T6 Phase A audit 2026-05-17: ...
```

The `[bias]` table merge with `strategy_params.toml` preserves all other bias fields (`htf_ema.*`, `regime.*`, `direction_filter.*`). Only `adr_suppress_threshold` is overridden.

`strategy_params.toml` keeps `[bias] adr_suppress_threshold = 0.80` as the conservative fallback — any future child config that doesn't override gets the old default rather than inheriting a tightened number that wasn't audited for its scope.

## Bucket C carry-over

No change. The threshold is per-config but not per-strategy/direction, so any "wish I could tighten this strategy more" would land in Bucket C. The current audit didn't surface any per-strategy demand for a different threshold beyond what the global per-config tightening already addresses.

Total Bucket C strategies after this PR: **8** (unchanged from PR #380):

- `bos`, `eqh_eql`, `fib_golden_zone`, `hammer_hanging_man`, `inside_bar`, `morning_evening_star`, `order_block`, `pin_bar`.

Resolution paths unchanged — schema extension (per-strategy / per-direction overrides) or T6 engine work.

## Phase A status — CLOSED

All 7 cells of `docs/redesign/buibui-redesign-t6-phase-a-plan.md` are now audited:

1. ✅ `volume_suppress` — PR #375.
2. ✅ `day_filter` — PR #377.
3. ✅ `strategy_timeframes` — PR #379.
4. ✅ `adr_exempt` — PR #380.
5. ✅ `volume_spike_boost` — PR #381 (later deprecated 2026-05-20 — structural inertness; see `2026-05-20-volume-spike-boost-structural-inertness.md`).
6. ✅ `adr_suppress_threshold` — this PR.

(Cell ordering revised mid-phase to put deep-audits ahead of the simpler global-knob audits; the 7-vs-6 count discrepancy in earlier prompts reflected reorderings.)

T6 Phase A is **closed**. Next phase is the backtest-live-parity engine work (`docs/redesign/buibui-redesign-t6-plan.md`), which unblocks Bucket C and the two remaining replay-only inverse questions (volume_suppress ON→OFF, adr_exempt OFF→ON for mon_fri/weekend). The originally-tracked `volume_spike_boost` OFF→ON inverse was closed 2026-05-20 by the structural-inertness finding (deprecation, not audit).

## Reproducibility

```bash
# Aggregate sweep + per-strategy view at the strictest candidate (0.60).
PYTHONPATH=. poetry run python tools/adr_threshold_audit.py \
    --config config/signal_watch.toml
PYTHONPATH=. poetry run python tools/adr_threshold_audit.py \
    --config config/signal_watch_weekdays.toml
PYTHONPATH=. poetry run python tools/adr_threshold_audit.py \
    --config config/signal_watch_all.toml

# Per-strategy view at a specific candidate (used for the decision-table cross-check).
PYTHONPATH=. poetry run python tools/adr_threshold_audit.py \
    --config config/signal_watch.toml --per-strategy-at 0.75
PYTHONPATH=. poetry run python tools/adr_threshold_audit.py \
    --config config/signal_watch_weekdays.toml --per-strategy-at 0.65
PYTHONPATH=. poetry run python tools/adr_threshold_audit.py \
    --config config/signal_watch_all.toml --per-strategy-at 0.70

# Cross-check against confidence_ratings (DuckDB)
poetry run python -c "
import duckdb
con = duckdb.connect('analytics.db', read_only=True)
for day_filter in ['tue_thu', 'mon_fri', 'weekend']:
    print(f'=== {day_filter} ===')
    print(con.execute(
        \"SELECT strategy, tf, direction, stars, avg_r, win_rate \"
        \"FROM confidence_ratings WHERE day_filter = ? AND stars >= 4 \"
        \"ORDER BY stars DESC, avg_r DESC LIMIT 20\",
        [day_filter]
    ).fetchdf().to_string(index=False))
"
```
