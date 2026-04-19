# A6 Backtest Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate redundant full backtest recomputes in the live signal daemon
by adding a two-layer cache (module-level dict + DuckDB table) with a
`cache_enabled` flag for instant rollback.

**Architecture:** `data_store.py` gets a `BacktestSnapshot` dataclass, a new
`backtest_cache` DB table, and three cache functions. `signal_lib.py` gets a
module-level `_bt_mem_cache` dict (L1) that writes through to DuckDB (L2).
The `cache_enabled` flag on `BacktestFilterConfig` gates both layers.

**Tech Stack:** Python 3.11, DuckDB, dataclasses, hashlib, pytest + duckdb
in-memory.

---

## File Map

| File | What changes |
| ---- | ------------ |
| `analytics/data_store.py` | New `BacktestSnapshot` dataclass; updated `_backtest_run_id` (8 new optional suffix params); `backtest_cache` table in `init_schema`; `_make_bt_cache_key`, `get_backtest_cache`, `put_backtest_cache`, `prune_backtest_cache` |
| `analytics/signal_config.py` | `cache_enabled: bool = True` field on `BacktestFilterConfig` |
| `analytics/signal_lib.py` | Module-level `_bt_mem_cache` + `_reset_bt_cache`; Phase 3 rewrite (L1→L2→compute); `bt_to_save` for end-of-cycle persist; updated imports |
| `analytics/signal_runner.py` | `prune_backtest_cache` call at startup |
| `tests/test_data_store.py` | New test class for cache get/put/prune/round-trip/TTL/run-id compat |
| `tests/test_signal_lib.py` | `_reset_bt_cache()` in fixtures |

---

## Task 1: `BacktestSnapshot` + cache functions in `data_store.py`

**Files:**

- Modify: `analytics/data_store.py`
- Test: `tests/test_data_store.py`

### Why `BacktestSnapshot`?

`BacktestResult` computes all stats from its `trades: list[Trade]`. We cannot
store individual trades in the cache (too large). `BacktestSnapshot` stores the
pre-computed scalar values and exposes them as properties with the same names
as `BacktestResult`, so the signal filter works unchanged.

Properties accessed by `signal_lib.py` that `BacktestSnapshot` must provide:

- `closed_trades` — only `len()` and truthiness used
- `long_closed_trades`, `short_closed_trades` — only `len()` used
- `win_count`, `loss_count`, `win_rate`, `avg_r`, `total_r`
- `long_win_count`, `long_win_rate`, `long_avg_r`, `long_total_r`
- `short_win_count`, `short_win_rate`, `short_avg_r`, `short_total_r`
- `median_duration_h`, `long_median_duration_h`, `short_median_duration_h`

### Step 1.1 — Write failing tests

- [ ] Add to `tests/test_data_store.py`:

