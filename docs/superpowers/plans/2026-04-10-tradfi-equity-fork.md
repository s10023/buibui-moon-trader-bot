# TradFi Equity Fork Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fork buibui-moon-trader-bot into a US-equities bot powered by Alpaca Markets API,
running on-demand (EOD/EOW) against a watchlist of single stocks.

**Architecture:** All strategy/backtest/web layers carry over unchanged. Only the data layer
is swapped (Binance → Alpaca). Three crypto-only concepts are removed (funding, OI, CME gap)
and replaced with their equity equivalents (vwap column, overnight gap). The fork is a
standalone repo — no shared packages, no cross-repo sync.

**Tech Stack:** Python 3.11+, Poetry, alpaca-py, DuckDB, FastAPI, Svelte 5

> **IMPORTANT — Scope:** All tasks below execute inside the **forked repo**, not buibui.
> After Task 1 you will be working in `~/repo/[BOTNAME]/`. File paths are relative to that root.
> `[BOTNAME]` is a placeholder — substitute the chosen name throughout (e.g. `lunafi`).

---

## File Map

| Action | Path | Purpose |
| --- | --- | --- |
| Create | `utils/alpaca_client.py` | Alpaca SDK client factory (replaces binance_client.py) |
| Rewrite | `analytics/data_fetcher.py` | Alpaca bar fetching (replaces Binance klines) |
| Modify | `analytics/data_store.py` | Rename `taker_buy_volume`→`vwap`; drop funding/OI tables |
| Modify | `analytics/data_sync.py` | Remove funding/OI sync; wire new fetcher |
| Create | `config/stocks.json` | Equity watchlist (replaces coins.json) |
| Modify | `utils/config_validation.py` | Validate stocks.json schema |
| Modify | `analytics/indicators_lib.py` | Remove funding_reversion+cvd from registry; fix ORB anchor |
| Modify | `signals/registry.py` | Remove funding_reversion+cvd entries |
| Create | `analytics/overnight_gap_lib.py` | Overnight gap detection (replaces cme_gap_lib.py) |
| Modify | `analytics/signal_lib.py` | Wire overnight_gap_lib; replace cme_gap imports |
| Delete | `analytics/cme_gap_lib.py` | Crypto-specific — no equity equivalent |
| Delete | `utils/binance_client.py` | Replaced by alpaca_client.py |

---

## Task 1: Fork repo and strip crypto skeleton

**Files:**

- Delete: `utils/binance_client.py`
- Delete: `analytics/cme_gap_lib.py`
- Delete: `monitor/live_price.py`, `monitor/live_position.py` (Binance WebSocket wrappers)

- [ ] **Step 1: Fork on GitHub**

  ```bash
  # On GitHub: fork buibui-moon-trader-bot → [BOTNAME]
  # Then clone:
  git clone git@github.com:<you>/[BOTNAME].git ~/repo/[BOTNAME]
  cd ~/repo/[BOTNAME]
  ```

- [ ] **Step 2: Global rename buibui → [BOTNAME] in CLI and config**

  ```bash
  # Entry point
  mv buibui.py [BOTNAME].py

  # Makefile — replace all references to "buibui" with [BOTNAME]
  sed -i 's/buibui/[BOTNAME]/g' Makefile

  # pyproject.toml — update [tool.poetry] name
  sed -i 's/name = "buibui-moon-trader-bot"/name = "[BOTNAME]"/' pyproject.toml
  ```

- [ ] **Step 3: Delete crypto-specific files**

  ```bash
  rm utils/binance_client.py
  rm analytics/cme_gap_lib.py
  rm monitor/live_price.py monitor/live_position.py
  ```

