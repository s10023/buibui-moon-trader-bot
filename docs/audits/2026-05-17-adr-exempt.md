# T6 Phase A — `adr_exempt` Audit Findings (2026-05-17)

**Gate**: `adr_exempt` (per-strategy boolean; default `false`). When `true`, the strategy bypasses `analytics/signal/gates.py::_filter_signals_by_adr` (drops signals where consumed-ADR ≥ `bias.adr_suppress_threshold = 0.80` in the chasing direction).

**Tool**: `tools/gate_audit.py adr-exempt --config <toml> --grain strategy_tf_dir --min-n 30`. Reuses `_filter_signals_by_adr` verbatim per (symbol, tf) — bug-for-bug parity with the live system.

**Decision rule** (per cell, n_supp ≥ 30):

- `supp_avg_r ≤ −0.05R` → **ENABLE** the gate at this scope (remove the exemption — ADR-late trades are losers, the gate is right to drop them).
- `supp_avg_r ≥ +0.05R` → **DISABLE** (keep the exemption — ADR-late trades are winners, the gate would kill them).
- else → INSUFFICIENT.

**Cross-check protocol** (PR #377 dual-view): every actionable cell is verified against `confidence_ratings` (winning-params view). Audit and CR must agree before any TOML flip.

## Outcome — no TOML edits

All 5 currently-exempt strategies in `signal_watch.toml` retain `adr_exempt = true`. `signal_watch_weekdays.toml` + `signal_watch_all.toml` retain no exemptions. Detailed rationale below.

## Audit scope

| Config | day_filter | Sweep run_ids | Rows | Exempt strategies (count) |
| --- | --- | --- | --- | --- |
| `signal_watch.toml` | `tue_thu` | 192 | 28,013 | 5 — `bos`, `cvd_divergence`, `eqh_eql`, `fib_golden_zone`, `smt_divergence` |
| `signal_watch_weekdays.toml` | `mon_fri` | 192 | 14,259 | 0 |
| `signal_watch_all.toml` | `weekend` | 214 | 16,275 | 0 |

Sweep IDs picked by `tools/gate_audit.py::_resolve_config_run_ids` — most-recent sweep whose `day_filter` matches the config (disjoint across configs since PR #372).

## Tue_thu (signal_watch.toml) — actionable cells

Only the cells with `n_supp ≥ 30` are decision-bearing. Cells with `n_supp = 0` mean the strategy is not in the exempt set (audit only masks exempt strategies); cells with `n_supp < 30` are insufficient evidence.

| Strategy | tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | Audit verdict | CR (config=signal_watch) | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bos` | 15m | long | 369 | −0.30 | 115 | **−0.77** | ENABLE | 1★ −0.41R | **DEFER (Bucket C, T3 router)** |
| `bos` | 1h | long | 57 | −0.36 | 49 | **−0.54** | ENABLE | 1★ −0.50R | **DEFER (Bucket C, T3 router)** |
| `bos` | 1h | short | 71 | −0.60 | 80 | **+0.42** | DISABLE | 1★ −0.06R | **DEFER (Bucket C, T3 router)** |
| `eqh_eql` | 15m | short | 882 | −0.19 | 49 | **+0.29** | DISABLE | 1★ −0.16R | **KEEP `adr_exempt = true`** |

### `bos` — directional split, deferred to T3 router

`bos` has opposite verdicts long-vs-short at the 1h grain — long ENABLE (−0.54R suppressed losers), short DISABLE (+0.42R suppressed winners). The current `adr_exempt` schema is per-strategy only and cannot express a per-direction flip.

Consistent with the T2a routing-audit memo (2026-05-13, `project_bos_routing_audit.md`) which found 131 of 644 high-confidence bos sub-cells positive (top +2.0R, 91 % WR) — aggregate losses are misrouting, not bad detector. Same defer rationale as PR #379: all bos cells stay for T3 router or hard-mode `direction_filter` flip.

Adds `bos` to the Bucket C carry-over alongside the strategies listed in PR #375 / #377 / #379.

### `eqh_eql 15m short` — audit confirms current state

`eqh_eql 15m short` is a 1★ losing cell overall (CR avg_r = −0.16R). The audit isolates the ADR-late subset (n_supp = 49, supp_avg_r = +0.29R) as the only profitable slice of this cell. Removing the exemption would suppress these winners and leave only the losing kept_avg_r = −0.19R. **Keep current `adr_exempt = true`.**

### Other tue_thu exempt strategies — INSUFFICIENT, CR justifies keeping

Below-threshold or zero ADR-late trades, but CR shows the cells the exemption protects are profitable:

| Strategy | Profitable cells in CR (tue_thu) | Why keep `adr_exempt = true` |
| --- | --- | --- |
| `cvd_divergence` | 1h short 5★ +1.65R, 1h long 5★ +1.26R, 1h combined 5★ +1.35R, 15m short 4★ +0.57R | Top performer; exemption protects rare ADR-late trades from interfering with strong base edge |
| `fib_golden_zone` | 1h all 3★ +0.29-0.42R, 4h long 3★ +0.24R, 4h combined 4★ +0.55R | HTF cells consistently profitable; ADR-late is rare on HTF |
| `smt_divergence` | 1h short 5★ +2.13R, 15m short 5★ +1.01R, 1h combined 5★ +0.90R | Strong short-side trend-pullback edge; suppressed sample too small to challenge |
| `eqh_eql` (other cells) | 1h short 3★ +0.44R, 4h short 3★ +0.46R | Profitable HTF shorts; exemption supports continuation logic |

## Mon_fri + weekend — not auditable from current data

Both configs have zero `adr_exempt` overrides — `params["exempt"] = set()` → audit masks nothing. Every cell shows `n_supp = 0`.

**Replay-only limitation**: `tools/gate_audit.py` can audit `OFF → ON` (mask-and-measure existing trades) but cannot audit `ON → OFF` because the ADR gate already ran at backtest time and the suppressed trades aren't in `backtest_trades`. The "should mon_fri / weekend ADD exemption?" question requires:

- **T6 backtest-live-parity engine work** (`docs/redesign/buibui-redesign-t6-plan.md`) — `LiveParityConfig` with engine-side gates plumbed through and configurable per-run, so a permissive baseline run captures the suppressed-side data; or
- A one-off permissive-baseline `make buibui-backtest` of each config with all `adr_exempt = true` injected for the 5 strategies, then re-audit with the production configs.

Notable cells worth re-evaluating once the baseline lands (CR profitable under default `adr_exempt = false`):

| Config | Cell | CR | Why interesting |
| --- | --- | --- | --- |
| `signal_watch_weekdays` | `cvd_divergence 15m short` | 5★ +1.26R | Top mon_fri short |
| `signal_watch_weekdays` | `fib_golden_zone 4h long` | 5★ +2.19R | Top mon_fri HTF long |
| `signal_watch_weekdays` | `fib_golden_zone 4h combined` | 5★ +0.97R | Aggregate HTF mon_fri winner |
| `signal_watch_weekdays` | `bos 4h short` | 5★ +1.11R | Cross-config asymmetry vs tue_thu (1★ −0.13R) |
| `signal_watch_all` | `cvd_divergence 15m long` | 4★ +0.58R | Top weekend long |
| `signal_watch_all` | `smt_divergence 15m` | 5★ +0.99R | Top weekend setup |
| `signal_watch_all` | `fib_golden_zone 1h short` | 4★ +0.75R | HTF short edge weekend |
| `signal_watch_all` | `fib_golden_zone 1d long` | 5★ +0.99R (n small) | HTF long edge weekend |

## Bucket C carry-over update

`bos` joins the Bucket C list (per-direction verdicts the current TOML schema can't express):

- From PR #375: `eqh_eql`, `fib_golden_zone`, `hammer_hanging_man`, `inside_bar`, `morning_evening_star`, `order_block`, `pin_bar` (volume_suppress directional).
- From PR #377: `inside_bar 4h` (day-filter long/short split).
- From PR #379: `bos` (strategy_timeframes routing-audit defer; reaffirmed here).
- **From this audit**: `bos` (adr_exempt directional 1h long vs short).

Total Bucket C strategies: 8. Resolution paths unchanged — schema extension (e.g. `adr_exempt_long` / `adr_exempt_short`) or T6 engine work.

## Phase A sweep status post-audit

Remaining cells in the Phase A order from `buibui-redesign-t6-phase-a-plan.md`:

1. ~~`volume_suppress`~~ — PR #375.
2. ~~`day_filter`~~ — PR #377.
3. ~~`strategy_timeframes`~~ — PR #379.
4. ~~`adr_exempt`~~ — this audit.
5. ~~`volume_spike_boost`~~ — deprecated 2026-05-20 (structural inertness; see `docs/audits/2026-05-20-volume-spike-boost-structural-inertness.md`).
6. `adr_suppress_threshold` (last).

## Reproducibility

```bash
# Run for each config
PYTHONPATH=. poetry run python tools/gate_audit.py adr-exempt \
    --config config/signal_watch.toml --grain strategy_tf_dir --min-n 30

# Cross-check vs confidence_ratings (DuckDB)
poetry run python -c "
import duckdb
con = duckdb.connect('analytics.db', read_only=True)
print(con.execute(\"\"\"
    SELECT config_name, strategy, tf, direction, stars, avg_r, win_rate, day_filter
    FROM confidence_ratings
    WHERE strategy IN ('bos','cvd_divergence','eqh_eql','smt_divergence','fib_golden_zone')
      AND tf IN ('15m','1h','4h','1d')
    ORDER BY day_filter, strategy, tf, direction
\"\"\").fetchdf().to_string(index=False))
"
```
