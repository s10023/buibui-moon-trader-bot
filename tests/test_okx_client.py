"""Tests for the OKX market-data adapter (utils/okx_client.py)."""

from typing import Any

import duckdb
import pandas as pd
import pytest

from utils.okx_client import (
    OKXClient,
    _okx_row_to_binance,
    _to_okx_bar,
    _to_okx_inst_id,
)


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    """Returns one page of OKX candles then an empty page (end of history)."""

    def __init__(self, pages: list[list[list[str]]]) -> None:
        self._pages = pages
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any], timeout: float) -> _FakeResp:
        self.calls.append(params)
        data = self._pages.pop(0) if self._pages else []
        return _FakeResp({"code": "0", "msg": "", "data": data})


_NOW = 1_755_000_000_000  # 2025-08-12, far outside the fixtures' recency window


def _candle(ts: int, confirm: str = "1") -> list[str]:
    return [str(ts), "1", "2", "0.5", "1.5", "100", "x", "y", confirm]


def test_okx_row_to_binance_writes_null_taker_volume() -> None:
    # OKX row: ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm
    okx = [
        "1726128000000",
        "60000.0",
        "60500.0",
        "59800.0",
        "60250.0",
        "1234.5",
        "74000000",
        "74000000",
        "1",
    ]
    row = _okx_row_to_binance(okx)
    assert row[0] == 1726128000000  # open_time (int ms)
    assert row[1] == "60000.0"  # open
    assert row[2] == "60500.0"  # high
    assert row[3] == "59800.0"  # low
    assert row[4] == "60250.0"  # close
    assert row[5] == "1234.5"  # volume
    # index 9 = taker_buy_volume. OKX publishes no taker-buy split, so this is
    # None -> SQL NULL. It must NOT be volume / 2: `ohlcv`'s PK has no venue
    # component and upsert REPLACES on conflict, so a fabricated number is
    # indistinguishable from the Binance reading it overwrites (SoT ST60).
    assert row[9] is None
    assert len(row) == 10


def test_okx_row_to_binance_open_time_is_int() -> None:
    okx = ["1726128000000", "1", "2", "0.5", "1.5", "10", "x", "y", "1"]
    row = _okx_row_to_binance(okx)
    assert isinstance(row[0], int)


def test_to_okx_inst_id_maps_usdt_perps() -> None:
    assert _to_okx_inst_id("BTCUSDT") == "BTC-USDT-SWAP"
    assert _to_okx_inst_id("ETHUSDT") == "ETH-USDT-SWAP"
    assert _to_okx_inst_id("SOLUSDT") == "SOL-USDT-SWAP"


def test_to_okx_inst_id_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="cannot map"):
        _to_okx_inst_id("FOOBAR")


def test_to_okx_bar_uses_utc_daily() -> None:
    assert _to_okx_bar("15m") == "15m"
    assert _to_okx_bar("1h") == "1H"
    assert _to_okx_bar("4h") == "4H"
    # 1Dutc so daily candle open aligns to 00:00 UTC like Binance open_time
    assert _to_okx_bar("1d") == "1Dutc"
    # 1Wutc likewise anchors the weekly open to UTC (Binance weekly = Mon 00:00 UTC)
    assert _to_okx_bar("1w") == "1Wutc"


def test_to_okx_bar_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _to_okx_bar("2h")


def test_futures_klines_filters_by_start_sorts_ascending_drops_unconfirmed() -> None:
    # newest-first page: ts 3000 (unconfirmed), 2000, 1000
    session = _FakeSession([[_candle(3000, "0"), _candle(2000), _candle(1000)]])
    client = OKXClient(session=session)
    df = client.futures_klines("BTCUSDT", "1h", start_time=2000, limit=1000)
    # 3000 dropped (unconfirmed); 1000 dropped (< start); only 2000 kept
    assert list(df["open_time"]) == [2000]
    assert df["symbol"].iloc[0] == "BTCUSDT"
    assert df["timeframe"].iloc[0] == "1h"  # stored as the Binance tf, not OKX bar
    # NaN in the frame -> NULL in the DB; never the old fabricated volume / 2.
    assert pd.isna(df["taker_buy_volume"].iloc[0])