- [ ] **Step 4: Verify tests still load (many will fail — that's expected)**

  ```bash
  poetry run pytest tests/ --collect-only 2>&1 | tail -20
  ```

  Expected: collection errors for binance imports — we'll fix these per task.

- [ ] **Step 5: Commit**

  ```bash
  git add -A
  git commit -m "chore: fork from buibui — strip crypto skeleton"
  ```

---

## Task 2: Add alpaca-py and create Alpaca client

**Files:**

- Create: `utils/alpaca_client.py`
- Create: `tests/test_alpaca_client.py`

- [ ] **Step 1: Add alpaca-py dependency**

  ```bash
  poetry add alpaca-py
  ```

- [ ] **Step 2: Write the failing test**

  Create `tests/test_alpaca_client.py`:

  ```python
  import os
  from unittest.mock import patch

  import pytest


  def test_create_data_client_requires_env_vars() -> None:
      from utils.alpaca_client import create_data_client

      with patch.dict(os.environ, {}, clear=True):
          with pytest.raises(KeyError):
              create_data_client()


  def test_create_data_client_returns_client() -> None:
      from utils.alpaca_client import create_data_client

      with patch.dict(
          os.environ,
          {"ALPACA_API_KEY": "test_key", "ALPACA_SECRET_KEY": "test_secret"},
      ):
          with patch("utils.alpaca_client.StockHistoricalDataClient") as mock_cls:
              create_data_client()
              mock_cls.assert_called_once_with("test_key", "test_secret")
  ```

- [ ] **Step 3: Run test to verify it fails**

  ```bash
  poetry run pytest tests/test_alpaca_client.py -v
  ```

  Expected: `ImportError: cannot import name 'create_data_client'`

- [ ] **Step 4: Create `utils/alpaca_client.py`**

  ```python
  """Alpaca Markets client factory.

  Reads credentials from environment variables:
    ALPACA_API_KEY    — Alpaca API key ID
    ALPACA_SECRET_KEY — Alpaca secret key

  No module-level side effects.
  """

  import os

  from alpaca.data.historical import StockHistoricalDataClient
  from alpaca.trading.client import TradingClient


  def create_data_client() -> StockHistoricalDataClient:
      """Return an authenticated Alpaca data client."""
      return StockHistoricalDataClient(
          api_key=os.environ["ALPACA_API_KEY"],
          secret_key=os.environ["ALPACA_SECRET_KEY"],
      )


  def create_trading_client(paper: bool = True) -> TradingClient:
      """Return an authenticated Alpaca trading client.

      paper=True uses paper trading endpoint (safe default).
      """
      return TradingClient(
          api_key=os.environ["ALPACA_API_KEY"],
          secret_key=os.environ["ALPACA_SECRET_KEY"],
          paper=paper,
      )
  ```

- [ ] **Step 5: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_alpaca_client.py -v
  ```

  Expected: 2 PASSED

- [ ] **Step 6: Commit**

  ```bash
  git add utils/alpaca_client.py tests/test_alpaca_client.py pyproject.toml poetry.lock
  git commit -m "feat: add alpaca-py client factory"
  ```

---

## Task 3: Replace data_fetcher.py with Alpaca implementation

**Files:**

- Rewrite: `analytics/data_fetcher.py`
- Modify: `tests/test_data_fetcher.py` (update existing tests)

The new `fetch_bars()` keeps the same call signature as the old `fetch_klines()` so
`data_sync.py` needs minimal changes.

- [ ] **Step 1: Write the failing tests**

  Replace the contents of `tests/test_data_fetcher.py`:

  ```python
  """Tests for Alpaca-backed data_fetcher."""

  from datetime import datetime, timezone
  from typing import Any
  from unittest.mock import MagicMock

  import pandas as pd
  import pytest
  from alpaca.data.historical import StockHistoricalDataClient

  from analytics.data_fetcher import BARS_MAX_LIMIT, OHLCV_COLUMNS, fetch_bars


  def _make_mock_client(rows: list[dict[str, Any]]) -> StockHistoricalDataClient:
      """Build a mock Alpaca client that returns the given rows as a bar DataFrame."""
      df = pd.DataFrame(rows)
      df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
      df = df.set_index(["symbol", "timestamp"])
      mock_bars = MagicMock()
      mock_bars.df = df
      client = MagicMock(spec=StockHistoricalDataClient)
      client.get_stock_bars.return_value = mock_bars
      return client


  def _sample_row(
      symbol: str = "AAPL",
      ts: str = "2024-01-15T14:30:00+00:00",
  ) -> dict[str, Any]:
      return {
          "symbol": symbol,
          "timestamp": ts,
          "open": 185.0,
          "high": 187.5,
          "low": 184.0,
          "close": 186.0,
          "volume": 1_000_000.0,
          "trade_count": 5000,
          "vwap": 185.8,
      }


  def test_fetch_bars_returns_ohlcv_columns() -> None:
      client = _make_mock_client([_sample_row()])
      start_ms = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1000)
      result = fetch_bars(client, "AAPL", "1d", start_ms)
      assert list(result.columns) == OHLCV_COLUMNS


  def test_fetch_bars_maps_fields_correctly() -> None:
      client = _make_mock_client([_sample_row()])
      start_ms = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1000)
      result = fetch_bars(client, "AAPL", "1d", start_ms)
      row = result.iloc[0]
      assert row["symbol"] == "AAPL"
      assert row["timeframe"] == "1d"
      assert row["close"] == 186.0
      assert row["vwap"] == 185.8
      # open_time is Unix milliseconds
      assert row["open_time"] == int(
          datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc).timestamp() * 1000
      )


  def test_fetch_bars_empty_returns_correct_columns() -> None:
      client = MagicMock(spec=StockHistoricalDataClient)
      mock_bars = MagicMock()
      mock_bars.df = pd.DataFrame()
      client.get_stock_bars.return_value = mock_bars
      start_ms = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1000)
      result = fetch_bars(client, "AAPL", "1d", start_ms)
      assert result.empty
      assert list(result.columns) == OHLCV_COLUMNS


  def test_fetch_bars_respects_limit() -> None:
      rows = [
          _sample_row(ts=f"2024-01-{15 + i:02d}T14:30:00+00:00") for i in range(5)
      ]
      client = _make_mock_client(rows)
      start_ms = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1000)
      result = fetch_bars(client, "AAPL", "1d", start_ms, limit=3)
      assert len(result) <= 3
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_data_fetcher.py -v
  ```

  Expected: `ImportError` — `fetch_bars` doesn't exist yet.

- [ ] **Step 3: Rewrite `analytics/data_fetcher.py`**

  ```python
  """Pure data-fetching logic — Alpaca Markets API to DataFrames.

  All functions accept an Alpaca client as a parameter.
  No module-level side effects.
  """

  from datetime import datetime, timezone

  import pandas as pd
  from alpaca.data.historical import StockHistoricalDataClient
  from alpaca.data.requests import StockBarsRequest
  from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

  BARS_MAX_LIMIT: int = 1000

  OHLCV_COLUMNS: list[str] = [
      "symbol",
      "timeframe",
      "open_time",
      "open",
      "high",
      "low",
      "close",
      "volume",
      "vwap",
  ]

  # Milliseconds per bar for each supported timeframe.
  # Used to compute end_ms from start_ms + limit when fetching paginated windows.
  _INTERVAL_MS: dict[str, int] = {
      "15m": 15 * 60 * 1_000,
      "1h": 60 * 60 * 1_000,
      "4h": 4 * 60 * 60 * 1_000,
      "1d": 24 * 60 * 60 * 1_000,
  }

  _TF_TO_ALPACA: dict[str, TimeFrame] = {
      "15m": TimeFrame(15, TimeFrameUnit.Minute),
      "1h": TimeFrame.Hour,
      "4h": TimeFrame(4, TimeFrameUnit.Hour),
      "1d": TimeFrame.Day,
  }


  def fetch_bars(
      client: StockHistoricalDataClient,
      symbol: str,
      interval: str,
      start_ms: int,
      limit: int = BARS_MAX_LIMIT,
  ) -> pd.DataFrame:
      """Fetch up to `limit` bars starting from start_ms (Unix ms).

      Returns a DataFrame with columns matching OHLCV_COLUMNS.
      Returns an empty DataFrame (with correct columns) if the API returns no data.
      Raises on API errors — callers decide whether to retry or skip.
      """
      if interval not in _INTERVAL_MS:
          raise ValueError(
              f"Unsupported interval '{interval}'. "
              f"Supported: {list(_INTERVAL_MS)}"
          )
      alpaca_tf = _TF_TO_ALPACA[interval]
      start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
      end_ms = start_ms + limit * _INTERVAL_MS[interval]
      end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

      req = StockBarsRequest(
          symbol_or_symbols=symbol,
          timeframe=alpaca_tf,
          start=start_dt,
          end=end_dt,
          adjustment="split",
      )
      bars = client.get_stock_bars(req)
      df = bars.df

      if df is None or df.empty:
          return pd.DataFrame(columns=OHLCV_COLUMNS)

      df = df.reset_index()  # columns: symbol, timestamp, open, high, low, close, volume, vwap, ...

      result = pd.DataFrame(
          {
              "symbol": symbol,
              "timeframe": interval,
              # Alpaca timestamps are tz-aware UTC; convert ns → ms
              "open_time": (
                  df["timestamp"].astype("int64") // 1_000_000
              ).values,
              "open": df["open"].astype(float).values,
              "high": df["high"].astype(float).values,
              "low": df["low"].astype(float).values,
              "close": df["close"].astype(float).values,
              "volume": df["volume"].astype(float).values,
              "vwap": df["vwap"].astype(float).values,
          }
      )
      return result.head(limit)
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_data_fetcher.py -v
  ```

  Expected: 4 PASSED

- [ ] **Step 5: Run full lint + typecheck**

  ```bash
  make lint-py && make typecheck
  ```

  Fix any issues before continuing.

- [ ] **Step 6: Commit**

  ```bash
  git add analytics/data_fetcher.py tests/test_data_fetcher.py
  git commit -m "feat: replace Binance data_fetcher with Alpaca fetch_bars"
  ```

---

## Task 4: Update data_store.py — rename vwap column, drop funding/OI tables

**Files:**

- Modify: `analytics/data_store.py`
- Modify: `tests/test_data_store.py`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/test_data_store.py`:

  ```python
  def test_ohlcv_schema_has_vwap_not_taker_buy_volume() -> None:
      import duckdb
      from analytics.data_store import init_db

      conn = duckdb.connect(":memory:")
      init_db(conn)
      cols = [
          r[1]
          for r in conn.execute(
              "PRAGMA table_info('ohlcv')"
          ).fetchall()
      ]
      assert "vwap" in cols
      assert "taker_buy_volume" not in cols
      conn.close()


  def test_no_funding_rates_table() -> None:
      import duckdb
      from analytics.data_store import init_db

      conn = duckdb.connect(":memory:")
      init_db(conn)
      tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
      assert "funding_rates" not in tables
      conn.close()


  def test_no_open_interest_table() -> None:
      import duckdb
      from analytics.data_store import init_db

      conn = duckdb.connect(":memory:")
      init_db(conn)
      tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
      assert "open_interest" not in tables
      conn.close()
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_data_store.py::test_ohlcv_schema_has_vwap_not_taker_buy_volume tests/test_data_store.py::test_no_funding_rates_table tests/test_data_store.py::test_no_open_interest_table -v
  ```

  Expected: 3 FAILED (column is still `taker_buy_volume`, tables still exist).

