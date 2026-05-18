# Bucket C TOML Decisions — Per-tf-direction Encoding (2026-05-18)

**Scope**: Hybrid step 2 of the Bucket C plan (scoping in `docs/redesign/buibui-redesign-bucket-c-options.md`, PR #383; schema in PR #384). Translates the carry-over deferrals from PR #375 / #377 / #379 into concrete TOML edits via the new per-tf-direction fields.

**Strategies covered**: `eqh_eql`, `fib_golden_zone`, `hammer_hanging_man`, `inside_bar`, `morning_evening_star`, `order_block`, `pin_bar`. `bos` deferred — T2a routing memo + `adr_exempt` PR #380 already pin it to T3 router work regardless of schema availability.

**Tool**: `tools/gate_audit.py volume-suppress --config <toml> --grain strategy_tf_dir --min-n 30`. Mirrors `analytics/backtest/gates.py::_is_low_volume` against the most-recent sweep per config (`day_filter`-matched run_ids).

**Decision rule** (per cell, n_supp ≥ 30):

- `supp_avg_r ≤ −0.05R` → **ENABLE** (set `volume_suppress_<dir>_per_tf[tf] = true`).
- `supp_avg_r ≥ +0.05R` → **DISABLE** (leave at base default `false`, no encoding needed).
- else → **INSUFFICIENT**.

**Skip filters** (applied on top of the audit verdict):

- `n_kept < 10` — kept side too small to trust post-gate; statistical noise.
- `kept_avg_r < supp_avg_r` — cell is dying (high-vol side worse than low-vol side); ENABLE saves R but the cell-wide direction still needs to be killed via `strategy_timeframes_<dir>` (deferred to next PR).

**Cross-check** (PR #377 dual-view): every ENABLE cell verified against `confidence_ratings` row for the same `(config_name, strategy, tf, direction)`. Since the new schema lets each direction flip independently, the original "no opposing direction has cr ≥ +0.10R or 3★" guard from PR #377 no longer applies — directional cells are encoded in isolation.

## Audit scope

| Config | day_filter | Sweep run_ids | Rows (audited strategies) |
| --- | --- | --- | --- |
| `signal_watch.toml` | `tue_thu` | 192 | ~23,878 |
| `signal_watch_weekdays.toml` | `mon_fri` | 175 | ~14,259 |
| `signal_watch_all.toml` | `weekend` | 228 | ~19,083 |

All 3 configs scoped post-PR #374 (per-config day_filter partitioning). `confidence_ratings` is post-`make db-update` (2026-05-18) under PR #382's per-config `adr_suppress_threshold` overrides.

## TOML edits per config

### `signal_watch.toml` (tue_thu) — 7 ENABLE cells + 1 directional cut

| Strategy | tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | CR | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `eqh_eql` | 15m | long | 324 | −0.167 | 627 | −0.095 | 1★ −0.12R | ENABLE |
| `eqh_eql` | 15m | short | 328 | −0.142 | 603 | −0.170 | 1★ −0.16R | ENABLE |
| `eqh_eql` | 1h | long | 36 | +0.043 | 64 | −0.277 | 1★ −0.19R | ENABLE |
| `hammer_hanging_man` | 15m | long | 218 | −0.087 | 518 | −0.220 | 1★ −0.18R | ENABLE |
| `inside_bar` | 15m | long | 218 | −0.256 | 1334 | −0.094 | 1★ −0.11R | ENABLE |
| `inside_bar` | 1h | long | 51 | +0.205 | 289 | −0.206 | 1★ −0.16R | ENABLE |
| `morning_evening_star` | 15m | long | 197 | −0.050 | 1236 | −0.056 | 1★ −0.05R | ENABLE |
| `inside_bar` | 4h | long | — | — | — | — | 1★ −0.03R (4h_S 3★ +0.22R) | **CUT via `strategy_timeframes_long`** |

Skipped (dying / low-n): `hammer_hanging_man 1h short` (kept −0.675 < supp −0.175, n_kept 16), `hammer_hanging_man 4h short` (n_kept 4).

### `signal_watch_weekdays.toml` (mon_fri) — 12 ENABLE cells + 1 directional cut

| Strategy | tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | CR | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `eqh_eql` | 15m | short | 228 | −0.237 | 463 | −0.322 | 1★ −0.29R | ENABLE |
| `eqh_eql` | 1h | short | 31 | −0.535 | 47 | −0.597 | 1★ −0.52R | ENABLE |
| `fib_golden_zone` | 15m | short | 26 | −0.024 | 170 | −0.227 | 1★ −0.19R | ENABLE |
| `fib_golden_zone` | 1h | short | 17 | +0.149 | 32 | −0.167 | 1★ −0.12R | ENABLE |
| `hammer_hanging_man` | 1h | long | 31 | +0.111 | 80 | −0.225 | 1★ −0.09R | ENABLE |
| `inside_bar` | 1h | short | 18 | +0.117 | 98 | −0.121 | 2★ +0.09R | ENABLE |
| `morning_evening_star` | 1h | long | 13 | +0.873 | 53 | −0.578 | 1★ −0.16R | ENABLE |
| `morning_evening_star` | 4h | short | 10 | +1.350 | 36 | −0.383 | 1★ −0.01R | ENABLE |
| `order_block` | 1h | long | 36 | +0.529 | 55 | −0.530 | 1★ −0.12R | ENABLE |
| `order_block` | 15m | short | 54 | −0.000 | 107 | −0.269 | 1★ −0.20R | ENABLE |
| `order_block` | 4h | short | 11 | −0.146 | 33 | −0.227 | 1★ −0.19R | ENABLE |
| `inside_bar` | 4h | long | — | — | — | — | 1★ −0.30R (4h_S 3★ +0.35R) | **CUT via `strategy_timeframes_long`** |

Skipped (dying / low-n): `inside_bar 4h long` (n_kept 4 — handled by stf cut instead), `morning_evening_star 4h long` (n_kept 4), `pin_bar 4h long` (kept −1.05 < supp −0.18).

### `signal_watch_all.toml` (weekend) — 5 ENABLE cells + 2 directional cuts

| Strategy | tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | CR | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `eqh_eql` | 15m | long | 283 | −0.085 | 638 | −0.280 | 1★ −0.21R | ENABLE |
| `eqh_eql` | 1h | long | 31 | +0.248 | 58 | −0.908 | 1★ −0.40R | ENABLE |
| `fib_golden_zone` | 15m | short | 40 | −0.059 | 179 | −0.266 | 1★ −0.22R | ENABLE |
| `order_block` | 15m | long | 45 | −0.062 | 65 | −0.285 | 1★ −0.19R | ENABLE |
| `order_block` | 15m | short | 29 | −0.056 | 65 | −0.412 | 1★ −0.31R | ENABLE |
| `inside_bar` | 4h | long | — | — | — | — | 1★ −0.10R (4h_S 3★ +0.35R) | **CUT via `strategy_timeframes_long`** |
| `hammer_hanging_man` | 1h | long | — | — | — | — | 1★ −0.33R (1h_S 3★ +0.23R) | **CUT via `strategy_timeframes_long`** |

Skipped (dying / low-n): `eqh_eql 15m short` (kept −0.42 < supp −0.21), `fib_golden_zone 15m long` (kept −0.36 < supp −0.32), `inside_bar 4h long` (n_kept 3 — handled by stf cut), `morning_evening_star 4h long/short` (n_kept ≤ 2), `pin_bar 1h long` (kept −0.41 < supp −0.25), `hammer_hanging_man 1h long` (covered by stf cut).

## Deferred to next PR (dying cells — strategy_timeframes_&lt;dir&gt; candidates)

Where `kept_avg_r < supp_avg_r` AND both views are negative, ENABLE saves marginal R but the cell remains unprofitable per-trade. These warrant a per-direction `strategy_timeframes_<dir>` kill rather than a `volume_suppress` filter. Holding for a follow-up PR to keep scope clean:

- tue_thu: `hammer_hanging_man 1h short`, `hammer_hanging_man 4h short`
- mon_fri: `pin_bar 4h long`
- weekend: `eqh_eql 15m short`, `fib_golden_zone 15m long`, `pin_bar 1h long`, `pin_bar 15m long/short`

## Replay-only constraint (unchanged)

`tools/gate_audit.py volume-suppress` can only test cells where the live gate is currently OFF (so suppressed trades appear in `backtest_trades`). The 3 ON→OFF inverse questions from PR #375 stay blocked on T6 backtest-live-parity engine work (`docs/redesign/buibui-redesign-t6-plan.md`):

- `volume_suppress` mon_fri / weekend (currently ON for `bos`, `engulfing`, `orb`, `liquidity_sweep`, `wick_fill`).
- `volume_suppress_long` ON→OFF for `ema` per-config.
- `volume_spike_boost` for non-`engulfing` strategies.

Not addressable here.

## Carry-over Bucket C strategies — status after this PR

- `bos`: stays deferred (T2a routing memo + per-direction `adr_exempt` schema gap — schema available but the right fix is direction_filter hard mode or T3 router).
- `eqh_eql`, `fib_golden_zone`, `hammer_hanging_man`, `inside_bar`, `morning_evening_star`, `order_block`, `pin_bar`: actionable ENABLEs encoded; dying-cell strategy_timeframes cuts pending.

Total cells encoded: **24 `volume_suppress_<dir>_per_tf[tf] = true`** + **4 `strategy_timeframes_long` directional cuts** across 3 configs. Zero code changes — schema + plumbing already shipped in PR #384.

## Post-merge

Run `make db-update` to refresh `confidence_ratings` under the new live-emission state. Regression goldens may drift on the live path — commit a separate `chore: regression fixture refresh` commit if so (the PR #379 pattern). Push that directly to `main` once the feature PR merges.
