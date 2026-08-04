import duckdb
import pandas as pd

from analytics.store.schema import init_schema
from analytics.store.venue_prices import get_venue_spot_daily, upsert_venue_spot_daily


def test_upsert_and_read_roundtrip_is_idempotent() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    df = pd.DataFrame(
        {
            "venue": ["coinbase", "coinbase", "binance"],
            "symbol": ["BTC-USD", "BTC-USD", "BTCUSDT"],
            "open_time": [1_600_000_000_000, 1_600_086_400_000, 1_600_000_000_000],
            "close": [10_000.0, 10_500.0, 9_990.0],
        }
    )
    upsert_venue_spot_daily(conn, df)
    upsert_venue_spot_daily(conn, df)  # idempotent: PK replace, not duplicate

    out = get_venue_spot_daily(conn, "coinbase", "BTC-USD")
    assert list(out["open_time"]) == [1_600_000_000_000, 1_600_086_400_000]
    assert list(out["close"]) == [10_000.0, 10_500.0]
    assert len(get_venue_spot_daily(conn, "binance", "BTCUSDT")) == 1
    assert get_venue_spot_daily(conn, "coinbase", "NOPE-USD").empty