- [ ] **Step 3: Edit `analytics/data_store.py` — rename column in CREATE TABLE**

  Find the line in `init_db()` that defines the `ohlcv` table. Change:

  ```python
  # Before
      taker_buy_volume DOUBLE NOT NULL,
  ```

  To:

  ```python
  # After
      vwap DOUBLE NOT NULL,
  ```

- [ ] **Step 4: Edit `analytics/data_store.py` — remove funding_rates and open_interest table creation**

  Remove the `CREATE TABLE IF NOT EXISTS funding_rates` block and the
  `CREATE TABLE IF NOT EXISTS open_interest` block from `init_db()`.

- [ ] **Step 5: Remove upsert_funding_rates, upsert_open_interest, get_funding_rates, get_open_interest functions**

  Delete these functions entirely from `data_store.py`. Also remove any
  corresponding exports from `__all__` if present.

- [ ] **Step 6: Update upsert_ohlcv to reference vwap**

  Find `upsert_ohlcv` in `data_store.py`. Anywhere it references `taker_buy_volume`,
  change it to `vwap`. The INSERT/ON CONFLICT column list and VALUES mapping both need updating.

- [ ] **Step 7: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_data_store.py -v
  ```

  Expected: all PASSED (including new tests).

- [ ] **Step 8: Commit**

  ```bash
  git add analytics/data_store.py tests/test_data_store.py
  git commit -m "feat: rename taker_buy_volume→vwap, drop funding/OI tables"
  ```

---

## Task 5: Update data_sync.py — remove funding/OI sync

**Files:**

- Modify: `analytics/data_sync.py`
- Modify: `tests/test_data_sync.py`

- [ ] **Step 1: Write the failing test**

  Add to `tests/test_data_sync.py`:

  ```python
  def test_data_sync_has_no_funding_or_oi_imports() -> None:
      import analytics.data_sync as m
      import inspect

      src = inspect.getsource(m)
      assert "funding" not in src.lower()
      assert "open_interest" not in src.lower()
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  poetry run pytest tests/test_data_sync.py::test_data_sync_has_no_funding_or_oi_imports -v
  ```

  Expected: FAILED (funding references still present).

- [ ] **Step 3: Edit `analytics/data_sync.py` imports**

  Remove from the `from analytics.data_fetcher import` block:

  ```python
  # Remove these lines
  OIPeriod,
  fetch_funding_rates,
  fetch_open_interest,
  ```

  Remove from the `from analytics.data_store import` block:

  ```python
  # Remove these lines
  upsert_funding_rates,
  upsert_open_interest,
  ```

  Remove the Binance `Client` import and replace with the Alpaca client type:

  ```python
  # Before
  from binance.client import Client

  # After
  from alpaca.data.historical import StockHistoricalDataClient
  ```

- [ ] **Step 4: Remove sync_funding_rates and sync_open_interest functions**

  Delete the `sync_funding_rates()` and `sync_open_interest()` functions entirely.

- [ ] **Step 5: Update backfill() to use fetch_bars**

  The `backfill()` function currently calls `fetch_klines`. Replace with `fetch_bars`:

  ```python
  # Before
  from analytics.data_fetcher import (
      KLINES_MAX_LIMIT,
      fetch_klines,
  )
  # ... inside backfill():
  df = fetch_klines(client, symbol, timeframe, current_start, limit=KLINES_MAX_LIMIT)

  # After
  from analytics.data_fetcher import (
      BARS_MAX_LIMIT,
      fetch_bars,
  )
  # ... inside backfill():
  df = fetch_bars(client, symbol, timeframe, current_start, limit=BARS_MAX_LIMIT)
  ```

  Update the `backfill()` signature to accept `StockHistoricalDataClient`:

  ```python
  def backfill(
      conn: duckdb.DuckDBPyConnection,
      client: StockHistoricalDataClient,
      symbol: str,
      timeframe: str,
      start_ms: int,
      sleep_fn: Callable[[float], None] | None = None,
  ) -> int:
  ```

  Also update the pagination check:

  ```python
  # Before
  if len(df) < KLINES_MAX_LIMIT:

  # After
  if len(df) < BARS_MAX_LIMIT:
  ```

- [ ] **Step 6: Update incremental_sync() similarly**

  Find `incremental_sync()` and replace any `fetch_klines` calls with `fetch_bars`,
  and update the `Client` type annotation to `StockHistoricalDataClient`.
  Remove any `sync_funding_rates` or `sync_open_interest` calls inside it.

- [ ] **Step 7: Run tests**

  ```bash
  poetry run pytest tests/test_data_sync.py -v && make typecheck
  ```

  Expected: all PASSED, no mypy errors.

- [ ] **Step 8: Update `analytics/analytics_runner.py`**

  This thin wrapper creates the client and calls sync. Update its import:

  ```python
  # Before
  from utils.binance_client import create_client

  # After
  from utils.alpaca_client import create_data_client
  ```

  Replace the `create_client()` call with `create_data_client()` and update the type
  annotation on the client variable to `StockHistoricalDataClient`.

- [ ] **Step 9: Commit**

  ```bash
  git add analytics/data_sync.py analytics/analytics_runner.py tests/test_data_sync.py
  git commit -m "feat: update data_sync and analytics_runner to use Alpaca, remove funding/OI"
  ```

---

## Task 6: Create stocks.json watchlist and update config validation

**Files:**

- Create: `config/stocks.json`
- Modify: `utils/config_validation.py`
- Modify: `tests/test_config_validation.py`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/test_config_validation.py`:

  ```python
  def test_valid_stocks_json_passes() -> None:
      from utils.config_validation import validate_stocks_config

      config = {
          "AAPL": {"sl_pct": 0.05},
          "MSTR": {"sl_pct": 0.08},
      }
      validate_stocks_config(config)  # should not raise


  def test_stocks_json_missing_sl_pct_raises() -> None:
      from utils.config_validation import validate_stocks_config

      with pytest.raises(ValueError, match="sl_pct"):
          validate_stocks_config({"AAPL": {}})


  def test_stocks_json_invalid_sl_pct_raises() -> None:
      from utils.config_validation import validate_stocks_config

      with pytest.raises(ValueError, match="sl_pct"):
          validate_stocks_config({"AAPL": {"sl_pct": -0.1}})
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_config_validation.py -k "stocks" -v
  ```

  Expected: `ImportError: cannot import name 'validate_stocks_config'`

