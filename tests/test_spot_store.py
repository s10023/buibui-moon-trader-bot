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
