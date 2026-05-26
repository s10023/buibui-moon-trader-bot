# GH Actions signal-watch on OKX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Execution deviations (2026-05-26):** Three changes from the plan as written below:

1. **Plain git instead of Git LFS** (Task 8). LFS bandwidth is metered even on public repos; hourly checkouts of the ~7 MB DB would be ~4.9 GB/month, exceeding the 1 GiB free tier. Public-repo *plain* checkout bandwidth is free, so the DB is committed normally and exempted from the `check-added-large-files` pre-commit hook (`exclude: '^live_signal\.duckdb$'`). No `.gitattributes`. Re-export + commit only when calibration changes, to bound history growth.
2. **Python 3.13, not 3.11** (Task 9). `pyproject.toml` requires `>=3.13,<3.14`; the workflow uses `setup-python@v5` with `3.13`.
3. **Seed `config/coins.json` from `coins.json.example`** (Task 9). `run_signal_watch` calls `load_coins_config()` unconditionally but `coins.json` is gitignored; the example is byte-equivalent for the 3 live symbols, so the workflow does `cp config/coins.json.example config/coins.json`.

The actual `live_signal.duckdb` exported was **6.8 MB** (ohlcv 97410 / confidence_ratings 486 / backtest_combos 4433 / backtest_cross_tf_combos 31563).

**Goal:** Run the signal daemon on an hourly GitHub Actions cron using OKX market data (Binance is geo-blocked from US runners) and fire Telegram alerts, reading committed Binance-derived calibration.

**Architecture:** A duck-typed **OKX client adapter** exposes `futures_klines(...)` so the existing `backfill`/`sync`/`fetch_klines` code works unchanged, selected by `DATA_SOURCE=okx`. A new `--once` flag runs a single scan cycle and exits. An export tool builds a slim ~12 MB `live_signal.duckdb` (calibration + OHLCV, **read-only** from the local Binance `analytics.db`), committed via Git LFS. The hourly workflow restores that DB to an **ephemeral** working copy, incremental-syncs new OKX candles, scans, alerts, and persists only `signal_state.json` via `actions/cache`. **No DB is ever written back or committed by the runner.**

**Tech Stack:** Python 3.11, Poetry, DuckDB, pandas, `requests` (OKX HTTP), GitHub Actions, Git LFS.

**Spec:** `docs/superpowers/specs/2026-05-25-gh-actions-signal-watch-okx-design.md`

---

## ⚠️ Two decisions baked into this plan (confirm before/while executing)

1. **`taker_buy_volume` on OKX candles** — OKX `/market/candles` does not expose taker-buy volume, which live `cvd_divergence` (enabled 1h in all 3 configs) consumes. This plan sets `taker_buy_volume = volume / 2` for OKX-sourced candles (neutral CVD delta = 0 — degrades but never crashes or fabricates a directional CVD signal). The committed history retains real Binance taker volume; only the newest OKX-synced candles are neutral. **If you want exact CVD parity, drop `cvd_divergence` from the GH path instead — out of scope here.**
2. **Never overwrite local data** — the export reads `analytics.db` **read-only**; the runner works on a copy named `analytics.db` *inside the ephemeral runner only* and never commits/pushes it. `DATA_SOURCE` defaults to `binance`, so local `make db-update` is unaffected.

---

## File structure

| File | Responsibility |
| --- | --- |
| Create `utils/okx_client.py` | `OKXClient` adapter: `futures_klines(symbol, interval, startTime, limit)` → Binance-shaped rows; OKX HTTP + pagination + symbol/bar mapping |
| Modify `analytics/data_fetcher.py` | Add `KlineClient` Protocol; retype `fetch_klines` to it |
| Modify `analytics/data_sync.py` | Retype `backfill` / `sync` `client` param to `KlineClient` |
| Modify `utils/binance_client.py` | Add `create_data_client()` dispatching on `DATA_SOURCE` env |
| Modify `analytics/signal_runner.py` | Use `create_data_client()`; add `max_cycles` param + loop break |
| Modify `cli/signal.py` | Add `--once` flag → `max_cycles=1` |
| Create `tools/export_live_db.py` | Read-only slim-DB export (calibration + OHLCV) |
| Modify `Makefile` | Add `export-live-db` target |
| Create `.github/workflows/signal-watch.yaml` | Hourly cron workflow |
| Modify `.gitattributes` (create if absent) | Git LFS track `*.duckdb` |
| Delete `.github/workflows/verify-data-source.yaml` | Throwaway probe — job done |
| Modify `CLAUDE.md`, `README.md` | Document the OKX source + GH workflow |