- [ ] **Step 3: Add `validate_stocks_config` to `utils/config_validation.py`**

  ```python
  def validate_stocks_config(config: dict[str, Any]) -> None:
      """Validate the stocks.json watchlist schema.

      Each key is a ticker symbol. Each value must have:
        sl_pct: float > 0  — stop-loss as a fraction of price (e.g. 0.05 = 5%)
      """
      for symbol, cfg in config.items():
          if "sl_pct" not in cfg:
              raise ValueError(
                  f"stocks.json: symbol '{symbol}' missing required field 'sl_pct'"
              )
          sl_pct = cfg["sl_pct"]
          if not isinstance(sl_pct, (int, float)) or sl_pct <= 0:
              raise ValueError(
                  f"stocks.json: symbol '{symbol}' sl_pct must be a positive number, got {sl_pct!r}"
              )
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_config_validation.py -k "stocks" -v
  ```

  Expected: 3 PASSED

- [ ] **Step 5: Create `config/stocks.json`**

  ```json
  {
    "AAPL": { "sl_pct": 0.05 },
    "MSTR": { "sl_pct": 0.08 },
    "CRCL": { "sl_pct": 0.07 }
  }
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add config/stocks.json utils/config_validation.py tests/test_config_validation.py
  git commit -m "feat: add stocks.json watchlist and validate_stocks_config"
  ```

