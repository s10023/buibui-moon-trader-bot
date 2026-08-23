"""Tests for the fabricated-CVD detector in analytics/store/market_data.py.

The hazard: the OKX adapter used to fabricate `taker_buy_volume = volume / 2` because
OKX publishes no taker-buy split (`utils/okx_client.py`, fixed by #678, which writes
NULL instead). `ohlcv_all` keys on `venue` (460fd17, ST60(b)), so an OKX write lands
BESIDE the Binance bar rather than replacing it -- the scan's job is finding any
pre-#678 fabrication still sitting in history, across every venue. These tests pin the
two signatures that separate a fabricated block from the 7 coincidental bars measured
on the live DB.
"""

import duckdb
import pandas as pd

from analytics.store import init_schema, upsert_ohlcv
from analytics.store.market_data import (
    FABRICATED_CVD_SQL,
    neutral_cvd_runs,
    suspect_neutral_cvd,
)

_HOUR = 3_600_000
_NOW = 1_755_000_000_000  # 2025-08-12, well after every fixture timestamp below
_OLD = 1_600_000_000_000  # 2020-09-13


def _bar(
    open_time: int,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    volume: float = 100.0,
    taker: float | None = None,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "open": 30000.0,
        "high": 31000.0,
        "low": 29000.0,
        "close": 30500.0,
        "volume": volume,
        "taker_buy_volume": volume * 0.61 if taker is None else taker,
    }


class TestTheSql:
    """The SQL leg — what the pure-Python tests below cannot reach."""

    def _rows(self, bars: list[dict[str, object]]) -> list[tuple[str, str, int]]:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        upsert_ohlcv(conn, pd.DataFrame(bars), venue="binance")
        return [
            (r[0], r[1], int(r[2])) for r in conn.execute(FABRICATED_CVD_SQL).fetchall()
        ]

    def test_selects_only_the_exactly_neutral_bars(self) -> None:
        rows = self._rows(
            [
                _bar(_OLD),  # 61% taker — a normal bar
                _bar(_OLD + _HOUR, taker=50.0),  # exactly half of 100
                _bar(_OLD + 2 * _HOUR, taker=50.001),  # near-half is NOT neutral
            ]
        )
        assert rows == [("BTCUSDT", "1h", _OLD + _HOUR)]

    def test_zero_volume_bars_are_never_selected(self) -> None:
        """Mutation guard on `volume > 0`.

        A zero-volume bar stores taker_buy_volume 0, which satisfies `0 = 0 / 2`. There
        are 1,136 on the live DB, so dropping this filter turns the check into a
        four-figure standing count nobody reads. Remove `volume > 0` and this fails.
        """
        rows = self._rows([_bar(_OLD, volume=0.0, taker=0.0)])
        assert rows == []

    def test_null_taker_volume_is_not_selected(self) -> None:
        rows = self._rows([_bar(_OLD, taker=None) | {"taker_buy_volume": None}])
        assert rows == []


class TestRunGrouping:
    def test_adjacent_bars_form_one_run(self) -> None:
        rows = [("BTCUSDT", "1h", _OLD + i * _HOUR) for i in range(5)]
        assert neutral_cvd_runs(rows) == [("BTCUSDT", "1h", _OLD, _OLD + 4 * _HOUR, 5)]

    def test_bars_years_apart_stay_separate(self) -> None:
        """Mutation guard on adjacency — this is the real BTCUSDT/15m case.

        The live DB holds two coincidental 15m bars for one symbol, 2019 and 2022. A
        rule counting bars per (symbol, timeframe) rather than grouping adjacent ones
        would red on them every morning.
        """
        rows = [
            ("BTCUSDT", "15m", 1_567_964_700_000),
            ("BTCUSDT", "15m", 1_656_661_500_000),
        ]
        assert [r.bars for r in neutral_cvd_runs(rows)] == [1, 1]

    def test_a_one_bar_gap_breaks_the_run(self) -> None:
        rows = [("BTCUSDT", "1h", _OLD), ("BTCUSDT", "1h", _OLD + 2 * _HOUR)]
        assert [r.bars for r in neutral_cvd_runs(rows)] == [1, 1]

    def test_symbols_and_timeframes_do_not_merge(self) -> None:
        rows = [
            ("BTCUSDT", "1h", _OLD),
            ("ETHUSDT", "1h", _OLD + _HOUR),
            ("BTCUSDT", "4h", _OLD + 2 * _HOUR),
        ]
        assert [r.bars for r in neutral_cvd_runs(rows)] == [1, 1, 1]

    def test_unsorted_input_still_groups(self) -> None:
        rows = [
            ("BTCUSDT", "1h", _OLD + 2 * _HOUR),
            ("BTCUSDT", "1h", _OLD),
            ("BTCUSDT", "1h", _OLD + _HOUR),
        ]
        assert [r.bars for r in neutral_cvd_runs(rows)] == [3]

    def test_an_unknown_timeframe_merges_rather_than_dissolving(self) -> None:
        """Fail-safe direction: a missing `_BAR_MS` entry must over-report, not hide."""
        rows = [("BTCUSDT", "3m", _OLD), ("BTCUSDT", "3m", _OLD + 999 * _HOUR)]
        assert [r.bars for r in neutral_cvd_runs(rows)] == [2]