---

## Task 1: OKX raw-row → Binance-shaped mapper

**Files:**

- Create: `utils/okx_client.py`
- Test: `tests/test_okx_client.py`

OKX `/market/candles` returns rows newest-first as string arrays: `[ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]`. Binance kline rows (which `fetch_klines` maps) index `k[0]=open_time … k[9]=taker_buy_volume`. This mapper converts one OKX row into a Binance-shaped list so `fetch_klines`'s existing mapper works unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_okx_client.py
from utils.okx_client import _okx_row_to_binance


def test_okx_row_to_binance_maps_and_sets_neutral_taker_volume() -> None:
    # OKX row: ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm
    okx = ["1726128000000", "60000.0", "60500.0", "59800.0", "60250.0",
           "1234.5", "74000000", "74000000", "1"]
    row = _okx_row_to_binance(okx)
    assert row[0] == 1726128000000          # open_time (int ms)
    assert row[1] == "60000.0"              # open
    assert row[2] == "60500.0"              # high
    assert row[3] == "59800.0"              # low
    assert row[4] == "60250.0"              # close
    assert row[5] == "1234.5"               # volume
    # index 9 = taker_buy_volume = volume / 2 (neutral CVD)
    assert float(row[9]) == 1234.5 / 2
    assert len(row) == 10


def test_okx_row_to_binance_open_time_is_int() -> None:
    okx = ["1726128000000", "1", "2", "0.5", "1.5", "10", "x", "y", "1"]
    row = _okx_row_to_binance(okx)
    assert isinstance(row[0], int)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_okx_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.okx_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# utils/okx_client.py
"""OKX V5 public market-data adapter.

Exposes a duck-typed `futures_klines(symbol, interval, startTime, limit)` matching
the subset of binance.Client used by analytics.data_fetcher.fetch_klines, so the
existing backfill / sync code works unchanged when DATA_SOURCE=okx.

OKX public market data is keyless (verified reachable from US GH runners; Bybit and
Binance are geo-blocked). Funding / OI are intentionally NOT implemented — no live
detector needs them on this path.
"""

from __future__ import annotations

from typing import Any


def _okx_row_to_binance(okx: list[str]) -> list[Any]:
    """Convert one OKX candle row to a Binance-shaped kline row.

    OKX: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm] (strings).
    Binance mapper reads k[0]=open_time, k[1..5]=OHLCV, k[9]=taker_buy_volume.
    OKX has no taker-buy split, so taker_buy_volume = volume / 2 (neutral CVD delta).
    """
    volume = float(okx[5])
    return [
        int(okx[0]),        # 0 open_time (ms)
        okx[1],             # 1 open
        okx[2],             # 2 high
        okx[3],             # 3 low
        okx[4],             # 4 close
        okx[5],             # 5 volume
        "0",                # 6 close_time (unused)
        "0",                # 7 quote_volume (unused)
        0,                  # 8 trades (unused)
        str(volume / 2),    # 9 taker_buy_volume (neutral)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_okx_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/okx_client.py tests/test_okx_client.py
git commit -m "feat(okx): OKX candle row to Binance-shaped kline mapper"
```

---

## Task 2: Symbol + bar mapping helpers

**Files:**

- Modify: `utils/okx_client.py`
- Test: `tests/test_okx_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_okx_client.py  (append)
import pytest
from utils.okx_client import _to_okx_inst_id, _to_okx_bar


def test_to_okx_inst_id_maps_usdt_perps() -> None:
    assert _to_okx_inst_id("BTCUSDT") == "BTC-USDT-SWAP"
    assert _to_okx_inst_id("ETHUSDT") == "ETH-USDT-SWAP"
    assert _to_okx_inst_id("SOLUSDT") == "SOL-USDT-SWAP"


def test_to_okx_inst_id_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="cannot map"):
        _to_okx_inst_id("FOOBAR")


def test_to_okx_bar_uses_utc_daily() -> None:
    assert _to_okx_bar("15m") == "15m"
    assert _to_okx_bar("1h") == "1H"
    assert _to_okx_bar("4h") == "4H"
    # 1Dutc so daily candle open aligns to 00:00 UTC like Binance open_time
    assert _to_okx_bar("1d") == "1Dutc"