---

## Task 7: Remove funding_reversion and cvd_divergence from all registries

**Files:**

- Modify: `analytics/indicators_lib.py`
- Modify: `signals/registry.py`
- Modify: `tests/test_indicators_lib.py`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/test_indicators_lib.py`:

  ```python
  def test_funding_reversion_not_in_strategy_registry() -> None:
      from analytics.indicators_lib import STRATEGY_REGISTRY

      assert "funding_reversion" not in STRATEGY_REGISTRY


  def test_cvd_divergence_not_in_strategy_registry() -> None:
      from analytics.indicators_lib import STRATEGY_REGISTRY

      assert "cvd_divergence" not in STRATEGY_REGISTRY


  def test_funding_reversion_not_in_signal_registry() -> None:
      from signals.registry import SIGNAL_REGISTRY

      assert "funding_reversion" not in SIGNAL_REGISTRY


  def test_cvd_divergence_not_in_signal_registry() -> None:
      from signals.registry import SIGNAL_REGISTRY

      assert "cvd_divergence" not in SIGNAL_REGISTRY
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_indicators_lib.py -k "funding_reversion or cvd_divergence" -v
  ```

  Expected: 4 FAILED

- [ ] **Step 3: Edit `analytics/indicators_lib.py` — remove from STRATEGY_REGISTRY**

  In `STRATEGY_REGISTRY`, delete the entire `"funding_reversion": StrategySpec(...)` block
  and the entire `"cvd_divergence": StrategySpec(...)` block.

- [ ] **Step 4: Edit `analytics/indicators_lib.py` — remove from DETECTOR_REGISTRY**

  In `DETECTOR_REGISTRY`, delete the `"funding_reversion"` and `"cvd_divergence"` entries.

- [ ] **Step 5: Edit `analytics/indicators_lib.py` — remove from KNOWN_STRATEGIES**

  Remove `"funding_reversion"` and `"cvd_divergence"` from the `KNOWN_STRATEGIES` list.

- [ ] **Step 6: Delete detect_funding_extreme and detect_cvd_divergence functions**

  Remove the entire `detect_funding_extreme()` function (and its docstring/comments).
  Remove the entire `detect_cvd_divergence()` function (and its docstring/comments).

  Also remove the `requires_funding: bool = False` field from `StrategySpec` if it is
  only used by the funding_reversion strategy. Check first:

  ```bash
  grep -n "requires_funding" analytics/indicators_lib.py
  ```

  If the only remaining usages are in the field definition itself, delete the field.

- [ ] **Step 7: Edit `signals/registry.py` — remove entries and import**

  Remove the import of `detect_cvd_divergence` from the top of the file.
  Delete the `"funding_reversion": SignalPlugin(...)` block.
  Delete the `"cvd_divergence": SignalPlugin(...)` block.

- [ ] **Step 8: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_indicators_lib.py -v && make typecheck
  ```

  Expected: all PASSED, no mypy errors.

