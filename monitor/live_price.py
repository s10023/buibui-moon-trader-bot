"""Live price monitor: WebSocket + kline refresh + Rich terminal."""

from typing import Any

from rich.table import Table

from utils.live_store import LiveDataStore

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
