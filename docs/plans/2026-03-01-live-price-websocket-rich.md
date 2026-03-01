# Live Price Monitor: WebSocket + Rich Terminal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the REST-polling live mode with Binance WebSocket push updates and a Rich
flicker-free terminal table.

**Architecture:** `LiveDataStore` holds thread-safe shared state. `live_price.run()` orchestrates
three threads: WebSocket callback (writes ticker data), kline refresh daemon (writes kline opens
every 60 s), and the main Rich render loop (reads via `snapshot()`). Snapshot mode is untouched.

**Tech Stack:** `python-binance` `ThreadedWebsocketManager`, `rich` (new dep), `threading.Lock`,
`pytest` + `unittest.mock`

---

## Task 1: Create feature branch

**Step 1:** Create and switch to branch

```bash
cd /home/kng/repo/buibui-moon-trader-bot
git checkout -b feat/live-price-websocket-rich
```

Expected: `Switched to a new branch 'feat/live-price-websocket-rich'`

---

## Task 2: Add `rich` dependency

**Files:**

- Modify: `pyproject.toml`

**Step 1:** Add rich

```bash
cd /home/kng/repo/buibui-moon-trader-bot
poetry add "rich>=14.0.0,<15.0.0"
```

Expected: poetry resolves and updates `poetry.lock`.

**Step 2:** Verify import works

```bash
poetry run python -c "from rich.live import Live; from rich.text import Text; print('ok')"
```

Expected: `ok`

**Step 3:** Commit

```bash
git add pyproject.toml poetry.lock
git commit -m "build(deps): add rich for live terminal rendering"
```

---

## Task 3: `LiveDataStore` — TDD

**Files:**

- Create: `utils/live_store.py`
- Create: `tests/test_live_store.py`

**Step 1: Write failing tests — create `tests/test_live_store.py`**

```python
"""Tests for utils/live_store.py."""

import threading
from datetime import datetime

from utils.live_store import KlineData, LiveDataStore, StoreSnapshot, TickerData


class TestUpdateTicker:
    def test_computes_change_24h_correctly(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=110.0, open_24h=100.0)
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].ticker is not None
        assert result.data["BTCUSDT"].ticker.last_price == 110.0
        assert abs(result.data["BTCUSDT"].ticker.change_24h - 10.0) < 0.001

    def test_zero_open_does_not_crash(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=100.0, open_24h=0.0)
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].ticker is not None
        assert result.data["BTCUSDT"].ticker.change_24h == 0.0

    def test_sets_last_update_timestamp(self) -> None:
        store = LiveDataStore()
        assert store.snapshot(["BTCUSDT"]).last_update is None
        store.update_ticker("BTCUSDT", last=100.0, open_24h=100.0)
        assert isinstance(store.snapshot(["BTCUSDT"]).last_update, datetime)


class TestUpdateKlines:
    def test_stores_and_retrieves(self) -> None:
        store = LiveDataStore()
        store.update_klines("BTCUSDT", open_15m=99.0, open_1h=95.0, asia_open=90.0)
        result = store.snapshot(["BTCUSDT"])
        klines = result.data["BTCUSDT"].klines
        assert klines is not None
        assert klines.open_15m == 99.0
        assert klines.open_1h == 95.0
        assert klines.asia_open == 90.0

    def test_none_values_allowed(self) -> None:
        store = LiveDataStore()
        store.update_klines("BTCUSDT", open_15m=None, open_1h=None, asia_open=None)
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].klines is not None


class TestSnapshot:
    def test_missing_symbol_returns_none_fields(self) -> None:
        store = LiveDataStore()
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].ticker is None
        assert result.data["BTCUSDT"].klines is None

    def test_returns_copy_not_live_reference(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=100.0, open_24h=100.0)
        snap1 = store.snapshot(["BTCUSDT"])
        store.update_ticker("BTCUSDT", last=200.0, open_24h=100.0)
        snap2 = store.snapshot(["BTCUSDT"])
        assert snap1.data["BTCUSDT"].ticker is not None
        assert snap1.data["BTCUSDT"].ticker.last_price == 100.0
        assert snap2.data["BTCUSDT"].ticker is not None
        assert snap2.data["BTCUSDT"].ticker.last_price == 200.0

    def test_includes_ws_status_and_last_update(self) -> None:
        store = LiveDataStore()
        result = store.snapshot(["BTCUSDT"])
        assert result.ws_connected is False
        assert result.last_update is None


class TestSetWsStatus:
    def test_transitions(self) -> None:
        store = LiveDataStore()
        assert store.snapshot(["BTCUSDT"]).ws_connected is False
        store.set_ws_status(connected=True)
        assert store.snapshot(["BTCUSDT"]).ws_connected is True
        store.set_ws_status(connected=False)
        assert store.snapshot(["BTCUSDT"]).ws_connected is False


class TestThreadSafety:
    def test_concurrent_writes_do_not_corrupt(self) -> None:
        store = LiveDataStore()
        errors: list[Exception] = []

        def writer(symbol: str, price: float) -> None:
            try:
                for _ in range(200):
                    store.update_ticker(symbol, last=price, open_24h=price)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(f"SYM{i}", float(i * 10)))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
```