```python
import hashlib
import time

from analytics.data_store import (
    BacktestSnapshot,
    _backtest_run_id,
    _make_bt_cache_key,
    get_backtest_cache,
    put_backtest_cache,
    prune_backtest_cache,
)
from analytics.backtest_lib import BacktestResult, Trade


def _make_result(symbol: str = "BTCUSDT", tf: str = "1h", strategy: str = "engulfing") -> BacktestResult:
    """Minimal BacktestResult with 2 wins and 1 loss."""
    entry = 50000.0
    sl = 49000.0
    tp = 52000.0
    win = Trade(
        signal_time=1_000_000,
        entry_time=1_100_000,
        entry_price=entry,
        direction="long",
        sl_price=sl,
        tp_price=tp,
        exit_time=2_000_000,
        exit_price=tp,
        outcome="win",
    )
    loss = Trade(
        signal_time=1_000_000,
        entry_time=1_100_000,
        entry_price=entry,
        direction="long",
        sl_price=sl,
        tp_price=tp,
        exit_time=2_000_000,
        exit_price=sl,
        outcome="loss",
    )
    return BacktestResult(symbol=symbol, timeframe=tf, strategy=strategy, trades=[win, win, loss])


class TestBacktestCache:
    def test_get_miss(self, conn: duckdb.DuckDBPyConnection) -> None:
        assert get_backtest_cache(conn, "nonexistent") is None

    def test_put_and_get_round_trip(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        snap = get_backtest_cache(conn, "key1")
        assert snap is not None
        assert isinstance(snap, BacktestSnapshot)
        assert len(snap.closed_trades) == len(result.closed_trades)
        assert len(snap.long_closed_trades) == len(result.long_closed_trades)
        assert len(snap.short_closed_trades) == len(result.short_closed_trades)
        assert snap.win_count == result.win_count
        assert snap.win_rate == pytest.approx(result.win_rate)
        assert snap.avg_r == pytest.approx(result.avg_r)
        assert snap.long_win_rate == pytest.approx(result.long_win_rate)
        assert snap.long_avg_r == pytest.approx(result.long_avg_r)

    def test_get_miss_on_different_key(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        assert get_backtest_cache(conn, "key2") is None

    def test_prune_removes_old_entries(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "new_key", "run_new", 100_000, result)
        # Clone the new_key row as "old_key" with cached_at_ms backdated 40 days.
        old_ms = int(time.time() * 1000) - 40 * 24 * 3600 * 1000
        conn.execute(
            "INSERT INTO backtest_cache "
            "SELECT 'old_key', run_id, last_candle_ts, symbol, timeframe, strategy, fee_pct, "
            "n_closed, n_long, n_short, n_win, n_loss, r_win_rate, r_avg, r_total, "
            "n_long_win, r_long_win_rate, r_long_avg, r_long_total, "
            "n_short_win, r_short_win_rate, r_short_avg, r_short_total, "
            "h_median, h_long_median, h_short_median, ? "
            "FROM backtest_cache WHERE cache_key = 'new_key'",
            [old_ms],
        )
        prune_backtest_cache(conn, keep_days=30)
        assert get_backtest_cache(conn, "old_key") is None
        assert get_backtest_cache(conn, "new_key") is not None

    def test_prune_keeps_recent_entries(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        prune_backtest_cache(conn, keep_days=30)
        assert get_backtest_cache(conn, "key1") is not None

    def test_backtest_run_id_unchanged_for_defaults(self) -> None:
        old_id = _backtest_run_id(
            "BTCUSDT", "1h", "engulfing", 90, 0.02, 3.0, 0.0, "off", 1, None
        )
        new_id = _backtest_run_id(
            "BTCUSDT", "1h", "engulfing", 90, 0.02, 3.0, 0.0, "off", 1, None,
            min_sl_pct=0.0,
            atr_sl_multiplier=None,
            tp_r_long=None,
            tp_r_short=None,
            volume_suppress_long=None,
            volume_suppress_short=None,
            volume_spike_boost_long=None,
            volume_spike_boost_short=None,
            adr_exempt=False,
        )
        assert old_id == new_id

    def test_backtest_run_id_changes_for_nondefault_min_sl(self) -> None:
        base = _backtest_run_id("BTCUSDT", "1h", "engulfing", 90, 0.02, 3.0, 0.0, "off", 1, None)
        with_min_sl = _backtest_run_id(
            "BTCUSDT", "1h", "engulfing", 90, 0.02, 3.0, 0.0, "off", 1, None,
            min_sl_pct=0.005,
        )
        assert base != with_min_sl

    def test_make_bt_cache_key_changes_with_ts(self) -> None:
        k1 = _make_bt_cache_key("run1", 100)
        k2 = _make_bt_cache_key("run1", 200)
        assert k1 != k2

    def test_make_bt_cache_key_changes_with_run_id(self) -> None:
        k1 = _make_bt_cache_key("run1", 100)
        k2 = _make_bt_cache_key("run2", 100)
        assert k1 != k2

    def test_snapshot_truthiness(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        snap = get_backtest_cache(conn, "key1")
        assert snap is not None
        assert bool(snap.closed_trades)   # len > 0 is truthy
        assert not bool(snap.short_closed_trades)  # all trades are long
```

