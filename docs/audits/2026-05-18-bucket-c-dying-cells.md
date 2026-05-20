# Bucket C Dying-Cell Directional Cuts (2026-05-18, post-#385)

**Scope**: Hybrid step 2.5 of the Bucket C plan. Translates the 6 deferred dying cells from PR #385 (`docs/audits/2026-05-18-bucket-c-toml.md`) — cells where `kept_avg_r < supp_avg_r` so `volume_suppress` would save R but leave the cell unprofitable — into `strategy_timeframes_<dir>` cuts where they actually move live behavior.

**Result**: 1 directional cut in 1 TOML. 5 of the 6 deferred cells turned out to be **already excluded** by the base `[strategy_timeframes]` entries set in PRs #377 / #379 day-filter audits, so the dying-cell pattern is a no-op for them. Documented below for closure.

## Method

1. **Re-run** `tools/gate_audit.py volume-suppress --config <toml> --grain strategy_tf_dir --min-n 30` against all 3 configs **post-PR #385** `make db-update` (chained backtest of all 3 configs → recalibrate → regression refresh). Confirms verdicts haven't shifted under the new live-emission state.
2. **Cross-check opposite direction**: query `confidence_ratings` for the non-dying direction. The dying-cell pattern only justifies a directional cut when (a) base doesn't already exclude the cell and (b) the opposite direction is profitable enough to keep. Otherwise the base entry already kills the cell for both directions and no further encoding is needed.
3. **Apply skip filters** from PR #385: `n_kept < 10` (noise) and `kept_avg_r < supp_avg_r` (dying).

## Per-cell decisions

| # | Config (day_filter) | Strategy | tf | Dying dir | Dying CR | Base entry covers? | Opposite dir CR | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `signal_watch.toml` (tue_thu) | `hammer_hanging_man` | 1h | short | 1★ −0.161 | ❌ no hammer base entry on tue_thu (default 4 TFs) | long **3★ +0.319** | **CUT short directionally** |
| 2 | `signal_watch.toml` (tue_thu) | `hammer_hanging_man` | 4h | short | 1★ −0.123 | ❌ no hammer base entry on tue_thu | long **4★ +0.546** | **CUT short directionally** |
| 3 | `signal_watch_weekdays.toml` (mon_fri) | `pin_bar` | 4h | long | 1★ −0.379 | ✅ base `["15m","1h","1d"]` excludes 4h | short 1★ −0.192 (also losing) | SKIP — base already covers both dirs |
| 4 | `signal_watch_all.toml` (weekend) | `eqh_eql` | 15m | short | 1★ −0.243 | ✅ base `["4h","1d"]` excludes 15m | long 1★ −0.241 (also losing) | SKIP — base already covers both dirs |
| 5 | `signal_watch_all.toml` (weekend) | `fib_golden_zone` | 15m | long | 1★ −0.347 | ✅ base `["1h","4h","1d"]` excludes 15m | short 1★ −0.194 (also losing) | SKIP — base already covers both dirs |
| 6 | `signal_watch_all.toml` (weekend) | `pin_bar` | 1h | long | 1★ −0.295 | ✅ base `["15m","4h","1d"]` excludes 1h | short 2★ +0.006 (coin flip) | SKIP — short edge too marginal to inverse-cut |

### Pin_bar 15m weekend — INCONCLUSIVE (per PR #385 caveat)

Audit verdict was DISABLE (supp_avg_r positive — gating removes winners), but CR is 2★ marginal both directions:

| Strategy | tf | dir | n_kept | kept_avg_r | n_supp | supp_avg_r | CR | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `pin_bar` | 15m | long | 218 | −0.022 | 898 | +0.095 | 2★ +0.068 | INCONCLUSIVE — leave alone |
| `pin_bar` | 15m | short | 175 | −0.113 | 747 | +0.075 | 2★ +0.028 | INCONCLUSIVE — leave alone |

Cell is essentially a coin flip on weekend. Don't cut, don't volume_suppress. Revisit in a future sweep cycle if signal counts grow materially.

## TOML edit summary

### `signal_watch.toml` (tue_thu) — 1 directional cut

```toml
[strategy_timeframes_short]
hammer_hanging_man = ["15m", "1d"]   # cr 1h_S 1★ -0.16R, 4h_S 1★ -0.12R dying — longs preserved (1h_L 3★ +0.32R, 4h_L 4★ +0.55R)
```

No base `[strategy_timeframes] hammer_hanging_man = ...` entry on tue_thu → resolver returns the directional list as-is for shorts. Longs are not in `[strategy_timeframes_long]` → resolver returns the default 4 TFs. Live effect: hammer 1h+4h shorts no longer fire on tue_thu; longs unchanged.

### `signal_watch_weekdays.toml` (mon_fri) — no edits

`pin_bar 4h long` (cell 3) is already excluded by base `pin_bar = ["15m", "1h", "1d"]`. The dying-cell pattern is a no-op here because base covers both directions and opposite-direction CR (short 1★ −0.192R) doesn't justify selectively re-enabling shorts via `[strategy_timeframes_short]`.

### `signal_watch_all.toml` (weekend) — no edits

Cells 4 (eqh_eql 15m short), 5 (fib_golden_zone 15m long), 6 (pin_bar 1h long) all already excluded by base. Opposite directions are 1★ losers or marginal 2★ coin flips — none qualify for inverse re-enable via directional encoding.

## Resolver verification

For tue_thu after the edit:

| Call | Expected | Result |
| --- | --- | --- |
| `effective_strategy_timeframes("hammer_hanging_man", "long")` | default `["15m","1h","4h","1d"]` (no base, no long override) | OK |
| `effective_strategy_timeframes("hammer_hanging_man", "short")` | `["15m","1d"]` (no base → directional list returned as-is) | OK |
| `effective_strategy_timeframes("hammer_hanging_man", None)` | `None` (no base entry; combined unaffected) | OK |
| `effective_strategy_timeframes("inside_bar", "long")` | `["15m","1h","1d"]` (PR #385 entry unchanged) | OK |

## Bucket C status

Closure of step 2.5 of the Hybrid plan. Status of the 8 strategies tracked since PR #383:

- **Encoded via PR #385 / #385.5**: `eqh_eql`, `fib_golden_zone`, `hammer_hanging_man` (now with directional short cut too), `inside_bar`, `morning_evening_star`, `order_block`, `pin_bar`.
- **Deferred to T3 router work**: `bos` (T2a routing memo + PR #380 `adr_exempt` schema gap).

Two replay-only ON↔OFF inverse questions (`volume_suppress` mon_fri/weekend, `adr_exempt` mon_fri/weekend additions) remain blocked on T6 backtest-live-parity engine work (`docs/redesign/buibui-redesign-t6-plan.md`). The third historically-tracked question (`volume_spike_boost` for non-`engulfing` strategies) was closed 2026-05-20 by the structural-inertness finding (deprecation, not audit — see `docs/audits/2026-05-20-volume-spike-boost-structural-inertness.md`).