def test_to_okx_bar_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _to_okx_bar("2h")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_okx_client.py -v`
Expected: FAIL — `ImportError: cannot import name '_to_okx_inst_id'`

- [ ] **Step 3: Write minimal implementation**

```python
# utils/okx_client.py  (append)

# USDT-perp symbol map: Binance "BTCUSDT" -> OKX "BTC-USDT-SWAP".
_INST_SUFFIX = "USDT"


def _to_okx_inst_id(symbol: str) -> str:
    if not symbol.endswith(_INST_SUFFIX):
        raise ValueError(f"cannot map symbol to OKX instId: {symbol!r}")
    base = symbol[: -len(_INST_SUFFIX)]
    return f"{base}-USDT-SWAP"


# Bar map. 1d -> 1Dutc so the daily candle opens at 00:00 UTC (matches Binance
# open_time / day_filter). OKX hour bars (1H/4H) are UTC-aligned by default.
_BAR_MAP = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}


def _to_okx_bar(timeframe: str) -> str:
    if timeframe not in _BAR_MAP:
        raise ValueError(f"unsupported OKX timeframe: {timeframe!r}")
    return _BAR_MAP[timeframe]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_okx_client.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/okx_client.py tests/test_okx_client.py
git commit -m "feat(okx): symbol + bar mapping helpers (1d -> 1Dutc)"
```

---

## Task 3: `OKXClient.futures_klines` — fetch, paginate, filter

**Files:**

- Modify: `utils/okx_client.py`
- Test: `tests/test_okx_client.py`

OKX `/market/candles` returns up to 300 rows newest-first and paginates *backward* via the `after` param (returns candles with ts < after). `futures_klines` must return rows with `open_time >= startTime`, ascending, dropping the unconfirmed in-progress candle (`confirm == "0"`). It accepts a `session` (a `requests.Session` or test double exposing `.get(url, params, timeout)`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_okx_client.py  (append)
from typing import Any
from utils.okx_client import OKXClient


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    """Returns one page of OKX candles then an empty page (end of history)."""

    def __init__(self, pages: list[list[list[str]]]) -> None:
        self._pages = pages
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any], timeout: float) -> _FakeResp:
        self.calls.append(params)
        data = self._pages.pop(0) if self._pages else []
        return _FakeResp({"code": "0", "msg": "", "data": data})


def _candle(ts: int, confirm: str = "1") -> list[str]:
    return [str(ts), "1", "2", "0.5", "1.5", "100", "x", "y", confirm]


def test_futures_klines_filters_by_start_sorts_ascending_drops_unconfirmed() -> None:
    # newest-first page: ts 3000 (unconfirmed), 2000, 1000
    session = _FakeSession([[_candle(3000, "0"), _candle(2000), _candle(1000)]])
    client = OKXClient(session=session)
    df = client.futures_klines("BTCUSDT", "1h", start_time=2000, limit=1000)
    # 3000 dropped (unconfirmed); 1000 dropped (< start); only 2000 kept
    assert list(df["open_time"]) == [2000]
    assert df["symbol"].iloc[0] == "BTCUSDT"
    assert df["timeframe"].iloc[0] == "1h"  # stored as the Binance tf, not OKX bar
    assert float(df["taker_buy_volume"].iloc[0]) == 100 / 2


def test_futures_klines_paginates_until_start_reached() -> None:
    # page 1 newest: 3000,2000 ; page 2: 1000 ; want start=1000 -> all 3
    session = _FakeSession([[_candle(3000), _candle(2000)], [_candle(1000)], []])
    client = OKXClient(session=session)
    df = client.futures_klines("BTCUSDT", "1h", start_time=1000, limit=1000)
    assert list(df["open_time"]) == [1000, 2000, 3000]
    # second call must carry an `after` cursor = oldest ts of page 1
    assert session.calls[1]["after"] == "2000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_okx_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'OKXClient'`

- [ ] **Step 3: Write minimal implementation**