- [ ] **Step 9: Commit**

  ```bash
  git add analytics/indicators_lib.py signals/registry.py tests/test_indicators_lib.py
  git commit -m "feat: remove funding_reversion and cvd_divergence — no equity equivalent"
  ```

---

## Task 8: Fix ORB session anchor for US market open

**Files:**

- Modify: `analytics/indicators_lib.py`
- Modify: `tests/test_indicators_lib.py`

The current `detect_orb_breakout` has `session_hour_utc` as a legacy param that is
intentionally ignored — the implementation always anchors at 00:00 UTC. For equities we need
it to anchor at 13:00 UTC (9:00am ET; market opens at 9:30, so the range window starts close
to open). This requires making the param functional again.

- [ ] **Step 1: Read the current ORB implementation**

  ```bash
  grep -n "session_hour_utc\|00:00\|anchor\|daily\|open_time" analytics/indicators_lib.py | head -40
  ```

  Understand how the function currently identifies "session start" candles before proceeding.

- [ ] **Step 2: Write the failing test**

  Add to `tests/test_indicators_lib.py`:

  ```python
  def test_orb_uses_session_hour_utc_anchor() -> None:
      """ORB should only use candles from the specified session hour, not 00:00 UTC."""
      import pandas as pd
      from analytics.indicators_lib import detect_orb_breakout

      # Build 3 days of hourly candles anchored at 13:00 UTC (US market open)
      base_ms = 1_705_276_800_000  # 2024-01-15 00:00 UTC
      hour_ms = 3_600_000
      rows = []
      for day in range(3):
          for hour in range(24):
              ts = base_ms + day * 24 * hour_ms + hour * hour_ms
              rows.append({
                  "open_time": ts,
                  "open": 185.0 + hour * 0.1,
                  "high": 187.0 + hour * 0.1,
                  "low": 183.0 + hour * 0.1,
                  "close": 186.0 + hour * 0.1,
                  "volume": 1_000_000.0,
                  "vwap": 185.5,
              })
      df = pd.DataFrame(rows)
      # With anchor at 13, signals reference 13:00 UTC candles as session open
      signals = detect_orb_breakout(df, session_hour_utc=13)
      # Should not raise and should return a DataFrame
      assert isinstance(signals, pd.DataFrame)
  ```

- [ ] **Step 3: Run test to verify it passes (or adjust if already works)**

  ```bash
  poetry run pytest tests/test_indicators_lib.py::test_orb_uses_session_hour_utc_anchor -v
  ```

  If it fails with a column error (`vwap` not expected, etc.), adjust the test row schema
  to match what the function actually reads.

- [ ] **Step 4: Make session_hour_utc functional in detect_orb_breakout**

  Find the section in `detect_orb_breakout` that identifies "session open" candles.
  The comment says the param is ignored and it anchors on 00:00 UTC.

  Change the logic so it uses `session_hour_utc` to identify the first candle of a session:

  ```python
  # Before (anchors on 00:00 UTC — hour == 0)
  session_mask = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.hour == 0

  # After (anchors on session_hour_utc — default 13 for 9am ET)
  session_mask = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.hour == session_hour_utc
  ```

  The exact code will differ — match the pattern you found in Step 1.
  Change the default value of `session_hour_utc` from `0` to `13`.
  Remove the comment saying the param is ignored.

- [ ] **Step 5: Run all ORB tests**

  ```bash
  poetry run pytest tests/ -k "orb" -v
  ```

  Expected: all PASSED.

- [ ] **Step 6: Commit**

  ```bash
  git add analytics/indicators_lib.py tests/test_indicators_lib.py
  git commit -m "fix(orb): make session_hour_utc functional, default to 13 (9am ET)"
  ```

---

## Task 9: Create overnight_gap_lib.py

**Files:**