**Step 2:** Run — verify tests fail

```bash
cd /home/kng/repo/buibui-moon-trader-bot
poetry run pytest tests/test_live_store.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'LiveDataStore' from 'utils.live_store'` (or module not found)

**Step 3: Implement `utils/live_store.py`**

```python
"""Thread-safe shared state for the live price monitor."""

import threading
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TickerData:
    last_price: float
    change_24h: float


@dataclass
class KlineData:
    open_15m: float | None
    open_1h: float | None
    asia_open: float | None


@dataclass
class StoreSnapshot:
    ticker: TickerData | None
    klines: KlineData | None


@dataclass
class SnapshotResult:
    data: dict[str, StoreSnapshot]
    ws_connected: bool
    last_update: datetime | None


class LiveDataStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tickers: dict[str, TickerData] = {}
        self._klines: dict[str, KlineData] = {}
        self._ws_connected: bool = False
        self._last_update: datetime | None = None

    def update_ticker(self, symbol: str, last: float, open_24h: float) -> None:
        change_24h = ((last - open_24h) / open_24h * 100) if open_24h else 0.0
        with self._lock:
            self._tickers[symbol] = TickerData(last_price=last, change_24h=change_24h)
            self._last_update = datetime.now()

    def update_klines(
        self,
        symbol: str,
        open_15m: float | None,
        open_1h: float | None,
        asia_open: float | None,
    ) -> None:
        with self._lock:
            self._klines[symbol] = KlineData(
                open_15m=open_15m,
                open_1h=open_1h,
                asia_open=asia_open,
            )

    def set_ws_status(self, connected: bool) -> None:
        with self._lock:
            self._ws_connected = connected

    def snapshot(self, symbols: list[str]) -> SnapshotResult:
        with self._lock:
            return SnapshotResult(
                data={
                    s: StoreSnapshot(
                        ticker=self._tickers.get(s),
                        klines=self._klines.get(s),
                    )
                    for s in symbols
                },
                ws_connected=self._ws_connected,
                last_update=self._last_update,
            )
```

**Step 4:** Run — verify tests pass

```bash
poetry run pytest tests/test_live_store.py -v
```

Expected: all green

**Step 5:** Lint + typecheck

```bash
make lint-py && make typecheck
```

Fix any issues before continuing.

**Step 6:** Commit

```bash
git add utils/live_store.py tests/test_live_store.py
git commit -m "feat: add LiveDataStore for thread-safe WebSocket state"
```

---

## Task 4: `format_pct_rich` + `sort_table_raw` — TDD

**Files:**

- Modify: `monitor/price_lib.py`
- Modify: `tests/test_price_monitor.py` (add new test classes at the bottom)

**Step 1: Write failing tests — append to `tests/test_price_monitor.py`**

Add these classes at the bottom of the file:

```python
from rich.text import Text
from monitor.price_lib import format_pct_rich, sort_table_raw


class TestFormatPctRich:
    def test_positive_returns_green_text(self) -> None:
        result = format_pct_rich(2.5)
        assert isinstance(result, Text)
        assert "+2.50%" in result.plain

    def test_negative_returns_red_text(self) -> None:
        result = format_pct_rich(-1.5)
        assert isinstance(result, Text)
        assert "-1.50%" in result.plain

    def test_zero_returns_yellow_text(self) -> None:
        result = format_pct_rich(0.0)
        assert isinstance(result, Text)
        assert "+0.00%" in result.plain

    def test_returns_text_instance_not_string(self) -> None:
        assert not isinstance(format_pct_rich(1.0), str)


class TestSortTableRaw:
    ROWS: list[list[Any]] = [
        ["C", "150", 2.0, None, None, None],
        ["A", "100", 1.0, None, None, None],
        ["B", "200", 3.0, None, None, None],
    ]

    def test_descending(self) -> None:
        result = sort_table_raw(self.ROWS, col_idx=2, reverse=True)
        assert [r[0] for r in result] == ["B", "C", "A"]

    def test_ascending(self) -> None:
        result = sort_table_raw(self.ROWS, col_idx=2, reverse=False)
        assert [r[0] for r in result] == ["A", "C", "B"]

    def test_none_sorts_last_in_descending(self) -> None:
        rows: list[list[Any]] = [
            ["A", "100", None, None, None, None],
            ["B", "200", 1.0, None, None, None],
        ]
        result = sort_table_raw(rows, col_idx=2, reverse=True)
        assert result[0][0] == "B"
        assert result[1][0] == "A"

    def test_none_sorts_last_in_ascending(self) -> None:
        rows: list[list[Any]] = [
            ["A", "100", None, None, None, None],
            ["B", "200", 1.0, None, None, None],
        ]
        result = sort_table_raw(rows, col_idx=2, reverse=False)
        assert result[0][0] == "B"
        assert result[1][0] == "A"
```

**Step 2:** Run — verify tests fail

```bash
poetry run pytest tests/test_price_monitor.py::TestFormatPctRich tests/test_price_monitor.py::TestSortTableRaw -v
```

Expected: `ImportError: cannot import name 'format_pct_rich'`

**Step 3: Add functions to `monitor/price_lib.py`**

Add these imports at the top of `price_lib.py` (ruff will sort them):

```python
from rich.text import Text
```

Add these two functions at the end of `monitor/price_lib.py`:

```python
def format_pct_rich(pct: float) -> Text:
    """Format % change as a Rich Text object with color."""
    formatted = f"{pct:+.2f}%"
    if pct > 0:
        return Text(formatted, style="green")
    elif pct < 0:
        return Text(formatted, style="red")
    else:
        return Text(formatted, style="yellow")


def sort_table_raw(
    rows: list[list[Any]], col_idx: int, reverse: bool
) -> list[list[Any]]:
    """Sort rows by a column of raw floats. None values always sort last."""

    def key(row: list[Any]) -> float:
        val = row[col_idx]
        if val is None:
            return float("-inf") if reverse else float("inf")
        return float(val)

    return sorted(rows, key=key, reverse=reverse)
```

**Step 4:** Run — verify tests pass

```bash
poetry run pytest tests/test_price_monitor.py::TestFormatPctRich tests/test_price_monitor.py::TestSortTableRaw -v
```

Expected: all green

**Step 5:** Lint + typecheck

```bash
make lint-py && make typecheck
```

ruff may reorder the `from rich.text import Text` import — that is expected.

**Step 6:** Commit

```bash
git add monitor/price_lib.py tests/test_price_monitor.py
git commit -m "feat: add format_pct_rich and sort_table_raw to price_lib"
```

---

## Task 5: `_handle_ws_msg` — TDD

**Files:**

- Create: `monitor/live_price.py` (skeleton + `_handle_ws_msg`)
- Create: `tests/test_live_price.py`

**Step 1: Write failing tests — create `tests/test_live_price.py`**