```python
# utils/okx_client.py  (append; add imports at top)
#   import pandas as pd
#   from analytics.data_fetcher import OHLCV_COLUMNS

_OKX_BASE = "https://www.okx.com"
_CANDLES_PATH = "/api/v5/market/candles"
_OKX_PAGE_LIMIT = 300  # OKX max rows per request


class OKXClient:
    """Minimal OKX market-data client exposing a Binance-compatible futures_klines."""

    def __init__(self, session: Any | None = None, base_url: str = _OKX_BASE) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._base = base_url

    def futures_klines(
        self,
        symbol: str,
        interval: str,
        startTime: int,
        limit: int = 1000,
    ) -> "pd.DataFrame":
        """Return klines with open_time >= startTime, ascending, Binance-shaped.

        Paginates OKX's newest-first pages backward via `after` until startTime is
        reached or history ends, then maps + filters + sorts. Drops the unconfirmed
        in-progress candle (confirm == "0").
        """
        inst_id = _to_okx_inst_id(symbol)
        bar = _to_okx_bar(interval)
        collected: list[list[Any]] = []
        after: str | None = None
        while len(collected) < limit:
            params: dict[str, Any] = {
                "instId": inst_id,
                "bar": bar,
                "limit": str(_OKX_PAGE_LIMIT),
            }
            if after is not None:
                params["after"] = after
            resp = self._session.get(
                f"{self._base}{_CANDLES_PATH}", params=params, timeout=15
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break
            for okx in data:
                if okx[8] == "0":  # unconfirmed in-progress candle
                    continue
                collected.append(_okx_row_to_binance(okx))
            oldest_ts = int(data[-1][0])
            after = str(oldest_ts)
            if oldest_ts <= startTime:
                break  # reached the requested window start

        rows = [r for r in collected if r[0] >= startTime]
        rows.sort(key=lambda r: r[0])
        return _rows_to_ohlcv_df(rows[:limit], symbol, interval)


def _rows_to_ohlcv_df(
    rows: list[list[Any]], symbol: str, interval: str
) -> "pd.DataFrame":
    import pandas as pd

    from analytics.data_fetcher import OHLCV_COLUMNS

    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": interval,
                "open_time": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
                "taker_buy_volume": float(r[9]),
            }
            for r in rows
        ],
        columns=OHLCV_COLUMNS,
    )
```

Note: `OKXClient.futures_klines` returns a **DataFrame** (matching what `fetch_klines` produces), unlike Binance's raw-list `futures_klines`. Task 4 routes around `fetch_klines` for OKX. (Rationale: OKX needs DataFrame-level filtering/sorting; wrapping it back into raw lists only to re-map is wasteful.)

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_okx_client.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/okx_client.py tests/test_okx_client.py
git commit -m "feat(okx): OKXClient.futures_klines with backward pagination + filtering"
```

---

## Task 4: Source-aware kline fetch + `create_data_client`

**Files:**

- Modify: `analytics/data_fetcher.py` (add `KlineClient` Protocol + `fetch_klines` dispatch)
- Modify: `utils/binance_client.py` (add `create_data_client`)
- Test: `tests/test_data_fetcher.py`, `tests/test_binance_client.py` (create if absent)

Because `OKXClient.futures_klines` returns a DataFrame while Binance returns raw lists, `fetch_klines` must branch: if the client is an `OKXClient`, return its DataFrame directly; else map the Binance raw list as today.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data_fetcher.py  (append; create file if absent with the imports)
import pandas as pd
from analytics.data_fetcher import fetch_klines, OHLCV_COLUMNS
from utils.okx_client import OHLCV_COLUMNS as _OKX_COLS  # noqa: F401  (sanity)


class _OKXLike:
    """Stands in for OKXClient: futures_klines returns a ready OHLCV DataFrame."""

    def futures_klines(self, symbol, interval, startTime, limit=1000):  # type: ignore[no-untyped-def]
        return pd.DataFrame(
            [{"symbol": symbol, "timeframe": interval, "open_time": startTime,
              "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
              "volume": 10.0, "taker_buy_volume": 5.0}],
            columns=OHLCV_COLUMNS,
        )


def test_fetch_klines_passes_through_okx_dataframe() -> None:
    from utils.okx_client import OKXClient
    client = _OKXLike()
    # mark it as OKX by type so fetch_klines branches
    df = fetch_klines.__wrapped__(client, "BTCUSDT", "1h", 1000) if hasattr(
        fetch_klines, "__wrapped__") else fetch_klines(client, "BTCUSDT", "1h", 1000)
    assert list(df.columns) == OHLCV_COLUMNS
    assert df["open_time"].iloc[0] == 1000
```

