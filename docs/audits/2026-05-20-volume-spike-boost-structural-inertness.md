# `volume_spike_boost` — Structural Inertness Finding (2026-05-20)

**TL;DR**: The `volume_spike_boost` flag (per-strategy / directional / per-tf) is **structurally inert** in both the backtest engine and the live signal scanner. The branch that the flag controls cannot execute: by construction, no candle can be tagged `volume_spike=True` and `low_volume=True` simultaneously. The flag has zero behavioural effect today. Recommendation: **deprecate** (remove from TOML, engine, resolvers, run-id hash, audit tool).

This finding closes the originally-scoped Task 1 ("audit non-engulfing for OFF→ON inverse") referenced in `/tmp/next-conversation-prompt.md`. The audit is moot — the flag is non-functional for every strategy, not just non-engulfing.

## Background

PR #381 (`docs/audits/2026-05-17-volume-spike-boost.md`) audited the ON→OFF direction on `engulfing` (the only strategy with the flag set in production) and reported DISABLE for `15m long` and `15m short`. The "replay-only limitations" section flagged the OFF→ON inverse for non-engulfing as deferred until T6 backtest-live-parity closure. With T6 closed (PR #395), this audit attempted the OFF→ON pass — and instead discovered the underlying mechanism is non-functional.

## Engine semantics — the suppression gate

`analytics/backtest/engine.py:956`:

```python
if _suppress and is_low_vol and not (_boost and is_spike):
    continue
```

A trade is suppressed (skipped) iff:

1. `volume_suppress` is on for this direction (`_suppress`), AND
2. The signal candle is classified `low_volume` (`is_low_vol`), AND
3. NOT (boost is on AND candle is `volume_spike`).

The boost branch (clause 3) is the only escape hatch for a `low_vol` candle to survive a `_suppress=True` strategy.

## Why the boost branch is structurally dead

`analytics/backtest/gates.py`:

| Helper | Condition | Threshold (default) |
| --- | --- | --- |
| `_is_low_volume` | `volume < multiplier × mean(20)` | `multiplier=1.5` |
| `_is_volume_spike` | `volume > multiplier × mean(20)` | `multiplier=3.0` |

Both helpers use the same `volume` column, the same 20-candle lookback, and operate on the same `idx`. Their predicates partition the value space:

- `is_low_vol` is true when `vol/mean < 1.5`.
- `is_spike` is true when `vol/mean > 3.0`.

A single number cannot be both `< 1.5` and `> 3.0`. Therefore for every candle: **`is_low_vol AND is_spike` is False**.

Substituting back into the engine gate at line 956: when `is_low_vol=True`, `is_spike=False`, so `_boost AND is_spike` is False, so `not (...)` is True, so the suppression `continue` always fires when the first two clauses are met. The boost is never load-bearing.

The live scanner imports the same helpers (`analytics/signal/scanner.py:23` → `analytics.backtest.gates`), so the inertness applies to the live signal path verbatim.

## Empirical confirmation

```sql
SELECT COUNT(*) FROM backtest_trades WHERE volume_spike = TRUE AND low_volume = TRUE;
```

**Result: 0 rows of 812,895** — no trade in the entire `backtest_trades` table has both classifications.