```python
"""Tests for monitor/live_price.py."""

from typing import Any
from unittest.mock import MagicMock, patch

from monitor.live_price import _build_table, _handle_ws_msg, _refresh_klines
from utils.live_store import LiveDataStore


def _miniticker_msg(symbol: str, last: str, open_24h: str) -> dict[str, Any]:
    return {
        "stream": f"{symbol.lower()}@miniTicker",
        "data": {
            "e": "24hrMiniTicker",
            "s": symbol,
            "c": last,
            "o": open_24h,
        },
    }


class TestHandleWsMsg:
    def test_valid_miniticker_updates_store(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg(_miniticker_msg("BTCUSDT", "67000.00", "65000.00"), store)
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].ticker is not None
        assert result.data["BTCUSDT"].ticker.last_price == 67000.0
        assert result.ws_connected is True

    def test_valid_msg_sets_ws_connected(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg(_miniticker_msg("BTCUSDT", "100.00", "90.00"), store)
        assert store.snapshot(["BTCUSDT"]).ws_connected is True

    def test_error_msg_sets_disconnected(self) -> None:
        store = LiveDataStore()
        store.set_ws_status(connected=True)
        _handle_ws_msg({"e": "error", "m": "stream error"}, store)
        assert store.snapshot(["BTCUSDT"]).ws_connected is False

    def test_error_msg_does_not_write_ticker(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg({"e": "error", "m": "stream error"}, store)
        assert store.snapshot(["BTCUSDT"]).data["BTCUSDT"].ticker is None

    def test_unknown_event_type_is_ignored(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg({"data": {"e": "trade", "s": "BTCUSDT"}}, store)
        assert store.snapshot(["BTCUSDT"]).data["BTCUSDT"].ticker is None
```

**Step 2:** Run — verify tests fail

```bash
poetry run pytest tests/test_live_price.py::TestHandleWsMsg -v 2>&1 | head -10
```

Expected: `ImportError: cannot import name '_handle_ws_msg'`

**Step 3: Create `monitor/live_price.py` with `_handle_ws_msg`**

```python
"""Live price monitor: WebSocket + kline refresh + Rich terminal."""

import threading
import time
from datetime import datetime
from typing import Any

from binance import ThreadedWebsocketManager
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from monitor.price_lib import (
    batch_get_asia_open,
    batch_get_klines,
    format_pct_rich,
    sort_table_raw,
)
from utils.live_store import KlineData, LiveDataStore, TickerData

HEADERS = ["Symbol", "Last Price", "15m %", "1h %", "Since Asia 8AM", "24h %"]
KLINE_REFRESH_INTERVAL = 60
_SORT_COL_MAP = {
    "change_15m": 2,
    "change_1h": 3,
    "change_asia": 4,
    "change_24h": 5,
}


def _handle_ws_msg(msg: dict[str, Any], store: LiveDataStore) -> None:
    """Handle one WebSocket message from the multiplex miniTicker stream."""
    if msg.get("e") == "error":
        store.set_ws_status(connected=False)
        return
    data: dict[str, Any] = msg.get("data", msg)
    if data.get("e") == "24hrMiniTicker":
        store.update_ticker(data["s"], float(data["c"]), float(data["o"]))
        store.set_ws_status(connected=True)


def _refresh_klines(client: Any, symbols: list[str], store: LiveDataStore) -> None:
    pass  # implement in Task 6


def _build_table(
    symbols: list[str],
    store: LiveDataStore,
    sort_col: str = "",
    sort_order: bool = True,
) -> Table:
    return Table()  # implement in Task 7


def run(
    client: Any,
    coins: list[str],
    sort_col: str = "",
    sort_order: bool = True,
) -> None:
    pass  # implement in Task 8
```

**Step 4:** Run — verify tests pass

```bash
poetry run pytest tests/test_live_price.py::TestHandleWsMsg -v
```

Expected: all green

**Step 5:** Lint + typecheck

```bash
make lint-py && make typecheck
```

**Step 6:** Commit

```bash
git add monitor/live_price.py tests/test_live_price.py
git commit -m "feat: add _handle_ws_msg to live_price"
```

---

## Task 6: `_refresh_klines` — TDD

**Files:**

- Modify: `tests/test_live_price.py` (add `TestRefreshKlines`)
- Modify: `monitor/live_price.py` (implement `_refresh_klines`)

