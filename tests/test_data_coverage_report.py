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
