"""Tests for signal_runner._update_ohlcv_cache and _sync_watched_series."""

from unittest.mock import MagicMock, patch

import duckdb
import pandas as pd
import pytest

from analytics.data_store import init_schema, upsert_ohlcv
from analytics.signal_runner import _sync_watched_series, _update_ohlcv_cache

_MS = 15 * 60 * 1000  # 15 minutes in ms
_T0 = 1_700_000_000_000  # arbitrary base timestamp (ms)


def _make_row(open_time: int, close: float, volume: float) -> dict:
    return {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "open_time": open_time,
        "open": close,
        "high": close + 10,
        "low": close - 10,
        "close": close,
        "volume": volume,
        "taker_buy_volume": volume / 2,
    }


def _seed_db(conn: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    upsert_ohlcv(conn, df, venue="binance")


def test_warm_cache_replaces_stale_last_row() -> None:
    """The last cached row (a partial candle) must be replaced with its final values.

    Bug: the old code queried from cached_max_ts+1, so the partial last row was
    never updated in the cache even after sync() finalised it in the DB.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    # Seed DB with two closed candles and one partial (current open).
    rows = [
        _make_row(_T0, close=100.0, volume=50.0),  # closed
        _make_row(_T0 + _MS, close=200.0, volume=10.0),  # partial (low volume)
    ]
    _seed_db(conn, rows)

    cache: dict = {}
    now_ms = _T0 + 2 * _MS

    # Cold start — cache built from DB.
    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, now_ms)
    assert len(cache[("BTCUSDT", "15m")]) == 2
    assert float(cache[("BTCUSDT", "15m")]["volume"].iloc[-1]) == 10.0  # partial

    # Simulate sync() finalising the partial candle with a large volume spike,
    # and adding the next partial candle.
    rows[1] = _make_row(_T0 + _MS, close=210.0, volume=500.0)  # now finalised
    rows.append(_make_row(_T0 + 2 * _MS, close=210.0, volume=5.0))  # new partial
    _seed_db(conn, rows)

    # Warm update — should replace the stale last row and append the new partial.
    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, _T0 + 3 * _MS)

    df = cache[("BTCUSDT", "15m")]
    assert len(df) == 3  # T0, T0+15m (finalised), T0+30m (new partial)
    # The finalised candle must have the updated volume, not the stale partial.
    assert float(df["volume"].iloc[1]) == 500.0, "stale partial volume not replaced"
    assert float(df["close"].iloc[1]) == 210.0, "stale partial close not replaced"


def test_warm_cache_invalidates_on_gap() -> None:
    """If >2 rows arrive the cache is fully rebuilt (missed cycle / gap)."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    rows = [
        _make_row(_T0 + i * _MS, close=float(i * 100), volume=50.0) for i in range(3)
    ]
    _seed_db(conn, rows)

    cache: dict = {}
    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, _T0 + 3 * _MS)
    assert len(cache[("BTCUSDT", "15m")]) == 3

    # Add 3 new rows — simulates daemon being down for 2 cycles.
    extra = [
        _make_row(_T0 + i * _MS, close=float(i * 100), volume=50.0) for i in range(3, 6)
    ]
    _seed_db(conn, extra)

    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, _T0 + 6 * _MS)
    assert len(cache[("BTCUSDT", "15m")]) == 6  # full rebuild


def test_create_data_client_returns_okx_when_env_set() -> None:
    import os
    from unittest.mock import patch

    from utils.binance_client import create_data_client
    from utils.okx_client import OKXClient

    with patch.dict(os.environ, {"DATA_SOURCE": "okx"}):
        assert isinstance(create_data_client(), OKXClient)


def test_run_signal_watch_uses_create_data_client() -> None:
    # The daemon must select its market-data client via create_data_client (so
    # DATA_SOURCE=okx is honoured), not hardcode the Binance create_client.
    import inspect

    from analytics import signal_runner

    src = inspect.getsource(signal_runner.run_signal_watch)
    assert "create_data_client()" in src


def test_run_signal_watch_accepts_max_cycles() -> None:
    import inspect

    from analytics import signal_runner

    sig = inspect.signature(signal_runner.run_signal_watch)
    assert "max_cycles" in sig.parameters
    assert sig.parameters["max_cycles"].default is None


class TestSyncWatchedSeriesNarrowsIOException:
    """A non-lock IOException must NOT be swallowed by the per-symbol sync loop.

    SoT ST70(a). The handler logged "will retry" and let the cycle continue, but
    BOTH production callers run `--once` (`deploy/run-signal.sh` and
    `.github/workflows/signal-watch.yaml`), so nothing retries: the cycle went on
    to scan, alert, write `signal_alert_outcomes` and advance the CooldownStore
    watermark against bars it had just failed to refresh. Under `--catch-up` that
    watermark advance is permanent, so the stale rows enter the OOS ledger and the
    real candles are never replayed.

    A lock conflict is the one case worth swallowing, and it is also the least
    likely to reach here: `conn` comes from `connect_with_retry`, which has already
    spent its retry budget, so this process holds the write lock. `is_lock_conflict`
    is what tells the two apart — see analytics/db_retry.py.
    """

    @staticmethod
    def _call(sync_effect: object) -> list[tuple[str, str]]:
        cache: dict[tuple[str, str], pd.DataFrame] = {}
        backfilled: list[tuple[str, str]] = []
        with (
            patch("analytics.signal_runner.sync", side_effect=sync_effect),
            patch(
                "analytics.signal_runner.backfill",
                side_effect=lambda c, cl, s, t, ms: backfilled.append((s, t)),
            ),
        ):
            _sync_watched_series(
                conn=MagicMock(),
                client=MagicMock(),
                symbols=["BTCUSDT"],
                timeframes=["15m"],
                backfill_start_ms=0,
                ohlcv_cache=cache,
            )
        return backfilled

    def test_lock_conflict_is_swallowed_so_the_cycle_can_continue(self) -> None:
        self._call(duckdb.IOException("Conflicting lock is held in /x/analytics.db"))

    def test_non_lock_ioexception_propagates(self) -> None:
        with pytest.raises(duckdb.IOException, match="No such file"):
            self._call(duckdb.IOException("IO Error: No such file or directory"))

    def test_missing_series_still_falls_back_to_backfill(self) -> None:
        assert self._call(ValueError("no data")) == [("BTCUSDT", "15m")]