def test_futures_klines_paginates_until_start_reached() -> None:
    # page 1 newest: 3000,2000 ; page 2: 1000 ; want start=1000 -> all 3
    session = _FakeSession([[_candle(3000), _candle(2000)], [_candle(1000)], []])
    client = OKXClient(session=session)
    df = client.futures_klines("BTCUSDT", "1h", start_time=1000, limit=1000)
    assert list(df["open_time"]) == [1000, 2000, 3000]
    # second call must carry an `after` cursor = oldest ts of page 1
    assert session.calls[1]["after"] == "2000"


class TestNullTakerVolumeIsNotAFabrication:
    """ST60 (a): an OKX overwrite is now VISIBLE rather than plausible.

    NULL does not make the OKX adapter's synthetic taker-buy value real — it stops
    that value passing for a measurement. (`ohlcv_all` keys on `venue` since
    460fd17, so an OKX write lands BESIDE the Binance row rather than replacing it;
    that overwrite hazard is closed. NULL's remaining job is keeping OKX's own rows
    honest, which matters once Task 4 points the tier-1 scan across every venue.)
    These pin the two consequences that decide whether NULL is safe to ship on the
    live path. The third (the `cvd_divergence` detector degrading to no signals
    rather than reading a fabricated flat series) is pinned in
    `tests/test_strategies.py`, where the fixture can be shown to fire before the
    column is nulled.
    """

    def _okx_frame(self) -> pd.DataFrame:
        session = _FakeSession([[_candle(3000), _candle(2000), _candle(1000)], []])
        client = OKXClient(session=session)
        return client.futures_klines("BTCUSDT", "1h", start_time=1000, limit=1000)

    def test_reaches_duckdb_as_sql_null(self) -> None:
        """The claim is SQL NULL, not float NaN — the two are distinct in DuckDB."""
        from analytics.store import init_schema, upsert_ohlcv

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Write as the real OKX write path will (venue="okx" from Task 5 onward) and
        # assert against the base table directly, rather than the `ohlcv` view --
        # the view defaults to binance-only, which would filter these rows out and
        # make the assertion pass for the wrong reason.
        upsert_ohlcv(conn, self._okx_frame(), venue="okx")
        row = conn.execute(
            "SELECT count(*) FILTER (WHERE taker_buy_volume IS NULL), count(*) "
            "FROM ohlcv_all WHERE venue = 'okx'"
        ).fetchone()
        assert row is not None
        assert (row[0], row[1]) == (3, 3)

    def _flagged(self, frame: pd.DataFrame) -> list[Any]:
        from analytics.store import init_schema, upsert_ohlcv
        from analytics.store.market_data import (
            FABRICATED_CVD_SQL,
            suspect_neutral_cvd,
        )

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # FABRICATED_CVD_SQL reads `ohlcv_all` directly (Task 4), so an OKX-venue
        # write is visible to it regardless of the `ohlcv` read-view's preference
        # order -- no `set_read_venue_order` call is needed here.
        upsert_ohlcv(conn, frame, venue="okx")
        rows = [
            (str(r[0]), str(r[1]), int(r[2]))
            for r in conn.execute(FABRICATED_CVD_SQL).fetchall()
        ]
        return list(suspect_neutral_cvd(rows, now_ms=_NOW))

    def test_the_fabricated_cvd_detector_finds_nothing_to_flag(self) -> None:
        """#676's tier-1 line must stay quiet — there is no fabrication to detect."""
        assert self._flagged(self._okx_frame()) == []

    def test_but_the_old_fabrication_on_the_same_bars_is_flagged(self) -> None:
        """The mutation control: without it the assertion above passes vacuously.

        Same three bars, same prices — only the NULL restored to the old
        `volume / 2`. This is what the adapter used to emit, and it must trip
        #676's adjacent-run signature.
        """
        fabricated = self._okx_frame()
        fabricated["taker_buy_volume"] = fabricated["volume"] / 2.0
        assert self._flagged(fabricated) != []
