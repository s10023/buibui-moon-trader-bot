"""Live price monitor: WebSocket + kline refresh + Rich terminal."""

from typing import Any

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


def _kline_refresh_loop(
    client: Any,
    symbols: list[str],
    store: LiveDataStore,
    interval: int = KLINE_REFRESH_INTERVAL,
) -> None:
    pass  # implement in Task 8


def run(
    client: Any,
    coins: list[str],
    sort_col: str = "",
    sort_order: bool = True,
) -> None:
    pass  # implement in Task 8