| Count | Rows |
| --- | --- |
| `volume_spike = TRUE AND low_volume = TRUE` | **0** |
| `volume_spike = TRUE` (total) | 24,086 |
| `low_volume = TRUE` (total) | 162,163 |
| `low_volume IS NULL` (pre-PR#371 backfill rows) | (separate concern) |
| Total trades | 812,895 |

Every existing spike-tagged trade has `low_volume=False` (or NULL pre-backfill). Every existing low-vol trade has `volume_spike=False`. The two sets are disjoint, matching the threshold-disjointness proof.

## Re-reading the PR #381 verdicts

PR #381 reported `engulfing 15m long DISABLE` at n_supp=38, supp_avg_r=+0.27R, and `15m short DISABLE` at n_supp=32, supp_avg_r=+0.36R. The `tools/gate_audit.py::_gate_volume_spike_boost` mask is `volume_spike=True AND strategy.isin(boosted)` — it isolates spike trades on engulfing.

These 38 + 32 spike trades exist in `backtest_trades` because they had `low_volume=False` (spike implies not-low-vol). They were never at risk of suppression — `is_low_vol=False` short-circuits the engine gate's first clause regardless of `_suppress` or `_boost`. The boost flag did not save them; their `low_volume=False` status did.

PR #381's `DISABLE` verdict, re-read: "spike candles on engulfing have higher avg_r than the population." That observation is genuine and useful (it's a feature of the engulfing strategy on high-volume entries), but it is **not** evidence that `volume_spike_boost` is load-bearing.

## Where the flag exists today (deprecation surface)

Module-by-module touchpoints requiring removal (30 files at scan time):

### Engine + plumbing

- `analytics/backtest/engine.py` — `volume_spike_boost`, `_long`, `_short` kwargs (lines 777, 780-781), the gate condition at line 956
- `analytics/backtest_config.py` — TOML field on `StrategyOverride` / `BacktestSweepConfig`
- `analytics/backtest_runner.py` — pass-through
- `analytics/backtest/formatters.py` — output column / display
- `analytics/store/backtest_runs.py` — `_backtest_run_id` hash contribution (lines 30-31, 57-60) → new runs after removal will have **different** run_id hashes than old runs with the flag set. Existing rows untouched.

### Live signal path

- `analytics/signal/resolvers.py` — `_resolve_volume_spike_boost`, `_long`, `_short`
- `analytics/signal/scanner.py` — flag plumb-through into per-call backtest
- `analytics/signal/bt_cache.py` — kwargs forwarded to engine
- `analytics/signal/__init__.py` — re-exports
- `analytics/signal_config.py` — TOML loading
- `analytics/signal_test_runner.py` — replay runner kwargs

### Audit tooling

- `tools/gate_audit.py` — `_gate_volume_spike_boost` handler and `"volume-spike-boost"` registry entry (no longer meaningful without the flag)

### Configs

- `config/strategy_params.toml` — `volume_spike_boost = true` on engulfing (line 161); no other production strategy has the flag set

### Docs

- `CLAUDE.md` — `volume_spike_boost` mentioned in `[strategy_params.*]` section listing
- `.claude/context/analytics.md` — flag mentioned in engine kwargs surface
- `.claude/skills/signal-watch/SKILL.md` — flag listed in TOML reference
- `docs/system-overview.md`, `docs/redesign/buibui-redesign-bucket-c-options.md`, `docs/redesign/buibui-redesign-t6-phase-a-plan.md`, `docs/superpowers/specs/2026-05-02-ema-strategy-design.md`
- `docs/audits/2026-05-17-volume-spike-boost.md` — keep as-is for the historical record; add a forward-reference to this doc at the top
- `docs/audits/2026-05-17-adr-exempt.md`, `docs/audits/2026-05-17-adr-suppress-threshold.md`, `docs/audits/2026-05-18-bucket-c-toml.md`, `docs/audits/2026-05-18-bucket-c-dying-cells.md` — incidental mentions

### Tests

- `tests/test_gate_audit.py`, `tests/test_data_store.py`, `tests/test_signal_config.py`, `tests/test_regression.py` — flag-handling assertions to drop

## Run-id hash drift

`_backtest_run_id` (`analytics/store/backtest_runs.py:11-65`) appends `|spike_l` / `|spike_s` to the hash key when the directional flags are set. Today only engulfing carries the flag, and only via the symmetric `volume_spike_boost = true` (not the `_long` / `_short` directional form), so in practice **no production run_id today includes a `|spike_*` suffix** — confirmed by checking the resolver chain (the symmetric form does not feed into the directional hash). New runs after deprecation will have byte-identical run_ids to today's.

(The directional `volume_spike_boost_long` / `volume_spike_boost_short` per-tf forms exist in the resolver chain but are not set in any TOML in this repo at the time of this audit. Confirmed by `grep -r "volume_spike_boost_long\|volume_spike_boost_short" config/`.)

## Recommendation — deprecate

Remove the flag, the resolvers, the engine branch, the gate-audit handler, and the TOML setting. Net code reduction; net behaviour identical (because the flag has no behaviour to remove).

Two design alternatives were considered and rejected:

1. **Lower the spike multiplier or raise the low-vol multiplier so they overlap.** Changes the meaning of both classifications in ways that would invalidate every prior volume-related audit (`A14b`, `A15`, PR #375, PR #381). Strong "no" without independent justification.
2. **Redesign the boost as "spike candles bypass suppression on normal-vol candles too."** That would re-purpose the flag (apply it on non-low-vol candles), but those candles are not suppressed in the first place (the `is_low_vol` clause already lets them through). The redesign would still be a no-op.

Both alternatives collapse to "no useful semantic exists for this flag under the current suppression mechanism." Deprecation is the clean call.

## Sequencing forward

This doc is a stand-alone audit artifact. The code-removal PR ("deprecate volume_spike_boost") follows as a separate but immediate sweep:

1. **PR A (this doc + zero code changes)** — captures the finding; merges immediately; no regression risk.
2. **PR B (deprecation sweep)** — 30-file change touching engine, configs, tests, and docs. Default-off byte-identical at the rowset level (because the gate it gated was never firing); the only observable diff is `engulfing` losing `volume_spike_boost = true` from `config/strategy_params.toml`, which by this audit is a no-op. Single `make test` + `make typecheck` + `make lint-py` sweep.

Skip `make db-update` — no behaviour change, regression goldens unchanged.

## Spike-candle observation worth preserving separately

Independent of the boost flag, spike candles on `engulfing 15m` have notably higher avg_r than the population (PR #381 figures: long +0.27R vs −0.17R; short +0.36R vs +0.43R). That's a real edge that today's mechanism does not capture (because no spike candle is also low-vol, no spike candle is at risk of suppression, and there is therefore nothing the boost is protecting). If a future design wants to **promote** spike candles (e.g., size-up, tighter SL, or alert-priority routing), it should be a separate feature, not a revival of this flag's broken semantics.
