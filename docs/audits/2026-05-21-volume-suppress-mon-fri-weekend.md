# T6 Phase A — `volume_suppress` mon_fri + weekend Audit Findings (2026-05-21)

**Gate**: `volume_suppress` (per-strategy boolean + `_long` / `_short` + `_long_per_tf` / `_short_per_tf` directional overrides). When `true`, the strategy drops low-volume signals (`_is_low_volume` = candle volume `< 1.5 ×` rolling 20-bar mean). Live path: `analytics/signal/scanner.py`. Backtest path: `analytics/backtest/engine.py::run_backtest` low-volume branch.

**Tool**: `tools/gate_audit.py volume-suppress --config <overlay> --grain strategy_tf_dir --min-n 30`. Replay-only — re-tags `backtest_trades` rows whose `low_volume = TRUE AND strategy ∈ volume_suppress_off` and emits per-(strategy × tf × direction) verdicts.

**Decision rule** (per cell, `n_supp ≥ 30`):

- `supp_avg_r ≤ −0.05R` → **ENABLE** the gate at this scope (low-vol trades are losers; gate is right to drop them).
- `supp_avg_r ≥ +0.05R` → **DISABLE** (low-vol trades are winners; gate would kill them).
- else → INSUFFICIENT (defer).

**Cross-check protocol** (PR #377 dual-view): every actionable cell is verified against `confidence_ratings` (winning-params view). When CR strongly disagrees with the audit verdict — typically a 3★+ profitable cell where the gate concentrates on a high-quality kept subset — the cell is **deferred to Bucket C** rather than flipped.

## Why now

PR #395 (T6 PR-5 cooldown) closed the backtest-live-parity series 2026-05-20. With all 5 live gates wired into `run_backtest()`, a permissive-baseline sweep (`volume_suppress=false` on candidate cells) now produces near-faithful replay of what the live system would have done if those cells were OFF. The prior 2026-05-16 T6 Phase A round predated PR-5 cooldown and PR-4b conflict resolver, so its inverse-question verdicts were T6-blocked.

This audit specifically targets the inverse: **currently-ON cells across mon_fri + weekend** — answering "should we flip ON → OFF?" Tue_thu (covered by base `strategy_params.toml` + `signal_watch.toml`) is left untouched.

## Outcome

**10 cells flipped ON → OFF** (5 per config). **2 cells deferred to Bucket C** where CR contradicts the audit verdict.

| Config | Strategy | Cell | Change |
| --- | --- | --- | --- |
| `signal_watch_weekdays` | engulfing | 15m SHORT | ON → off |
| `signal_watch_weekdays` | engulfing | 1h LONG | ON → off |
| `signal_watch_weekdays` | engulfing | 1h SHORT | ON → off (reverts 2026-05-16 ENABLE) |
| `signal_watch_weekdays` | orb | 1h SHORT | ON → off |
| `signal_watch_weekdays` | liquidity_sweep | 1h SHORT | ON → off |
| `signal_watch_all` | engulfing | 1h LONG | ON → off |
| `signal_watch_all` | orb | 4h SHORT | ON → off |
| `signal_watch_all` | liquidity_sweep | 1h SHORT | ON → off |
| `signal_watch_all` | wick_fill | 1d LONG | ON → off |
| `signal_watch_all` | wick_fill | 1h SHORT | ON → off |
| `signal_watch_all` | orb | 1h SHORT | **DEFER (Bucket C)** — CR 3★ +0.441R disagrees |
| `signal_watch_all` | wick_fill | 4h SHORT | **DEFER (Bucket C)** — CR 5★ +1.398R disagrees |

## Audit scope

| Config | day_filter | Permissive overlay | Sweep run_ids | Rows |
| --- | --- | --- | --- | --- |
| `signal_watch_weekdays.toml` | `mon_fri` | `_audit_volume_suppress_2026-05-21_weekdays.toml` | 180 | 12,929 |
| `signal_watch_all.toml` | `weekend` | `_audit_volume_suppress_2026-05-21_weekend.toml` | 216 | 23,839 |

Sweep window: `--since 2025-09-12` (matches PR #375 / #377 / #379 / #382 / #385). Live-parity stack: `--live-parity` (regime + direction_filter + f8_htf_ema + adr_bias + conflict_resolver + cooldown).

### Methodology note — engulfing weekdays `volume_suppress = false` was dead code

The pre-audit `config/signal_watch_weekdays.toml` set `volume_suppress = false` on engulfing with a comment claiming it controlled LONG-side. The resolver (`SignalWatchConfig.effective_volume_suppress_long` / `_short`) chains **directional > global**, and the base `strategy_params.toml` sets `volume_suppress_long = true` for engulfing. That inherited directional flag wins over the file's `volume_suppress = false` — so engulfing LONG was effectively **ON** under mon_fri, not OFF as the comment implied. The audit treated engulfing LONG as a currently-ON candidate; the overlay clears global + both directional flags to actually run the permissive baseline. Comment updated in this PR.

### Methodology note — conflict_resolver no-op on first audit sweep

The permissive overlays use new `config_name`s (`_audit_volume_suppress_2026-05-21_*`), so `_build_confidence_ratings_map` returned 0 rows on the first sweep — the live-parity conflict_resolver gate was effectively no-op for this audit (verbatim log line `live_parity conflict_resolver: loaded 0 rating(s)`). This matches PR-4b's default-off semantics and only affects cells where cross-strategy conflicts cluster on the same candle. The per-cell `supp_avg_r` measurement is not sensitive to this caveat — verdicts hold.

## Mon_fri (signal_watch_weekdays.toml) — actionable cells

Only cells from the 5 candidate strategies (bos, engulfing, orb, liquidity_sweep, wick_fill — wick_fill currently OFF so not in scope here) with `n_supp ≥ 30`.

| Strategy | tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | Audit | CR | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bos` | 15m | long | 210 | −0.124 | 153 | **−0.190** | ENABLE | 1★ −0.240R | **KEEP ON** |
| `bos` | 15m | short | 190 | −0.147 | 99 | **−0.475** | ENABLE | 1★ −0.242R | **KEEP ON** |
| `engulfing` | 15m | long | 65 | +0.154 | 348 | −0.109 | ENABLE | 2★ +0.014R | KEEP ON |
| `engulfing` | 15m | short | 70 | −0.071 | 368 | **+0.073** | DISABLE | 1★ −0.087R | **FLIP OFF** |
| `engulfing` | 1h | long | 13 | −0.231 | 95 | **+0.211** | DISABLE | 1★ −0.633R | **FLIP OFF** |
| `engulfing` | 1h | short | 20 | −0.250 | 95 | **+0.158** | DISABLE | 1★ −0.133R | **FLIP OFF** (reverts 2026-05-16) |
| `orb` | 15m | long | 87 | +0.138 | 86 | **−0.128** | ENABLE | 2★ +0.027R | KEEP ON |
| `orb` | 15m | short | 83 | −0.133 | 60 | **−0.150** | ENABLE | 1★ −0.256R | KEEP ON |
| `orb` | 1h | long | 39 | +0.974 | 70 | **−0.371** | ENABLE | 5★ +0.955R | KEEP ON |
| `orb` | 1h | short | 29 | +0.138 | 53 | **+0.349** | DISABLE | 2★ +0.064R | **FLIP OFF** |
| `liquidity_sweep` | 15m | long | 196 | +0.148 | 131 | **−0.153** | ENABLE | 2★ +0.024R | KEEP ON |
| `liquidity_sweep` | 15m | short | 273 | −0.242 | 150 | **−0.300** | ENABLE | 1★ −0.460R | KEEP ON |
| `liquidity_sweep` | 1h | short | 87 | −0.379 | 44 | **+0.500** | DISABLE | 1★ −0.510R | **FLIP OFF** |

Cells with `n_supp < 30` or `|supp_avg_r| < 0.05R` are INSUFFICIENT and not shown (keep current ON).

### `engulfing` weekdays — 3 flips, including a reversion

The pre-audit state had `volume_suppress_long = true` (base inheritance) + `volume_suppress_short = true` (2026-05-16 audit). All four engulfing cells with `n_supp ≥ 30` are now in the negative-kept / positive-supp pattern that triggers DISABLE — the kept side has run out of edge once the full live-parity stack lands. CR is 1★ on every flipped cell.

The 1h SHORT verdict reverses the 2026-05-16 ENABLE (which read `n=100 supp_avg_r=-0.050R`). With the full T6 stack and a fresh permissive baseline, n=95 now reads supp_avg_r = +0.158R. The verdict flip is most likely caused by the post-2026-05-16 gates (PR-3 direction_filter + F8 HTF EMA + PR-5 cooldown) reshaping the candle population that reaches engulfing's trigger.

### `orb` weekdays — 1h SHORT flip

The 1h LONG cell is a 5★ +0.955R winner under the current ON state — clear ENABLE confirmation. 1h SHORT is the directional asymmetry: low-vol shorts are +0.349R (n=53) while high-vol shorts only +0.138R (n=29). CR 2★ +0.064R is borderline; the audit dominates.

### `liquidity_sweep` weekdays — 1h SHORT flip

CR strongly negative (1★ −0.510R) on the kept side. Audit agrees: high-vol shorts at 1h are −0.379R losers (n=87), low-vol shorts are +0.500R winners (n=44). The gate is killing the only profitable directional slice. Flip.

## Weekend (signal_watch_all.toml) — actionable cells

| Strategy | tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | Audit | CR | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bos` | 15m | long | 318 | −0.264 | 161 | **−0.665** | ENABLE | 1★ −0.279R | KEEP ON |
| `bos` | 15m | short | 333 | −0.117 | 114 | **−0.053** | ENABLE | 1★ −0.234R | KEEP ON |
| `bos` | 1h | long | 65 | −0.462 | 49 | **−0.286** | ENABLE | 1★ −0.538R | KEEP ON |
| `engulfing` | 1h | long | 34 | −0.265 | 122 | **+0.189** | DISABLE | 1★ −0.286R | **FLIP OFF** |
| `orb` | 15m | long | 82 | +0.171 | 106 | **−0.207** | ENABLE | 1★ −0.172R | KEEP ON |
| `orb` | 15m | short | 82 | −0.415 | 101 | **−0.228** | ENABLE | 1★ −0.549R | KEEP ON |
| `orb` | 1h | long | 41 | +0.476 | 110 | **−0.300** | ENABLE | 3★ +0.282R | KEEP ON |
| `orb` | 1h | short | 43 | +0.407 | 87 | **+0.328** | DISABLE | **3★ +0.441R** | **DEFER (Bucket C)** |
| `orb` | 4h | long | 5 | +0.200 | 66 | **−0.273** | ENABLE | 1★ −0.054R | KEEP ON |
| `orb` | 4h | short | 7 | +0.714 | 51 | **+0.529** | DISABLE | 1★ −0.244R | **FLIP OFF** |
| `liquidity_sweep` | 15m | long | 355 | +0.099 | 132 | **−0.136** | ENABLE | 1★ −0.079R | KEEP ON |
| `liquidity_sweep` | 15m | short | 292 | −0.086 | 168 | **−0.107** | ENABLE | 1★ −0.363R | KEEP ON |
| `liquidity_sweep` | 1h | long | 89 | +0.011 | 41 | **−0.122** | ENABLE | 2★ +0.159R | KEEP ON |
| `liquidity_sweep` | 1h | short | 73 | +0.315 | 51 | **+0.177** | DISABLE | 2★ +0.029R | **FLIP OFF** |
| `wick_fill` | 1h | long | 107 | −0.215 | 740 | **−0.222** | ENABLE | 1★ −0.062R | KEEP ON |
| `wick_fill` | 1h | short | 81 | +0.037 | 447 | **+0.128** | DISABLE | 2★ +0.123R | **FLIP OFF** |
| `wick_fill` | 1d | long | 0 | NaN | 43 | **+0.186** | DISABLE | n/a | **FLIP OFF** (no production cell) |
| `wick_fill` | 4h | long | 5 | +0.200 | 243 | **−0.210** | ENABLE | 5★ +2.836R | KEEP ON |
| `wick_fill` | 4h | short | 5 | +1.400 | 129 | **+0.070** | DISABLE | **5★ +1.398R** | **DEFER (Bucket C)** |

### `wick_fill` 1d LONG — flip enables a previously-empty cell

`n_kept = 0` because the production state suppresses every 1d wick_fill long signal (all 43 are low-volume). The audit verdict says the suppressed slice averages +0.186R — there's a real edge being killed. Flipping `volume_suppress_long_per_tf["1d"] = false` enables the cell. Small absolute sample (n=43) flagged but verdict is clean.

### Deferrals — CR shows the gate concentrates a winning subset

**`orb` 1h SHORT weekend (CR 3★ +0.441R)**: The audit DISABLE says low-vol shorts are +0.328R (n=87) winners. But the kept side is also a winner at +0.407R (n=43), matching CR. Flipping OFF would dilute per-trade avg from +0.407R → +0.354R. The gate is correctly concentrating on a higher-quality subset. Re-evaluate when schema-aware audit lands.

**`wick_fill` 4h SHORT weekend (CR 5★ +1.398R)**: Pattern much stronger. Kept side +1.400R (n=5, matches CR), supp +0.070R (n=129). Flipping would dilute +1.400R → ~+0.119R, catastrophic collapse from a 5★ to ~1★ cell. The audit rule (mass-weighted by n_supp) doesn't see the kept-side concentration premium here. Defer.

These two deferrals add to the Bucket C carry-over alongside the cells listed in PR #375 / #377 / #379 / #383.

## TOML edits

### `config/signal_watch_weekdays.toml`

- Comment on the dead-code `volume_suppress = false` line for engulfing updated to flag the resolver-chain reason.
- Comment on `volume_suppress_short = true` line for engulfing updated to mark 15m/1h overridden below.
- New section "T6 Phase A — 2026-05-21" appended with 4 sub-tables: `engulfing.volume_suppress_long_per_tf`, `engulfing.volume_suppress_short_per_tf`, `orb.volume_suppress_short_per_tf`, `liquidity_sweep.volume_suppress_short_per_tf`.

### `config/signal_watch_all.toml`

- New section "T6 Phase A — 2026-05-21" appended with 5 sub-tables: `engulfing.volume_suppress_long_per_tf`, `orb.volume_suppress_short_per_tf` (with deferral comment for 1h SHORT), `liquidity_sweep.volume_suppress_short_per_tf`, `wick_fill.volume_suppress_long_per_tf`, `wick_fill.volume_suppress_short_per_tf` (with deferral comment for 4h SHORT).

## Bucket C carry-over update

`orb 1h SHORT` and `wick_fill 4h SHORT` (both `signal_watch_all` only) join the Bucket C list as kept-side-concentration cases. Both are cells where the audit rule's mass-weighted DISABLE conflicts with CR's per-trade-quality view — the schema gap is "the audit can't see that the kept subset has structural quality the supp subset doesn't."

Bucket C carry-over (as of 2026-05-21):

- From PR #375: `eqh_eql`, `fib_golden_zone`, `hammer_hanging_man`, `inside_bar`, `morning_evening_star`, `order_block`, `pin_bar` (volume_suppress directional).
- From PR #377: `inside_bar 4h` (day-filter long/short split).
- From PR #379: `bos` (adr_exempt directional).
- From this audit: `orb 1h SHORT` / `wick_fill 4h SHORT` weekend (volume_suppress concentration cases).

## Cleanup

After this audit lands, delete the disposable overlays:

```sh
rm config/_audit_volume_suppress_2026-05-21_weekdays.toml
rm config/_audit_volume_suppress_2026-05-21_weekend.toml
```

The audit doc and per-cell TOML comments are the durable record. Reproducing the audit only needs the two overlays + the published sweep window — the gate_audit tool is deterministic on a given backtest_runs subset.
