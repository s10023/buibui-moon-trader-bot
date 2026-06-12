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