- [ ] Run — expect import errors (symbols don't exist yet):

```bash
cd /home/kng/repo/buibui-moon-trader-bot
poetry run pytest tests/test_data_store.py::TestBacktestCache -v 2>&1 | head -30
```

### Step 1.2 — Add `BacktestSnapshot` dataclass to `data_store.py`

- [ ] In `analytics/data_store.py`, check the existing imports for
  `from dataclasses import dataclass`. If not present, add it. Then find the
  line `def init_schema` and insert before it:

```python
@dataclass
class BacktestSnapshot:
    """Pre-computed aggregate stats cached in backtest_cache table.

    Duck-type compatible with BacktestResult for signal filtering.
    closed_trades / long_closed_trades / short_closed_trades return dummy
    lists of the correct length — only len() and truthiness are used by callers.
    """

    symbol: str
    timeframe: str
    strategy: str
    fee_pct: float = 0.0
    n_closed: int = 0
    n_long: int = 0
    n_short: int = 0
    n_win: int = 0
    n_loss: int = 0
    r_win_rate: float = 0.0
    r_avg: float = 0.0
    r_total: float = 0.0
    n_long_win: int = 0
    r_long_win_rate: float | None = None
    r_long_avg: float | None = None
    r_long_total: float = 0.0
    n_short_win: int = 0
    r_short_win_rate: float | None = None
    r_short_avg: float | None = None
    r_short_total: float = 0.0
    h_median: float | None = None
    h_long_median: float | None = None
    h_short_median: float | None = None

    @property
    def closed_trades(self) -> list[None]:
        return [None] * self.n_closed

    @property
    def long_closed_trades(self) -> list[None]:
        return [None] * self.n_long

    @property
    def short_closed_trades(self) -> list[None]:
        return [None] * self.n_short

    @property
    def win_count(self) -> int:
        return self.n_win

    @property
    def loss_count(self) -> int:
        return self.n_loss

    @property
    def win_rate(self) -> float:
        return self.r_win_rate

    @property
    def avg_r(self) -> float:
        return self.r_avg

    @property
    def total_r(self) -> float:
        return self.r_total

    @property
    def long_win_count(self) -> int:
        return self.n_long_win

    @property
    def long_win_rate(self) -> float | None:
        return self.r_long_win_rate

    @property
    def long_avg_r(self) -> float | None:
        return self.r_long_avg

    @property
    def long_total_r(self) -> float:
        return self.r_long_total

    @property
    def short_win_count(self) -> int:
        return self.n_short_win

    @property
    def short_win_rate(self) -> float | None:
        return self.r_short_win_rate

    @property
    def short_avg_r(self) -> float | None:
        return self.r_short_avg

    @property
    def short_total_r(self) -> float:
        return self.r_short_total

    @property
    def median_duration_h(self) -> float | None:
        return self.h_median

    @property
    def long_median_duration_h(self) -> float | None:
        return self.h_long_median

    @property
    def short_median_duration_h(self) -> float | None:
        return self.h_short_median
```

Note: `data_store.py` is a pure data-access module and does not currently use
`@dataclass`. This will be the first use — add the import.

### Step 1.3 — Add `backtest_cache` table to `init_schema`

- [ ] In `analytics/data_store.py`, at the end of `init_schema()`, append:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_cache (
            cache_key       TEXT    PRIMARY KEY,
            run_id          TEXT    NOT NULL,
            last_candle_ts  BIGINT  NOT NULL,
            symbol          TEXT    NOT NULL,
            timeframe       TEXT    NOT NULL,
            strategy        TEXT    NOT NULL,
            fee_pct         DOUBLE  NOT NULL,
            n_closed        INTEGER NOT NULL,
            n_long          INTEGER NOT NULL,
            n_short         INTEGER NOT NULL,
            n_win           INTEGER NOT NULL,
            n_loss          INTEGER NOT NULL,
            r_win_rate      DOUBLE  NOT NULL,
            r_avg           DOUBLE  NOT NULL,
            r_total         DOUBLE  NOT NULL,
            n_long_win      INTEGER NOT NULL,
            r_long_win_rate DOUBLE,
            r_long_avg      DOUBLE,
            r_long_total    DOUBLE  NOT NULL,
            n_short_win     INTEGER NOT NULL,
            r_short_win_rate DOUBLE,
            r_short_avg     DOUBLE,
            r_short_total   DOUBLE  NOT NULL,
            h_median        DOUBLE,
            h_long_median   DOUBLE,
            h_short_median  DOUBLE,
            cached_at_ms    BIGINT  NOT NULL
        )
    """)
```

### Step 1.4 — Update `_backtest_run_id` with missing params

- [ ] Replace the existing `_backtest_run_id` function (lines 520–544) with:

```python
def _backtest_run_id(
    symbol: str,
    timeframe: str,
    strategy: str,
    days: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
    smt_trend_filter: int,
    secondary_symbol: str | None,
    adr_suppress_threshold: float | None = None,
    volume_suppress: bool | None = None,
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    tp_r_long: float | None = None,
    tp_r_short: float | None = None,
    volume_suppress_long: bool | None = None,
    volume_suppress_short: bool | None = None,
    volume_spike_boost_long: bool | None = None,
    volume_spike_boost_short: bool | None = None,
    adr_exempt: bool = False,
) -> str:
    """Return a deterministic 16-char hex ID for a backtest param combination.

    Optional suffixes are appended only when set so existing run_ids are
    unchanged (None = flag not applied, same hash as before these columns).
    """
    key = f"{symbol}|{timeframe}|{strategy}|{days}|{sl_pct}|{tp_r}|{fee_pct}|{day_filter}|{smt_trend_filter}|{secondary_symbol}"
    if adr_suppress_threshold is not None:
        key += f"|adr:{adr_suppress_threshold}"
    if volume_suppress:
        key += "|vol_suppress"
    if min_sl_pct > 0.0:
        key += f"|min_sl:{min_sl_pct}"
    if atr_sl_multiplier is not None:
        key += f"|atr_sl:{atr_sl_multiplier}"
    if tp_r_long is not None:
        key += f"|tp_long:{tp_r_long}"
    if tp_r_short is not None:
        key += f"|tp_short:{tp_r_short}"
    if volume_suppress_long:
        key += "|vol_sup_l"
    if volume_suppress_short:
        key += "|vol_sup_s"
    if volume_spike_boost_long:
        key += "|spike_l"
    if volume_spike_boost_short:
        key += "|spike_s"
    if adr_exempt:
        key += "|adr_exempt"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

Check `analytics/data_store.py` imports — `hashlib` must be imported. Add it if missing.

### Step 1.5 — Add `_make_bt_cache_key`, `get_backtest_cache`, `put_backtest_cache`, `prune_backtest_cache`

- [ ] After `_backtest_run_id`, add:

```python
def _make_bt_cache_key(run_id: str, last_candle_ts: int) -> str:
    """24-char hex key combining run params hash and last closed candle timestamp."""
    return hashlib.sha256(f"{run_id}|{last_candle_ts}".encode()).hexdigest()[:24]


def get_backtest_cache(
    conn: duckdb.DuckDBPyConnection,
    cache_key: str,
) -> "BacktestSnapshot | None":
    """Return cached BacktestSnapshot for cache_key, or None on miss."""
    row = conn.execute(
        "SELECT symbol, timeframe, strategy, fee_pct, "
        "n_closed, n_long, n_short, n_win, n_loss, "
        "r_win_rate, r_avg, r_total, "
        "n_long_win, r_long_win_rate, r_long_avg, r_long_total, "
        "n_short_win, r_short_win_rate, r_short_avg, r_short_total, "
        "h_median, h_long_median, h_short_median "
        "FROM backtest_cache WHERE cache_key = ?",
        [cache_key],
    ).fetchone()
    if row is None:
        return None
    return BacktestSnapshot(
        symbol=str(row[0]),
        timeframe=str(row[1]),
        strategy=str(row[2]),
        fee_pct=float(row[3]),
        n_closed=int(row[4]),
        n_long=int(row[5]),
        n_short=int(row[6]),
        n_win=int(row[7]),
        n_loss=int(row[8]),
        r_win_rate=float(row[9]),
        r_avg=float(row[10]),
        r_total=float(row[11]),
        n_long_win=int(row[12]),
        r_long_win_rate=float(row[13]) if row[13] is not None else None,
        r_long_avg=float(row[14]) if row[14] is not None else None,
        r_long_total=float(row[15]) if row[15] is not None else 0.0,
        n_short_win=int(row[16]),
        r_short_win_rate=float(row[17]) if row[17] is not None else None,
        r_short_avg=float(row[18]) if row[18] is not None else None,
        r_short_total=float(row[19]) if row[19] is not None else 0.0,
        h_median=float(row[20]) if row[20] is not None else None,
        h_long_median=float(row[21]) if row[21] is not None else None,
        h_short_median=float(row[22]) if row[22] is not None else None,
    )


def put_backtest_cache(
    conn: duckdb.DuckDBPyConnection,
    cache_key: str,
    run_id: str,
    last_candle_ts: int,
    result: Any,
) -> None:
    """Persist a BacktestResult's aggregate stats to backtest_cache.

    result must be a BacktestResult instance. Trades are not stored.
    Uses INSERT OR REPLACE — safe to call multiple times with the same key.
    """
    from analytics.backtest_lib import BacktestResult

    assert isinstance(result, BacktestResult)
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT OR REPLACE INTO backtest_cache VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            cache_key,
            run_id,
            last_candle_ts,
            result.symbol,
            result.timeframe,
            result.strategy,
            result.fee_pct,
            len(result.closed_trades),
            len(result.long_closed_trades),
            len(result.short_closed_trades),
            result.win_count,
            result.loss_count,
            result.win_rate,
            result.avg_r,
            result.total_r,
            result.long_win_count,
            result.long_win_rate,
            result.long_avg_r,
            result.long_total_r,
            result.short_win_count,
            result.short_win_rate,
            result.short_avg_r,
            result.short_total_r,
            result.median_duration_h,
            result.long_median_duration_h,
            result.short_median_duration_h,
            now_ms,
        ],
    )


def prune_backtest_cache(
    conn: duckdb.DuckDBPyConnection,
    keep_days: int = 30,
) -> None:
    """Delete backtest_cache rows older than keep_days."""
    cutoff_ms = int(time.time() * 1000) - keep_days * 24 * 3600 * 1000
    conn.execute("DELETE FROM backtest_cache WHERE cached_at_ms < ?", [cutoff_ms])
```

Note: `time` is already imported in `data_store.py`. Verify — if not, add
`import time`.

### Step 1.6 — Run the tests

- [ ] Run:

```bash
cd /home/kng/repo/buibui-moon-trader-bot
poetry run pytest tests/test_data_store.py::TestBacktestCache -v
```

Expected: all 9 tests pass.

- [ ] Also run the full data_store test suite to check no regressions:

```bash
poetry run pytest tests/test_data_store.py -v
```

### Step 1.7 — Typecheck and lint

- [ ] Run:

```bash
make typecheck && make lint-py
```

Fix any mypy or ruff errors before continuing.

### Step 1.8 — Commit

- [ ] Update imports in test file — add `BacktestSnapshot`, `_make_bt_cache_key`,
  `get_backtest_cache`, `put_backtest_cache`, `prune_backtest_cache` to the import
  block in `tests/test_data_store.py`.

- [ ] Commit:

```bash
git add analytics/data_store.py tests/test_data_store.py
git commit -m "feat(cache): add BacktestSnapshot, backtest_cache table, cache get/put/prune"
```

---

## Task 2: Add `cache_enabled` to `BacktestFilterConfig`

**Files:**

- Modify: `analytics/signal_config.py`
- Test: `tests/test_signal_lib.py` (existing TOML loading tests cover this indirectly)

### Step 2.1 — Add field

- [ ] In `analytics/signal_config.py`, in the `BacktestFilterConfig` dataclass
  (after `volume_spike_boost: bool = False` at ~line 178), add:

```python
    # Set to false to disable the two-layer backtest cache and revert to
    # recomputing on every cycle. Useful for debugging or instant rollback.
    cache_enabled: bool = True
```

### Step 2.2 — Write failing test

- [ ] In `tests/test_signal_lib.py` (or `tests/test_signal_config.py` if it exists),
  add:

```python
from analytics.signal_config import BacktestFilterConfig


class TestBacktestFilterConfigCache:
    def test_cache_enabled_defaults_true(self) -> None:
        cfg = BacktestFilterConfig()
        assert cfg.cache_enabled is True

    def test_cache_enabled_can_be_disabled(self) -> None:
        cfg = BacktestFilterConfig(cache_enabled=False)
        assert cfg.cache_enabled is False
```

- [ ] Run — expect failure (field doesn't exist yet):

```bash
poetry run pytest tests/test_signal_lib.py::TestBacktestFilterConfigCache -v
```

### Step 2.3 — Run tests after adding field

- [ ] After adding the field, run:

```bash
poetry run pytest tests/test_signal_lib.py::TestBacktestFilterConfigCache -v
```

Expected: 2 tests pass.

### Step 2.4 — Commit

```bash
git add analytics/signal_config.py tests/test_signal_lib.py
git commit -m "feat(cache): add cache_enabled flag to BacktestFilterConfig"
```

---

## Task 3: Rewrite Phase 3 backtest lookup in `signal_lib.py`

**Files:**

- Modify: `analytics/signal_lib.py`
- Test: `tests/test_signal_lib.py`

This is the largest task. Read it fully before starting.

### Overview of changes

1. Remove `bt_cache: dict[...] = {}` at line 968.
2. Add module-level `_bt_mem_cache` and `_reset_bt_cache()`.
3. Introduce `bt_to_save` (local per-cycle dict) for end-of-cycle persist.
4. Rewrite the `for event in passing_events:` block (lines 1134–1199).
5. Update end-of-cycle save block (lines 1570–1605) to use `bt_to_save`.
6. Add imports: `BacktestSnapshot`, `_make_bt_cache_key`, `get_backtest_cache`,
   `put_backtest_cache` from `analytics.data_store`.

### Step 3.1 — Write failing test for `_reset_bt_cache`

- [ ] Add to `tests/test_signal_lib.py`:

```python
from analytics.signal_lib import _bt_mem_cache, _reset_bt_cache


class TestBtMemCache:
    def setup_method(self) -> None:
        _reset_bt_cache()

    def teardown_method(self) -> None:
        _reset_bt_cache()

    def test_reset_clears_cache(self) -> None:
        _bt_mem_cache["some_key"] = None
        assert "some_key" in _bt_mem_cache
        _reset_bt_cache()
        assert "some_key" not in _bt_mem_cache

    def test_cache_starts_empty_after_reset(self) -> None:
        _reset_bt_cache()
        assert len(_bt_mem_cache) == 0
```

- [ ] Run — expect ImportError (`_bt_mem_cache` not exported yet):

```bash
poetry run pytest tests/test_signal_lib.py::TestBtMemCache -v
```

### Step 3.2 — Add module-level cache to `signal_lib.py`

- [ ] In `analytics/signal_lib.py`, update the `from analytics.data_store import ...`
  block to include the new symbols. The full updated block should be:

```python
from analytics.data_store import (
    BacktestSnapshot,
    _backtest_run_id,
    _make_bt_cache_key,
    get_backtest_cache,
    get_funding_rates,
    get_ohlcv,
    get_signals_history,
    put_backtest_cache,
    upsert_backtest_run,
    upsert_signal_outcome,
    upsert_signals,
)
```

- [ ] After the logger line (`logger = logging.getLogger(__name__)`), add:

```python
# Persistent cross-cycle backtest cache (L1). Keys are sha256 cache keys from
# _make_bt_cache_key(). Survives across scan cycles; cleared only by _reset_bt_cache().
_bt_mem_cache: dict[str, "BacktestResult | BacktestSnapshot | None"] = {}


def _reset_bt_cache() -> None:
    """Clear the module-level backtest cache. Used in tests to prevent state bleed."""
    _bt_mem_cache.clear()
```

- [ ] Run the `TestBtMemCache` tests:

```bash
poetry run pytest tests/test_signal_lib.py::TestBtMemCache -v
```

Expected: 2 tests pass.

### Step 3.3 — Remove per-cycle `bt_cache` and add `bt_to_save`

- [ ] In `analytics/signal_lib.py`, find and remove the per-cycle `bt_cache` dict at line 968:

```python
# REMOVE this line:
bt_cache: dict[tuple[str, str, str], BacktestResult | None] = {}
```

- [ ] Directly above it (around line 966), add `bt_to_save` in its place:

```python
# Collects only newly computed BacktestResult objects this cycle for end-of-cycle
# persist to backtest_runs. Cache hits (BacktestSnapshot) are excluded — they
# were already saved when first computed.
bt_to_save: dict[tuple[str, str, str], BacktestResult | None] = {}
```

### Step 3.4 — Rewrite the `for event in passing_events:` block

- [ ] Find the block starting at line 1133 (approximately):

```python
        bt_results: dict[str, BacktestResult | None] = {}
        if backtest_cfg and backtest_cfg.mode != "off":
            for event in passing_events:
                bt_key = (symbol, tf, event.strategy)
                if bt_key not in bt_cache:
                    eff_tp_r = _resolve_tp_r(
                        strategy_params, event.strategy, symbol, tf, tp_r
                    )
                    eff_sl_pct = _resolve_sl_pct(
                        strategy_params, event.strategy, symbol, tf, sl_pct
                    )
                    eff_atr_sl = _resolve_atr_sl_multiplier(
                        strategy_params,
                        event.strategy,
                        symbol,
                        tf,
                        atr_sl_multiplier,
                    )
                    _tp_r_long = _resolve_tp_r(
                        strategy_params, event.strategy, symbol, tf, tp_r, "long"
                    )
                _tp_r_short = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r, "short"
                )
                bt_cache[bt_key] = _compute_backtest(
                    ohlcv_df=ohlcv_df,
                    strategy=event.strategy,
                    secondary_df=sec_df,
                    funding_df=funding_df,
                    symbol=symbol,
                    timeframe=tf,
                    sl_pct=eff_sl_pct,
                    tp_r=eff_tp_r,
                    fee_pct=backtest_cfg.fee_pct,
                    day_filter=day_filter,
                    min_sl_pct=backtest_cfg.min_sl_pct,
                    atr_sl_multiplier=eff_atr_sl,
                    adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                    if bias_cfg
                    else None,
                    adr_exempt=_is_adr_exempt(strategy_params, event.strategy),
                    volume_suppress=_resolve_volume_suppress(
                        strategy_params,
                        event.strategy,
                        backtest_cfg.volume_suppress,
                    ),
                    volume_spike_boost=_resolve_volume_spike_boost(
                        strategy_params,
                        event.strategy,
                        backtest_cfg.volume_spike_boost,
                    ),
                    volume_suppress_long=_resolve_volume_suppress_long(
                        strategy_params, event.strategy
                    ),
                    volume_suppress_short=_resolve_volume_suppress_short(
                        strategy_params, event.strategy
                    ),
                    volume_spike_boost_long=_resolve_volume_spike_boost_long(
                        strategy_params, event.strategy
                    ),
                    volume_spike_boost_short=_resolve_volume_spike_boost_short(
                        strategy_params, event.strategy
                    ),
                    tp_r_long=_tp_r_long if _tp_r_long != eff_tp_r else None,
                    tp_r_short=_tp_r_short if _tp_r_short != eff_tp_r else None,
                )
                bt_results[event.strategy] = bt_cache[bt_key]
```

Replace the entire block with:

```python
        bt_results: dict[str, BacktestResult | BacktestSnapshot | None] = {}
        if backtest_cfg and backtest_cfg.mode != "off":
            for event in passing_events:
                eff_tp_r = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r
                )
                eff_sl_pct = _resolve_sl_pct(
                    strategy_params, event.strategy, symbol, tf, sl_pct
                )
                eff_atr_sl = _resolve_atr_sl_multiplier(
                    strategy_params, event.strategy, symbol, tf, atr_sl_multiplier
                )
                _tp_r_long = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r, "long"
                )
                _tp_r_short = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r, "short"
                )
                _eff_tp_r_long = _tp_r_long if _tp_r_long != eff_tp_r else None
                _eff_tp_r_short = _tp_r_short if _tp_r_short != eff_tp_r else None

                if backtest_cfg.cache_enabled:
                    _sec_sym = (
                        (secondary_map or {}).get(symbol)
                        if event.strategy == "smt_divergence"
                        else None
                    )
                    run_id = _backtest_run_id(
                        symbol=symbol,
                        timeframe=tf,
                        strategy=event.strategy,
                        days=backtest_cfg.days,
                        sl_pct=eff_sl_pct,
                        tp_r=eff_tp_r,
                        fee_pct=backtest_cfg.fee_pct,
                        day_filter=day_filter,
                        smt_trend_filter=smt_trend_filter,
                        secondary_symbol=_sec_sym,
                        adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                        if bias_cfg
                        else None,
                        volume_suppress=_resolve_volume_suppress(
                            strategy_params, event.strategy, backtest_cfg.volume_suppress
                        )
                        or False,
                        min_sl_pct=backtest_cfg.min_sl_pct,
                        atr_sl_multiplier=eff_atr_sl,
                        tp_r_long=_eff_tp_r_long,
                        tp_r_short=_eff_tp_r_short,
                        volume_suppress_long=_resolve_volume_suppress_long(
                            strategy_params, event.strategy
                        ),
                        volume_suppress_short=_resolve_volume_suppress_short(
                            strategy_params, event.strategy
                        ),
                        volume_spike_boost_long=_resolve_volume_spike_boost_long(
                            strategy_params, event.strategy
                        ),
                        volume_spike_boost_short=_resolve_volume_spike_boost_short(
                            strategy_params, event.strategy
                        ),
                        adr_exempt=_is_adr_exempt(strategy_params, event.strategy),
                    )
                    last_candle_ts = int(ohlcv_df["open_time"].iloc[-2])
                    cache_key = _make_bt_cache_key(run_id, last_candle_ts)

                    if cache_key in _bt_mem_cache:
                        bt_results[event.strategy] = _bt_mem_cache[cache_key]
                        continue

                    snapshot = get_backtest_cache(conn, cache_key)
                    if snapshot is not None:
                        _bt_mem_cache[cache_key] = snapshot
                        bt_results[event.strategy] = snapshot
                        continue

                    # Cache miss — full compute
                    _bt_key = (symbol, tf, event.strategy)
                    computed = _compute_backtest(
                        ohlcv_df=ohlcv_df,
                        strategy=event.strategy,
                        secondary_df=sec_df,
                        funding_df=funding_df,
                        symbol=symbol,
                        timeframe=tf,
                        sl_pct=eff_sl_pct,
                        tp_r=eff_tp_r,
                        fee_pct=backtest_cfg.fee_pct,
                        day_filter=day_filter,
                        min_sl_pct=backtest_cfg.min_sl_pct,
                        atr_sl_multiplier=eff_atr_sl,
                        adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                        if bias_cfg
                        else None,
                        adr_exempt=_is_adr_exempt(strategy_params, event.strategy),
                        volume_suppress=_resolve_volume_suppress(
                            strategy_params, event.strategy, backtest_cfg.volume_suppress
                        ),
                        volume_spike_boost=_resolve_volume_spike_boost(
                            strategy_params, event.strategy, backtest_cfg.volume_spike_boost
                        ),
                        volume_suppress_long=_resolve_volume_suppress_long(
                            strategy_params, event.strategy
                        ),
                        volume_suppress_short=_resolve_volume_suppress_short(
                            strategy_params, event.strategy
                        ),
                        volume_spike_boost_long=_resolve_volume_spike_boost_long(
                            strategy_params, event.strategy
                        ),
                        volume_spike_boost_short=_resolve_volume_spike_boost_short(
                            strategy_params, event.strategy
                        ),
                        tp_r_long=_eff_tp_r_long,
                        tp_r_short=_eff_tp_r_short,
                    )
                    if computed is not None:
                        put_backtest_cache(conn, cache_key, run_id, last_candle_ts, computed)
                        bt_to_save[_bt_key] = computed
                    _bt_mem_cache[cache_key] = computed
                    bt_results[event.strategy] = computed
                else:
                    # cache_enabled=False — per-cycle dedup only, pre-A6 behaviour
                    _bt_key = (symbol, tf, event.strategy)
                    if _bt_key not in bt_to_save:
                        bt_to_save[_bt_key] = _compute_backtest(
                            ohlcv_df=ohlcv_df,
                            strategy=event.strategy,
                            secondary_df=sec_df,
                            funding_df=funding_df,
                            symbol=symbol,
                            timeframe=tf,
                            sl_pct=eff_sl_pct,
                            tp_r=eff_tp_r,
                            fee_pct=backtest_cfg.fee_pct,
                            day_filter=day_filter,
                            min_sl_pct=backtest_cfg.min_sl_pct,
                            atr_sl_multiplier=eff_atr_sl,
                            adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                            if bias_cfg
                            else None,
                            adr_exempt=_is_adr_exempt(strategy_params, event.strategy),
                            volume_suppress=_resolve_volume_suppress(
                                strategy_params, event.strategy, backtest_cfg.volume_suppress
                            ),
                            volume_spike_boost=_resolve_volume_spike_boost(
                                strategy_params, event.strategy, backtest_cfg.volume_spike_boost
                            ),
                            volume_suppress_long=_resolve_volume_suppress_long(
                                strategy_params, event.strategy
                            ),
                            volume_suppress_short=_resolve_volume_suppress_short(
                                strategy_params, event.strategy
                            ),
                            volume_spike_boost_long=_resolve_volume_spike_boost_long(
                                strategy_params, event.strategy
                            ),
                            volume_spike_boost_short=_resolve_volume_spike_boost_short(
                                strategy_params, event.strategy
                            ),
                            tp_r_long=_eff_tp_r_long,
                            tp_r_short=_eff_tp_r_short,
                        )
                    bt_results[event.strategy] = bt_to_save[_bt_key]
```

Note: `_backtest_run_id` is imported from `analytics.data_store` — add it to the
import block if not already there.

### Step 3.5 — Update end-of-cycle save block

- [ ] Find the end-of-cycle persist block (around line 1570):

```python
    if backtest_cfg and backtest_cfg.save_results and bt_cache:
        for (sym, tf, strategy), bt_result in bt_cache.items():
```

Replace `bt_cache` with `bt_to_save` in both the condition and the iteration:

```python
    if backtest_cfg and backtest_cfg.save_results and bt_to_save:
        for (sym, tf, strategy), bt_result in bt_to_save.items():
```

The rest of the save block body is unchanged.

### Step 3.6 — Run full test suite

- [ ] Run:

```bash
poetry run pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass (1095 green).

### Step 3.7 — Typecheck and lint

- [ ] Run:

```bash
make typecheck && make lint-py
```

Fix any mypy errors. Common ones to expect:

- `_backtest_run_id` now imported from `data_store` — remove any local usage of
  old inline version.
- `bt_results` type changed to `dict[str, BacktestResult | BacktestSnapshot | None]` —
  mypy may flag downstream usages. Add `# type: ignore` only if the property access
  is duck-type compatible and proven safe (prefer fixing the type annotation instead).

### Step 3.8 — Commit

```bash
git add analytics/signal_lib.py tests/test_signal_lib.py
git commit -m "feat(cache): wire L1→L2→compute backtest cache in run_scan_cycle"
```

---

## Task 4: Add `prune_backtest_cache` at daemon startup

**Files:**

- Modify: `analytics/signal_runner.py`

### Step 4.1 — Add import and prune call

- [ ] In `analytics/signal_runner.py`, add `prune_backtest_cache` to the
  `from analytics.data_store import ...` block.

- [ ] Find the startup block (around line 175):

```python
        with duckdb.connect(str(db_path)) as init_conn:
            init_schema(init_conn)
```

Add a prune call immediately after:

```python
        with duckdb.connect(str(db_path)) as init_conn:
            init_schema(init_conn)

        with duckdb.connect(str(db_path)) as prune_conn:
            prune_backtest_cache(prune_conn)
```

### Step 4.2 — Typecheck and lint

```bash
make typecheck && make lint-py
```

### Step 4.3 — Commit

```bash
git add analytics/signal_runner.py
git commit -m "feat(cache): prune backtest_cache at daemon startup"
```

---

## Task 5: Final validation

### Step 5.1 — Full test suite

- [ ] Run:

```bash
make test
```

Expected: 1095+ tests green (count may increase with new tests added in Task 1–2).

### Step 5.2 — Regression golden files

- [ ] Run:

```bash
make test-regression
```

Expected: matches golden files (no changes to `_compute_backtest` logic).

If regression tests fail, check that `_compute_backtest` itself was not
accidentally modified. The cache is a layer in front of it.

### Step 5.3 — Typecheck

```bash
make typecheck
```

### Step 5.4 — Log output smoke check

Run the daemon briefly with cache enabled (the default) and verify log output
shows cache hits after the first cycle:

```bash
# In one terminal, start the daemon in dry-run / non-Telegram mode:
poetry run python buibui.py signal-watch --no-telegram --timeframes 1h --strategies engulfing 2>&1 | head -60
```

After two cycles, the second cycle should NOT log `_compute_backtest` calls
for the same (symbol, tf, strategy) — only cache hits. You can add a temporary
`logger.debug("cache miss: %s", cache_key)` log line to verify if needed.

### Step 5.5 — Commit final state

```bash
git add .
git commit -m "feat(a6): backtest cache — final cleanup and validation"
```

---

## Rollback

If cache causes issues after deploy:

1. In the active `signal_watch.toml` config, set `cache_enabled = false` under
   `[backtest]` and restart the daemon. Takes effect immediately.
2. To wipe the cache table: `DROP TABLE IF EXISTS backtest_cache;` via DuckDB CLI.
3. Full git revert: `git revert feat/a6-backtest-cache` and redeploy.
