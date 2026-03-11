"""Thread-safe shared state for the live price monitor."""

import threading
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TickerData:
    last_price: float
    change_24h: float


@dataclass(frozen=True)
class KlineData:
    open_15m: float | None
    open_1h: float | None
    asia_open: float | None


@dataclass(frozen=True)
class StoreSnapshot:
    ticker: TickerData | None
    klines: KlineData | None


@dataclass(frozen=True)
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
            self._ws_connected = True

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
