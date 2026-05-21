# T6 Phase A — `adr_exempt` mon_fri + weekend Audit Findings (2026-05-21)

**Gate**: `adr_exempt` (per-strategy boolean; default `false`). When `true`, the strategy bypasses `analytics/signal/gates.py::_filter_signals_by_adr` (drops signals where consumed-ADR ≥ `bias.adr_suppress_threshold` in the chasing direction). The threshold is config-scoped: tue_thu `0.80`, mon_fri `0.65`, weekend `0.70` after the 2026-05-17 sweep (PR #382).

**Tool**: `tools/gate_audit.py adr-exempt --config <overlay> --grain strategy_tf_dir --min-n 30`. Reuses live `_filter_signals_by_adr` verbatim per (symbol, tf) — bug-for-bug parity with the live system.

**Decision rule** (per cell, `n_supp ≥ 30`):

- `supp_avg_r ≤ −0.05R` → **ENABLE** the gate at this scope (keep `adr_exempt = false`; ADR-late trades are losers, gate is right to drop them).
- `supp_avg_r ≥ +0.05R` → **DISABLE** (flip to `adr_exempt = true`; ADR-late trades are winners, gate would kill them).
- else → INSUFFICIENT.

**Cross-check protocol** (PR #377 dual-view): every actionable cell is verified against `confidence_ratings` (winning-params view). Audit and CR must agree before any TOML flip.

## Why now

PR #395 (T6 PR-5 cooldown) closed the backtest-live-parity series 2026-05-20. The 2026-05-17 `adr_exempt` audit (`docs/audits/2026-05-17-adr-exempt.md`) found mon_fri + weekend "not auditable from current data" because both configs had zero `adr_exempt = true` overrides — the audit could only mask exempt strategies, so every cell read `n_supp = 0`. The fix needed was a permissive baseline. PR-5 closure makes that baseline meaningful: `--live-parity` now applies regime + direction_filter + F8 HTF EMA + ADR bias + conflict resolver + cooldown inside `run_backtest()`, so a sweep with `adr_exempt = true` on the candidate strategies is a near-faithful replay of what production would emit if those exemptions shipped.

This audit specifically tests the inverse: **on mon_fri + weekend, should we ADD exemptions to any of the 5 strategies that are exempt on tue_thu?** The candidate set mirrors `signal_watch.toml`'s exempt list — `bos`, `cvd_divergence`, `eqh_eql`, `fib_golden_zone`, `smt_divergence`. Tue_thu (`signal_watch.toml`) is left untouched.

## Outcome — no production TOML edits

**No cells flip OFF → ON.** All actionable cells either:

- **ENABLE** verdict (audit confirms ADR-late is losers; production `adr_exempt = false` is correct), or
- **DISABLE** verdict with directional conflict — current `backtest_config.py` schema only supports strategy-wide `adr_exempt`, not the live-side `adr_exempt_long` / `adr_exempt_short` from PR #384. Flipping per-direction would create a live/backtest mismatch under `--live-parity` until the backtest gate is schema-extended.

**1 Bucket C addition**: `bos 15m short mon_fri` (audit DISABLE / CR 1★ −0.242R confirms low quality on kept side, but +0.10R lift available on supp side that the global flag can't unlock without dragging the long-side disaster along).

## Audit scope

| Config | day_filter | adr_suppress_threshold | Permissive overlay | Sweep run_ids | Rows |
| --- | --- | --- | --- | --- | --- |
| `signal_watch_weekdays.toml` | `mon_fri` | `0.65` | `_audit_adr_exempt_2026-05-21_weekdays.toml` | 192 | 878 (bos slice) |
| `signal_watch_all.toml` | `weekend` | `0.70` | `_audit_adr_exempt_2026-05-21_weekend.toml` | 228 | 947 (bos slice) |

Sweep window: `--since 2025-09-12` (matches PR #375 / #377 / #379 / #382 / #385 / #398). Live-parity stack: `--live-parity` (regime + direction_filter + f8_htf_ema + adr_bias + conflict_resolver + cooldown).

### Methodology note — conflict_resolver no-op on first audit sweep

Per the same caveat as PR #398: the permissive overlays use new `config_name`s, so `_build_confidence_ratings_map` returns 0 rows on the first sweep and the live-parity conflict_resolver gate is no-op for this audit (log line `live_parity conflict_resolver: loaded 0 rating(s)`). Per-cell `supp_avg_r` measurement is not sensitive to this caveat — verdicts hold.

### Methodology note — backtest_config schema asymmetry

`backtest_config.StrategyOverride` has only the global `adr_exempt: bool` flag. `signal_config.StrategyOverride` adds `adr_exempt_long` / `adr_exempt_short` (PR #384, live-only). The backtest engine's ADR bias gate (PR-4) reads from `backtest_config` and therefore cannot honour per-direction exemptions. Any per-direction flip would be live-only and break `--live-parity` for the affected (strategy, direction) cell. This blocks DISABLE verdicts on cells where the opposite direction reads ENABLE.

## Mon_fri (signal_watch_weekdays.toml) — actionable cells

Only cells from the 5 candidate strategies (`bos`, `cvd_divergence`, `eqh_eql`, `fib_golden_zone`, `smt_divergence`) with `n_supp ≥ 30`.

| Strategy | tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | Audit | CR (signal_watch_weekdays) | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bos` | 15m | long | 210 | −0.234 | 82 | **−0.524** | ENABLE | 1★ −0.244R | KEEP OFF |
| `bos` | 15m | short | 190 | −0.246 | 138 | **+0.104** | DISABLE | 1★ −0.242R | **DEFER (Bucket C)** — directional conflict with 15m long; backtest schema asymmetry |
| `bos` | 1h | long | 39 | −0.233 | 43 | **−0.663** | ENABLE | 1★ −0.232R | KEEP OFF |
| `bos` | 1h | short | 39 | −0.027 | 46 | **−0.075** | ENABLE | 1★ −0.027R | KEEP OFF (close to threshold) |
| `eqh_eql` | 15m | short | 694 | −0.293 | 30 | **−0.629** | ENABLE | 1★ −0.289R | KEEP OFF |

Cells with `n_supp < 30` are INSUFFICIENT (not shown). The 8 "Notable cells worth re-evaluating" called out in the 2026-05-17 doc — including the headliners `fib_golden_zone 4h long mon_fri` 5★ +2.189R and `cvd_divergence 15m short mon_fri` 5★ +1.325R — all read `n_supp = 0` in this audit: the ADR-suppression gate at `threshold = 0.65` is inert for these strong cells, because the underlying detector simply does not fire ADR-late in the chasing direction. CR's profitability there is driven entirely by the kept side; an exemption would unlock nothing.

### `bos 15m short mon_fri` — the only DISABLE, deferred to Bucket C

The audit isolates `n_supp = 138` ADR-late shorts averaging `+0.104R` against `n_kept = 190` of `−0.246R`. Pooling: `(190×−0.246 + 138×+0.104) / 328 = −0.099R` — a `+0.144R` lift over kept-only `−0.246R`, but still negative. CR 1★ `−0.242R` matches the kept side.

The opposite direction `bos 15m long mon_fri` is a clean ENABLE (`n_supp = 82` averaging `−0.524R`). Flipping strategy-wide `adr_exempt = true` would drag the long-side disaster along: `(210×−0.234 + 82×−0.524 + 190×−0.246 + 138×+0.104) / 620 = −0.201R`, worse than the current kept-side `−0.240R` aggregate over `bos 15m mon_fri`.

The split is the same shape as the 2026-05-17 finding on `bos 1h tue_thu` (long ENABLE, short DISABLE). The live-only `adr_exempt_short` schema (PR #384) could express this, but `backtest_config` doesn't read it — shipping the flip would break `--live-parity`. Defer until `backtest_config` is schema-extended.

### `bos 4h short mon_fri` — flagged but `n_supp = 15` (INSUFFICIENT)

CR shows 4★ +0.895R on this cell — the 2026-05-17 doc's only mon_fri-specific cross-config asymmetry. Audit reads `n_kept = 10 / kept_avg = +0.984R` vs `n_supp = 15 / supp_avg = −0.747R`. The pattern is "kept is the winner, supp is losers" — `adr_exempt = false` is correct, the gate is concentrating on the high-quality kept subset. Below `min_n = 30` so verdict is INSUFFICIENT, but the directional signal is consistent with KEEP OFF.

## Weekend (signal_watch_all.toml) — actionable cells

| Strategy | tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | Audit | CR (signal_watch_all) | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bos` | 15m | short | 334 | −0.236 | 53 | **−0.717** | ENABLE | 1★ −0.236R | KEEP OFF |
| `bos` | 1h | short | 83 | −0.418 | 30 | **−0.292** | ENABLE | 1★ −0.410R | KEEP OFF |

Only 2 actionable cells; both confirm `adr_exempt = false` (ADR-late shorts are losers; gate is right). All other candidate cells have `n_supp < 30`.

The 4 "Notable cells worth re-evaluating" called out for weekend in the 2026-05-17 doc (`cvd_divergence 15m long` 4★ +0.575R, `smt_divergence 15m combined` 5★ +1.091R, `fib_golden_zone 1h short` 5★ +0.932R, `fib_golden_zone 1d long` 5★ +0.986R) all read `n_supp = 0` here: at `threshold = 0.70` (the strictest of the three production configs), these detectors simply don't fire ADR-late on chasing direction. An exemption would unlock nothing.

## Bucket C carry-over update

`bos 15m short mon_fri` joins the directional-schema cohort:

- From PR #375: `eqh_eql`, `fib_golden_zone`, `hammer_hanging_man`, `inside_bar`, `morning_evening_star`, `order_block`, `pin_bar` (volume_suppress directional).
- From PR #377: `inside_bar 4h` (day-filter long/short split).
- From PR #379 + PR #380: `bos` 1h tue_thu (adr_exempt directional).
- From PR #398: `orb 1h SHORT` / `wick_fill 4h SHORT` weekend (volume_suppress concentration cases).
- **From this audit**: `bos 15m short mon_fri` (adr_exempt directional). Same shape as the existing `bos` Bucket C entry from PRs #379 / #380.

The blocker for all `adr_exempt` Bucket C entries is the same: `backtest_config.StrategyOverride` needs `adr_exempt_long` / `adr_exempt_short` fields and `is_adr_exempt(strategy, direction)` to honour them, so `run_backtest()` ADR bias gate (PR-4) can faithfully replay the live behaviour. This is part of the open Bucket C schema-extension scope (PR #383 / `project_bucket_c_options.md`).

## Phase A sweep status post-audit

Remaining cells in the Phase A order from `buibui-redesign-t6-phase-a-plan.md`:

1. ~~`volume_suppress` tue_thu~~ — PR #375.
2. ~~`day_filter`~~ — PR #377.
3. ~~`strategy_timeframes`~~ — PR #379.
4. ~~`adr_exempt` tue_thu~~ — PR #380.
5. ~~`volume_spike_boost`~~ — deprecated 2026-05-20 (structural inertness; PR #396/#397).
6. ~~`adr_suppress_threshold`~~ — PR #382.
7. ~~`volume_suppress` mon_fri + weekend~~ — PR #398.
8. ~~`adr_exempt` mon_fri + weekend~~ — **this audit** (no edits).

Phase A inverse-question close-out: every originally-T6-unblocked inverse question now has a closing audit doc on disk. The remaining work is Bucket C schema-extension (orthogonal to T6) and the parked spike-candle promote-shaped redesign.

## Reproducibility

```bash
# Sweep with permissive overlay (one per config)
PYTHONPATH=. poetry run python buibui.py backtest \
    --config config/_audit_adr_exempt_2026-05-21_weekdays.toml \
    --since 2025-09-12 --save --live-parity

PYTHONPATH=. poetry run python buibui.py backtest \
    --config config/_audit_adr_exempt_2026-05-21_weekend.toml \
    --since 2025-09-12 --save --live-parity

# Audit each (strategy×tf×direction) grain
PYTHONPATH=. poetry run python tools/gate_audit.py adr-exempt \
    --config config/_audit_adr_exempt_2026-05-21_weekdays.toml \
    --grain strategy_tf_dir --min-n 30

PYTHONPATH=. poetry run python tools/gate_audit.py adr-exempt \
    --config config/_audit_adr_exempt_2026-05-21_weekend.toml \
    --grain strategy_tf_dir --min-n 30

# CR dual-view cross-check
poetry run python -c "
import duckdb
con = duckdb.connect('analytics.db', read_only=True)
print(con.execute('''
    SELECT config_name, strategy, tf, direction, stars, ROUND(avg_r,3) AS avg_r,
           ROUND(win_rate,3) AS wr, day_filter
    FROM confidence_ratings
    WHERE strategy IN ('bos','cvd_divergence','eqh_eql','fib_golden_zone','smt_divergence')
      AND tf IN ('15m','1h','4h','1d')
      AND day_filter IN ('mon_fri','weekend')
    ORDER BY day_filter, strategy, tf, direction
''').fetchdf().to_string(index=False))
"
```

## Cleanup

After this audit lands, delete the disposable overlays:

```sh
rm config/_audit_adr_exempt_2026-05-21_weekdays.toml
rm config/_audit_adr_exempt_2026-05-21_weekend.toml
```

The audit doc is the durable record. Reproducing the audit only needs the two overlays + the published sweep window — the gate_audit tool is deterministic on a given `backtest_runs` subset.
