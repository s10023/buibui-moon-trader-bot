# N3 Universe + Deep-History Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest a 25-symbol liquid USDT-M perp universe with deep OHLCV (listing→now, 1h/4h/1d/1w + majors 15m) and funding history into `analytics.db`, with survivorship-aware `symbol_lifecycle` metadata and a committed coverage report — zero behaviour change to the live daemon, backtests, or regression goldens.

**Architecture:** Thin extensions to the existing `data_fetcher → data_sync → analytics_runner → cli` ingest stack: a committed `config/universe.toml` + loader, a new `symbol_lifecycle` table refreshed at every backfill/sync, a `--universe` CLI flag, and two read-only tools (universe selector, coverage report). `tools/export_live_db.py` gets scoped so deep/universe rows never reach the committed slim DB.

**Tech Stack:** Python 3.13, DuckDB, pandas, python-binance client (mocked in tests), tomllib, pytest + unittest.mock. Spec: `docs/superpowers/specs/2026-06-12-n3-universe-backfill-design.md`.

**Conventions (apply to every task):** mypy strict — every function fully annotated. Tests: in-memory DuckDB (`duckdb.connect(":memory:")` + `init_schema`), `MagicMock` clients, never the real `analytics.db`, no network. After each task's tests pass, the commit step runs. Final DoD: `make lint-py` + `make typecheck` + `make test` + `make test-regression` (goldens must be byte-identical).

---

## Task 1: `config/universe.toml` + `analytics/universe.py` loader

**Files:**

- Create: `config/universe.toml`
- Create: `analytics/universe.py`
- Test: `tests/test_universe.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_universe.py`:

```python
"""Tests for analytics/universe.py — research-universe config loader."""

from pathlib import Path

import pytest

from analytics.universe import DEFAULT_UNIVERSE_PATH, load_universe


def _write_toml(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


class TestLoadUniverse:
    def test_loads_symbols_list(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path / "universe.toml",
            '[universe]\nselected_at = "2026-06-12"\n'
            'criterion = "test"\nsymbols = ["BTCUSDT", "ETHUSDT"]\n',
        )
        assert load_universe(p) == ["BTCUSDT", "ETHUSDT"]

    def test_uppercases_strips_and_dedupes(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path / "universe.toml",
            '[universe]\nsymbols = [" btcusdt", "BTCUSDT", "ethusdt "]\n',
        )
        assert load_universe(p) == ["BTCUSDT", "ETHUSDT"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_universe(tmp_path / "nope.toml")

    def test_empty_symbols_raises(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path / "universe.toml", "[universe]\nsymbols = []\n")
        with pytest.raises(ValueError, match="symbols"):
            load_universe(p)

    def test_missing_universe_block_raises(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path / "universe.toml", '[other]\nx = 1\n')
        with pytest.raises(ValueError, match="symbols"):
            load_universe(p)

    def test_non_string_entry_raises(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path / "universe.toml", "[universe]\nsymbols = [42]\n")
        with pytest.raises(ValueError, match="invalid symbol"):
            load_universe(p)

    def test_default_path_points_at_committed_config(self) -> None:
        # The committed config/universe.toml must load through the default path.
        assert DEFAULT_UNIVERSE_PATH == Path("config/universe.toml")
        symbols = load_universe()
        assert "BTCUSDT" in symbols
        assert 10 <= len(symbols) <= 30
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_universe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.universe'`

- [ ] **Step 1.3: Create `config/universe.toml`**

```toml
# Research universe (SoT item N3) — selected by tools/select_universe.py.
# Refresh = rerun the tool, review the diff, commit. Rotated-out symbols keep
# their OHLCV/funding data; the symbol_lifecycle table records the history.
# This file is deliberately NOT coins.json — coins.json drives the live daemon
# and backtest defaults; the universe is research-only breadth (P2/P3).
[universe]
selected_at = "2026-06-12"
criterion = "top-25 USDT-M perpetuals by 30d median daily quote volume; status TRADING; stablecoin bases excluded; listed >= 1y (Binance fapi snapshot)"
symbols = [
  "BTCUSDT",
  "ETHUSDT",
  "SOLUSDT",
  "ZECUSDT",
  "HYPEUSDT",
  "XRPUSDT",
  "DOGEUSDT",
  "NEARUSDT",
  "BNBUSDT",
  "WLDUSDT",
  "SUIUSDT",
  "1000PEPEUSDT",
  "ADAUSDT",
  "TONUSDT",
  "ONDOUSDT",
  "TAOUSDT",
  "LINKUSDT",
  "AVAXUSDT",
  "BCHUSDT",
  "FILUSDT",
  "INJUSDT",
  "ENAUSDT",
  "XLMUSDT",
  "VVVUSDT",
  "PAXGUSDT",
]
```

- [ ] **Step 1.4: Create `analytics/universe.py`**

```python
"""Research-universe config loader (N3).

The universe is the breadth set for P2 trend / P3 XS-momentum research —
deliberately separate from config/coins.json (which drives the live daemon and
backtest defaults). Selection criterion + refresh tool: tools/select_universe.py.
"""

import tomllib
from pathlib import Path

DEFAULT_UNIVERSE_PATH = Path("config/universe.toml")


def load_universe(path: Path | str = DEFAULT_UNIVERSE_PATH) -> list[str]:
    """Return the universe symbols from a [universe] TOML block.

    Symbols are stripped, uppercased, and deduped (first occurrence wins, order
    preserved). Raises FileNotFoundError if the file is missing and ValueError
    on a missing/empty/malformed symbols list.
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)
    block = data.get("universe", {})
    raw = block.get("symbols", [])
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"universe config {path} has no [universe].symbols list")
    seen: set[str] = set()
    symbols: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"invalid symbol entry in {path}: {entry!r}")
        sym = entry.strip().upper()
        if sym not in seen:
            seen.add(sym)
            symbols.append(sym)
    return symbols
```

- [ ] **Step 1.5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_universe.py -v`
Expected: 7 PASS

- [ ] **Step 1.6: Commit**

```bash
git add config/universe.toml analytics/universe.py tests/test_universe.py
git commit -m "feat(universe): committed 25-perp research universe + loader (N3)"
```

---

## Task 2: `symbol_lifecycle` table + store accessors

**Files:**

- Modify: `analytics/store/schema.py` (append in `init_schema`, after the `open_interest` block)
- Modify: `analytics/store/market_data.py` (append accessors)
- Modify: `analytics/store/__init__.py` (export new names)
- Test: `tests/test_symbol_lifecycle.py` (new)

- [ ] **Step 2.1: Write the failing tests**

Create `tests/test_symbol_lifecycle.py`:

```python
"""Tests for the symbol_lifecycle table (N3 survivorship guard)."""

import duckdb
import pandas as pd

from analytics.data_store import (
    get_symbol_lifecycle,
    init_schema,
    upsert_symbol_lifecycle,
)

LIFE_COLS = [
    "symbol",
    "status",
    "onboard_ms",
    "first_checked_ms",
    "last_checked_ms",
    "delisted_noted_ms",
]


