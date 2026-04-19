# A6 — Backtest Cache Design Spec

**Date:** 2026-04-19
**Status:** Approved
**Branch:** `feat/a6-backtest-cache`

## Problem

The live signal daemon reruns the full backtest on every scan cycle even when
candles haven't changed. `bt_cache` in `run_scan_cycle()` (signal_lib.py:968)
is a fresh dict born and discarded each cycle. Between candle closes:

- 15m TF: ~15 identical recomputes per candle
- 1h TF:  ~60 identical recomputes per candle
- 4h TF: ~240 identical recomputes per candle

At 5 symbols × 3 TFs × 10 strategies = 150 combos, this is the dominant
cost per cycle and causes alert latency. Daemon restarts multiple times per
day — in-memory-only solutions still pay the cold-start penalty each time.

## Solution

Two-layer cache: module-level dict (L1, fast) backed by a DuckDB table (L2,
survives restarts). Gated behind a `cache_enabled` flag so it can be disabled
in one TOML edit without a code deploy.

```text
Per cycle, per (symbol, tf, strategy) needing a backtest:

  1. Compute cache_key = sha256(run_id + last_candle_ts)[:24]
  2. L1 check: _bt_mem_cache dict  →  hit? return immediately
  3. L2 check: backtest_cache DB   →  hit? populate L1, return
  4. Miss: _compute_backtest() → store in L2 → populate L1 → return
```

## Cache Key Design

`last_candle_ts = ohlcv_df["open_time"].iloc[-2]` — the most recently closed
candle (index -2 because `_compute_backtest` strips the forming candle at -1).

When a new candle closes → timestamp advances → automatic invalidation.
When TOML params change → different `run_id` → different key → automatic
invalidation. No manual invalidation needed.

### Fix: `_backtest_run_id` Missing Params (data_store.py:539)

Current key string omits params that affect backtest results. All missing params
added as optional suffixes (same pattern as existing `adr_suppress_threshold`
suffix), so default values (`None`/`False`/`0.0`) produce no suffix change —
existing `backtest_runs` hashes are unchanged for default-param combos.

Params to add:

- `min_sl_pct` (when > 0)
- `atr_sl_multiplier` (when not None)
- `tp_r_long` / `tp_r_short` (when not None)
- `volume_suppress_long` / `volume_suppress_short` (when True)
- `volume_spike_boost_long` / `volume_spike_boost_short` (when True)
- `adr_exempt` (when True)

## DB Schema

New table added to `init_schema()` in `data_store.py`. Separate from
`backtest_runs` (user-visible Backtest tab) — internal scratch only.

```sql
CREATE TABLE IF NOT EXISTS backtest_cache (
    cache_key           TEXT    PRIMARY KEY,
    run_id              TEXT    NOT NULL,
    last_candle_ts      BIGINT  NOT NULL,
    symbol              TEXT    NOT NULL,
    timeframe           TEXT    NOT NULL,
    strategy            TEXT    NOT NULL,
    total_signals       INTEGER,
    closed_trades       INTEGER,
    win_count           INTEGER,
    loss_count          INTEGER,
    win_rate            DOUBLE,
    avg_r               DOUBLE,
    total_r             DOUBLE,
    max_drawdown_r      DOUBLE,
    recovery_factor     DOUBLE,
    long_closed_trades  INTEGER,
    long_win_count      INTEGER,
    long_win_rate       DOUBLE,
    long_avg_r          DOUBLE,
    long_total_r        DOUBLE,
    short_closed_trades INTEGER,
    short_win_count     INTEGER,
    short_win_rate      DOUBLE,
    short_avg_r         DOUBLE,
    short_total_r       DOUBLE,
    cached_at_ms        BIGINT  NOT NULL
)
```

`BacktestResult.trades` (individual Trade objects) are NOT stored — too large,
not needed for signal filtering. Only aggregate stats are cached.

## New Functions in `data_store.py`

All follow the existing `conn.register`/`conn.unregister` try/finally pattern
(CRITICAL: never switch to implicit replacement — malloc heap corruption risk).

```text
get_backtest_cache(conn, cache_key) -> BacktestResult | None
put_backtest_cache(conn, cache_key, run_id, last_candle_ts,
                   symbol, tf, strategy, result) -> None   # INSERT OR REPLACE
prune_backtest_cache(conn, keep_days=30) -> None
```

## Feature Flag

`BacktestFilterConfig` in `signal_config.py` gains a `cache_enabled: bool`
field (default `True`). In TOML:

```toml
[backtest]
cache_enabled = true
```

When `false`: `run_scan_cycle` skips all get/put calls, calls
`_compute_backtest` directly — exact pre-A6 behaviour. Rollback = flip to
`false` + restart, no code deploy needed.