- Create: `analytics/overnight_gap_lib.py`
- Create: `tests/test_overnight_gap_lib.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_overnight_gap_lib.py`:

  ```python
  """Tests for overnight gap detection (replaces CME gap for equities)."""

  import pandas as pd
  import pytest

  from analytics.overnight_gap_lib import OvernightGap, gap_fill_warning, get_overnight_gap


  def _make_df(rows: list[dict]) -> pd.DataFrame:  # type: ignore[type-arg]
      return pd.DataFrame(rows)


  def test_gap_up_detected() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
          {"open_time": 2, "open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0, "volume": 1e6, "vwap": 103.5},
      ])
      gap = get_overnight_gap(df)
      assert gap is not None
      assert gap.gap_up is True
      assert gap.prev_close == pytest.approx(101.0)
      assert gap.today_open == pytest.approx(103.0)
      assert gap.gap_pct == pytest.approx((103.0 - 101.0) / 101.0)


  def test_gap_down_detected() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
          {"open_time": 2, "open": 98.0, "high": 99.0, "low": 97.0, "close": 98.5, "volume": 1e6, "vwap": 98.2},
      ])
      gap = get_overnight_gap(df)
      assert gap is not None
      assert gap.gap_up is False
      assert gap.gap_pct == pytest.approx((98.0 - 101.0) / 101.0)


  def test_gap_up_filled_when_low_touches_prev_close() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
          # Gap up to 105 open but low comes back to 101 (filled)
          {"open_time": 2, "open": 105.0, "high": 107.0, "low": 101.0, "close": 106.0, "volume": 1e6, "vwap": 105.0},
      ])
      gap = get_overnight_gap(df)
      assert gap is not None
      assert gap.filled is True


  def test_gap_up_not_filled_when_low_above_prev_close() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
          {"open_time": 2, "open": 105.0, "high": 107.0, "low": 103.0, "close": 106.0, "volume": 1e6, "vwap": 105.0},
      ])
      gap = get_overnight_gap(df)
      assert gap is not None
      assert gap.filled is False


  def test_returns_none_when_fewer_than_two_rows() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
      ])
      assert get_overnight_gap(df) is None


  def test_gap_fill_warning_long_below_unfilled_gap_down() -> None:
      gap = OvernightGap(gap_pct=-0.02, gap_up=False, filled=False, prev_close=101.0, today_open=99.0)
      # Going long; unfilled gap-down at 101 is above entry — gap may act as resistance
      warning = gap_fill_warning(gap, direction="long", entry=100.0)
      assert warning is not None
      assert "101" in warning


  def test_gap_fill_warning_none_when_gap_filled() -> None:
      gap = OvernightGap(gap_pct=-0.02, gap_up=False, filled=True, prev_close=101.0, today_open=99.0)
      assert gap_fill_warning(gap, direction="long", entry=100.0) is None


  def test_gap_fill_warning_short_above_unfilled_gap_up() -> None:
      gap = OvernightGap(gap_pct=0.02, gap_up=True, filled=False, prev_close=99.0, today_open=101.0)
      # Going short; unfilled gap-up prev_close at 99 is below entry — may act as support
      warning = gap_fill_warning(gap, direction="short", entry=100.0)
      assert warning is not None
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_overnight_gap_lib.py -v
  ```

  Expected: `ImportError` — module doesn't exist yet.

- [ ] **Step 3: Create `analytics/overnight_gap_lib.py`**

  ```python
  """Overnight gap detection for US equities.

  Every US equity trading session leaves an overnight gap between the prior
  session's close and the next session's open. Unfilled gaps act as price
  magnets and can invalidate trade setups.

  Replaces cme_gap_lib.py from buibui (crypto-specific weekend gap).
  No module-level side effects.
  """

  from dataclasses import dataclass

  import pandas as pd


  @dataclass
  class OvernightGap:
      gap_pct: float      # (today_open - prev_close) / prev_close; negative = gap down
      gap_up: bool        # True if today opened above yesterday's close
      filled: bool        # True if price returned to prev_close during today's session
      prev_close: float   # Yesterday's closing price
      today_open: float   # Today's opening price


  def get_overnight_gap(ohlcv_df: pd.DataFrame) -> OvernightGap | None:
      """Compute the overnight gap from the two most recent rows of OHLCV data.

      ohlcv_df must have columns: open_time, open, high, low, close.
      Returns None if fewer than two rows are available.
      """
      if len(ohlcv_df) < 2:
          return None

      prev = ohlcv_df.iloc[-2]
      curr = ohlcv_df.iloc[-1]

      prev_close = float(prev["close"])
      today_open = float(curr["open"])
      gap_pct = (today_open - prev_close) / prev_close
      gap_up = today_open > prev_close

      if gap_up:
          # Gap up is filled when today's low touches or goes below prev_close
          filled = float(curr["low"]) <= prev_close
      else:
          # Gap down is filled when today's high touches or exceeds prev_close
          filled = float(curr["high"]) >= prev_close

      return OvernightGap(
          gap_pct=gap_pct,
          gap_up=gap_up,
          filled=filled,
          prev_close=prev_close,
          today_open=today_open,
      )


  def gap_fill_warning(
      gap: OvernightGap,
      direction: str,
      entry: float,
  ) -> str | None:
      """Return a warning string if the unfilled gap threatens the trade.

      For a LONG trade: an unfilled gap-down above entry may act as resistance
      as price attempts to fill the gap (pulling price down).
      For a SHORT trade: an unfilled gap-up below entry may act as support
      as price attempts to fill the gap (pushing price up).

      Returns None when the gap is already filled or poses no threat.
      """
      if gap.filled:
          return None

      if direction == "long" and not gap.gap_up:
          # Gap-down: prev_close is above today's open; if entry < prev_close,
          # the unfilled gap zone (today_open → prev_close) is overhead resistance
          if entry < gap.prev_close:
              return (
                  f"⚠️ Unfilled gap-down at ${gap.prev_close:.2f} "
                  f"({abs(gap.gap_pct):.1%} above open) — potential overhead resistance"
              )

      elif direction == "short" and gap.gap_up:
          # Gap-up: prev_close is below today's open; if entry > prev_close,
          # the unfilled gap zone (prev_close → today_open) is below as support
          if entry > gap.prev_close:
              return (
                  f"⚠️ Unfilled gap-up at ${gap.prev_close:.2f} "
                  f"({gap.gap_pct:.1%} below open) — potential downside support"
              )

      return None
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_overnight_gap_lib.py -v
  ```

  Expected: 8 PASSED