> Note to implementer: prefer an `isinstance(client, OKXClient)` branch (import locally to avoid a util→analytics cycle). Adjust the test to construct a real `OKXClient` with a `_FakeSession` (see Task 3) rather than `_OKXLike` if you implement the branch via `isinstance`.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_data_fetcher.py -v`
Expected: FAIL (pass-through branch not implemented)

- [ ] **Step 3: Write minimal implementation**

```python
# analytics/data_fetcher.py
# 1) Add near the top, after imports:
from typing import Protocol


class KlineClient(Protocol):
    def futures_klines(
        self, symbol: str, interval: str, startTime: int, limit: int = ...
    ) -> Any: ...


# 2) Change fetch_klines signature `client: Client` -> `client: KlineClient`
#    and add the OKX pass-through branch at the top of the body:
def fetch_klines(
    client: KlineClient,
    symbol: str,
    interval: str,
    start_time: int,
    limit: int = KLINES_MAX_LIMIT,
) -> pd.DataFrame:
    from utils.okx_client import OKXClient

    if isinstance(client, OKXClient):
        return client.futures_klines(symbol, interval, start_time, limit)
    raw = client.futures_klines(
        symbol=symbol, interval=interval, startTime=start_time, limit=limit
    )
    return _fetch_to_df(raw, lambda k: {...}, OHLCV_COLUMNS)  # unchanged mapper body
```

```python
# utils/binance_client.py  (append)
import os


def create_data_client() -> Any:
    """Return the market-data client for the active DATA_SOURCE.

    DATA_SOURCE=okx  -> OKXClient (keyless OKX V5 public market data).
    anything else    -> Binance futures client (create_client()), the default.
    """
    source = os.environ.get("DATA_SOURCE", "binance").lower()
    if source == "okx":
        from utils.okx_client import OKXClient

        return OKXClient()
    return create_client()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_data_fetcher.py tests/test_binance_client.py -v`
Expected: PASS

- [ ] **Step 5: Retype `backfill` / `sync` and verify mypy**

In `analytics/data_sync.py`, change the `client: Client` annotations on `backfill` and `sync` to `client: KlineClient` (import `from analytics.data_fetcher import KlineClient`). Leave `sync_funding_rates` / `sync_open_interest` on `Client` (Binance-only; not used on the OKX path).

Run: `make typecheck`
Expected: 0 issues.

- [ ] **Step 6: Commit**

```bash
git add analytics/data_fetcher.py analytics/data_sync.py utils/binance_client.py tests/
git commit -m "feat(data): DATA_SOURCE dispatch + KlineClient protocol for OKX"
```

---

## Task 5: Wire `create_data_client` into the daemon

**Files:**

- Modify: `analytics/signal_runner.py:134`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_signal_runner.py  (append)
import os
from unittest.mock import patch
from utils.binance_client import create_data_client
from utils.okx_client import OKXClient


def test_create_data_client_returns_okx_when_env_set() -> None:
    with patch.dict(os.environ, {"DATA_SOURCE": "okx"}):
        assert isinstance(create_data_client(), OKXClient)


def test_create_data_client_defaults_to_binance(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DATA_SOURCE", raising=False)
    with patch("utils.binance_client.create_client", return_value="BINANCE") as m:
        assert create_data_client() == "BINANCE"
        m.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails (if not already covered by Task 4)**

Run: `poetry run pytest tests/test_signal_runner.py -k create_data_client -v`
Expected: PASS for env var; this step mainly guards the daemon wiring below.

- [ ] **Step 3: Edit the daemon**

In `analytics/signal_runner.py`, change the import and the call:

```python
# at the import block (near line 46):
from utils.binance_client import create_data_client, load_coins_config

# line 134:
client = create_data_client()
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_signal_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/signal_runner.py tests/test_signal_runner.py
git commit -m "feat(daemon): select market-data client via DATA_SOURCE"
```

---

## Task 6: Single-shot `--once` mode

**Files:**

- Modify: `analytics/signal_runner.py` (signature line 100-124; loop line 261; break before sleep ~393)
- Modify: `cli/signal.py` (watch parser ~243; `run_signal_watch` call ~134)
- Test: `tests/test_signal_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_signal_runner.py  (append)
import inspect
from analytics import signal_runner