**Step 1: Add failing tests to `tests/test_live_price.py`**

```python
class TestRefreshKlines:
    def test_writes_klines_to_store(self) -> None:
        store = LiveDataStore()
        mock_client = MagicMock()
        with (
            patch("monitor.live_price.batch_get_klines") as mock_klines,
            patch("monitor.live_price.batch_get_asia_open") as mock_asia,
        ):
            mock_klines.return_value = {
                ("BTCUSDT", "15m"): [None, "66000.00"],
                ("BTCUSDT", "1h"): [None, "64000.00"],
            }
            mock_asia.return_value = {"BTCUSDT": 63000.0}
            _refresh_klines(mock_client, ["BTCUSDT"], store)

        result = store.snapshot(["BTCUSDT"])
        klines = result.data["BTCUSDT"].klines
        assert klines is not None
        assert klines.open_15m == 66000.0
        assert klines.open_1h == 64000.0
        assert klines.asia_open == 63000.0

    def test_missing_kline_result_stores_none(self) -> None:
        store = LiveDataStore()
        mock_client = MagicMock()
        with (
            patch("monitor.live_price.batch_get_klines") as mock_klines,
            patch("monitor.live_price.batch_get_asia_open") as mock_asia,
        ):
            mock_klines.return_value = {}
            mock_asia.return_value = {}
            _refresh_klines(mock_client, ["BTCUSDT"], store)

        result = store.snapshot(["BTCUSDT"])
        klines = result.data["BTCUSDT"].klines
        assert klines is not None
        assert klines.open_15m is None
        assert klines.open_1h is None
        assert klines.asia_open is None
```

**Step 2:** Run — verify tests fail

```bash
poetry run pytest tests/test_live_price.py::TestRefreshKlines -v
```

Expected: FAIL (stub returns `pass`, store has no klines)

**Step 3: Implement `_refresh_klines` in `monitor/live_price.py`**

Replace the `pass` stub:

```python
def _refresh_klines(client: Any, symbols: list[str], store: LiveDataStore) -> None:
    """Fetch kline open prices for all symbols and write to store."""
    kline_map = batch_get_klines(client, symbols, [("15m", 15), ("1h", 60)])
    asia_map = batch_get_asia_open(client, symbols)
    for sym in symbols:
        k15 = kline_map.get((sym, "15m"))
        k60 = kline_map.get((sym, "1h"))
        store.update_klines(
            sym,
            float(k15[1]) if k15 else None,
            float(k60[1]) if k60 else None,
            asia_map.get(sym),
        )
```

**Step 4:** Run — verify tests pass

```bash
poetry run pytest tests/test_live_price.py::TestRefreshKlines -v
```

**Step 5:** Commit

```bash
git add monitor/live_price.py tests/test_live_price.py
git commit -m "feat: implement _refresh_klines in live_price"
```

---

## Task 7: `_build_table` — TDD

**Files:**

- Modify: `tests/test_live_price.py` (add `TestBuildTable`)
- Modify: `monitor/live_price.py` (implement `_build_table`)

**Step 1: Add failing tests to `tests/test_live_price.py`**

```python
class TestBuildTable:
    def test_no_ticker_shows_dots_in_price_column(self) -> None:
        store = LiveDataStore()
        table = _build_table(["BTCUSDT"], store)
        assert table.row_count == 1

    def test_ticker_no_klines_shows_row(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=67000.0, open_24h=65000.0)
        table = _build_table(["BTCUSDT"], store)
        assert table.row_count == 1

    def test_title_shows_stale_when_disconnected(self) -> None:
        store = LiveDataStore()
        table = _build_table(["BTCUSDT"], store)
        assert table.title is not None
        assert "reconnecting" in str(table.title).lower()

    def test_title_shows_last_update_when_connected(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=67000.0, open_24h=65000.0)
        table = _build_table(["BTCUSDT"], store)
        assert table.title is not None
        assert "last update" in str(table.title).lower()

    def test_sort_col_applied(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=100.0, open_24h=90.0)
        store.update_ticker("ETHUSDT", last=100.0, open_24h=95.0)
        store.update_klines("BTCUSDT", open_15m=98.0, open_1h=95.0, asia_open=90.0)
        store.update_klines("ETHUSDT", open_15m=99.0, open_1h=97.0, asia_open=92.0)
        table = _build_table(
            ["BTCUSDT", "ETHUSDT"], store, sort_col="change_24h", sort_order=True
        )
        assert table.row_count == 2

    def test_multiple_symbols(self) -> None:
        store = LiveDataStore()
        table = _build_table(["BTCUSDT", "ETHUSDT", "SOLUSDT"], store)
        assert table.row_count == 3
```