## Changes to `signal_lib.py`

`bt_cache: dict[...] = {}` (line 968) removed. Replaced with module-level:

```python
_bt_mem_cache: dict[str, BacktestResult | None] = {}
```

Phase 3 of `run_scan_cycle` lookup. The `cache_enabled` flag gates **both**
layers — when `false`, behaviour is byte-for-byte identical to pre-A6:

```python
if backtest_cfg.cache_enabled:
    cache_key = _make_bt_cache_key(run_id, last_candle_ts)
    if cache_key in _bt_mem_cache:
        result = _bt_mem_cache[cache_key]             # L1 hit
    else:
        result = get_backtest_cache(conn, cache_key)  # L2 hit
        if result is None:
            result = _compute_backtest(...)           # full compute
            if result is not None:
                put_backtest_cache(conn, cache_key, ...)
        _bt_mem_cache[cache_key] = result
else:
    result = _compute_backtest(...)                   # cache disabled: pre-A6 path
```

`_bt_mem_cache` stays bounded naturally: 150 combos × ~2 active candle
timestamps per TF = ~300 entries at steady state. Old entries are never
explicitly evicted — they become unreachable as candles advance.

Expose `_reset_bt_cache() -> None` (`_bt_mem_cache.clear()`) for test isolation.

## Changes to `signal_runner.py`

Call `prune_backtest_cache` once at startup after `init_schema`:

```python
with duckdb.connect(str(db_path)) as prune_conn:
    prune_backtest_cache(prune_conn)
```

## Regression Safety

- **Additive schema**: `backtest_cache` is a new table. No existing tables
  touched. Rollback = `DROP TABLE IF EXISTS backtest_cache` (or just leave it).
- **run_id backward compat**: new params use optional-suffix pattern — default
  values produce identical hashes to today. Existing `backtest_runs` unaffected.
- **`_compute_backtest` unchanged**: cache is a layer in front of it, not a
  replacement. Regression golden files (`make test-regression`) verify this.
- **Test isolation**: `_reset_bt_cache()` called in test fixtures to prevent
  module-level state bleed between tests.

## Testing Checklist

| Test | Location |
| ---- | -------- |
| `get/put/prune_backtest_cache` with `:memory:` DB | `tests/test_data_store.py` |
| Round-trip fidelity: `_compute_backtest` result == `get_backtest_cache` result | `tests/test_data_store.py` |
| Cache miss on new candle (different `last_candle_ts`) | `tests/test_data_store.py` |
| Cache miss on param change (different `run_id`) | `tests/test_data_store.py` |
| Prune respects TTL (old rows deleted, recent rows kept) | `tests/test_data_store.py` |
| `_backtest_run_id` unchanged for default params | `tests/test_data_store.py` |
| Module-level isolation via `_reset_bt_cache()` | `tests/test_signal_lib.py` |
| Full suite green | `make test` (1095 tests) |
| Regression golden files | `make test-regression` |

## Expected Cache Hit Rate (Post-First-Cycle)

| TF  | Cycles between closes | L1/L2 hit rate |
| --- | --------------------- | -------------- |
| 15m | ~15                   | ~93%           |
| 1h  | ~60                   | ~98%           |
| 4h  | ~240                  | ~99.6%         |

Post-restart: L2 (DB) hit on the first cycle — no cold-start penalty.

## Rollback Playbook

| Situation | Action | Time |
| --------- | ------ | ---- |
| Cache returning wrong results | Set `cache_enabled = false` in TOML + restart | <1 min |
| DB table causing issues | `DROP TABLE IF EXISTS backtest_cache` | <1 min |
| Full rollback | Git revert + drop table + restart | ~5 min |

## Deployment Order

1. Deploy with `cache_enabled = false` — daemon runs as today, table unused
2. Confirm daemon starts clean
3. Flip to `cache_enabled = true`, restart
4. Monitor one full candle-close cycle — check logs for hit/miss counts
5. If wrong: flip back to `false` in <1 min

## File Change Summary

| File | Change |
| ---- | ------ |
| `analytics/data_store.py` | Fix `_backtest_run_id`; new `backtest_cache` table; `get/put/prune_backtest_cache` |
| `analytics/signal_lib.py` | `bt_cache` → module-level `_bt_mem_cache`; L1→L2→compute lookup; `_reset_bt_cache()` |
| `analytics/signal_runner.py` | `prune_backtest_cache` at startup |
| `analytics/signal_config.py` | `cache_enabled: bool = True` on `BacktestFilterConfig` |
| `tests/test_data_store.py` | Cache get/put/prune/round-trip/TTL tests |
| `tests/test_signal_lib.py` | `_reset_bt_cache()` in fixtures |