def _make_conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def _life_df(rows: list[dict[str, object]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=LIFE_COLS)
    for col in ("onboard_ms", "first_checked_ms", "last_checked_ms", "delisted_noted_ms"):
        df[col] = df[col].astype("Int64")
    return df


class TestSymbolLifecycleStore:
    def test_upsert_and_get_roundtrip(self) -> None:
        conn = _make_conn()
        upsert_symbol_lifecycle(
            conn,
            _life_df(
                [
                    {
                        "symbol": "BTCUSDT",
                        "status": "TRADING",
                        "onboard_ms": 1_567_900_800_000,
                        "first_checked_ms": 100,
                        "last_checked_ms": 100,
                        "delisted_noted_ms": None,
                    }
                ]
            ),
        )
        df = get_symbol_lifecycle(conn)
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "BTCUSDT"
        assert df.iloc[0]["status"] == "TRADING"
        assert pd.isna(df.iloc[0]["delisted_noted_ms"])

    def test_upsert_replaces_on_symbol_conflict(self) -> None:
        conn = _make_conn()
        row = {
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "onboard_ms": 1,
            "first_checked_ms": 100,
            "last_checked_ms": 100,
            "delisted_noted_ms": None,
        }
        upsert_symbol_lifecycle(conn, _life_df([row]))
        row["status"] = "DELISTED"
        row["last_checked_ms"] = 200
        row["delisted_noted_ms"] = 200
        upsert_symbol_lifecycle(conn, _life_df([row]))
        df = get_symbol_lifecycle(conn)
        assert len(df) == 1
        assert df.iloc[0]["status"] == "DELISTED"
        assert int(df.iloc[0]["delisted_noted_ms"]) == 200
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_symbol_lifecycle.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_symbol_lifecycle'`

- [ ] **Step 2.3: Add the table to `analytics/store/schema.py`**

In `init_schema`, immediately after the `open_interest` `conn.execute("""...""")` block, add:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbol_lifecycle (
            symbol            TEXT   PRIMARY KEY,
            status            TEXT   NOT NULL,
            onboard_ms        BIGINT,
            first_checked_ms  BIGINT NOT NULL,
            last_checked_ms   BIGINT NOT NULL,
            delisted_noted_ms BIGINT
        )
    """)
```

- [ ] **Step 2.4: Add accessors to `analytics/store/market_data.py`**

Append:

```python
def upsert_symbol_lifecycle(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace symbol lifecycle rows (N3 survivorship guard).

    df must have columns: symbol, status, onboard_ms, first_checked_ms,
    last_checked_ms, delisted_noted_ms. Conflicts on (symbol) are replaced.
    """
    _upsert(
        conn,
        df,
        "symbol_lifecycle",
        "symbol, status, onboard_ms, first_checked_ms, last_checked_ms, delisted_noted_ms",
    )


def get_symbol_lifecycle(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return all symbol lifecycle rows ordered by symbol."""
    return conn.execute(
        "SELECT symbol, status, onboard_ms, first_checked_ms, last_checked_ms, "
        "delisted_noted_ms FROM symbol_lifecycle ORDER BY symbol"
    ).df()
```

- [ ] **Step 2.5: Export from `analytics/store/__init__.py`**

In the `from analytics.store.market_data import (...)` block add `get_symbol_lifecycle,` and `upsert_symbol_lifecycle,` (alphabetical position), and add `"get_symbol_lifecycle",` and `"upsert_symbol_lifecycle",` to `__all__` (alphabetical position).

- [ ] **Step 2.6: Run tests to verify they pass**

Run: `poetry run pytest tests/test_symbol_lifecycle.py -v`
Expected: 2 PASS

- [ ] **Step 2.7: Commit**

```bash
git add analytics/store/schema.py analytics/store/market_data.py analytics/store/__init__.py tests/test_symbol_lifecycle.py
git commit -m "feat(store): symbol_lifecycle table + accessors (N3 survivorship guard)"
```

---

## Task 3: `fetch_futures_symbol_info` in `analytics/data_fetcher.py`

**Files:**

- Modify: `analytics/data_fetcher.py`
- Test: `tests/test_data_fetcher.py` (append class)

- [ ] **Step 3.1: Write the failing tests**

Append to `tests/test_data_fetcher.py`:

```python
_EXCHANGE_INFO_RAW: dict[str, Any] = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "onboardDate": 1_569_398_400_000,
            "quoteAsset": "USDT",
            "contractType": "PERPETUAL",
        },
        {
            "symbol": "ETHUSDT_260626",  # dated future, not a perp — excluded
            "status": "TRADING",
            "onboardDate": 1_700_000_000_000,
            "quoteAsset": "USDT",
            "contractType": "CURRENT_QUARTER",
        },
        {
            "symbol": "BTCUSDC",  # wrong quote — excluded
            "status": "TRADING",
            "onboardDate": 1_700_000_000_000,
            "quoteAsset": "USDC",
            "contractType": "PERPETUAL",
        },
        {
            "symbol": "OLDUSDT",  # non-TRADING status still returned (caller decides)
            "status": "SETTLING",
            "onboardDate": 1_600_000_000_000,
            "quoteAsset": "USDT",
            "contractType": "PERPETUAL",
        },
    ]
}


class TestFetchFuturesSymbolInfo:
    def test_returns_usdt_perpetuals_only(self) -> None:
        client = MagicMock()
        client.futures_exchange_info.return_value = _EXCHANGE_INFO_RAW
        df = fetch_futures_symbol_info(client)
        assert list(df.columns) == LIFECYCLE_COLUMNS
        assert sorted(df["symbol"]) == ["BTCUSDT", "OLDUSDT"]

    def test_maps_status_and_onboard_ms(self) -> None:
        client = MagicMock()
        client.futures_exchange_info.return_value = _EXCHANGE_INFO_RAW
        df = fetch_futures_symbol_info(client)
        btc = df[df["symbol"] == "BTCUSDT"].iloc[0]
        assert btc["status"] == "TRADING"
        assert int(btc["onboard_ms"]) == 1_569_398_400_000

    def test_empty_response_returns_empty_frame_with_columns(self) -> None:
        client = MagicMock()
        client.futures_exchange_info.return_value = {"symbols": []}
        df = fetch_futures_symbol_info(client)
        assert df.empty
        assert list(df.columns) == LIFECYCLE_COLUMNS
```

Also extend the imports at the top of the file:

```python
from analytics.data_fetcher import (
    FUNDING_COLUMNS,
    KLINES_MAX_LIMIT,
    LIFECYCLE_COLUMNS,
    OHLCV_COLUMNS,
    OI_COLUMNS,
    fetch_funding_rates,
    fetch_futures_symbol_info,
    fetch_klines,
    fetch_open_interest,
)
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_data_fetcher.py -v`
Expected: FAIL — `ImportError: cannot import name 'LIFECYCLE_COLUMNS'`

- [ ] **Step 3.3: Implement in `analytics/data_fetcher.py`**

Next to the other `*_COLUMNS` constants add:

```python
LIFECYCLE_COLUMNS: list[str] = ["symbol", "status", "onboard_ms"]
```

Append the fetcher (after `fetch_open_interest`):

```python
def fetch_futures_symbol_info(client: Client) -> pd.DataFrame:
    """Fetch USDT-M perpetual symbol metadata from futures exchangeInfo.

    Returns a DataFrame with columns matching LIFECYCLE_COLUMNS — one row per
    USDT-quoted PERPETUAL contract, regardless of status (callers decide how to
    treat non-TRADING). Binance-only: the OKX adapter does not serve
    exchangeInfo, and lifecycle refresh runs on the Binance backfill path.
    """
    raw = client.futures_exchange_info()
    rows = [
        {
            "symbol": s["symbol"],
            "status": str(s.get("status", "")),
            "onboard_ms": int(s["onboardDate"]) if s.get("onboardDate") is not None else None,
        }
        for s in raw.get("symbols", [])
        if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL"
    ]
    df = pd.DataFrame(rows, columns=LIFECYCLE_COLUMNS)
    df["onboard_ms"] = df["onboard_ms"].astype("Int64")
    return df
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_data_fetcher.py -v`
Expected: all PASS (existing + 3 new)

- [ ] **Step 3.5: Commit**

```bash
git add analytics/data_fetcher.py tests/test_data_fetcher.py
git commit -m "feat(fetcher): fetch_futures_symbol_info — USDT-M perp metadata for lifecycle"
```

---

## Task 4: `refresh_symbol_lifecycle` in `analytics/data_sync.py`

**Files:**

- Modify: `analytics/data_sync.py`
- Test: `tests/test_symbol_lifecycle.py` (append class)

- [ ] **Step 4.1: Write the failing tests**

Append to `tests/test_symbol_lifecycle.py`:

```python
from unittest.mock import patch

from analytics.data_sync import refresh_symbol_lifecycle


def _info_df(rows: list[dict[str, object]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["symbol", "status", "onboard_ms"])
    df["onboard_ms"] = df["onboard_ms"].astype("Int64")
    return df


class TestRefreshSymbolLifecycle:
    def test_inserts_new_symbols(self) -> None:
        conn = _make_conn()
        info = _info_df(
            [{"symbol": "BTCUSDT", "status": "TRADING", "onboard_ms": 111}]
        )
        with patch("analytics.data_sync.fetch_futures_symbol_info", return_value=info):
            n = refresh_symbol_lifecycle(conn, object(), ["BTCUSDT"], now_ms=1_000)
        assert n == 1
        df = get_symbol_lifecycle(conn)
        row = df.iloc[0]
        assert row["symbol"] == "BTCUSDT"
        assert row["status"] == "TRADING"
        assert int(row["onboard_ms"]) == 111
        assert int(row["first_checked_ms"]) == 1_000
        assert int(row["last_checked_ms"]) == 1_000
        assert pd.isna(row["delisted_noted_ms"])

    def test_update_preserves_first_checked_ms(self) -> None:
        conn = _make_conn()
        info = _info_df(
            [{"symbol": "BTCUSDT", "status": "TRADING", "onboard_ms": 111}]
        )
        with patch("analytics.data_sync.fetch_futures_symbol_info", return_value=info):
            refresh_symbol_lifecycle(conn, object(), ["BTCUSDT"], now_ms=1_000)
            refresh_symbol_lifecycle(conn, object(), ["BTCUSDT"], now_ms=2_000)
        row = get_symbol_lifecycle(conn).iloc[0]
        assert int(row["first_checked_ms"]) == 1_000
        assert int(row["last_checked_ms"]) == 2_000

    def test_absent_symbol_marked_delisted_once(self) -> None:
        conn = _make_conn()
        present = _info_df(
            [{"symbol": "BTCUSDT", "status": "TRADING", "onboard_ms": 111}]
        )
        gone = _info_df([])
        with patch("analytics.data_sync.fetch_futures_symbol_info", return_value=present):
            refresh_symbol_lifecycle(conn, object(), ["BTCUSDT"], now_ms=1_000)
        with patch("analytics.data_sync.fetch_futures_symbol_info", return_value=gone):
            refresh_symbol_lifecycle(conn, object(), [], now_ms=2_000)
            refresh_symbol_lifecycle(conn, object(), [], now_ms=3_000)
        row = get_symbol_lifecycle(conn).iloc[0]
        assert row["status"] == "DELISTED"
        # noted at first absence and sticky thereafter
        assert int(row["delisted_noted_ms"]) == 2_000
        # onboard_ms survives delisting
        assert int(row["onboard_ms"]) == 111

    def test_delisting_never_touches_ohlcv(self) -> None:
        conn = _make_conn()
        conn.execute(
            "INSERT INTO ohlcv VALUES ('GONEUSDT', '1h', 1, 10, 11, 9, 10.5, 100, 50)"
        )
        with patch(
            "analytics.data_sync.fetch_futures_symbol_info", return_value=_info_df([])
        ):
            refresh_symbol_lifecycle(conn, object(), ["GONEUSDT"], now_ms=1_000)
        assert conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone() == (1,)
        assert get_symbol_lifecycle(conn).iloc[0]["status"] == "DELISTED"

    def test_tracks_union_of_existing_and_requested(self) -> None:
        conn = _make_conn()
        info = _info_df(
            [
                {"symbol": "BTCUSDT", "status": "TRADING", "onboard_ms": 1},
                {"symbol": "ETHUSDT", "status": "TRADING", "onboard_ms": 2},
            ]
        )
        with patch("analytics.data_sync.fetch_futures_symbol_info", return_value=info):
            refresh_symbol_lifecycle(conn, object(), ["BTCUSDT"], now_ms=1_000)
            # second run requests only ETHUSDT — BTCUSDT must still be refreshed
            refresh_symbol_lifecycle(conn, object(), ["ETHUSDT"], now_ms=2_000)
        df = get_symbol_lifecycle(conn)
        assert sorted(df["symbol"]) == ["BTCUSDT", "ETHUSDT"]
        assert int(df[df["symbol"] == "BTCUSDT"].iloc[0]["last_checked_ms"]) == 2_000
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_symbol_lifecycle.py -v`
Expected: FAIL — `ImportError: cannot import name 'refresh_symbol_lifecycle'`

- [ ] **Step 4.3: Implement in `analytics/data_sync.py`**

Extend the two import blocks at the top:

```python
from analytics.data_fetcher import (
    KLINES_MAX_LIMIT,
    KlineClient,
    OIPeriod,
    fetch_funding_rates,
    fetch_futures_symbol_info,
    fetch_klines,
    fetch_open_interest,
)
from analytics.data_store import (
    get_latest_open_time,
    get_symbol_lifecycle,
    upsert_funding_rates,
    upsert_ohlcv,
    upsert_open_interest,
    upsert_symbol_lifecycle,
)
```

Add `import pandas as pd` to the imports (after `import duckdb`). Append the function:

```python
def refresh_symbol_lifecycle(
    conn: duckdb.DuckDBPyConnection,
    client: Client,
    symbols: list[str],
    now_ms: int | None = None,
) -> int:
    """Refresh the symbol_lifecycle table from futures exchangeInfo (N3).

    Tracked set = existing lifecycle rows ∪ `symbols` for this run. Symbols
    present in exchangeInfo get their status + last_checked_ms updated
    (first_checked_ms preserved). Symbols absent are marked DELISTED with a
    sticky delisted_noted_ms — their OHLCV/funding rows are NEVER touched
    (noted, not dropped). Returns the number of rows upserted.

    Survivorship limitation (documented): perps delisted before first tracking
    are not enumerable from the free API, so the guard is forward-looking.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    info = fetch_futures_symbol_info(client)
    info_map = {str(r["symbol"]): r for _, r in info.iterrows()}
    existing = get_symbol_lifecycle(conn)
    existing_map = {str(r["symbol"]): r for _, r in existing.iterrows()}
    tracked = sorted(set(symbols) | set(existing_map))
    if not tracked:
        return 0

    def _opt_int(value: object) -> int | None:
        return None if value is None or pd.isna(value) else int(value)  # type: ignore[arg-type]

    rows: list[dict[str, object]] = []
    for sym in tracked:
        prev = existing_map.get(sym)
        live = info_map.get(sym)
        first_checked = _opt_int(prev["first_checked_ms"]) if prev is not None else None
        onboard = _opt_int(prev["onboard_ms"]) if prev is not None else None
        delisted_noted = _opt_int(prev["delisted_noted_ms"]) if prev is not None else None
        if live is not None:
            status = str(live["status"])
            onboard = _opt_int(live["onboard_ms"])
        else:
            status = "DELISTED"
            if delisted_noted is None:
                delisted_noted = now
        rows.append(
            {
                "symbol": sym,
                "status": status,
                "onboard_ms": onboard,
                "first_checked_ms": first_checked if first_checked is not None else now,
                "last_checked_ms": now,
                "delisted_noted_ms": delisted_noted,
            }
        )
    df = pd.DataFrame(
        rows,
        columns=[
            "symbol",
            "status",
            "onboard_ms",
            "first_checked_ms",
            "last_checked_ms",
            "delisted_noted_ms",
        ],
    )
    for col in ("onboard_ms", "first_checked_ms", "last_checked_ms", "delisted_noted_ms"):
        df[col] = df[col].astype("Int64")
    upsert_symbol_lifecycle(conn, df)
    logging.info("refresh_symbol_lifecycle: %d symbols tracked", len(df))
    return len(df)
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_symbol_lifecycle.py tests/test_data_sync.py -v`
Expected: all PASS

- [ ] **Step 4.5: Commit**

```bash
git add analytics/data_sync.py tests/test_symbol_lifecycle.py
git commit -m "feat(sync): refresh_symbol_lifecycle — delisted noted, never dropped"
```

---

## Task 5: Runner wiring — lifecycle refresh + per-symbol resilience

**Files:**

- Modify: `analytics/analytics_runner.py`
- Test: `tests/test_analytics_runner.py` (new)

- [ ] **Step 5.1: Write the failing tests**

Create `tests/test_analytics_runner.py`:

```python
"""Tests for analytics/analytics_runner.py — lifecycle wiring + resilience."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from analytics.analytics_runner import run_backfill, run_sync


def _patches(**overrides: Any) -> Any:
    """Patch every collaborator of the runner; return the patch context tuple."""
    return (
        patch("analytics.analytics_runner.create_client", return_value=MagicMock()),
        patch(
            "analytics.analytics_runner.refresh_symbol_lifecycle",
            **overrides.get("lifecycle", {"return_value": 0}),
        ),
        patch(
            "analytics.analytics_runner.backfill",
            **overrides.get("backfill", {"return_value": 1}),
        ),
        patch(
            "analytics.analytics_runner.sync",
            **overrides.get("sync", {"return_value": 1}),
        ),
        patch("analytics.analytics_runner._sync_ancillary"),
    )


class TestRunBackfillResilience:
    def test_continues_past_failing_symbol_then_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        def fake_backfill(
            conn: Any, client: Any, symbol: str, timeframe: str, since_ms: int
        ) -> int:
            calls.append(symbol)
            if symbol == "AAAUSDT":
                raise RuntimeError("boom")
            return 1

        p = _patches(backfill={"side_effect": fake_backfill})
        with p[0], p[1], p[2], p[3], p[4], pytest.raises(SystemExit):
            run_backfill(
                ["AAAUSDT", "BBBUSDT"], ["1h"], 0, db_path=tmp_path / "t.db"
            )
        assert "BBBUSDT" in calls  # later symbol still processed

    def test_all_green_does_not_exit(self, tmp_path: Path) -> None:
        p = _patches()
        with p[0], p[1], p[2], p[3], p[4]:
            run_backfill(["AAAUSDT"], ["1h"], 0, db_path=tmp_path / "t.db")

    def test_lifecycle_failure_is_nonfatal(self, tmp_path: Path) -> None:
        p = _patches(lifecycle={"side_effect": RuntimeError("api down")})
        with p[0], p[1] as mock_life, p[2] as mock_backfill, p[3], p[4]:
            run_backfill(["AAAUSDT"], ["1h"], 0, db_path=tmp_path / "t.db")
        assert mock_life.called
        assert mock_backfill.called  # ingest proceeded despite lifecycle failure

    def test_lifecycle_called_with_resolved_symbols(self, tmp_path: Path) -> None:
        p = _patches()
        with p[0], p[1] as mock_life, p[2], p[3], p[4]:
            run_backfill(["AAAUSDT", "BBBUSDT"], ["1h"], 0, db_path=tmp_path / "t.db")
        assert mock_life.call_args[0][2] == ["AAAUSDT", "BBBUSDT"]


class TestRunSyncResilience:
    def test_continues_past_failing_symbol_then_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        def fake_sync(conn: Any, client: Any, symbol: str, timeframe: str) -> int:
            calls.append(symbol)
            if symbol == "AAAUSDT":
                raise RuntimeError("boom")
            return 1

        p = _patches(sync={"side_effect": fake_sync})
        with p[0], p[1], p[2], p[3], p[4], pytest.raises(SystemExit):
            run_sync(["AAAUSDT", "BBBUSDT"], ["1h"], db_path=tmp_path / "t.db")
        assert "BBBUSDT" in calls
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_analytics_runner.py -v`
Expected: FAIL — `ImportError: cannot import name 'refresh_symbol_lifecycle'` (not yet imported by the runner) or patch target missing

- [ ] **Step 5.3: Implement in `analytics/analytics_runner.py`**

Extend the data_sync import:

```python
from analytics.data_sync import (
    backfill,
    backfill_funding_rates,
    refresh_symbol_lifecycle,
    sync,
    sync_funding_rates,
    sync_open_interest,
)
```

Add a safe-refresh helper after `_sync_ancillary`:

```python
def _refresh_lifecycle_safe(
    conn: duckdb.DuckDBPyConnection, client: Any, symbols: list[str]
) -> None:
    """Refresh symbol_lifecycle; non-fatal on error (must never block ingest)."""
    try:
        refresh_symbol_lifecycle(conn, client, symbols)
    except Exception as e:
        logging.warning("symbol_lifecycle refresh failed (continuing): %s", e)
```

Replace `run_backfill` and `run_sync` bodies with the resilient versions:

```python
def run_backfill(
    symbols: list[str] | None,
    timeframes: list[str],
    since_ms: int,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    resolved = _resolve_symbols(symbols)
    failures: list[str] = []
    with _open_session(db_path) as (client, conn):
        _refresh_lifecycle_safe(conn, client, resolved)
        for symbol in resolved:
            try:
                for timeframe in timeframes:
                    logging.info("Backfilling %s %s ...", symbol, timeframe)
                    total = backfill(conn, client, symbol, timeframe, since_ms)
                    logging.info(
                        "Backfill complete: %s %s — %d rows", symbol, timeframe, total
                    )
                _sync_ancillary(conn, client, symbol, funding_since_ms=since_ms)
            except Exception:
                logging.exception("backfill failed for %s — continuing", symbol)
                failures.append(symbol)
    if failures:
        logging.error("backfill finished with failures: %s", ", ".join(failures))
        sys.exit(1)


def run_sync(
    symbols: list[str] | None,
    timeframes: list[str],
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    resolved = _resolve_symbols(symbols)
    failures: list[str] = []
    with _open_session(db_path) as (client, conn):
        _refresh_lifecycle_safe(conn, client, resolved)
        for symbol in resolved:
            try:
                for timeframe in timeframes:
                    logging.info("Syncing %s %s ...", symbol, timeframe)
                    try:
                        total = sync(conn, client, symbol, timeframe)
                        logging.info(
                            "Sync complete: %s %s — %d new rows",
                            symbol,
                            timeframe,
                            total,
                        )
                    except ValueError as e:
                        logging.warning("%s — skipping (run backfill first)", e)
                _sync_ancillary(conn, client, symbol)
            except Exception:
                logging.exception("sync failed for %s — continuing", symbol)
                failures.append(symbol)
    if failures:
        logging.error("sync finished with failures: %s", ", ".join(failures))
        sys.exit(1)
```

(`sys` is already imported. The inner `ValueError` catch for "run backfill first" keeps its existing semantics.)

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_analytics_runner.py -v`
Expected: 6 PASS

- [ ] **Step 5.5: Commit**

```bash
git add analytics/analytics_runner.py tests/test_analytics_runner.py
git commit -m "feat(runner): lifecycle refresh at ingest + per-symbol resilience"
```

---

## Task 6: CLI `--universe` flag + Makefile target

**Files:**

- Modify: `cli/analytics.py`
- Modify: `Makefile` (`.PHONY` line 14 + new target after `buibui-analytics-sync`)
- Test: `tests/test_cli_analytics.py` (new)

- [ ] **Step 6.1: Write the failing tests**

Create `tests/test_cli_analytics.py`:

```python
"""Tests for cli/analytics.py — --universe flag wiring."""

import argparse
from unittest.mock import patch

import pytest

from cli.analytics import add_analytics_subparser


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    add_analytics_subparser(sub)
    return parser


class TestUniverseFlag:
    def test_universe_and_symbols_mutually_exclusive(self) -> None:
        parser = _make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["analytics", "backfill", "--universe", "--symbols", "BTCUSDT"]
            )

    def test_backfill_universe_resolves_symbols_from_toml(self) -> None:
        parser = _make_parser()
        args = parser.parse_args(["analytics", "backfill", "--universe"])
        with (
            patch("cli.analytics.load_universe", return_value=["AAAUSDT", "BBBUSDT"]),
            patch("cli.analytics.analytics_runner.run_backfill") as mock_run,
        ):
            args.func(args)
        assert mock_run.call_args.kwargs["symbols"] == ["AAAUSDT", "BBBUSDT"]

    def test_backfill_default_passes_none_through(self) -> None:
        parser = _make_parser()
        args = parser.parse_args(["analytics", "backfill"])
        with patch("cli.analytics.analytics_runner.run_backfill") as mock_run:
            args.func(args)
        assert mock_run.call_args.kwargs["symbols"] is None

    def test_sync_universe_resolves_symbols_from_toml(self) -> None:
        parser = _make_parser()
        args = parser.parse_args(["analytics", "sync", "--universe"])
        with (
            patch("cli.analytics.load_universe", return_value=["AAAUSDT"]),
            patch("cli.analytics.analytics_runner.run_sync") as mock_run,
        ):
            args.func(args)
        assert mock_run.call_args.kwargs["symbols"] == ["AAAUSDT"]
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_cli_analytics.py -v`
Expected: FAIL — `error: unrecognized arguments: --universe` (SystemExit in the wrong place) / `AttributeError: ... load_universe`

- [ ] **Step 6.3: Implement in `cli/analytics.py`**

Add the import:

```python
from analytics.universe import load_universe
```

Add a resolver and use it in both handlers:

```python
def _resolve_symbol_args(args: argparse.Namespace) -> list[str] | None:
    """--universe → symbols from config/universe.toml; else passthrough --symbols."""
    if getattr(args, "universe", False):
        return load_universe()
    symbols: list[str] | None = args.symbols
    return symbols


def run_analytics_backfill(args: argparse.Namespace) -> None:
    analytics_runner.run_backfill(
        symbols=_resolve_symbol_args(args),
        timeframes=args.timeframes,
        since_ms=parse_since_to_ms(args.since),
    )


def run_analytics_sync(args: argparse.Namespace) -> None:
    analytics_runner.run_sync(
        symbols=_resolve_symbol_args(args),
        timeframes=args.timeframes,
    )
```

In `add_analytics_subparser`, replace each plain `--symbols` `add_argument` on the backfill and sync parsers with a mutually exclusive group (same pattern for both parsers):

```python
    backfill_group = backfill_parser.add_mutually_exclusive_group()
    backfill_group.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to backfill (default: all from coins.json)",
    )
    backfill_group.add_argument(
        "--universe",
        action="store_true",
        help="Use the research universe from config/universe.toml",
    )
```

```python
    sync_group = sync_parser.add_mutually_exclusive_group()
    sync_group.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to sync (default: all from coins.json)",
    )
    sync_group.add_argument(
        "--universe",
        action="store_true",
        help="Use the research universe from config/universe.toml",
    )
```

- [ ] **Step 6.4: Add the Makefile target**

After the `buibui-analytics-sync` target add:

<!-- markdownlint-disable MD010 -->

```make
universe-backfill:  ## Deep universe backfill — config/universe.toml, 1h/4h/1d/1w since 2019 (N3)
	@echo "🌌 Running universe deep-history backfill..."
	@poetry run python buibui.py analytics backfill --universe \
		--timeframes 1h 4h 1d 1w --since $(or $(SINCE),2019-01-01)
```

<!-- markdownlint-enable MD010 -->

Append `universe-backfill` to the `.PHONY` list on line 14.

- [ ] **Step 6.5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_cli_analytics.py -v`
Expected: 4 PASS

- [ ] **Step 6.6: Commit**

```bash
git add cli/analytics.py Makefile tests/test_cli_analytics.py
git commit -m "feat(cli): analytics backfill/sync --universe + make universe-backfill"
```

---

## Task 7: OKX adapter `1w` bar mapping

**Files:**

- Modify: `utils/okx_client.py:55` (`_BAR_MAP`)
- Test: `tests/test_okx_client.py:86-91`

- [ ] **Step 7.1: Extend the existing test**

In `tests/test_okx_client.py`, `test_to_okx_bar_uses_utc_daily` currently ends with the `1d` assertion. Add one line:

```python
    # 1Wutc likewise anchors the weekly open to UTC (Binance weekly = Mon 00:00 UTC)
    assert _to_okx_bar("1w") == "1Wutc"
```

- [ ] **Step 7.2: Run test to verify it fails**

Run: `poetry run pytest tests/test_okx_client.py::test_to_okx_bar_uses_utc_daily -v`
Expected: FAIL — `ValueError: unsupported OKX timeframe: '1w'`

- [ ] **Step 7.3: Implement**

In `utils/okx_client.py` line 55:

```python
_BAR_MAP = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc", "1w": "1Wutc"}
```

- [ ] **Step 7.4: Run test to verify it passes**

Run: `poetry run pytest tests/test_okx_client.py -v`
Expected: all PASS

- [ ] **Step 7.5: Commit**

```bash
git add utils/okx_client.py tests/test_okx_client.py
git commit -m "feat(okx): 1w bar mapping (1Wutc) for the keyless adapter"
```

---

## Task 8: Scope `tools/export_live_db.py` (cost guard)

**Files:**

- Modify: `tools/export_live_db.py`
- Test: `tests/test_export_live_db.py` (modify 3 existing call sites + add 1 test)

- [ ] **Step 8.1: Write the failing test + update existing call sites**

In `tests/test_export_live_db.py`, every existing `export_live_db(src, out)` call (3 sites) becomes:

```python
    export_live_db(src, out, ohlcv_symbols=["BTCUSDT"], now_ms=1_000)
```

(`now_ms=1_000` puts the 400-day floor far below the seeded `open_time=1`, preserving the rows those tests assert on.)

Append the new test:

```python
def test_export_scopes_ohlcv_to_symbols_and_floor(tmp_path: Path) -> None:
    """Universe/deep-history rows must never reach the committed slim DB."""
    src = tmp_path / "analytics.db"
    out = tmp_path / "live_signal.duckdb"
    _make_source(src)
    con = duckdb.connect(str(src))
    # Universe symbol — excluded by symbol scoping.
    con.execute(
        "INSERT INTO ohlcv VALUES ('ZECUSDT', '1h', 1, 10, 11, 9, 10.5, 100, 50)"
    )
    # Live symbol but ancient — excluded by the 400-day floor.
    con.execute(
        "INSERT INTO ohlcv VALUES ('BTCUSDT', '1h', 2, 10, 11, 9, 10.5, 100, 50)"
    )
    # Live symbol, recent — kept.
    now_ms = 500 * 86_400_000
    recent = now_ms - 86_400_000  # 1 day old, floor is 400 days
    con.execute(
        "INSERT INTO ohlcv VALUES ('BTCUSDT', '1h', ?, 10, 11, 9, 10.5, 100, 50)",
        [recent],
    )
    con.close()

    export_live_db(src, out, ohlcv_symbols=["BTCUSDT"], now_ms=now_ms)

    con = duckdb.connect(str(out), read_only=True)
    rows = con.execute("SELECT symbol, open_time FROM ohlcv ORDER BY open_time").fetchall()
    # Calibration tables stay unscoped.
    cr = con.execute("SELECT COUNT(*) FROM confidence_ratings").fetchone()
    con.close()
    assert rows == [("BTCUSDT", recent)]
    assert cr == (1,)
```

- [ ] **Step 8.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_export_live_db.py -v`
Expected: FAIL — `TypeError: export_live_db() got an unexpected keyword argument 'ohlcv_symbols'`

- [ ] **Step 8.3: Implement in `tools/export_live_db.py`**

Add imports `import time` and `from utils.binance_client import load_coins_config`. Add the constant next to `DEFAULT_SRC`/`DEFAULT_OUT`:

```python
# The committed slim DB is checked out hourly by GH Actions — keep it scoped to
# what the daemon actually reads: live (coins.json) symbols, rolling window.
_OHLCV_FLOOR_DAYS: int = 400
```

Change the signature and the copy loop:

```python
def export_live_db(
    src: Path = DEFAULT_SRC,
    out: Path = DEFAULT_OUT,
    *,
    ohlcv_symbols: list[str] | None = None,
    ohlcv_floor_days: int = _OHLCV_FLOOR_DAYS,
    now_ms: int | None = None,
) -> None:
```

Inside, before the table loop:

```python
    if ohlcv_symbols is None:
        ohlcv_symbols = sorted(load_coins_config().keys())
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    floor_ms = now - ohlcv_floor_days * 86_400_000
```

Replace the unconditional copy inside the `for table in LIVE_TABLES:` loop:

```python
        for table in LIVE_TABLES:
            if not _table_exists(src_con, table):
                continue
            if table == "ohlcv":
                placeholders = ", ".join("?" for _ in ohlcv_symbols)
                df = src_con.execute(
                    f"SELECT * FROM ohlcv WHERE symbol IN ({placeholders}) "
                    "AND open_time >= ?",
                    [*ohlcv_symbols, floor_ms],
                ).fetchdf()  # noqa: F841
            else:
                df = src_con.execute(f'SELECT * FROM "{table}"').fetchdf()  # noqa: F841
            out_con.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM df')
```

Update the module docstring's first paragraph to mention the scoping (append one sentence): `The ohlcv copy is scoped to coins.json symbols within a 400-day rolling window so universe/deep-history rows never reach the committed file.`

- [ ] **Step 8.4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_export_live_db.py -v`
Expected: all PASS (3 updated + 1 new)

- [ ] **Step 8.5: Commit**

```bash
git add tools/export_live_db.py tests/test_export_live_db.py
git commit -m "fix(export-live-db): scope ohlcv to coins.json symbols + 400d floor"
```

---

## Task 9: `tools/select_universe.py` (criterion, executable)

**Files:**

- Create: `tools/select_universe.py`
- Test: `tests/test_select_universe.py`

- [ ] **Step 9.1: Write the failing tests**

Create `tests/test_select_universe.py`:

```python
"""Tests for tools/select_universe.py — pure selection/ranking logic."""

from typing import Any

from tools.select_universe import eligible_perps, format_universe_toml, rank_by_median_volume

_DAY_MS = 86_400_000
_AS_OF = 1_000 * _DAY_MS  # arbitrary "today"


def _sym(symbol: str, *, base: str, onboard_days_ago: int, **over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "symbol": symbol,
        "baseAsset": base,
        "quoteAsset": "USDT",
        "contractType": "PERPETUAL",
        "status": "TRADING",
        "onboardDate": _AS_OF - onboard_days_ago * _DAY_MS,
    }
    d.update(over)
    return d


class TestEligiblePerps:
    def test_filters_young_stable_nonperp_nontrading(self) -> None:
        info = {
            "symbols": [
                _sym("BTCUSDT", base="BTC", onboard_days_ago=900),
                _sym("NEWUSDT", base="NEW", onboard_days_ago=100),  # too young
                _sym("USDCUSDT", base="USDC", onboard_days_ago=900),  # stable base
                _sym("ETHUSDT_2606", base="ETH", onboard_days_ago=900,
                     contractType="CURRENT_QUARTER"),  # not a perp
                _sym("OLDUSDT", base="OLD", onboard_days_ago=900,
                     status="SETTLING"),  # not trading
                _sym("ETHBTC", base="ETH", onboard_days_ago=900,
                     quoteAsset="BTC"),  # wrong quote
            ]
        }
        out = eligible_perps(info, as_of_ms=_AS_OF, min_age_days=365)
        assert out == ["BTCUSDT"]


class TestRankByMedianVolume:
    def test_ranks_by_median_and_truncates(self) -> None:
        vols = {
            "AUSDT": [100.0, 100.0, 100.0],
            "BUSDT": [300.0, 300.0, 300.0],
            "CUSDT": [200.0, 1_000_000.0, 200.0],  # spike doesn't move the median
        }
        assert rank_by_median_volume(vols, top_n=2) == ["BUSDT", "CUSDT"]

    def test_empty_series_ranks_last(self) -> None:
        vols: dict[str, list[float]] = {"AUSDT": [100.0], "BUSDT": []}
        assert rank_by_median_volume(vols, top_n=2) == ["AUSDT", "BUSDT"]


class TestFormatUniverseToml:
    def test_emits_universe_block(self) -> None:
        out = format_universe_toml(["BTCUSDT", "ETHUSDT"], selected_at="2026-06-12",
                                   criterion="test crit")
        assert "[universe]" in out
        assert 'selected_at = "2026-06-12"' in out
        assert '"BTCUSDT",' in out
```

- [ ] **Step 9.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_select_universe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.select_universe'`

- [ ] **Step 9.3: Implement `tools/select_universe.py`**

```python
"""Universe selection tool (N3) — makes the committed criterion executable.

Criterion: top-N USDT-M perpetuals by 30-day median daily quote volume;
status TRADING; stablecoin bases excluded; listed >= min-age. Prints a
ready-to-paste [universe] TOML block — it NEVER writes config/universe.toml
itself; a universe refresh stays a deliberate, reviewed commit.

Read-only / keyless (public fapi endpoints). Run via:
    PYTHONPATH=. poetry run python tools/select_universe.py [--top-n 25] [--min-age-days 365]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

_FAPI = "https://fapi.binance.com"
_DAY_MS = 86_400_000
_CANDIDATE_POOL = 60  # pre-rank by 24h volume, re-rank this many by 30d median

STABLE_BASES: set[str] = {
    "USDC", "FDUSD", "TUSD", "DAI", "BUSD", "EURI", "USDP", "AEUR",
    "USD1", "USDE", "BFUSD", "XUSD",
}


def eligible_perps(
    exchange_info: dict[str, Any], *, as_of_ms: int, min_age_days: int
) -> list[str]:
    """Symbols passing the static filters: USDT perp, TRADING, non-stable, aged."""
    out: list[str] = []
    for s in exchange_info.get("symbols", []):
        if s.get("quoteAsset") != "USDT" or s.get("contractType") != "PERPETUAL":
            continue
        if s.get("status") != "TRADING":
            continue
        if str(s.get("baseAsset", "")).upper() in STABLE_BASES:
            continue
        onboard = int(s.get("onboardDate", 0))
        if as_of_ms - onboard < min_age_days * _DAY_MS:
            continue
        out.append(str(s["symbol"]))
    return out


def rank_by_median_volume(
    daily_quote_volumes: dict[str, list[float]], top_n: int
) -> list[str]:
    """Rank symbols by median daily quote volume, descending; truncate to top_n."""
    ranked = sorted(
        daily_quote_volumes,
        key=lambda s: statistics.median(daily_quote_volumes[s])
        if daily_quote_volumes[s]
        else 0.0,
        reverse=True,
    )
    return ranked[:top_n]


def format_universe_toml(
    symbols: list[str], *, selected_at: str, criterion: str
) -> str:
    """Render the [universe] block ready to paste into config/universe.toml."""
    lines = [
        "[universe]",
        f'selected_at = "{selected_at}"',
        f'criterion = "{criterion}"',
        "symbols = [",
        *[f'  "{s}",' for s in symbols],
        "]",
    ]
    return "\n".join(lines) + "\n"


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--min-age-days", type=int, default=365)
    args = parser.parse_args()

    as_of_ms = int(time.time() * 1000)
    info = _get_json(f"{_FAPI}/fapi/v1/exchangeInfo")
    tickers = _get_json(f"{_FAPI}/fapi/v1/ticker/24hr")
    eligible = set(
        eligible_perps(info, as_of_ms=as_of_ms, min_age_days=args.min_age_days)
    )
    by_24h = sorted(
        (t for t in tickers if t["symbol"] in eligible),
        key=lambda t: float(t["quoteVolume"]),
        reverse=True,
    )[:_CANDIDATE_POOL]

    vols: dict[str, list[float]] = {}
    for t in by_24h:
        sym = str(t["symbol"])
        kl = _get_json(
            f"{_FAPI}/fapi/v1/klines?symbol={sym}&interval=1d&limit=31"
        )
        vols[sym] = [float(k[7]) for k in kl[:-1]]  # k[7] = quote vol; drop partial day
        time.sleep(0.15)

    winners = rank_by_median_volume(vols, top_n=args.top_n)
    criterion = (
        f"top-{args.top_n} USDT-M perpetuals by 30d median daily quote volume; "
        f"status TRADING; stablecoin bases excluded; "
        f"listed >= {args.min_age_days}d (Binance fapi snapshot)"
    )
    print(
        format_universe_toml(
            winners,
            selected_at=datetime.now(UTC).date().isoformat(),
            criterion=criterion,
        )
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 9.4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_select_universe.py -v`
Expected: 4 PASS

- [ ] **Step 9.5: Commit**

```bash
git add tools/select_universe.py tests/test_select_universe.py
git commit -m "feat(tools): select_universe — executable universe criterion"
```

---

## Task 10: `tools/data_coverage_report.py` (acceptance artifact)

**Files:**

- Create: `tools/data_coverage_report.py`
- Test: `tests/test_data_coverage_report.py`

- [ ] **Step 10.1: Write the failing tests**

Create `tests/test_data_coverage_report.py`:

```python
"""Tests for tools/data_coverage_report.py — coverage math + report shape."""

import duckdb

from analytics.data_store import init_schema
from tools.data_coverage_report import (
    TF_MS,
    format_report,
    funding_coverage,
    ohlcv_coverage,
)

_H = 3_600_000


def _make_conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def _seed_ohlcv(
    conn: duckdb.DuckDBPyConnection, symbol: str, tf: str, open_times: list[int]
) -> None:
    for t in open_times:
        conn.execute(
            "INSERT INTO ohlcv VALUES (?, ?, ?, 10, 11, 9, 10.5, 100, 50)",
            [symbol, tf, t],
        )


class TestOhlcvCoverage:
    def test_full_coverage_has_zero_gap(self) -> None:
        conn = _make_conn()
        _seed_ohlcv(conn, "BTCUSDT", "1h", [0, _H, 2 * _H, 3 * _H])
        df = ohlcv_coverage(conn)
        row = df.iloc[0]
        assert int(row["n"]) == 4
        assert int(row["expected"]) == 4
        assert float(row["gap_pct"]) == 0.0

    def test_gap_detected(self) -> None:
        conn = _make_conn()
        # 0..4h with 2 of 5 bars missing -> 40% gap
        _seed_ohlcv(conn, "BTCUSDT", "1h", [0, 2 * _H, 4 * _H])
        df = ohlcv_coverage(conn)
        row = df.iloc[0]
        assert int(row["expected"]) == 5
        assert abs(float(row["gap_pct"]) - 0.4) < 1e-9

    def test_weekly_tf_supported(self) -> None:
        conn = _make_conn()
        w = TF_MS["1w"]
        _seed_ohlcv(conn, "BTCUSDT", "1w", [0, w, 2 * w])
        df = ohlcv_coverage(conn)
        assert int(df.iloc[0]["expected"]) == 3
        assert float(df.iloc[0]["gap_pct"]) == 0.0

    def test_unknown_tf_gets_null_expected(self) -> None:
        conn = _make_conn()
        _seed_ohlcv(conn, "BTCUSDT", "3m", [0, 180_000])
        df = ohlcv_coverage(conn)
        assert df.iloc[0]["expected"] is None or str(df.iloc[0]["expected"]) == "nan"


class TestFormatReport:
    def test_report_contains_sections_and_symbols(self) -> None:
        conn = _make_conn()
        _seed_ohlcv(conn, "BTCUSDT", "1h", [0, _H])
        conn.execute("INSERT INTO funding_rates VALUES ('BTCUSDT', 0, 0.0001)")
        report = format_report(
            ohlcv_coverage(conn), funding_coverage(conn), lifecycle=None
        )
        assert "## OHLCV coverage" in report
        assert "## Funding coverage" in report
        assert "BTCUSDT" in report
```

- [ ] **Step 10.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_data_coverage_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.data_coverage_report'`

- [ ] **Step 10.3: Implement `tools/data_coverage_report.py`**

```python
"""Data coverage report (N3 acceptance artifact).

Read-only against analytics.db: per (symbol x timeframe) row counts, date
range, expected-bar count and gap %, plus funding coverage and lifecycle
status. Output is markdown (optionally CSV) — committed to docs/audits/ after
a universe backfill.

Run via: PYTHONPATH=. poetry run python tools/data_coverage_report.py [--db analytics.db] [--csv out.csv]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

TF_MS: dict[str, int] = {
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def ohlcv_coverage(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per (symbol, timeframe): n, first/last day, expected bars, gap_pct."""
    df = conn.execute(
        "SELECT symbol, timeframe, COUNT(*) AS n, "
        "MIN(open_time) AS first_ms, MAX(open_time) AS last_ms, "
        "to_timestamp(MIN(open_time)/1000)::DATE AS first_day, "
        "to_timestamp(MAX(open_time)/1000)::DATE AS last_day "
        "FROM ohlcv GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
    ).df()

    def _expected(row: pd.Series) -> object:
        tf_ms = TF_MS.get(str(row["timeframe"]))
        if tf_ms is None:
            return None
        return int((int(row["last_ms"]) - int(row["first_ms"])) // tf_ms) + 1

    df["expected"] = df.apply(_expected, axis=1)
    df["gap_pct"] = df.apply(
        lambda r: round(1.0 - float(r["n"]) / float(r["expected"]), 4)
        if r["expected"] is not None and float(r["expected"]) > 0
        else None,
        axis=1,
    )
    return df


def funding_coverage(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per symbol: funding row count + date range."""
    return conn.execute(
        "SELECT symbol, COUNT(*) AS n, "
        "to_timestamp(MIN(funding_time)/1000)::DATE AS first_day, "
        "to_timestamp(MAX(funding_time)/1000)::DATE AS last_day "
        "FROM funding_rates GROUP BY symbol ORDER BY symbol"
    ).df()


def lifecycle_table(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Lifecycle rows with onboard date rendered."""
    return conn.execute(
        "SELECT symbol, status, "
        "to_timestamp(onboard_ms/1000)::DATE AS onboarded, "
        "to_timestamp(delisted_noted_ms/1000)::DATE AS delisted_noted "
        "FROM symbol_lifecycle ORDER BY symbol"
    ).df()


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(no rows)\n"
    return df.to_markdown(index=False) + "\n"


def format_report(
    ohlcv: pd.DataFrame,
    funding: pd.DataFrame,
    lifecycle: pd.DataFrame | None,
) -> str:
    """Render the markdown coverage report."""
    parts = [
        "# Data coverage report",
        "",
        f"Symbols: {ohlcv['symbol'].nunique() if not ohlcv.empty else 0} · "
        f"OHLCV rows: {int(ohlcv['n'].sum()) if not ohlcv.empty else 0:,}",
        "",
        "## OHLCV coverage",
        "",
        _md_table(
            ohlcv[
                ["symbol", "timeframe", "n", "first_day", "last_day", "expected", "gap_pct"]
            ]
            if not ohlcv.empty
            else ohlcv
        ),
        "## Funding coverage",
        "",
        _md_table(funding),
    ]
    if lifecycle is not None and not lifecycle.empty:
        parts += ["## Symbol lifecycle", "", _md_table(lifecycle)]
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="analytics.db")
    parser.add_argument("--csv", default=None, help="Also write the OHLCV table as CSV")
    args = parser.parse_args()

    conn = duckdb.connect(args.db, read_only=True)
    try:
        ohlcv = ohlcv_coverage(conn)
        funding = funding_coverage(conn)
        life = lifecycle_table(conn)
    finally:
        conn.close()
    print(format_report(ohlcv, funding, life))
    if args.csv:
        ohlcv.to_csv(Path(args.csv), index=False)


if __name__ == "__main__":
    main()
```

Note: `df.to_markdown` needs `tabulate` — it is already a transitive dev dependency via pandas in this repo; if `make test` reports it missing, replace `_md_table` with a manual pipe-table renderer:

```python
def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(no rows)\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 10.4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_data_coverage_report.py -v`
Expected: 5 PASS

- [ ] **Step 10.5: Commit**

```bash
git add tools/data_coverage_report.py tests/test_data_coverage_report.py
git commit -m "feat(tools): data_coverage_report — symbol x tf gap audit (N3)"
```

---

## Task 11: DoD gates + docs sync

**Files:**

- Modify: `CLAUDE.md`, `README.md`, `.claude/context/analytics.md`

- [ ] **Step 11.1: Run the full Definition-of-Done gate**

```bash
make lint-py && make typecheck && make test && make test-regression
```

Expected: all green; regression goldens UNMOVED (no backtest-pipeline change in this branch). Fix anything that fails before proceeding.

- [ ] **Step 11.2: Docs sync**

- `CLAUDE.md` Project Structure: add `analytics/universe.py` bullet (universe loader), `tools/select_universe.py` + `tools/data_coverage_report.py` bullets, note `symbol_lifecycle` in the `store/` line (`market_data.py` — add "+ `symbol_lifecycle` upsert/get (N3)"), add `config/universe.toml` bullet, and extend the CLI section's `buibui analytics` line with `--universe`.
- `README.md`: extend the analytics backfill section with `--universe`, `make universe-backfill`, and a one-line universe/coverage-report description.
- `.claude/context/analytics.md`: mirror the new store accessors + `refresh_symbol_lifecycle` + runner resilience.
- Run `make lint-md`.

- [ ] **Step 11.3: Commit**

```bash
git add CLAUDE.md README.md .claude/context/analytics.md
git commit -m "docs: N3 universe backfill — CLAUDE.md/README/context sync"
```

---

## Task 12: Execute the backfill + coverage audit doc

**Files:**

- Create: `docs/audits/2026-06-12-universe-backfill-coverage.md`

- [ ] **Step 12.1: Run the universe backfill (background, ~30–45 min)**

```bash
make universe-backfill 2>&1 | tee /tmp/universe-backfill.log
```

Notes: Binance offline path (`DATA_SOURCE` unset → binance), upsert-only — per project guardrails this never touches Telegram and never deletes data. If individual symbols fail, the runner now continues and lists them at exit; re-run is idempotent.

- [ ] **Step 12.2: Deepen majors 15m**

```bash
poetry run python buibui.py analytics backfill \
  --symbols BTCUSDT ETHUSDT SOLUSDT --timeframes 15m --since 2019-01-01 \
  2>&1 | tee /tmp/majors-15m-backfill.log
```

- [ ] **Step 12.3: Generate + sanity-check the coverage report**

```bash
PYTHONPATH=. poetry run python tools/data_coverage_report.py > /tmp/coverage.md
```

Sanity checks before committing: all 25 universe symbols present at 1h/4h/1d/1w; majors' 15m/1h first_day ≈ listing dates (BTC 2019-09, ETH 2019-11, SOL 2020-09); funding first_day ≈ listing; younger symbols (HYPE, VVV, PAXG) start at their onboard dates — that is correct, not a gap; `gap_pct` near 0 for majors (Binance has genuine small gaps around outages — note any cell > 1% in the doc rather than chasing it).

- [ ] **Step 12.4: Write the audit doc**

Create `docs/audits/2026-06-12-universe-backfill-coverage.md` with: header (goal, criterion, run date, configs), the coverage report output, the sanity-check notes, and the survivorship caveat (forward-looking from the 2026-06-12 snapshot). Run `make lint-md`.

- [ ] **Step 12.5: Verify the DB and goldens are still clean**

```bash
make test-regression
git status --short
```

Expected: regression green, goldens unmoved; only the new audit doc untracked.

- [ ] **Step 12.6: Commit**

```bash
git add docs/audits/2026-06-12-universe-backfill-coverage.md
git commit -m "feat(data): N3 universe deep-history backfill — coverage audit"
```

---

## Task 13: PR

- [ ] **Step 13.1:** `/pr-summary` → `gh pr create` (gh on `s10023`) → `/post-branch` docs sweep → report PR URL.