def test_run_signal_watch_accepts_max_cycles() -> None:
    sig = inspect.signature(signal_runner.run_signal_watch)
    assert "max_cycles" in sig.parameters
    assert sig.parameters["max_cycles"].default is None
```

(A full loop-exit integration test requires heavy mocking of `duckdb`, `sync`, and `run_scan_cycle`; the signature guard plus manual `--once` run in Task 9 covers this. If the existing suite already mocks the cycle, add an assertion that the loop breaks after one iteration when `max_cycles=1`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_signal_runner.py -k max_cycles -v`
Expected: FAIL — `assert 'max_cycles' in {...}`

- [ ] **Step 3: Add the param + loop break**

In `analytics/signal_runner.py`, add to the `run_signal_watch` signature (after `combo_cfg`):

```python
    max_cycles: int | None = None,
```

Then immediately after the `backfill_outcomes` try/except block and the `if alerts: ... else: ...` logging (right before `sleep_secs, wake_ts = secs_until_next_boundary(...)` at ~line 393), insert:

```python
            if max_cycles is not None and _cycle_count >= max_cycles:
                logger.info("max_cycles=%d reached — exiting after one-shot run", max_cycles)
                break
```

This exits before the sleep, so a single-shot run does not block.

- [ ] **Step 4: Add the CLI flag + thread it through**

In `cli/signal.py`, add to the watch parser (before `watch_parser.set_defaults`):

```python
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan cycle then exit (for cron / GitHub Actions).",
    )
```

And in the `run_signal_watch(args)` body, add to the `signal_runner.run_signal_watch(...)` call kwargs:

```python
        max_cycles=1 if getattr(args, "once", False) else None,
```

- [ ] **Step 5: Run tests + typecheck**

Run: `poetry run pytest tests/test_signal_runner.py -k max_cycles -v && make typecheck`
Expected: PASS, 0 mypy issues.

- [ ] **Step 6: Commit**

```bash
git add analytics/signal_runner.py cli/signal.py tests/test_signal_runner.py
git commit -m "feat(signal): --once single-shot scan mode for cron"
```

---

## Task 7: Slim live-DB export tool (read-only source)

**Files:**

- Create: `tools/export_live_db.py`
- Modify: `Makefile` (add `export-live-db` target)
- Test: `tests/test_export_live_db.py`

Builds `live_signal.duckdb` containing only the live-path tables (`ohlcv`, `confidence_ratings`, `backtest_combos`, `backtest_cross_tf_combos`). The source `analytics.db` is opened **read-only**; the output is written to a fresh file (overwriting any prior output only).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export_live_db.py
import duckdb
from pathlib import Path
from tools.export_live_db import export_live_db, LIVE_TABLES


def _make_source(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE ohlcv (symbol TEXT, open_time BIGINT)")
    con.execute("INSERT INTO ohlcv VALUES ('BTCUSDT', 1)")
    con.execute("CREATE TABLE confidence_ratings (strategy TEXT)")
    con.execute("INSERT INTO confidence_ratings VALUES ('bos')")
    con.execute("CREATE TABLE backtest_combos (combo_id TEXT)")
    con.execute("CREATE TABLE backtest_cross_tf_combos (combo_id TEXT)")
    con.execute("CREATE TABLE backtest_trades (id BIGINT)")  # must NOT be copied
    con.execute("INSERT INTO backtest_trades VALUES (1)")
    con.close()


def test_export_copies_only_live_tables(tmp_path: Path) -> None:
    src = tmp_path / "analytics.db"
    out = tmp_path / "live_signal.duckdb"
    _make_source(src)

    export_live_db(src, out)

    con = duckdb.connect(str(out), read_only=True)
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()}
    con.close()
    assert tables == set(LIVE_TABLES)
    assert "backtest_trades" not in tables