**Step 2:** Run — verify tests fail

```bash
poetry run pytest tests/test_live_price.py::TestBuildTable -v
```

Expected: most tests fail (stub returns empty `Table()`)

**Step 3: Implement `_build_table` in `monitor/live_price.py`**

Replace the stub:

```python
def _build_table(
    symbols: list[str],
    store: LiveDataStore,
    sort_col: str = "",
    sort_order: bool = True,
) -> Table:
    """Build a Rich Table from the current store snapshot."""
    result = store.snapshot(symbols)
    ts = result.last_update.strftime("%H:%M:%S") if result.last_update else "--:--:--"
    if result.ws_connected:
        title = f"\U0001f4c8 Live Crypto Price Monitor \u2014 Buibui Moon Bot  |  Last update: {ts}"
    else:
        title = f"\u26a0\ufe0f  WebSocket reconnecting...  |  Last update: {ts}"

    table = Table(title=title, expand=True)
    for header in HEADERS:
        table.add_column(header, justify="right")

    # Build raw rows with float pct values for sorting
    # Format: [symbol, price_str_or_none, pct_15m, pct_1h, pct_asia, pct_24h]
    raw_rows: list[list[Any]] = []
    for sym in symbols:
        snap = result.data[sym]
        ticker: TickerData | None = snap.ticker
        klines: KlineData | None = snap.klines

        if ticker is None:
            raw_rows.append([sym, None, None, None, None, None])
            continue

        last = ticker.last_price
        open_15m = klines.open_15m if klines else None
        open_1h = klines.open_1h if klines else None
        asia_open = klines.asia_open if klines else None

        pct_15m = ((last - open_15m) / open_15m * 100) if open_15m else None
        pct_1h = ((last - open_1h) / open_1h * 100) if open_1h else None
        pct_asia = ((last - asia_open) / asia_open * 100) if asia_open else None

        raw_rows.append(
            [sym, str(round(last, 4)), pct_15m, pct_1h, pct_asia, ticker.change_24h]
        )

    if sort_col in _SORT_COL_MAP:
        raw_rows = sort_table_raw(raw_rows, _SORT_COL_MAP[sort_col], sort_order)

    def _fmt(val: float | None) -> Text:
        return format_pct_rich(val) if val is not None else Text("N/A")

    for row in raw_rows:
        if row[1] is None:
            table.add_row(row[0], "...", "", "", "", "")
        else:
            table.add_row(
                row[0],
                row[1],
                _fmt(row[2]),
                _fmt(row[3]),
                _fmt(row[4]),
                _fmt(row[5]),
            )

    return table
```

**Step 4:** Run — verify tests pass

```bash
poetry run pytest tests/test_live_price.py::TestBuildTable -v
```

**Step 5:** Lint + typecheck

```bash
make lint-py && make typecheck
```

**Step 6:** Commit

```bash
git add monitor/live_price.py tests/test_live_price.py
git commit -m "feat: implement _build_table with Rich Live table"
```

---

## Task 8: Implement `run()` + wire `price_monitor.py`

No new tests — `run()` is integration logic (threads + network). Correctness verified by
running the bot manually.

**Files:**

- Modify: `monitor/live_price.py` (implement `run`)
- Modify: `monitor/price_monitor.py` (swap live loop for `live_price.run()`)

**Step 1: Implement `_kline_refresh_loop` + `run()` in `monitor/live_price.py`**

Replace the `pass` stubs:

```python
def _kline_refresh_loop(
    client: Any,
    symbols: list[str],
    store: LiveDataStore,
    interval: int = KLINE_REFRESH_INTERVAL,
) -> None:
    """Background daemon: refresh kline opens every `interval` seconds."""
    while True:
        time.sleep(interval)
        _refresh_klines(client, symbols, store)


def run(
    client: Any,
    coins: list[str],
    sort_col: str = "",
    sort_order: bool = True,
) -> None:
    """Run the live price monitor: WebSocket + Rich terminal, blocking until Ctrl-C."""
    store = LiveDataStore()

    # 1. Pre-fill klines synchronously so first render has open prices
    _refresh_klines(client, coins, store)

    # 2. Start WebSocket (miniTicker is a public stream — no API keys needed)
    twm = ThreadedWebsocketManager()
    twm.start()
    streams = [f"{sym.lower()}@miniTicker" for sym in coins]

    def ws_callback(msg: dict[str, Any]) -> None:
        _handle_ws_msg(msg, store)

    twm.start_multiplex_socket(callback=ws_callback, streams=streams)

    # 3. Kline refresh daemon
    kline_thread = threading.Thread(
        target=_kline_refresh_loop,
        args=(client, coins, store),
        daemon=True,
    )
    kline_thread.start()

    # 4. Rich Live render loop
    console = Console()
    try:
        with Live(console=console, refresh_per_second=2) as live:
            while True:
                live.update(_build_table(coins, store, sort_col, sort_order))
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        twm.stop()
        console.print("\nExiting gracefully. Goodbye!")
```

**Step 2: Update `monitor/price_monitor.py`**

Add import at top with the other monitor imports:

```python
from monitor import live_price
```

Replace the `else` branch (lines 92-111 approximately — the entire `try: while True: ... except KeyboardInterrupt` block):

```python
    else:
        live_price.run(client, coins, sort_col=sort_col, sort_order=sort_order)
```

**Step 3:** Lint + typecheck

```bash
make lint-py && make typecheck
```

**Step 4:** Commit

```bash
git add monitor/live_price.py monitor/price_monitor.py
git commit -m "feat: implement live_price.run() and wire into price_monitor"
```

---

## Task 9: Full suite, lint, typecheck, push, PR

**Step 1:** Run full test suite

```bash
make test
```

Expected: all tests pass. Fix any failures before continuing.

**Step 2:** Run lint and typecheck

```bash
make lint-py && make typecheck
```

Fix any issues.

**Step 3:** Push branch

```bash
git push -u origin feat/live-price-websocket-rich
```

**Step 4:** PR summary (paste into GitHub manually — gh pr create not available)

```text
Title: feat(monitor): WebSocket + Rich live price monitor [VS-???]

## Summary
- Replaces 5-second REST polling in `--live` mode with Binance WebSocket `@miniTicker`
  push updates via `ThreadedWebsocketManager` (automatic reconnect, no new dependencies)
- Adds flicker-free `Rich.Live` terminal table with green/red/yellow % formatting
- Adds `LiveDataStore` (`utils/live_store.py`) for thread-safe shared state between
  the WebSocket callback thread and the render thread
- Kline opens (15m, 1h, Asia 8AM) pre-fetched on startup via existing REST batch
  functions, refreshed every 60 s in a background daemon thread
- Shows `⚠️ WebSocket reconnecting...` in table title on disconnect; last known prices
  remain visible
- Shows `...` placeholder in price cell on startup before first WS message arrives
- Snapshot mode (`--live` off) is completely untouched

## Test plan
- [ ] `make test` passes (new: `test_live_store.py`, `test_live_price.py`, extended
  `test_price_monitor.py`)
- [ ] `make lint-py` passes
- [ ] `make typecheck` passes
- [ ] Run `python buibui.py monitor price --live` manually, verify Rich table renders
  without flicker and prices update in real time
- [ ] Run `python buibui.py monitor price --live --sort change_24h:desc` and verify
  sort applies correctly
- [ ] Disconnect network briefly, verify stale indicator appears; reconnect, verify
  prices resume
- [ ] Ctrl-C exits cleanly with "Exiting gracefully. Goodbye!"
```