class TestSuspectClassification:
    def test_an_isolated_old_bar_is_a_coincidence(self) -> None:
        assert suspect_neutral_cvd([("BTCUSDT", "1h", _OLD)], now_ms=_NOW) == []

    def test_a_contiguous_block_is_flagged_however_old(self) -> None:
        rows = [("BTCUSDT", "1h", _OLD + i * _HOUR) for i in range(3)]
        found = suspect_neutral_cvd(rows, now_ms=_NOW)
        assert [r.bars for r in found] == [3]

    def test_a_single_recent_bar_is_flagged(self) -> None:
        """The incremental-sync case: one fabricated candle, no run to detect."""
        found = suspect_neutral_cvd([("BTCUSDT", "1h", _NOW - _HOUR)], now_ms=_NOW)
        assert [r.bars for r in found] == [1]

    def test_the_recency_window_has_a_clean_baseline(self) -> None:
        """The newest coincidental bar on the live DB is 2024-02-25."""
        newest_coincidence = 1_708_902_000_000
        assert (
            suspect_neutral_cvd([("SUIUSDT", "1h", newest_coincidence)], now_ms=_NOW)
            == []
        )

    def test_the_live_baseline_is_silent(self) -> None:
        """All 7 coincidental bars measured on analytics.db, 2026-08-21."""
        live = [
            ("BCHUSDT", "1h", 1_576_742_400_000),
            ("BTCUSDT", "15m", 1_567_964_700_000),
            ("BTCUSDT", "15m", 1_656_661_500_000),
            ("BTCUSDT", "1h", 1_567_962_000_000),
            ("SOLUSDT", "15m", 1_665_620_100_000),
            ("SOLUSDT", "1h", 1_626_739_200_000),
            ("SUIUSDT", "1h", 1_708_902_000_000),
        ]
        assert suspect_neutral_cvd(live, now_ms=1_755_000_000_000) == []


def test_fabricated_cvd_scan_sees_non_binance_rows() -> None:
    """A fabricated bar parked under a non-default venue must still be flagged."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    conn.execute(
        "INSERT INTO ohlcv_all VALUES "
        "('okx', 'BTCUSDT', '1h', 1, 10, 11, 9, 10.5, 100, 50)"
    )
    flagged = conn.execute(FABRICATED_CVD_SQL).fetchall()
    assert flagged == [("BTCUSDT", "1h", 1)], (
        "the check guards history across venues; the binance-only view would miss this"
    )


def test_two_venues_at_the_same_bar_collapse_to_one_row() -> None:
    """A duplicate (symbol, timeframe, open_time) across venues must not fake a run.

    Without DISTINCT, two rows sharing one open_time sort adjacently and satisfy the
    adjacency test at delta 0, so an isolated >90-day-old bar fabricated under two
    venues would trip `run.bars >= 2` and be flagged as a "run" it is not.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    conn.execute(
        "INSERT INTO ohlcv_all VALUES "
        f"('binance', 'BTCUSDT', '1h', {_OLD}, 10, 11, 9, 10.5, 100, 50), "
        f"('okx', 'BTCUSDT', '1h', {_OLD}, 10, 11, 9, 10.5, 100, 50)"
    )
    rows = [
        (str(r[0]), str(r[1]), int(r[2]))
        for r in conn.execute(FABRICATED_CVD_SQL).fetchall()
    ]
    assert rows == [("BTCUSDT", "1h", _OLD)], (
        "two venues fabricating the same bar-time must collapse to ONE row"
    )
    assert suspect_neutral_cvd(rows, now_ms=_NOW) == [], (
        "one isolated old bar-time must not be flaggable as a 2-bar run"
    )
