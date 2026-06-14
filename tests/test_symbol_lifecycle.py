"""Tests for the symbol_lifecycle table (N3 survivorship guard)."""

from unittest.mock import MagicMock, patch

import duckdb
import pandas as pd

from analytics.data_store import (
    get_symbol_lifecycle,
    init_schema,
    upsert_symbol_lifecycle,
)
from analytics.data_sync import refresh_symbol_lifecycle

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
    for col in (
        "onboard_ms",
        "first_checked_ms",
        "last_checked_ms",
        "delisted_noted_ms",
    ):
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


def _info_df(rows: list[dict[str, object]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["symbol", "status", "onboard_ms"])
    df["onboard_ms"] = df["onboard_ms"].astype("Int64")
    return df


class TestRefreshSymbolLifecycle:
    def test_inserts_new_symbols(self) -> None:
        conn = _make_conn()
        info = _info_df([{"symbol": "BTCUSDT", "status": "TRADING", "onboard_ms": 111}])
        with patch("analytics.data_sync.fetch_futures_symbol_info", return_value=info):
            n = refresh_symbol_lifecycle(conn, MagicMock(), ["BTCUSDT"], now_ms=1_000)
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
        info = _info_df([{"symbol": "BTCUSDT", "status": "TRADING", "onboard_ms": 111}])
        with patch("analytics.data_sync.fetch_futures_symbol_info", return_value=info):
            refresh_symbol_lifecycle(conn, MagicMock(), ["BTCUSDT"], now_ms=1_000)
            refresh_symbol_lifecycle(conn, MagicMock(), ["BTCUSDT"], now_ms=2_000)
        row = get_symbol_lifecycle(conn).iloc[0]
        assert int(row["first_checked_ms"]) == 1_000
        assert int(row["last_checked_ms"]) == 2_000

    def test_absent_symbol_marked_delisted_once(self) -> None:
        conn = _make_conn()
        present = _info_df(
            [{"symbol": "BTCUSDT", "status": "TRADING", "onboard_ms": 111}]
        )
        gone = _info_df([])
        with patch(
            "analytics.data_sync.fetch_futures_symbol_info", return_value=present
        ):
            refresh_symbol_lifecycle(conn, MagicMock(), ["BTCUSDT"], now_ms=1_000)
        with patch("analytics.data_sync.fetch_futures_symbol_info", return_value=gone):
            refresh_symbol_lifecycle(conn, MagicMock(), [], now_ms=2_000)
            refresh_symbol_lifecycle(conn, MagicMock(), [], now_ms=3_000)
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
            refresh_symbol_lifecycle(conn, MagicMock(), ["GONEUSDT"], now_ms=1_000)
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
            refresh_symbol_lifecycle(conn, MagicMock(), ["BTCUSDT"], now_ms=1_000)
            # second run requests only ETHUSDT — BTCUSDT must still be refreshed
            refresh_symbol_lifecycle(conn, MagicMock(), ["ETHUSDT"], now_ms=2_000)
        df = get_symbol_lifecycle(conn)
        assert sorted(df["symbol"]) == ["BTCUSDT", "ETHUSDT"]
        assert int(df[df["symbol"] == "BTCUSDT"].iloc[0]["last_checked_ms"]) == 2_000
