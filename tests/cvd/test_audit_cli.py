from typing import Any

import duckdb

from analytics.store.schema import init_schema
from analytics.store.spot_data import get_spot_ohlcv
from tools.cvd_audit import backfill, build_parser

DAY = 86_400_000


def _kline(t: int) -> list[Any]:
    return [t, "1", "2", "0.5", "1.5", "10.0", t + DAY - 1, "0", 0, "6.0", "0", "0"]


def test_parser_exposes_backfill_and_run() -> None:
    parser = build_parser()
    assert parser.parse_args(["backfill"]).command == "backfill"
    assert parser.parse_args(["run"]).command == "run"


TRADING = frozenset({"BTCUSDT", "ETHUSDT", "PEPEUSDT"})


def test_backfill_writes_only_spot_ohlcv() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        return [_kline(0)] if "startTime=0" in url else []

    counts = backfill(
        conn,
        symbols=["BTCUSDT"],
        start_ms=0,
        get=fake_get,
        sleep=lambda _s: None,
        trading=TRADING,
    )
    assert counts == {"BTCUSDT": 1}
    assert len(get_spot_ohlcv(conn, "BTCUSDT", 0, DAY)) == 1
    assert conn.execute("SELECT count(*) FROM ohlcv").fetchone() == (0,)


def test_backfill_skips_perp_only_symbols_without_calling_the_api() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        return []

    counts = backfill(
        conn,
        symbols=["HYPEUSDT"],
        start_ms=0,
        get=fake_get,
        sleep=lambda _s: None,
        trading=TRADING,
    )
    assert counts == {}
    assert calls == []


def test_backfill_maps_the_1000pepe_symbol_on_the_request() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        return []

    backfill(
        conn,
        symbols=["1000PEPEUSDT"],
        start_ms=0,
        get=fake_get,
        sleep=lambda _s: None,
        trading=TRADING,
    )
    assert "symbol=PEPEUSDT" in calls[0]


def test_backfill_stores_1000pepe_rows_under_the_perp_symbol() -> None:
    """The request carries the SPOT name; the stored row carries the PERP name.

    `test_backfill_maps_the_1000pepe_symbol_on_the_request` above never reaches
    the write path (its fake getter returns `[]` unconditionally), so it cannot
    catch a regression that stores under `spot_sym` instead of `sym`. This one
    serves klines so the write happens, then asserts on both sides: the row
    exists under the perp symbol, and does NOT exist under the spot symbol —
    the second assertion is the one that actually fails against that
    regression.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        return [_kline(0)] if "startTime=0" in url else []

    backfill(
        conn,
        symbols=["1000PEPEUSDT"],
        start_ms=0,
        get=fake_get,
        sleep=lambda _s: None,
        trading=frozenset({"PEPEUSDT"}),
    )
    assert "symbol=PEPEUSDT" in calls[0]
    assert len(get_spot_ohlcv(conn, "1000PEPEUSDT", 0, DAY)) == 1
    assert len(get_spot_ohlcv(conn, "PEPEUSDT", 0, DAY)) == 0


def test_backfill_skips_a_symbol_whose_spot_market_is_halted() -> None:
    """Kline availability is NOT proof a pair is live.

    Probed 2026-08-11: TONUSDT returns HTTP 200 daily klines while its spot
    exchangeInfo status is BREAK. Without the TRADING filter, a halted market's
    bars enter the panel and are indistinguishable from real data. The fake
    getter below deliberately SERVES klines for TONUSDT — so this test fails
    against a backfill that filters only on `spot_symbol_for`.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        return [_kline(0)] if "startTime=0" in url else []

    counts = backfill(
        conn,
        symbols=["TONUSDT"],
        start_ms=0,
        get=fake_get,
        sleep=lambda _s: None,
        trading=TRADING,
    )
    assert counts == {}
    assert calls == []
    assert len(get_spot_ohlcv(conn, "TONUSDT", 0, DAY)) == 0


def test_backfill_fetches_the_trading_set_when_not_supplied() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        if "exchangeInfo" in url:
            return {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING"}]}
        return [_kline(0)] if "startTime=0" in url else []

    counts = backfill(
        conn, symbols=["BTCUSDT"], start_ms=0, get=fake_get, sleep=lambda _s: None
    )
    assert counts == {"BTCUSDT": 1}
    assert any("exchangeInfo" in u for u in calls)