- [ ] **Step 5: Run typecheck**

  ```bash
  make typecheck
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add analytics/overnight_gap_lib.py tests/test_overnight_gap_lib.py
  git commit -m "feat: add overnight_gap_lib — replaces cme_gap_lib for equities"
  ```

---

## Task 10: Wire overnight gap into signal_lib.py

**Files:**

- Modify: `analytics/signal_lib.py`

- [ ] **Step 1: Replace CME gap import**

  In `analytics/signal_lib.py`, find and replace:

  ```python
  # Before
  from analytics.cme_gap_lib import cme_gap_alert_warning, get_recent_cme_gap

  # After
  from analytics.overnight_gap_lib import gap_fill_warning, get_overnight_gap
  ```

- [ ] **Step 2: Replace get_recent_cme_gap call**

  Around line 723 (find with `grep -n "cme_gap\|get_recent_cme_gap" analytics/signal_lib.py`):

  ```python
  # Before
  cme_gap = get_recent_cme_gap(ohlcv_df)

  # After
  overnight_gap = get_overnight_gap(ohlcv_df)
  ```

- [ ] **Step 3: Replace cme_gap_alert_warning calls**

  Around lines 1097–1109, replace:

  ```python
  # Before
  _gap_warning = cme_gap_alert_warning(
      cme_gap, direction, _entry, _rough_tp
  )
  # ...
  cme_gap_warning=_gap_warning,

  # After
  _gap_warning = gap_fill_warning(overnight_gap, direction, _entry)
  # ...
  cme_gap_warning=_gap_warning,
  ```

  Note: `gap_fill_warning` takes 3 args (`gap`, `direction`, `entry`) vs
  `cme_gap_alert_warning` which took 4 (also `tp_price`). The new version does not
  need `tp_price`.

- [ ] **Step 4: Run scan-path tests**

  ```bash
  poetry run pytest tests/test_signal_lib.py -v && make typecheck
  ```

  Fix any remaining `cme_gap` references found by mypy.

- [ ] **Step 5: Confirm no remaining CME gap references**

  ```bash
  grep -rn "cme_gap" analytics/ signals/ monitor/ utils/ web/ || echo "clean"
  ```

  Expected: `clean`

- [ ] **Step 6: Commit**

  ```bash
  git add analytics/signal_lib.py
  git commit -m "feat: wire overnight_gap_lib into signal scan — replaces CME gap"
  ```

---

## Task 11: End-to-end smoke test

- [ ] **Step 1: Set Alpaca credentials**

  ```bash
  export ALPACA_API_KEY=your_paper_key
  export ALPACA_SECRET_KEY=your_paper_secret
  ```

- [ ] **Step 2: Run the full test suite**

  ```bash
  make test
  ```

  Expected: all tests pass. Fix any remaining import errors.

- [ ] **Step 3: Backfill AAPL daily candles**

  ```bash
  poetry run python [BOTNAME].py sync --symbol AAPL --timeframe 1d --since 2023-01-01
  ```

  Expected: candles stored in `analytics.db`.

- [ ] **Step 4: Run a backtest on AAPL 1d**

  ```bash
  poetry run python [BOTNAME].py backtest --symbol AAPL --timeframe 1d --days 365
  ```

  Expected: backtest results printed; no crash.

- [ ] **Step 5: Run the signal scanner**

  ```bash
  poetry run python [BOTNAME].py scan --symbol AAPL --timeframe 1d
  ```

  Expected: signals printed or "no signals"; no crash.

- [ ] **Step 6: Start the web dashboard**

  ```bash
  poetry run python [BOTNAME].py web
  ```

  Visit `http://localhost:8000`. Expected: dashboard loads, Chart tab shows AAPL.

- [ ] **Step 7: Final commit**

  ```bash
  git add -A
  git commit -m "chore: smoke test passed — TradFi equity fork operational on AAPL 1d"
  ```