def test_export_does_not_mutate_source(tmp_path: Path) -> None:
    src = tmp_path / "analytics.db"
    out = tmp_path / "live_signal.duckdb"
    _make_source(src)
    before = src.stat().st_mtime_ns

    export_live_db(src, out)

    # source untouched (read-only access); mtime unchanged
    assert src.stat().st_mtime_ns == before
    con = duckdb.connect(str(src), read_only=True)
    assert con.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0] == 1
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_export_live_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.export_live_db'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/export_live_db.py
"""Export a slim live-signal DuckDB for the GitHub Actions signal-watch job.

Copies ONLY the tables the live daemon reads (ohlcv + calibration) from the local
Binance analytics.db into a fresh live_signal.duckdb, excluding the bulky
backtest_trades / backtest_runs / backtest_cache. The source is opened READ-ONLY
and never mutated. Run after `make db-update` / recalibrate, then commit the output
via Git LFS.

Usage: PYTHONPATH=. poetry run python tools/export_live_db.py [SRC] [OUT]
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

LIVE_TABLES: list[str] = [
    "ohlcv",
    "confidence_ratings",
    "backtest_combos",
    "backtest_cross_tf_combos",
]

DEFAULT_SRC = Path("analytics.db")
DEFAULT_OUT = Path("live_signal.duckdb")


def export_live_db(src: Path = DEFAULT_SRC, out: Path = DEFAULT_OUT) -> None:
    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")
    out.unlink(missing_ok=True)  # fresh file; never appends to a stale one

    # Source opened READ-ONLY — guarantees we never overwrite local Binance data.
    src_con = duckdb.connect(str(src), read_only=True)
    out_con = duckdb.connect(str(out))
    try:
        for table in LIVE_TABLES:
            exists = src_con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='main' AND table_name=?",
                [table],
            ).fetchone()[0]
            if not exists:
                continue
            df = src_con.execute(f'SELECT * FROM "{table}"').fetchdf()  # noqa: F841
            out_con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM df')
        rows = {
            t: out_con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            for t in LIVE_TABLES
            if out_con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='main' AND table_name=?",
                [t],
            ).fetchone()[0]
        }
        print(f"Exported {out} ({out.stat().st_size / 1e6:.1f} MB): {rows}")
    finally:
        src_con.close()
        out_con.close()


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    export_live_db(src, out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_export_live_db.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Add the Make target**

In `Makefile` (near the other `buibui-*` targets), add (recipe line MUST be a real TAB):

<!-- markdownlint-disable MD010 -->
```makefile
export-live-db:  ## Export slim live_signal.duckdb (calibration + OHLCV) for GH Actions
	@PYTHONPATH=. poetry run python tools/export_live_db.py
```
<!-- markdownlint-enable MD010 -->

Add `export-live-db` to the `.PHONY` line.

- [ ] **Step 6: Commit**

```bash
git add tools/export_live_db.py tests/test_export_live_db.py Makefile
git commit -m "feat(tools): export_live_db — read-only slim live DB for GH Actions"
```

---

## Task 8: Git LFS tracking + generate the live DB

**Files:**

- Create/Modify: `.gitattributes`
- Run: `make export-live-db`

- [ ] **Step 1: Track DuckDB files via LFS**

```bash
git lfs install
git lfs track "*.duckdb"
cat .gitattributes   # expect: *.duckdb filter=lfs diff=lfs merge=lfs -text
```

- [ ] **Step 2: Generate the live DB from the real local DB**

```bash
make export-live-db
ls -lh live_signal.duckdb   # expect roughly 10-20 MB
```

- [ ] **Step 3: Verify it is NOT gitignored and the source is intact**

```bash
git check-ignore live_signal.duckdb || echo "OK: not ignored"
# analytics.db must remain gitignored (never committed):
git check-ignore analytics.db && echo "OK: analytics.db still ignored"
```

- [ ] **Step 4: Commit the LFS pointer + DB**

```bash
git add .gitattributes live_signal.duckdb
git commit -m "build(lfs): track *.duckdb; add live_signal.duckdb calibration+OHLCV"
```

---

## Task 9: The hourly workflow

**Files:**

- Create: `.github/workflows/signal-watch.yaml`

Repo is public → standard-runner minutes are free, so cadence stays hourly. The job restores the committed DB to `analytics.db` **inside the runner only** (ephemeral), incremental-syncs OKX candles, runs one scan, and persists only `signal_state.json`. It never commits or pushes any DB.

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/signal-watch.yaml
name: Signal Watch (OKX)

on:
  schedule:
    - cron: '0 * * * *'   # hourly, on the hour (UTC)
  workflow_dispatch:

concurrency:
  group: signal-watch
  cancel-in-progress: false   # let a run finish; don't drop alerts mid-flight

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout (with LFS for live_signal.duckdb)
        uses: actions/checkout@v4
        with:
          lfs: true

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install Poetry
        run: pipx install poetry

      - name: Cache Poetry venv
        uses: actions/cache@v4
        with:
          path: ~/.cache/pypoetry
          key: poetry-${{ runner.os }}-${{ hashFiles('poetry.lock') }}

      - name: Install dependencies
        run: poetry install --no-root

      - name: Restore signal_state.json (cooldown/dedup)
        uses: actions/cache@v4
        with:
          path: signal_state.json
          key: signal-state-${{ github.run_id }}
          restore-keys: |
            signal-state-

      - name: Seed working DB from committed live DB (ephemeral, never committed)
        run: cp live_signal.duckdb analytics.db

      - name: Run one scan cycle
        env:
          DATA_SOURCE: okx
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: poetry run python buibui.py signal watch --once --telegram

      - name: Save signal_state.json
        if: always()
        uses: actions/cache/save@v4
        with:
          path: signal_state.json
          key: signal-state-${{ github.run_id }}
```

- [ ] **Step 2: Confirm the working DB never gets committed**

The runner writes `analytics.db` (already gitignored) and never runs `git add`/`push`. Verify locally that `analytics.db` is gitignored (Task 8 Step 3). No assertion needed in CI.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/signal-watch.yaml
git commit -m "feat(ci): hourly signal-watch on OKX with state cache"
```

---

## Task 10: Remove the throwaway probe + docs

**Files:**

- Delete: `.github/workflows/verify-data-source.yaml`
- Modify: `CLAUDE.md`, `README.md`

- [ ] **Step 1: Delete the probe workflow**

```bash
git rm .github/workflows/verify-data-source.yaml
```

- [ ] **Step 2: Document the deployment**

Add to `README.md` a "Scheduled alerts (GitHub Actions)" section: OKX data source, `make export-live-db` → commit (LFS) cadence, `signal watch --once`, required repo secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), and the `cvd_divergence` taker-volume caveat. Add a one-line note to `CLAUDE.md` under CLI / Project Structure that `DATA_SOURCE=okx` selects `utils/okx_client.py` and `signal watch --once` is the cron entry.

- [ ] **Step 3: Lint docs + commit**

```bash
make lint-md
git add -A
git commit -m "docs: GH Actions OKX signal-watch deployment + remove geo-probe"
```

---

## Task 11: Full quality gate + manual smoke test

- [ ] **Step 1: Full local gate**

Run: `make lint-py && make typecheck && make test`
Expected: ruff clean, mypy 0 issues, all tests pass.

- [ ] **Step 2: Manual OKX one-shot smoke (local, no Telegram)**

```bash
cp live_signal.duckdb /tmp/smoke.duckdb
DATA_SOURCE=okx poetry run python buibui.py signal watch --once \
  --config config/signal_watch.toml --state-file /tmp/smoke_state.json
```

(Point the daemon at a throwaway DB by temporarily copying, or run with the committed `live_signal.duckdb` copied to `analytics.db` in a scratch checkout — never against your real `analytics.db`.) Expected: it OKX-syncs a few candles, runs one cycle, logs alerts-or-none, and exits 0 without blocking.

- [ ] **Step 3: Push the branch + open PR**

```bash
git push -u origin feat/gh-actions-signal-watch-okx
gh pr create --title "feat: scheduled signal-watch on GitHub Actions (OKX)" --body "Implements docs/superpowers/specs/2026-05-25-gh-actions-signal-watch-okx-design.md"
```

- [ ] **Step 4: Dispatch the workflow once on main (after merge) to verify a live run**

```bash
gh workflow run signal-watch.yaml --ref main
```

Expected: green run; a Telegram alert if any signal fires this cycle.

---

## Self-review notes

- **Spec coverage:** OKX adapter (T1-3), DATA_SOURCE dispatch (T4-5), `--once` (T6), slim read-only export (T7), LFS (T8), hourly workflow + state cache (T9), probe cleanup + docs (T10). All spec components covered.
- **Open risk carried into tasks:** OKX `1d → 1Dutc` and 4H UTC alignment — verify in T11 Step 2 that synced candle `open_time`s match existing committed candle boundaries (compare last committed Binance candle ts vs first OKX-synced ts spacing).
- **taker_buy_volume / cvd_divergence:** handled as a flagged decision (neutral volume/2); revisit if exact CVD parity is wanted.
- **Don't-overwrite guarantee:** export is read-only on source (T7 test asserts mtime unchanged); runner DB is gitignored + never pushed (T8/T9).
