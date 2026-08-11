# D1 Spot-perp CVD Divergence Sleeve — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a spot-vs-perp taker-imbalance forecast and run it through both of this repo's book constructions to get a three-leg gate verdict on whether venue-split order flow is a second, decorrelated edge.

**Architecture:** A new `analytics/cvd/` package computes a scale-free taker-imbalance difference per symbol-day and turns it into a Carver-convention forecast matrix. That matrix is injected into the existing cross-sectional book through `run_xs_backtest(forecasts=...)`, which already accepts one, and into the time-series book through an injection parameter this plan adds to `analytics/forecast/book.py` by mirroring the XS socket. Daily spot bars land in a new isolated `spot_ohlcv` table. Nothing touches the live signal path.

**Tech Stack:** Python 3.11+, pandas, numpy, DuckDB, pytest, Poetry. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-11-d1-spot-perp-cvd-design.md`. Read it before Task 1 — it carries the pre-registered commitments (sign convention, trial family, decision rule) that this plan implements but does not restate.

## Global Constraints

- **Python 3.11+, mypy strict.** Every function needs full type annotations including return types (`-> None` on test methods).
- **No new dependencies.** Everything needed is already in `pyproject.toml`.
- **No test makes a real network call.** Fetchers take an injected `get: Getter` exactly as `analytics/venue_fetch.py` does.
- **Analytics tests use `duckdb.connect(":memory:")`.** Never touch the real `analytics.db`.
- **Pass every path explicitly in tests.** All nine CLI default paths are gitignored *and* present on this machine, so a default-path test passes here with 100% reliability and fails on a fresh clone.
- **Nothing in `analytics/cvd/` may be imported by** `signals/`, `web/`, `card/`, `trade/`, or the backtest surface.
- **Forecast matrices are UN-shifted.** Both books shift internally (`xs_leverage` does `demeaned.shift(1)`; `instrument_returns` does `combine_forecasts(...).shift(1)`). Shifting in `analytics/cvd/forecast.py` would double-shift and silently destroy a day of signal.
- **Gate thresholds are never restated.** Call `analytics.research_guards.passes_gate`. Do not write `0.95` or `0.5` anywhere.
- **Gates after every Python change:** `make lint-py`, `make typecheck`, `make test`. **`make test-regression` is NOT required** — no task here touches `analytics/backtest/`, `analytics/strategies/`, `analytics/signal_config.py`, `config/*signal_watch*.toml`, `config/strategy_params.toml`, `tests/fixtures/`, or `poetry.lock`. State which branch you took.
- **Branch:** all work lands on `docs/d1-spot-perp-cvd-spec` (already created, spec committed at `1087cb6`). One PR when every task is done.

## File Structure

| file | responsibility | status |
| --- | --- | --- |
| `analytics/store/schema.py` | add `spot_ohlcv` CREATE TABLE | modify |
| `analytics/store/spot_data.py` | `upsert_spot_ohlcv` / `get_spot_ohlcv` | create |
| `analytics/cvd/__init__.py` | package marker, no logic | create |
| `analytics/cvd/fetch.py` | keyless daily spot klines + symbol map + TRADING check | create |
| `analytics/cvd/imbalance.py` | `taker_imbalance`, `divergence` — pure pandas | create |
| `analytics/cvd/forecast.py` | `x` → capped vol-scaled forecast matrix | create |
| `analytics/forecast/book.py` | add forecast-injection socket to the TS book | modify |
| `analytics/cvd/replay.py` | DB front door; both shapes + trial families; benchmark | create |
| `analytics/cvd/report.py` | metrics, gate, `corr_to_xsmom` | create |
| `tools/cvd_audit.py` | backfill + run driver | create |

**Why `spot_ohlcv` is a new table and not columns on the existing `venue_spot_daily`:** `analytics/store/_common.py:16 _upsert` issues `INSERT OR REPLACE INTO {table} SELECT {columns} FROM _upsert_df` — an explicit SELECT list but **no INSERT column list**. Adding columns to a table therefore changes the arity contract of every existing upsert into it, and `venue_spot_daily` is H14's working table. A new table cannot break H14.

---

### Task 1: `spot_ohlcv` table and store functions

**Files:**

- Modify: `analytics/store/schema.py:48` (insert a new block after the `venue_spot_daily` block)
- Create: `analytics/store/spot_data.py`
- Test: `tests/test_spot_store.py`

**Interfaces:**

- Consumes: `analytics.store._common._upsert`, `init_schema`
- Produces:
  - `upsert_spot_ohlcv(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None`
  - `get_spot_ohlcv(conn: duckdb.DuckDBPyConnection, symbol: str, start: int, end: int) -> pd.DataFrame`

- [ ] **Step 1: Write the failing test**

Create `tests/test_spot_store.py`:

```python
import duckdb
import pandas as pd

from analytics.store.schema import init_schema
from analytics.store.spot_data import get_spot_ohlcv, upsert_spot_ohlcv


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


def _row(open_time: int, close: float, vol: float, tbv: float) -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "open_time": open_time,
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": close,
        "volume": vol,
        "taker_buy_volume": tbv,
    }


def test_upsert_then_get_roundtrips() -> None:
    conn = _conn()
    df = pd.DataFrame([_row(0, 101.0, 10.0, 6.0), _row(86_400_000, 102.0, 20.0, 9.0)])
    upsert_spot_ohlcv(conn, df)
    out = get_spot_ohlcv(conn, "BTCUSDT", 0, 86_400_000)
    assert list(out["open_time"]) == [0, 86_400_000]
    assert list(out["taker_buy_volume"]) == [6.0, 9.0]


def test_upsert_replaces_on_conflict() -> None:
    conn = _conn()
    upsert_spot_ohlcv(conn, pd.DataFrame([_row(0, 101.0, 10.0, 6.0)]))
    upsert_spot_ohlcv(conn, pd.DataFrame([_row(0, 999.0, 11.0, 7.0)]))
    out = get_spot_ohlcv(conn, "BTCUSDT", 0, 0)
    assert len(out) == 1
    assert out["close"].iloc[0] == 999.0


def test_get_is_bounded_inclusive_and_ordered() -> None:
    conn = _conn()
    rows = [_row(2 * 86_400_000, 3.0, 1.0, 1.0), _row(0, 1.0, 1.0, 1.0)]
    upsert_spot_ohlcv(conn, pd.DataFrame(rows))
    out = get_spot_ohlcv(conn, "BTCUSDT", 0, 86_400_000)
    assert list(out["close"]) == [1.0]


def test_empty_frame_is_a_noop() -> None:
    conn = _conn()
    upsert_spot_ohlcv(conn, pd.DataFrame())
    assert get_spot_ohlcv(conn, "BTCUSDT", 0, 10).empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_spot_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.store.spot_data'`

- [ ] **Step 3: Add the table to the schema**

In `analytics/store/schema.py`, immediately after the `venue_spot_daily` block (which ends at line 55 with `""")`), insert:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spot_ohlcv (
            symbol           TEXT   NOT NULL,
            open_time        BIGINT NOT NULL,
            open             DOUBLE NOT NULL,
            high             DOUBLE NOT NULL,
            low              DOUBLE NOT NULL,
            close            DOUBLE NOT NULL,
            volume           DOUBLE NOT NULL,
            taker_buy_volume DOUBLE,
            PRIMARY KEY (symbol, open_time)
        )
    """)
```

Daily bars only — there is deliberately no `timeframe` column. The spec's §3 identity shows intraday buys nothing a daily sleeve reads, and §12 scopes it out.

- [ ] **Step 4: Write the store module**

Create `analytics/store/spot_data.py`:

```python
"""Binance SPOT daily bars — the second venue for the D1 CVD sleeve.

Deliberately a separate table from ``ohlcv`` (perp) and from H14's
``venue_spot_daily`` (closes only). ``_upsert`` emits ``INSERT OR REPLACE INTO
{table} SELECT {columns}`` with no INSERT column list, so widening an existing
table changes the arity contract of every upsert already writing to it.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store._common import _upsert

_COLUMNS = "symbol, open_time, open, high, low, close, volume, taker_buy_volume"


def upsert_spot_ohlcv(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace daily spot bars.

    df must have columns: symbol, open_time, open, high, low, close, volume,
    taker_buy_volume. Conflicts on (symbol, open_time) are replaced.
    """
    _upsert(conn, df, "spot_ohlcv", _COLUMNS)


def get_spot_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start: int,
    end: int,
) -> pd.DataFrame:
    """Daily spot bars for `symbol` between start and end (Unix ms, inclusive)."""
    return conn.execute(
        f"SELECT {_COLUMNS} FROM spot_ohlcv "  # noqa: S608
        "WHERE symbol = ? AND open_time >= ? AND open_time <= ? "
        "ORDER BY open_time",
        [symbol, start, end],
    ).df()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_spot_store.py -v`
Expected: 4 passed

- [ ] **Step 6: Run the gates and commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/test_spot_store.py -q
git add analytics/store/schema.py analytics/store/spot_data.py tests/test_spot_store.py
git commit -m "feat(cvd): add spot_ohlcv table and store accessors"
```

---

### Task 2: Keyless spot kline fetcher

**Files:**

- Create: `analytics/cvd/__init__.py` (empty), `analytics/cvd/fetch.py`
- Test: `tests/cvd/__init__.py` (empty), `tests/cvd/test_fetch.py`

**Interfaces:**

- Consumes: `analytics.venue_fetch.Getter`, `analytics.venue_fetch.http_get_json`, `analytics.venue_fetch.DAY_MS`
- Produces:
  - `SPOT_SYMBOL_OVERRIDES: dict[str, str]`
  - `PERP_ONLY: frozenset[str]`
  - `spot_symbol_for(perp_symbol: str) -> str | None`
  - `fetch_spot_ohlcv_daily(symbol: str, start_ms: int, *, get: Getter = http_get_json) -> pd.DataFrame`
  - `fetch_trading_spot_symbols(*, get: Getter = http_get_json) -> frozenset[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/cvd/__init__.py` (empty) and `tests/cvd/test_fetch.py`:

```python
from typing import Any

import pandas as pd

from analytics.cvd.fetch import (
    fetch_spot_ohlcv_daily,
    fetch_trading_spot_symbols,
    spot_symbol_for,
)

DAY = 86_400_000


def _kline(open_time: int, close: float, vol: float, tbv: float) -> list[Any]:
    """A Binance kline row. Field 5 is volume, field 9 is taker buy base volume."""
    return [
        open_time,
        "100.0",
        "110.0",
        "90.0",
        str(close),
        str(vol),
        open_time + DAY - 1,
        "0",
        0,
        str(tbv),
        "0",
        "0",
    ]


class _FakeGet:
    def __init__(self, pages: list[list[Any]]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        return self.pages.pop(0) if self.pages else []


def test_symbol_map_handles_the_three_special_cases() -> None:
    assert spot_symbol_for("BTCUSDT") == "BTCUSDT"
    assert spot_symbol_for("1000PEPEUSDT") == "PEPEUSDT"
    assert spot_symbol_for("HYPEUSDT") is None
    assert spot_symbol_for("VVVUSDT") is None


def test_fetch_returns_volume_and_taker_buy_columns() -> None:
    get = _FakeGet([[_kline(0, 101.0, 10.0, 6.0)]])
    df = fetch_spot_ohlcv_daily("BTCUSDT", 0, get=get)
    assert list(df.columns) == [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "taker_buy_volume",
    ]
    assert df["volume"].iloc[0] == 10.0
    assert df["taker_buy_volume"].iloc[0] == 6.0


def test_request_shape_is_the_keyless_spot_endpoint() -> None:
    get = _FakeGet([[_kline(0, 101.0, 10.0, 6.0)]])
    fetch_spot_ohlcv_daily("ETHUSDT", 123, get=get)
    url = get.urls[0]
    assert url.startswith("https://api.binance.com/api/v3/klines?")
    assert "symbol=ETHUSDT" in url
    assert "interval=1d" in url
    assert "startTime=123" in url
    assert "limit=1000" in url
    assert "apiKey" not in url and "signature" not in url


def test_paging_advances_the_cursor_past_the_last_bar() -> None:
    full = [_kline(i * DAY, 1.0, 1.0, 1.0) for i in range(1000)]
    get = _FakeGet([full, [_kline(1000 * DAY, 2.0, 1.0, 1.0)]])
    df = fetch_spot_ohlcv_daily("BTCUSDT", 0, get=get)
    assert len(df) == 1001
    assert f"startTime={1000 * DAY}" in get.urls[1]


def test_stale_cursor_page_terminates_instead_of_looping() -> None:
    """A full page whose last open_time does not advance must not loop forever."""
    stale = [_kline(0, 1.0, 1.0, 1.0) for _ in range(1000)]
    get = _FakeGet([stale] * 5)
    df = fetch_spot_ohlcv_daily("BTCUSDT", 10 * DAY, get=get)
    assert len(get.urls) == 1
    assert isinstance(df, pd.DataFrame)


def test_trading_symbols_excludes_non_trading_status() -> None:
    payload = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING"},
            {"symbol": "TONUSDT", "status": "BREAK"},
        ]
    }
    got = fetch_trading_spot_symbols(get=lambda _url: payload)
    assert "BTCUSDT" in got
    assert "TONUSDT" not in got
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/cvd/test_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.cvd'`

- [ ] **Step 3: Write the fetcher**

Create `analytics/cvd/__init__.py` as an empty file, then `analytics/cvd/fetch.py`:

```python
"""Keyless Binance SPOT daily klines for the D1 CVD sleeve.

Sibling of ``analytics/venue_fetch.py:73 fetch_binance_spot_daily``, which hits
the same endpoint for H14 but keeps only the close. This one keeps volume
(field 5) and taker buy base volume (field 9) — the CVD inputs.

``get`` is injected so the suite is network-free. No API key is used or required.
"""

from __future__ import annotations

import pandas as pd

from analytics.venue_fetch import DAY_MS, Getter, http_get_json

_LIMIT = 1000
_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "taker_buy_volume"]

# The perp name carries a 1000x multiplier that the spot pair does not. The
# imbalance primitive is a ratio, so the multiplier cancels and no rescaling is
# needed — but the mapping is explicit, never derived by stripping the prefix.
SPOT_SYMBOL_OVERRIDES: dict[str, str] = {"1000PEPEUSDT": "PEPEUSDT"}

# No spot pair exists at all. Verified 2026-07-31.
PERP_ONLY: frozenset[str] = frozenset({"HYPEUSDT", "VVVUSDT"})


def spot_symbol_for(perp_symbol: str) -> str | None:
    """Spot pair for a perp symbol, or None when no spot pair exists."""
    if perp_symbol in PERP_ONLY:
        return None
    return SPOT_SYMBOL_OVERRIDES.get(perp_symbol, perp_symbol)


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in _COLUMNS}).astype(
        {"open_time": "int64"}
    )


def fetch_spot_ohlcv_daily(
    symbol: str, start_ms: int, *, get: Getter = http_get_json
) -> pd.DataFrame:
    """Daily spot bars for a Binance SPOT symbol from ``start_ms`` to now."""
    rows: list[tuple[int, float, float, float, float, float, float]] = []
    cursor = start_ms
    while True:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1d&startTime={cursor}&limit={_LIMIT}"
        )
        page = get(url) or []
        for row in page:
            rows.append(
                (
                    int(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5]),
                    float(row[9]),
                )
            )
        if len(page) < _LIMIT:
            break
        next_cursor = int(page[-1][0]) + DAY_MS
        if next_cursor <= cursor:
            # A full page whose last open_time didn't advance past the cursor we
            # requested (stale/misbehaving response) — stop instead of
            # re-requesting the same window forever. Mirrors venue_fetch.py:90.
            break
        cursor = next_cursor
    if not rows:
        return _empty()
    df = pd.DataFrame(rows, columns=_COLUMNS)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df.astype({"open_time": "int64"})


def fetch_trading_spot_symbols(*, get: Getter = http_get_json) -> frozenset[str]:
    """Spot symbols whose exchangeInfo status is TRADING.

    TONUSDT returns klines while not being in the TRADING set, so kline
    availability is not proof a pair is live. Check against this before use.
    """
    payload = get("https://api.binance.com/api/v3/exchangeInfo") or {}
    return frozenset(
        str(s["symbol"])
        for s in payload.get("symbols", [])
        if s.get("status") == "TRADING"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/cvd/test_fetch.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the gates and commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/cvd -q
git add analytics/cvd tests/cvd
git commit -m "feat(cvd): keyless Binance spot daily kline fetcher with symbol map"
```

---

### Task 3: The imbalance primitive

**Files:**

- Create: `analytics/cvd/imbalance.py`
- Test: `tests/cvd/test_imbalance.py`

**Interfaces:**

- Consumes: nothing from earlier tasks (pure pandas)
- Produces:
  - `taker_imbalance(volume: pd.Series, taker_buy_volume: pd.Series) -> pd.Series`
  - `divergence(spot: pd.DataFrame, perp: pd.DataFrame) -> pd.Series`

`divergence` takes two frames each carrying `open_time`, `volume`, `taker_buy_volume`, inner-joins them on `open_time`, and returns `imb_spot - imb_perp` indexed by UTC-midnight `pd.Timestamp` so it aligns with `load_daily_inputs`' close index.

- [ ] **Step 1: Write the failing test**

Create `tests/cvd/test_imbalance.py`:

```python
import numpy as np
import pandas as pd
import pytest

from analytics.cvd.imbalance import divergence, taker_imbalance

DAY = 86_400_000


def test_all_buyers_is_plus_one_all_sellers_is_minus_one() -> None:
    vol = pd.Series([10.0, 10.0, 10.0])
    tbv = pd.Series([10.0, 0.0, 5.0])
    out = taker_imbalance(vol, tbv)
    assert list(out) == [1.0, -1.0, 0.0]


def test_zero_volume_is_nan_not_an_exception() -> None:
    out = taker_imbalance(pd.Series([0.0]), pd.Series([0.0]))
    assert np.isnan(out.iloc[0])


def test_imbalance_is_scale_free_across_a_1000x_multiplier() -> None:
    """The 1000PEPE multiplier must cancel — this is why no rescaling is needed."""
    small = taker_imbalance(pd.Series([10.0]), pd.Series([6.0]))
    big = taker_imbalance(pd.Series([10_000.0]), pd.Series([6_000.0]))
    assert small.iloc[0] == big.iloc[0]


def _frame(times: list[int], vol: list[float], tbv: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"open_time": times, "volume": vol, "taker_buy_volume": tbv})


def test_divergence_is_spot_minus_perp_on_the_inner_join() -> None:
    spot = _frame([0, DAY], [10.0, 10.0], [10.0, 5.0])  # imb = +1.0, 0.0
    perp = _frame([0, DAY], [10.0, 10.0], [0.0, 5.0])  # imb = -1.0, 0.0
    out = divergence(spot, perp)
    assert list(out) == [2.0, 0.0]


def test_divergence_index_is_utc_midnight_timestamps() -> None:
    out = divergence(_frame([0], [10.0], [6.0]), _frame([0], [10.0], [4.0]))
    assert out.index[0] == pd.Timestamp("1970-01-01", tz="UTC")


def test_divergence_drops_days_present_on_only_one_venue() -> None:
    spot = _frame([0, DAY], [10.0, 10.0], [6.0, 6.0])
    perp = _frame([0], [10.0], [4.0])
    out = divergence(spot, perp)
    assert len(out) == 1


def test_daily_bars_are_information_complete_for_daily_cvd() -> None:
    """Spec section 3: aggregating intraday taker flow to a day is an IDENTITY.

    sum_i(2*tbv_i - vol_i) == 2*sum_i(tbv_i) - sum_i(vol_i). This is the test
    that lets a future session trust the cheap daily ingestion path instead of
    re-deriving it. Pure arithmetic — no network.
    """
    rng = np.random.default_rng(11)
    intraday_vol = rng.uniform(1.0, 100.0, size=96)
    intraday_tbv = intraday_vol * rng.uniform(0.0, 1.0, size=96)

    from_intraday = float((2.0 * intraday_tbv - intraday_vol).sum())
    day_vol = float(intraday_vol.sum())
    day_tbv = float(intraday_tbv.sum())
    from_daily = 2.0 * day_tbv - day_vol

    assert from_intraday == pytest.approx(from_daily)

    imb_daily = taker_imbalance(pd.Series([day_vol]), pd.Series([day_tbv])).iloc[0]
    assert imb_daily == pytest.approx(from_daily / day_vol)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/cvd/test_imbalance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.cvd.imbalance'`

- [ ] **Step 3: Write the primitive**

Create `analytics/cvd/imbalance.py`:

```python
"""Scale-free taker-flow imbalance and the spot-perp divergence.

``imb = (2*taker_buy_volume - volume) / volume`` in [-1, +1]: +1 = every taker
was a buyer, -1 = every taker was a seller, 0 = balanced. Being a ratio is what
makes the rest simple — no cross-symbol volume normalisation, no cross-era
normalisation as volumes grow, and the 1000PEPE multiplier cancels.

Daily bars are information-complete here: the day's taker-buy field IS the exact
intraday sum, so ``sum_i(2*tbv_i - vol_i) == 2*tbv_day - vol_day``. See the spec
section 3 and ``test_daily_bars_are_information_complete_for_daily_cvd``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def taker_imbalance(volume: pd.Series, taker_buy_volume: pd.Series) -> pd.Series:
    """Signed taker-flow share in [-1, +1]. Zero-volume days are NaN."""
    vol = volume.astype(float)
    tbv = taker_buy_volume.astype(float)
    out = (2.0 * tbv - vol) / vol.where(vol != 0.0)
    return out.replace([np.inf, -np.inf], np.nan)


def divergence(spot: pd.DataFrame, perp: pd.DataFrame) -> pd.Series:
    """``imb_spot - imb_perp`` on the days both venues have a bar.

    Positive = spot takers were more aggressive buyers than perp takers, which
    the spec pre-registers as the LONG direction. Index is UTC-midnight
    Timestamps so it aligns with ``load_daily_inputs``' close index.
    """
    cols = ["open_time", "volume", "taker_buy_volume"]
    merged = spot[cols].merge(perp[cols], on="open_time", suffixes=("_s", "_p"))
    if merged.empty:
        return pd.Series(dtype="float64", index=pd.DatetimeIndex([], tz="UTC"))
    imb_s = taker_imbalance(merged["volume_s"], merged["taker_buy_volume_s"])
    imb_p = taker_imbalance(merged["volume_p"], merged["taker_buy_volume_p"])
    idx = pd.to_datetime(merged["open_time"], unit="ms", utc=True)
    return pd.Series((imb_s - imb_p).to_numpy(), index=pd.DatetimeIndex(idx))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/cvd/test_imbalance.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the gates and commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/cvd -q
git add analytics/cvd/imbalance.py tests/cvd/test_imbalance.py
git commit -m "feat(cvd): scale-free taker imbalance and spot-perp divergence"
```

---

### Task 4: The forecast matrix

**Files:**

- Create: `analytics/cvd/forecast.py`
- Test: `tests/cvd/test_forecast.py`

**Interfaces:**

- Consumes: `analytics.cvd.imbalance.divergence` (only in later tasks; this module is pure over `x`)
- Produces:
  - `DEFAULT_SPANS: tuple[int, ...] = (8, 16, 32, 64)`
  - `FORECAST_SCALAR: float = 10.0`
  - `cvd_forecast(x: pd.Series, span: int, vol_span: int, cap: float) -> pd.Series`
  - `cvd_combined_forecast(x: pd.Series, spans: tuple[int, ...], fdm: float, vol_span: int, cap: float) -> pd.Series`
  - `cvd_forecast_matrix(x_by_symbol: dict[str, pd.Series], spans: tuple[int, ...], fdm: float, vol_span: int, cap: float) -> pd.DataFrame`

**Two commitments this module encodes, both from the spec:**

1. **Spans are 8/16/32/64** — the *fast* legs of `_DEFAULT_SPEEDS`. EWMAC's speeds are fast/slow crossover pairs and `x` takes a single smoothing span, so the pairs cannot be inherited whole.
2. **`FORECAST_SCALAR` is fixed at 10.0 a priori, never fitted.** Dividing a smoothed series by its own trailing std gives roughly unit variance, so 10.0 lands the average absolute forecast near the Carver convention without touching the data. A fitted scalar would be in-sample. Vol targeting and the governor absorb the level downstream, so the scalar's only real bite is where the ±cap binds.

- [ ] **Step 1: Write the failing test**

Create `tests/cvd/test_forecast.py`:

```python
import numpy as np
import pandas as pd
import pytest

from analytics.cvd.forecast import (
    DEFAULT_SPANS,
    cvd_combined_forecast,
    cvd_forecast,
    cvd_forecast_matrix,
)


def _x(n: int, seed: int = 3) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.Series(rng.uniform(-1.0, 1.0, size=n), index=idx)


def test_spans_are_the_fast_legs_of_default_speeds() -> None:
    assert DEFAULT_SPANS == (8, 16, 32, 64)


def test_forecast_is_capped_both_sides() -> None:
    x = pd.Series(
        [0.0] * 40 + [50.0] * 40,
        index=pd.date_range("2020-01-01", periods=80, freq="D", tz="UTC"),
    )
    out = cvd_forecast(x, span=8, vol_span=32, cap=20.0)
    assert out.max() <= 20.0
    assert out.min() >= -20.0


def test_warmup_is_nan_not_zero() -> None:
    """NaN warm-up is load-bearing: xs_demeaned_forecasts skips NaN so only
    warmed-up instruments contribute to the cross-sectional mean. Zeros would
    silently drag that mean."""
    out = cvd_forecast(_x(100), span=8, vol_span=32, cap=20.0)
    assert out.iloc[:32].isna().all()
    assert out.iloc[-1] == out.iloc[-1]


def test_forecast_is_unshifted_and_uses_the_same_day() -> None:
    """CRITICAL: both books shift internally. This module must NOT shift.

    Changing x on the LAST day must move the last forecast value. If it does
    not, someone has added a shift here and the books will double-shift.
    """
    x = _x(120)
    base = cvd_forecast(x, span=8, vol_span=32, cap=20.0)
    bumped = x.copy()
    bumped.iloc[-1] = bumped.iloc[-1] + 5.0
    after = cvd_forecast(bumped, span=8, vol_span=32, cap=20.0)
    assert base.iloc[-1] != after.iloc[-1]


def test_causality_a_future_value_never_moves_a_past_forecast() -> None:
    """Injected-violation test: perturb the LAST day, assert nothing before it
    moves. A guard that has never been shown to fail is not known to cover
    anything, so this asserts the failure mode directly."""
    x = _x(120)
    base = cvd_forecast(x, span=8, vol_span=32, cap=20.0)
    bumped = x.copy()
    bumped.iloc[-1] = bumped.iloc[-1] + 5.0
    after = cvd_forecast(bumped, span=8, vol_span=32, cap=20.0)
    pd.testing.assert_series_equal(base.iloc[:-1], after.iloc[:-1])


def test_causality_guard_catches_an_injected_lookahead() -> None:
    """Prove the test above is non-vacuous by feeding it a deliberately
    look-ahead series and asserting the comparison FAILS."""
    x = _x(120)
    leaky_base = cvd_forecast(x, span=8, vol_span=32, cap=20.0).shift(-1)
    bumped = x.copy()
    bumped.iloc[-1] = bumped.iloc[-1] + 5.0
    leaky_after = cvd_forecast(bumped, span=8, vol_span=32, cap=20.0).shift(-1)
    with pytest.raises(AssertionError):
        pd.testing.assert_series_equal(leaky_base.iloc[:-1], leaky_after.iloc[:-1])


def test_combined_is_capped_after_fdm() -> None:
    out = cvd_combined_forecast(
        _x(300), spans=DEFAULT_SPANS, fdm=1.25, vol_span=32, cap=20.0
    )
    assert out.dropna().abs().max() <= 20.0


def test_matrix_is_union_indexed_with_symbol_columns() -> None:
    a = _x(120, seed=1)
    b = _x(90, seed=2)
    mat = cvd_forecast_matrix(
        {"BTCUSDT": a, "ETHUSDT": b},
        spans=DEFAULT_SPANS,
        fdm=1.25,
        vol_span=32,
        cap=20.0,
    )
    assert list(mat.columns) == ["BTCUSDT", "ETHUSDT"]
    assert len(mat.index) == 120
    assert mat.index.equals(a.index.union(b.index))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/cvd/test_forecast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.cvd.forecast'`

- [ ] **Step 3: Write the forecast module**

Create `analytics/cvd/forecast.py`:

```python
"""Turn the spot-perp divergence into a Carver-convention forecast matrix.

Mirrors ``analytics/forecast/ewmac.py`` structure: vol-normalise, scale, cap,
then combine across spans with an FDM and re-cap. The output feeds
``run_xs_backtest(forecasts=...)`` and ``run_forecast_backtest(forecasts=...)``.

**UN-SHIFTED BY DESIGN.** Both books shift internally — ``xs_leverage`` does
``demeaned.shift(1)`` and ``instrument_returns`` does
``combine_forecasts(...).shift(1)``. Adding a shift here would double-shift and
silently destroy a day of signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# The fast legs of analytics/forecast/config.py:16 _DEFAULT_SPEEDS. EWMAC's
# speeds are fast/slow crossover PAIRS; `x` takes a single smoothing span, so
# the pairs cannot be inherited whole. Taker imbalance is a bounded, no-drift
# quantity — a 256-day EWMA of it is near-constant and would carry no signal.
DEFAULT_SPANS: tuple[int, ...] = (8, 16, 32, 64)

# Fixed a priori, never fitted. A smoothed series divided by its own trailing
# std is roughly unit-variance, so 10.0 lands the average absolute forecast near
# the Carver convention without touching the data.
FORECAST_SCALAR: float = 10.0


def cvd_forecast(x: pd.Series, span: int, vol_span: int, cap: float) -> pd.Series:
    """Vol-normalised, scaled, capped single-span forecast over the divergence."""
    smoothed = x.astype(float).ewm(span=span, adjust=False).mean()
    # Causal scale: .shift(1) mirrors analytics/forecast/vol.py ew_return_vol,
    # so day d's normaliser uses only values through d-1.
    scale = smoothed.ewm(span=vol_span, min_periods=vol_span).std().shift(1)
    normalised = smoothed / scale.where(scale != 0.0)
    out = normalised.replace([np.inf, -np.inf], np.nan) * FORECAST_SCALAR
    return out.clip(lower=-cap, upper=cap)


def cvd_combined_forecast(
    x: pd.Series,
    spans: tuple[int, ...],
    fdm: float,
    vol_span: int,
    cap: float,
) -> pd.Series:
    """Equal-weight mean of per-span forecasts x FDM, re-capped to +/-cap."""
    parts = [cvd_forecast(x, span, vol_span, cap) for span in spans]
    stacked = pd.concat(parts, axis=1)
    return (stacked.mean(axis=1) * fdm).clip(lower=-cap, upper=cap)


def cvd_forecast_matrix(
    x_by_symbol: dict[str, pd.Series],
    spans: tuple[int, ...],
    fdm: float,
    vol_span: int,
    cap: float,
) -> pd.DataFrame:
    """Per-symbol combined forecasts on the union daily index.

    Columns = symbols, index = sorted union of all symbols' dates, NaN during
    warm-up. Same contract as ``analytics/xsmom/book.py:27 xs_forecasts``,
    because it feeds the same socket. NaN warm-up is intentional: the
    cross-sectional demean skips NaN, so only warmed-up instruments contribute.
    """
    union = pd.DatetimeIndex([])
    for s in x_by_symbol.values():
        union = union.union(pd.DatetimeIndex(s.index))
    union = union.sort_values()
    cols = {
        sym: cvd_combined_forecast(x, spans, fdm, vol_span, cap).reindex(union)
        for sym, x in x_by_symbol.items()
    }
    return pd.DataFrame(cols, index=union)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/cvd/test_forecast.py -v`
Expected: 8 passed

- [ ] **Step 5: Run the gates and commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/cvd -q
git add analytics/cvd/forecast.py tests/cvd/test_forecast.py
git commit -m "feat(cvd): Carver-convention forecast matrix over the divergence"
```

---

### Task 5: Add a forecast-injection socket to the time-series book

**Files:**

- Modify: `analytics/forecast/book.py:20-59` (`instrument_returns`), `analytics/forecast/book.py:72-95` (`run_forecast_backtest`)
- Test: `tests/forecast/test_book_instrument.py` (append), `tests/forecast/test_book_portfolio.py` (append)

**Interfaces:**

- Consumes: nothing from earlier tasks
- Produces:
  - `instrument_returns(close: pd.Series, funding_daily: pd.Series, cfg: ForecastConfig, *, forecast: pd.Series | None = None) -> pd.DataFrame`
  - `run_forecast_backtest(closes: dict[str, pd.Series], fundings: dict[str, pd.Series], cfg: ForecastConfig, *, forecasts: dict[str, pd.Series] | None = None) -> ForecastBookResult`

**Why this task exists:** the XS book already has this socket (`run_xs_backtest(forecasts=...)`, `book.py:114`), but the TS book does not — `instrument_returns` calls `combine_forecasts(...)` internally with no way to override. Shape B cannot run without it. `None` must stay byte-identical, which is what the tests below pin.

**The injected series is UN-shifted.** `instrument_returns` applies `.shift(1)` itself; the injected forecast takes the same path as the internally-computed one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/forecast/test_book_instrument.py`:

```python
def test_injected_forecast_matching_internal_is_byte_identical() -> None:
    """None and an explicitly-passed identical forecast must agree exactly."""
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    rng = np.random.default_rng(5)
    close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))), index=idx)
    funding = pd.Series(0.0, index=idx)
    cfg = ForecastConfig()

    internal = combine_forecasts(
        close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
    )
    baseline = instrument_returns(close, funding, cfg)
    injected = instrument_returns(close, funding, cfg, forecast=internal)
    pd.testing.assert_frame_equal(baseline, injected)


def test_injected_forecast_is_shifted_by_the_book_not_the_caller() -> None:
    """A constant forecast must produce leverage from day 1 of vol warm-up,
    shifted one day — proving the book applies the shift to injected series
    exactly as it does to internal ones."""
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    rng = np.random.default_rng(6)
    close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))), index=idx)
    funding = pd.Series(0.0, index=idx)
    cfg = ForecastConfig()

    spiky = pd.Series(0.0, index=idx)
    spiky.iloc[200] = 10.0
    out = instrument_returns(close, funding, cfg, forecast=spiky)
    assert out["leverage"].iloc[200] == 0.0
    assert out["leverage"].iloc[201] != 0.0
```

Append to `tests/forecast/test_book_portfolio.py`:

```python
def test_run_forecast_backtest_injection_defaults_to_byte_identical() -> None:
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    rng = np.random.default_rng(7)
    closes = {
        sym: pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))), index=idx)
        for sym in ("AAA", "BBB")
    }
    fundings = {sym: pd.Series(0.0, index=idx) for sym in closes}
    cfg = ForecastConfig()

    baseline = run_forecast_backtest(closes, fundings, cfg)
    explicit = run_forecast_backtest(closes, fundings, cfg, forecasts=None)
    np.testing.assert_array_equal(baseline.portfolio_return, explicit.portfolio_return)


def test_run_forecast_backtest_uses_the_injected_matrix() -> None:
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    rng = np.random.default_rng(8)
    closes = {
        sym: pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))), index=idx)
        for sym in ("AAA", "BBB")
    }
    fundings = {sym: pd.Series(0.0, index=idx) for sym in closes}
    cfg = ForecastConfig()

    flat = {sym: pd.Series(0.0, index=idx) for sym in closes}
    out = run_forecast_backtest(closes, fundings, cfg, forecasts=flat)
    assert np.allclose(out.portfolio_return, 0.0)
```

Ensure `combine_forecasts`, `ForecastConfig`, `instrument_returns`, `run_forecast_backtest`, `numpy as np` and `pandas as pd` are imported at the top of each test file; add whichever are missing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/forecast/test_book_instrument.py tests/forecast/test_book_portfolio.py -v`
Expected: FAIL — `TypeError: instrument_returns() got an unexpected keyword argument 'forecast'`

- [ ] **Step 3: Add the socket to `instrument_returns`**

In `analytics/forecast/book.py`, change the signature and the first statement of the body:

```python
def instrument_returns(
    close: pd.Series,
    funding_daily: pd.Series,
    cfg: ForecastConfig,
    *,
    forecast: pd.Series | None = None,
) -> pd.DataFrame:
    """Causal subsystem returns for one instrument.

    Columns: leverage, gross, turnover_cost, funding_cost, net (indexed like
    `close`). `funding_daily` is the day's summed funding rate aligned to the
    close index (0.0 where missing). ``forecast`` (default ``None``) injects a
    sibling sleeve's raw, UN-shifted forecast in place of the EWMAC path — the
    time-series twin of ``run_xs_backtest(forecasts=...)``. ``None`` is
    byte-identical to the EWMAC path. The ``.shift(1)`` below applies to an
    injected series exactly as it does to an internal one, so callers must not
    pre-shift.
    """
    raw = (
        combine_forecasts(
            close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
        )
        if forecast is None
        else forecast.reindex(close.index)
    )
    forecast_shifted = raw.shift(1)
```

Then replace the remaining uses of the old local name: the line `leverage = (forecast / 10.0) * (cfg.vol_target_annual / vol_ann)` becomes

```python
    leverage = (forecast_shifted / 10.0) * (cfg.vol_target_annual / vol_ann)
```

Leave every other line of the function untouched.

- [ ] **Step 4: Add the socket to `run_forecast_backtest`**

Change the signature and the per-instrument call:

```python
def run_forecast_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    forecasts: dict[str, pd.Series] | None = None,
) -> ForecastBookResult:
    """Aggregate per-instrument subsystem returns + causal vol governor.

    ``forecasts`` (default ``None``) injects a sibling sleeve's raw, un-shifted
    per-instrument forecasts in place of the EWMAC path; ``None`` is
    byte-identical. A symbol absent from the dict falls back to EWMAC.
    """
```

and inside the per-symbol loop, replace `out = instrument_returns(close, fund, cfg)` with:

```python
        injected = None if forecasts is None else forecasts.get(sym)
        out = instrument_returns(close, fund, cfg, forecast=injected)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/forecast -v`
Expected: all pass, including the pre-existing suite (byte-identity is the point)

- [ ] **Step 6: Run the full gates and commit**

```bash
make lint-py && make typecheck && make test
git add analytics/forecast/book.py tests/forecast
git commit -m "feat(forecast): add forecast-injection socket to the time-series book"
```

Note in the commit body that `make test-regression` was not run and why: this diff touches `analytics/forecast/`, which is **not** in the regression-triggering set.

---

### Task 6: DB front door and replays

**Files:**

- Create: `analytics/cvd/replay.py`
- Test: `tests/cvd/test_replay.py`

**Interfaces:**

- Consumes: `analytics.cvd.fetch.spot_symbol_for`, `analytics.cvd.imbalance.divergence`, `analytics.cvd.forecast.{DEFAULT_SPANS, cvd_forecast_matrix, cvd_forecast}`, `analytics.store.spot_data.get_spot_ohlcv`, `analytics.store.market_data.get_ohlcv`, `analytics.forecast.replay.load_daily_inputs`, `analytics.universe.load_universe`, `analytics.xsmom.book.run_xs_backtest`, `analytics.forecast.book.run_forecast_backtest`
- Produces:
  - `cvd_universe(symbols: list[str] | None = None) -> list[str]`
  - `load_divergences(conn: duckdb.DuckDBPyConnection, symbols: list[str]) -> dict[str, pd.Series]`
  - `replay_cvd_xs(conn, cfg, *, symbols=None, spans=DEFAULT_SPANS) -> XSBookResult`
  - `replay_cvd_xs_trials(conn, cfg, *, symbols=None, spans=DEFAULT_SPANS) -> dict[str, np.ndarray]`
  - `replay_cvd_ts(conn, cfg, *, symbols=None, spans=DEFAULT_SPANS) -> ForecastBookResult`
  - `replay_cvd_ts_trials(conn, cfg, *, symbols=None, spans=DEFAULT_SPANS) -> dict[str, np.ndarray]`
  - `replay_xsmom_benchmark(conn, cfg, *, symbols=None) -> XSBookResult`

`cvd_universe` returns the universe minus `PERP_ONLY` — the 23 symbols. Trial dict keys are `span8`, `span16`, `span32`, `span64`, `combined` (5 per shape, matching the spec's declared family).

- [ ] **Step 1: Write the failing test**

Create `tests/cvd/test_replay.py`:

```python
import duckdb
import numpy as np
import pandas as pd

from analytics.cvd.replay import (
    cvd_universe,
    load_divergences,
    replay_cvd_ts_trials,
    replay_cvd_xs,
    replay_cvd_xs_trials,
)
from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.store.spot_data import upsert_spot_ohlcv

DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbols: list[str], n: int = 500) -> None:
    rng = np.random.default_rng(4)
    for sym in symbols:
        times = [i * DAY for i in range(n)]
        close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
        vol = rng.uniform(50.0, 150.0, n)
        upsert_ohlcv(
            conn,
            pd.DataFrame(
                {
                    "symbol": sym,
                    "timeframe": "1d",
                    "open_time": times,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": vol,
                    "taker_buy_volume": vol * rng.uniform(0.3, 0.7, n),
                }
            ),
        )
        svol = rng.uniform(50.0, 150.0, n)
        upsert_spot_ohlcv(
            conn,
            pd.DataFrame(
                {
                    "symbol": sym,
                    "open_time": times,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": svol,
                    "taker_buy_volume": svol * rng.uniform(0.3, 0.7, n),
                }
            ),
        )


def _conn(symbols: list[str]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, symbols)
    return conn


def test_cvd_universe_drops_the_two_perp_only_symbols() -> None:
    out = cvd_universe(["BTCUSDT", "HYPEUSDT", "VVVUSDT", "ETHUSDT"])
    assert out == ["BTCUSDT", "ETHUSDT"]


def test_load_divergences_returns_one_series_per_symbol() -> None:
    conn = _conn(["BTCUSDT", "ETHUSDT"])
    out = load_divergences(conn, ["BTCUSDT", "ETHUSDT"])
    assert set(out) == {"BTCUSDT", "ETHUSDT"}
    assert isinstance(out["BTCUSDT"].index, pd.DatetimeIndex)
    assert out["BTCUSDT"].abs().max() <= 2.0


def test_load_divergences_skips_a_symbol_with_no_spot_rows() -> None:
    conn = _conn(["BTCUSDT"])
    out = load_divergences(conn, ["BTCUSDT", "ETHUSDT"])
    assert set(out) == {"BTCUSDT"}


def test_xs_replay_produces_a_finite_return_series() -> None:
    conn = _conn(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    result = replay_cvd_xs(
        conn, ForecastConfig(), symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    )
    assert len(result.portfolio_return) > 0
    assert np.isfinite(result.portfolio_return).all()


def test_trial_family_has_the_five_declared_keys() -> None:
    conn = _conn(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    xs = replay_cvd_xs_trials(conn, ForecastConfig(), symbols=syms)
    ts = replay_cvd_ts_trials(conn, ForecastConfig(), symbols=syms)
    expected = {"span8", "span16", "span32", "span64", "combined"}
    assert set(xs) == expected
    assert set(ts) == expected


def test_trials_are_not_all_identical() -> None:
    """A family whose members are the same series makes PBO meaningless."""
    conn = _conn(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    trials = replay_cvd_xs_trials(
        conn, ForecastConfig(), symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    )
    assert not np.allclose(trials["span8"], trials["span64"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/cvd/test_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.cvd.replay'`

- [ ] **Step 3: Write the replay module**

Create `analytics/cvd/replay.py`:

```python
"""Read-only DuckDB front door for the D1 spot-perp CVD sleeve.

Reuses the trend sleeve's ``load_daily_inputs`` for closes and funding, adds the
spot leg, and runs both book shapes. The only module in ``analytics/cvd/`` that
touches the DB; never writes.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.cvd.fetch import spot_symbol_for
from analytics.cvd.forecast import (
    DEFAULT_SPANS,
    cvd_forecast,
    cvd_forecast_matrix,
)
from analytics.cvd.imbalance import divergence
from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.store.market_data import get_ohlcv
from analytics.store.spot_data import get_spot_ohlcv
from analytics.universe import load_universe
from analytics.xsmom.book import XSBookResult, run_xs_backtest

_FAR_PAST = 0
_FAR_FUTURE = 9_999_999_999_999


def cvd_universe(symbols: list[str] | None = None) -> list[str]:
    """Universe symbols that have a spot pair — 23 of the committed 25."""
    syms = symbols if symbols is not None else load_universe()
    return [s for s in syms if spot_symbol_for(s) is not None]


def load_divergences(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> dict[str, pd.Series]:
    """``imb_spot - imb_perp`` per symbol. Symbols missing either leg are skipped."""
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        spot_sym = spot_symbol_for(sym)
        if spot_sym is None:
            continue
        perp = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        spot = get_spot_ohlcv(conn, sym, _FAR_PAST, _FAR_FUTURE)
        if perp.empty or spot.empty:
            continue
        x = divergence(spot, perp)
        if not x.empty:
            out[sym] = x
    return out


def _inputs(
    conn: duckdb.DuckDBPyConnection, symbols: list[str] | None
) -> tuple[dict[str, pd.Series], dict[str, pd.Series], dict[str, pd.Series]]:
    syms = cvd_universe(symbols)
    xs = load_divergences(conn, syms)
    closes, fundings = load_daily_inputs(conn, list(xs))
    keep = set(closes) & set(xs)
    return (
        {k: v for k, v in closes.items() if k in keep},
        {k: v for k, v in fundings.items() if k in keep},
        {k: v for k, v in xs.items() if k in keep},
    )


def _matrix(
    x_by_symbol: dict[str, pd.Series], cfg: ForecastConfig, spans: tuple[int, ...]
) -> pd.DataFrame:
    return cvd_forecast_matrix(
        x_by_symbol, spans=spans, fdm=cfg.fdm, vol_span=cfg.vol_span, cap=cfg.cap
    )


def replay_cvd_xs(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
    spans: tuple[int, ...] = DEFAULT_SPANS,
) -> XSBookResult:
    """Shape A — the dollar-neutral cross-sectional book over the CVD forecast."""
    closes, fundings, xs = _inputs(conn, symbols)
    return run_xs_backtest(closes, fundings, cfg, forecasts=_matrix(xs, cfg, spans))


def replay_cvd_xs_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
    spans: tuple[int, ...] = DEFAULT_SPANS,
) -> dict[str, np.ndarray]:
    """Daily XS portfolio returns per single-span sleeve + the combined book.

    The declared multiple-testing family for DSR/PBO. Keys: `span{n}` per span,
    plus `combined`.
    """
    closes, fundings, xs = _inputs(conn, symbols)
    trials: dict[str, np.ndarray] = {}
    for span in spans:
        one = _matrix(xs, cfg, (span,))
        trials[f"span{span}"] = run_xs_backtest(
            closes, fundings, cfg, forecasts=one
        ).portfolio_return
    trials["combined"] = run_xs_backtest(
        closes, fundings, cfg, forecasts=_matrix(xs, cfg, spans)
    ).portfolio_return
    return trials


def _ts_forecasts(
    x_by_symbol: dict[str, pd.Series],
    cfg: ForecastConfig,
    spans: tuple[int, ...],
) -> dict[str, pd.Series]:
    mat = _matrix(x_by_symbol, cfg, spans)
    return {sym: mat[sym] for sym in mat.columns}


def replay_cvd_ts(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
    spans: tuple[int, ...] = DEFAULT_SPANS,
) -> ForecastBookResult:
    """Shape B — the per-symbol time-series book over the CVD forecast."""
    closes, fundings, xs = _inputs(conn, symbols)
    return run_forecast_backtest(
        closes, fundings, cfg, forecasts=_ts_forecasts(xs, cfg, spans)
    )


def replay_cvd_ts_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
    spans: tuple[int, ...] = DEFAULT_SPANS,
) -> dict[str, np.ndarray]:
    """Daily TS portfolio returns per single-span sleeve + the combined book."""
    closes, fundings, xs = _inputs(conn, symbols)
    trials: dict[str, np.ndarray] = {}
    for span in spans:
        single = {
            sym: cvd_forecast(x, span, cfg.vol_span, cfg.cap) for sym, x in xs.items()
        }
        trials[f"span{span}"] = run_forecast_backtest(
            closes, fundings, cfg, forecasts=single
        ).portfolio_return
    trials["combined"] = run_forecast_backtest(
        closes, fundings, cfg, forecasts=_ts_forecasts(xs, cfg, spans)
    ).portfolio_return
    return trials


def replay_xsmom_benchmark(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
) -> XSBookResult:
    """XS-momentum on the SAME 23 symbols, for a like-for-like comparison.

    The published +1.375 Sharpe is a 25-symbol number; comparing a 23-symbol CVD
    book against it directly would attribute a universe difference to the signal.
    """
    closes, fundings, _ = _inputs(conn, symbols)
    return run_xs_backtest(closes, fundings, cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/cvd/test_replay.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the gates and commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/cvd -q
git add analytics/cvd/replay.py tests/cvd/test_replay.py
git commit -m "feat(cvd): DB front door, both book shapes, and the trial families"
```

---

### Task 7: Report, gate, and the `corr_to_xsmom` stamp

**Files:**

- Create: `analytics/cvd/report.py`
- Test: `tests/cvd/test_report.py`

**Interfaces:**

- Consumes: `analytics.research_guards.{block_bootstrap_ci, cscv_pbo, deflated_sharpe_ratio, min_track_record_length, passes_gate}`, `analytics.xsmom.book.equity_curve`, `portfolio.metrics`
- Produces:
  - `CVDReport` frozen dataclass
  - `evaluate_cvd(portfolio_return: np.ndarray, cfg: ForecastConfig, trial_returns: dict[str, np.ndarray], xsmom_returns: np.ndarray, *, fold_to_magnitude: bool = False) -> CVDReport`
  - `cvd_gate_verdict(report: CVDReport) -> bool`

`CVDReport` fields: `sharpe_annual`, `max_dd`, `annual_return`, `annual_vol`, `n_obs`, `dsr`, `pbo`, `boot_lo`, `boot_hi`, `min_trl`, `corr_to_xsmom`, `xsmom_sharpe`, `folded_to_magnitude` — all `float` except `n_obs: int` and `folded_to_magnitude: bool`.

**`fold_to_magnitude` implements the spec's negative-direction rule.** `deflated_sharpe_ratio` and `min_track_record_length` are directional — DSR of a raw negative Sharpe collapses to ~0 and MinTRL is `inf`, so a negative verdict gated on either is structurally unreachable. When the flag is set, the target Sharpe **and every trial Sharpe** fold to `abs()`. The flag is recorded on the report so the choice is auditable.

- [ ] **Step 1: Write the failing test**

Create `tests/cvd/test_report.py`:

```python
import numpy as np
import pytest

from analytics.cvd.report import CVDReport, cvd_gate_verdict, evaluate_cvd
from analytics.forecast.config import ForecastConfig


def _returns(mean: float, n: int = 800, seed: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(mean, 0.01, n)


def _trials(mean: float) -> dict[str, np.ndarray]:
    return {f"span{s}": _returns(mean, seed=s) for s in (8, 16, 32, 64)} | {
        "combined": _returns(mean, seed=99)
    }


def test_report_carries_n_obs_and_the_correlation_stamp() -> None:
    r = _returns(0.001)
    rep = evaluate_cvd(r, ForecastConfig(), _trials(0.001), xsmom_returns=r)
    assert rep.n_obs == len(r)
    assert rep.corr_to_xsmom == pytest.approx(1.0)


def test_corr_to_xsmom_is_near_zero_for_independent_books() -> None:
    rep = evaluate_cvd(
        _returns(0.001, seed=1),
        ForecastConfig(),
        _trials(0.001),
        xsmom_returns=_returns(0.001, seed=77),
    )
    assert abs(rep.corr_to_xsmom) < 0.2


def test_corr_to_xsmom_handles_a_length_mismatch_by_aligning_tails() -> None:
    rep = evaluate_cvd(
        _returns(0.001, n=800),
        ForecastConfig(),
        _trials(0.001),
        xsmom_returns=_returns(0.001, n=500, seed=1),
    )
    assert np.isfinite(rep.corr_to_xsmom)


def test_gate_delegates_and_never_restates_thresholds() -> None:
    passing = CVDReport(
        sharpe_annual=1.5,
        max_dd=-0.1,
        annual_return=0.3,
        annual_vol=0.2,
        n_obs=800,
        dsr=0.99,
        pbo=0.2,
        boot_lo=0.4,
        boot_hi=2.0,
        min_trl=100.0,
        corr_to_xsmom=0.1,
        xsmom_sharpe=1.3,
        folded_to_magnitude=False,
    )
    assert cvd_gate_verdict(passing) is True
    assert cvd_gate_verdict(CVDReport(**{**passing.__dict__, "boot_lo": -0.1})) is False
    assert cvd_gate_verdict(CVDReport(**{**passing.__dict__, "pbo": 0.9})) is False
    assert cvd_gate_verdict(CVDReport(**{**passing.__dict__, "dsr": 0.5})) is False


def test_nan_pbo_fails_the_gate() -> None:
    """A single-config run yields NaN PBO, which must not read as a pass."""
    rep = CVDReport(
        sharpe_annual=1.5,
        max_dd=-0.1,
        annual_return=0.3,
        annual_vol=0.2,
        n_obs=800,
        dsr=0.99,
        pbo=float("nan"),
        boot_lo=0.4,
        boot_hi=2.0,
        min_trl=100.0,
        corr_to_xsmom=0.1,
        xsmom_sharpe=1.3,
        folded_to_magnitude=False,
    )
    assert cvd_gate_verdict(rep) is False


def test_negative_sharpe_collapses_dsr_unless_folded() -> None:
    """The H8/H14 defect: a reliably-negative book scores DSR ~0, making a
    negative verdict structurally unreachable. Folding to magnitude is the fix."""
    r = _returns(-0.001)
    trials = _trials(-0.001)
    signed = evaluate_cvd(r, ForecastConfig(), trials, xsmom_returns=r)
    folded = evaluate_cvd(
        r, ForecastConfig(), trials, xsmom_returns=r, fold_to_magnitude=True
    )
    assert signed.dsr < 0.5
    assert folded.dsr > signed.dsr
    assert folded.folded_to_magnitude is True
    assert signed.folded_to_magnitude is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/cvd/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.cvd.report'`

- [ ] **Step 3: Write the report module**

Create `analytics/cvd/report.py`:

```python
"""Assemble the D1 CVD verdict: headline metrics + the three-leg gate.

Mirrors ``analytics/xsmom/report.py`` and swaps its ``corr_to_trend`` stamp for
``corr_to_xsmom`` — for this sleeve the decisive comparison is against the
deploy core, not the shelved trend sleeve. A CVD book correlating ~0.9 with
XS-momentum is a restatement of the first edge, not a second one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
    passes_gate,
)
from portfolio import metrics


@dataclass(frozen=True)
class CVDReport:
    """Headline metrics + guards + the decorrelation read for the D1 verdict."""

    sharpe_annual: float
    max_dd: float
    annual_return: float
    annual_vol: float
    n_obs: int
    dsr: float
    pbo: float
    boot_lo: float
    boot_hi: float
    min_trl: float
    corr_to_xsmom: float
    xsmom_sharpe: float
    folded_to_magnitude: bool


def _per_period_sharpe(r: npt.NDArray[np.float64]) -> float:
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    return float(np.mean(r)) / sd if sd else 0.0


def _ann_sharpe(r: npt.NDArray[np.float64], ann: float) -> float:
    return _per_period_sharpe(r) * ann


def _aligned_corr(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return float("nan")
    x, y = a[-n:], b[-n:]
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def evaluate_cvd(
    portfolio_return: npt.NDArray[np.float64],
    cfg: ForecastConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    xsmom_returns: npt.NDArray[np.float64],
    *,
    fold_to_magnitude: bool = False,
) -> CVDReport:
    """All D1 metrics + research-guard stamps + the decorrelation read.

    ``trial_returns`` is the declared multiple-testing family (per-span sleeves
    + combined). ``fold_to_magnitude`` implements the spec's negative-direction
    rule: DSR and MinTRL are directional, so a negative-direction verdict gated
    on either is structurally unreachable unless the target AND every trial
    Sharpe fold to ``abs()``. Folding shrinks trial dispersion in a mixed-sign
    family, so the gate becomes marginally MORE permissive — bias runs toward
    more passes, never fewer.
    """
    r = np.asarray(portfolio_return, dtype=np.float64)
    curve = (1.0 + pd.Series(r)).cumprod()
    ann = math.sqrt(cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [_per_period_sharpe(np.asarray(v)) for v in trial_returns.values()]
    if fold_to_magnitude:
        sr_d = abs(sr_d)
        trial_srs = [abs(s) for s in trial_srs]

    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack(
            [np.asarray(v)[-min_len:] for v in trial_returns.values()]
        )
        pbo = cscv_pbo(mat).pbo
    else:
        pbo = float("nan")

    if sr_d != 0.0:

        def _stat_fn(x: npt.NDArray[np.float64]) -> float:
            return _ann_sharpe(x, ann)

        boot = block_bootstrap_ci(r, stat_fn=_stat_fn, seed=7)
        boot_lo, boot_hi = boot.lo, boot.hi
        dsr = deflated_sharpe_ratio(sr_d, len(r), trial_srs=trial_srs)
        min_trl = min_track_record_length(sr_d, target_sr=1.0 / ann, confidence=0.95)
    else:
        boot_lo = boot_hi = dsr = 0.0
        min_trl = float("inf")

    xsm = np.asarray(xsmom_returns, dtype=np.float64)
    xsm_curve = (1.0 + pd.Series(xsm)).cumprod()

    return CVDReport(
        sharpe_annual=metrics.sharpe(curve),
        max_dd=metrics.max_drawdown(curve),
        annual_return=metrics.annual_return(curve),
        annual_vol=metrics.annual_vol(curve),
        n_obs=len(r),
        dsr=dsr,
        pbo=pbo,
        boot_lo=boot_lo,
        boot_hi=boot_hi,
        min_trl=min_trl,
        corr_to_xsmom=_aligned_corr(r, xsm),
        xsmom_sharpe=metrics.sharpe(xsm_curve) if len(xsm) >= 2 else 0.0,
        folded_to_magnitude=fold_to_magnitude,
    )


def cvd_gate_verdict(report: CVDReport) -> bool:
    """The headline gate: DSR >= 0.95 AND PBO <= 0.5 AND boot_lo > 0.

    Delegates to :func:`analytics.research_guards.passes_gate`; the thresholds
    live only there. ``min_trl`` and ``corr_to_xsmom`` are reported stamps, NOT
    legs — ``corr_to_xsmom`` enters the verdict as a judgement under the spec's
    section 8, with the number printed.
    """
    return passes_gate(report.dsr, report.pbo, report.boot_lo)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/cvd/test_report.py -v`
Expected: 6 passed

If `test_nan_pbo_fails_the_gate` fails, `passes_gate` does not guard NaN. Do **not** add a threshold to `analytics/cvd/report.py` — fix it in `analytics/research_guards/gate.py` so all five sleeves inherit the fix, and add a test there.

- [ ] **Step 5: Run the gates and commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/cvd -q
git add analytics/cvd/report.py tests/cvd/test_report.py
git commit -m "feat(cvd): three-leg gate report with the corr_to_xsmom stamp"
```

---

### Task 8: Backfill and audit driver

**Files:**

- Create: `tools/cvd_audit.py`
- Test: `tests/cvd/test_audit_cli.py`

**Interfaces:**

- Consumes: everything from Tasks 1-7
- Produces:
  - `build_parser() -> argparse.ArgumentParser`
  - `backfill(conn, *, symbols, start_ms, get, sleep, trading=None) -> dict[str, int]`
  - `main(argv: list[str] | None = None) -> int`

Two subcommands: `backfill` (the only write path — `upsert_spot_ohlcv`, nothing else) and `run` (read-only; prints both shapes' reports).

- [ ] **Step 1: Write the failing test**

Create `tests/cvd/test_audit_cli.py`:

```python
from typing import Any

import duckdb
import pandas as pd

from analytics.store.schema import init_schema
from analytics.store.spot_data import get_spot_ohlcv
from tools.cvd_audit import backfill, build_parser

DAY = 86_400_000


def _kline(t: int) -> list[Any]:
    return [t, "1", "2", "0.5", "1.5", "10.0", t + DAY - 1, "0", 0, "6.0", "0", "0"]


def test_parser_exposes_backfill_and_run() -> None:
    parser = build_parser()
    assert parser.parse_args(["backfill"]).command == "backfill"
    assert parser.parse_args(["run"]).command == "run"


TRADING = frozenset({"BTCUSDT", "ETHUSDT", "PEPEUSDT"})


def test_backfill_writes_only_spot_ohlcv() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        return [_kline(0)] if "startTime=0" in url else []

    counts = backfill(
        conn,
        symbols=["BTCUSDT"],
        start_ms=0,
        get=fake_get,
        sleep=lambda _s: None,
        trading=TRADING,
    )
    assert counts == {"BTCUSDT": 1}
    assert len(get_spot_ohlcv(conn, "BTCUSDT", 0, DAY)) == 1
    assert conn.execute("SELECT count(*) FROM ohlcv").fetchone()[0] == 0


def test_backfill_skips_perp_only_symbols_without_calling_the_api() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        return []

    counts = backfill(
        conn,
        symbols=["HYPEUSDT"],
        start_ms=0,
        get=fake_get,
        sleep=lambda _s: None,
        trading=TRADING,
    )
    assert counts == {}
    assert calls == []


def test_backfill_maps_the_1000pepe_symbol_on_the_request() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        return []

    backfill(
        conn,
        symbols=["1000PEPEUSDT"],
        start_ms=0,
        get=fake_get,
        sleep=lambda _s: None,
        trading=TRADING,
    )
    assert "symbol=PEPEUSDT" in calls[0]


def test_backfill_skips_a_symbol_whose_spot_market_is_halted() -> None:
    """Kline availability is NOT proof a pair is live.

    Probed 2026-08-11: TONUSDT returns HTTP 200 daily klines while its spot
    exchangeInfo status is BREAK. Without the TRADING filter, a halted market's
    bars enter the panel and are indistinguishable from real data. The fake
    getter below deliberately SERVES klines for TONUSDT — so this test fails
    against a backfill that filters only on `spot_symbol_for`.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        return [_kline(0)] if "startTime=0" in url else []

    counts = backfill(
        conn,
        symbols=["TONUSDT"],
        start_ms=0,
        get=fake_get,
        sleep=lambda _s: None,
        trading=TRADING,
    )
    assert counts == {}
    assert calls == []
    assert len(get_spot_ohlcv(conn, "TONUSDT", 0, DAY)) == 0


def test_backfill_fetches_the_trading_set_when_not_supplied() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        if "exchangeInfo" in url:
            return {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING"}]}
        return [_kline(0)] if "startTime=0" in url else []

    counts = backfill(
        conn, symbols=["BTCUSDT"], start_ms=0, get=fake_get, sleep=lambda _s: None
    )
    assert counts == {"BTCUSDT": 1}
    assert any("exchangeInfo" in u for u in calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/cvd/test_audit_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.cvd_audit'`

- [ ] **Step 3: Write the driver**

Create `tools/cvd_audit.py`:

```python
"""D1 spot-perp CVD sleeve — backfill the spot leg and run the gate.

``backfill`` is the ONLY write path this tool has, and it writes only into the
additive ``spot_ohlcv`` table — never ``ohlcv``, never anything on the live
signal path. ``run`` is read-only.

Spec: docs/superpowers/specs/2026-08-11-d1-spot-perp-cvd-design.md
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from analytics.cvd.fetch import (
    fetch_spot_ohlcv_daily,
    fetch_trading_spot_symbols,
    spot_symbol_for,
)
from analytics.cvd.replay import (
    cvd_universe,
    replay_cvd_ts,
    replay_cvd_ts_trials,
    replay_cvd_xs,
    replay_cvd_xs_trials,
    replay_xsmom_benchmark,
)
from analytics.cvd.report import CVDReport, cvd_gate_verdict, evaluate_cvd
from analytics.forecast.config import ForecastConfig
from analytics.store._common import DEFAULT_DB_PATH
from analytics.store.schema import init_schema
from analytics.store.spot_data import upsert_spot_ohlcv
from analytics.venue_fetch import Getter, http_get_json

_START_MS = 1_546_300_800_000  # 2019-01-01, before the earliest perp listing.


def backfill(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    start_ms: int,
    get: Getter = http_get_json,
    sleep: Callable[[float], None] = time.sleep,
    trading: frozenset[str] | None = None,
) -> dict[str, int]:
    """Fetch and upsert daily spot bars. Returns rows written per perp symbol.

    ``trading`` is the set of spot symbols whose exchangeInfo status is TRADING;
    ``None`` fetches it. **Filtering on it is load-bearing, not defensive.**
    Kline availability is NOT proof a pair is live: probed 2026-08-11, TONUSDT
    returns HTTP 200 daily klines while its spot status is ``BREAK``. Without
    this filter a halted market's bars enter the panel and look like data.
    """
    live = fetch_trading_spot_symbols(get=get) if trading is None else trading
    written: dict[str, int] = {}
    skipped_not_trading: list[str] = []
    for sym in symbols:
        spot_sym = spot_symbol_for(sym)
        if spot_sym is None:
            continue
        if spot_sym not in live:
            skipped_not_trading.append(sym)
            continue
        df = fetch_spot_ohlcv_daily(spot_sym, start_ms, get=get)
        if df.empty:
            continue
        # Stored under the PERP symbol so joins against `ohlcv` need no mapping.
        df = df.assign(symbol=sym)
        upsert_spot_ohlcv(
            conn,
            df[
                [
                    "symbol",
                    "open_time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "taker_buy_volume",
                ]
            ],
        )
        written[sym] = len(df)
        sleep(0.25)
    if skipped_not_trading:
        print(f"[backfill] skipped, spot not TRADING: {', '.join(skipped_not_trading)}")
    return written


def _print_report(label: str, report: CVDReport) -> None:
    verdict = "PASS" if cvd_gate_verdict(report) else "FAIL"
    print(f"\n=== {label} ===")
    print(f"  Sharpe (ann)      {report.sharpe_annual:+.3f}")
    print(f"  max drawdown      {report.max_dd:+.3f}")
    print(f"  n_obs             {report.n_obs}")
    print(f"  DSR               {report.dsr:.4f}")
    print(f"  PBO               {report.pbo:.4f}")
    print(f"  boot CI           [{report.boot_lo:+.3f}, {report.boot_hi:+.3f}]")
    print(f"  GATE (3 legs)     {verdict}")
    print(f"  -- stamps, not legs --")
    print(f"  MinTRL            {report.min_trl:.0f}")
    print(f"  corr_to_xsmom     {report.corr_to_xsmom:+.3f}")
    print(f"  xsmom Sharpe/23   {report.xsmom_sharpe:+.3f}")
    if report.folded_to_magnitude:
        print("  NOTE: Sharpes folded to magnitude (negative-direction rule).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    bf = sub.add_parser("backfill", help="fetch daily spot bars (the only write path)")
    bf.add_argument("--start-ms", type=int, default=_START_MS)

    rn = sub.add_parser("run", help="run both book shapes and print the gate")
    rn.add_argument(
        "--fold-to-magnitude",
        action="store_true",
        help="negative-direction rule: fold target AND trial Sharpes to abs()",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = ForecastConfig()

    if args.command == "backfill":
        conn = duckdb.connect(str(args.db))
        init_schema(conn)
        counts = backfill(conn, symbols=cvd_universe(), start_ms=args.start_ms)
        print(f"[backfill] {sum(counts.values())} rows across {len(counts)} symbols")
        return 0

    conn = duckdb.connect(str(args.db), read_only=True)
    bench = replay_xsmom_benchmark(conn, cfg).portfolio_return
    _print_report(
        "Shape A - cross-sectional",
        evaluate_cvd(
            replay_cvd_xs(conn, cfg).portfolio_return,
            cfg,
            replay_cvd_xs_trials(conn, cfg),
            bench,
            fold_to_magnitude=args.fold_to_magnitude,
        ),
    )
    _print_report(
        "Shape B - time-series",
        evaluate_cvd(
            replay_cvd_ts(conn, cfg).portfolio_return,
            cfg,
            replay_cvd_ts_trials(conn, cfg),
            bench,
            fold_to_magnitude=args.fold_to_magnitude,
        ),
    )
    print("\n10 trials declared before any result: 5 per shape x 2 shapes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/cvd/test_audit_cli.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full gates and commit**

```bash
make lint-py && make typecheck && make test
git add tools/cvd_audit.py tests/cvd/test_audit_cli.py
git commit -m "feat(cvd): backfill and audit driver for the D1 sleeve"
```

---

### Task 9: Run the backfill, then the gate, then write the verdict

This is the only task that touches the network and the real DB. It produces no library code.

**Files:**

- Create: `docs/audits/2026-<MM>-<DD>-d1-spot-perp-cvd.md`
- Modify: `CLAUDE.md` (sleeve-verdict table)

- [ ] **Step 1: Check the DB is free before writing**

The 15-minute `buibui-signal-watch` timer holds `analytics.db`. Run:

```bash
poetry run python docs/plans/daily_check.py 2>&1 | rg 'analytics.db lock'
```

Expected: `free - scheduled write jobs can run`. If it names a holder, wait. Avoid minutes `:01/:16/:31/:46`.

- [ ] **Step 2: Back up before the first write**

```bash
make buibui-backup
```

- [ ] **Step 3: Run the backfill**

```bash
poetry run python -m tools.cvd_audit backfill
```

Expected: **~55k rows across 22 symbols**, plus a printed `skipped, spot not TRADING: TONUSDT` line. Probed 2026-08-11: TONUSDT's spot status is `BREAK` while its klines still return HTTP 200, so the TRADING filter excludes it — that skip line is the expected, correct outcome, not a failure. If a *different* symbol reports 0 rows, check its spot pair before assuming a bug. Record the actual admitted-symbol count in the verdict; the spec's "23" was written before this probe and the real number is whatever the filter admits on the day.

- [ ] **Step 4: Run the gate**

```bash
poetry run python -m tools.cvd_audit run 2>&1 | tee /tmp/cvd-verdict.txt
```

If either shape's Sharpe is reliably negative, re-run with `--fold-to-magnitude` and report **both** readings — the signed one and the folded one — never the folded one alone.

- [ ] **Step 5: Write the verdict document**

Create `docs/audits/2026-<MM>-<DD>-d1-spot-perp-cvd.md` carrying, per the spec's §11:

1. The three-leg stamp per shape (DSR, PBO, boot CI) and the PASS/FAIL.
2. `corr_to_xsmom` and the 23-symbol xsmom benchmark Sharpe.
3. The declared trial count (10) and confirmation that no parameter was swept.
4. The §3 intraday-path caveat: daily taker imbalance is coarse, so a NO here does not rule out an intraday-sequencing effect.
5. The §8 decision-rule row that the result lands in.
6. If folded to magnitude: the disclosure that folding makes the gate marginally more permissive.

- [ ] **Step 6: Add the sleeve-verdict row to CLAUDE.md**

In the "Sleeve verdicts — do NOT rebuild a shelved sleeve" table, add a row for `cvd/` spot-perp CVD with the measured Sharpe and the verdict, so a shelved result is not rebuilt.

- [ ] **Step 7: Commit**

```bash
git add docs/audits CLAUDE.md
git commit -m "docs(cvd): D1 spot-perp CVD verdict"
```

- [ ] **Step 8: Invoke `/post-branch`, then open the PR**

Steps 1-5b and 7 run **before** `gh pr create` so doc fixes ship in the initial push; Steps 6 and 10a/10c run after. Use `export GH_TOKEN=$(gh auth token --user s10023)`.

---

## Plan Self-Review

**Spec coverage:**

| spec section | task |
| --- | --- |
| §3 data, 23/25 coverage, symbol map | Task 2 |
| §3 daily-completeness identity | Task 3, `test_daily_bars_are_information_complete_for_daily_cvd` |
| §3 storage isolation | Task 1 |
| §4 primitive + scale-free | Task 3 |
| §4 sign convention | Task 3 docstring; asserted in `test_divergence_is_spot_minus_perp_on_the_inner_join` |
| §4 causality, assert on position | Task 4 causality tests + Task 5 shift tests |
| §5 Shape A | Task 6 `replay_cvd_xs` |
| §5 Shape B | Task 5 (socket) + Task 6 `replay_cvd_ts` |
| §6 no double-deflation | not applicable — no per-symbol cut is taken; if one is added later it must use `effective_independent_series` |
| §7 trial family of 5 per shape | Task 6, `test_trial_family_has_the_five_declared_keys` |
| §7 no tuning, inherited params | Task 4 `DEFAULT_SPANS` / `FORECAST_SCALAR` |
| §7 three legs via `passes_gate` | Task 7 |
| §7 `corr_to_xsmom` stamp | Task 7 |
| §7 benchmark on the same 23 | Task 6 `replay_xsmom_benchmark` |
| §8 decision rule | Task 9 Step 5 |
| §8 negative-direction folding | Task 7 `fold_to_magnitude` |
| §10 testing rules | every task |
| §11 deliverables | Tasks 1-9 |

**Gap found and closed:** the spec assumed both books had an injection socket. `analytics/forecast/book.py` does not — `instrument_returns` computes `combine_forecasts(...)` internally. Task 5 exists to add it and is a prerequisite for Shape B.

**Type consistency:** `spot_symbol_for` returns `str | None` in Tasks 2, 6 and 8. Trial keys are `span{n}` + `combined` in Tasks 6 and 7. `evaluate_cvd` takes `portfolio_return` as an array (not an `XSBookResult`) so it serves both `XSBookResult` and `ForecastBookResult` — Task 8 passes `.portfolio_return` from each.

**Known open question, deliberately not resolved here:** the spec leaves the `corr_to_xsmom` cutoff as a printed judgement rather than a threshold. Task 9 Step 5 records the number; the decision is the operator's.
