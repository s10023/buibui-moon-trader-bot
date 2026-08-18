"""Tests for the give-back / heat-and-run measurement.

The excursion-window tests are built so the guard is REACHABLE: each fixture puts
a price extreme on the bar that must NOT be counted, large enough that including
it changes the reported number. A fixture whose excluded bar carries an
unremarkable price would pass whether or not the window is correct.
"""

from __future__ import annotations

import duckdb
import pytest

from analytics.giveback import (
    GivebackRow,
    load_giveback_rows,
    sl_pct_bucket,
    summarise,
    summarise_by,
)
from portfolio.replay import restate_on_resolution_clock

_PARITY_MS = 1_781_149_644_000
_POST = _PARITY_MS + 86_400_000  # comfortably post-parity: no restatement


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE signal_alert_outcomes ("
        " signal_id VARCHAR, symbol VARCHAR, tf VARCHAR, strategy VARCHAR,"
        " direction VARCHAR, fired_at_ms BIGINT, candle_ts_ms BIGINT,"
        " entry_price DOUBLE, sl_price DOUBLE, tp_price DOUBLE, rr_ratio DOUBLE,"
        " confidence_at_fire INTEGER, tags VARCHAR, outcome VARCHAR,"
        " outcome_r DOUBLE, outcome_filled_at_ms BIGINT)"
    )
    c.execute(
        "CREATE TABLE ohlcv (symbol VARCHAR, timeframe VARCHAR, open_time BIGINT,"
        " open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,"
        " taker_buy_volume DOUBLE)"
    )
    return c


def _signal(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str = "s1",
    direction: str = "long",
    entry: float = 100.0,
    sl: float = 90.0,
    outcome: str = "loss",
    outcome_r: float = -1.0,
    candle_ts_ms: int = 1_000,
    filled_at_ms: int = _POST,
    strategy: str = "bos",
    tf: str = "1h",
) -> None:
    conn.execute(
        "INSERT INTO signal_alert_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            signal_id,
            "BTCUSDT",
            tf,
            strategy,
            direction,
            candle_ts_ms,
            candle_ts_ms,
            entry,
            sl,
            120.0,
            2.0,
            3,
            "",
            outcome,
            outcome_r,
            filled_at_ms,
        ],
    )


def _bar(
    conn: duckdb.DuckDBPyConnection, open_time: int, high: float, low: float
) -> None:
    conn.execute(
        "INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
        ["BTCUSDT", "1h", open_time, 100.0, high, low, 100.0, 1.0, 0.5],
    )


# --- the excursion window ---------------------------------------------------


def test_signal_candle_bar_is_excluded_from_mfe(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The bar AT candle_ts_ms is pre-entry and must not count.

    Entry fills at the OPEN of the first post-signal bar, so excursion on the
    signal bar itself was never reachable. The excluded bar carries high=200
    (MFE 10R); the admitted bar carries high=105 (MFE 0.5R) — so a window that
    wrongly includes the signal bar reports 10.0 instead of 0.5.
    """
    _signal(conn, candle_ts_ms=1_000, filled_at_ms=3_000)
    _bar(conn, 1_000, high=200.0, low=100.0)  # signal candle — pre-entry
    _bar(conn, 2_000, high=105.0, low=95.0)
    _bar(conn, 3_000, high=101.0, low=90.0)

    row = load_giveback_rows(conn)[0]
    assert row.mfe_r == pytest.approx(0.5)
    assert row.bars == 2


def test_exit_bar_is_included_in_mfe(conn: duckdb.DuckDBPyConnection) -> None:
    """outcome_filled_at_ms IS the exit bar's open_time, and its excursion was
    reachable, so the window is inclusive at the top end."""
    _signal(conn, candle_ts_ms=1_000, filled_at_ms=3_000)
    _bar(conn, 2_000, high=101.0, low=99.0)
    _bar(conn, 3_000, high=130.0, low=90.0)  # exit bar carries the extreme
    _bar(conn, 4_000, high=500.0, low=90.0)  # after the exit — must not count

    row = load_giveback_rows(conn)[0]
    assert row.mfe_r == pytest.approx(3.0)
    assert row.bars == 2


def test_short_mfe_reads_the_low(conn: duckdb.DuckDBPyConnection) -> None:
    _signal(conn, direction="short", entry=100.0, sl=110.0, candle_ts_ms=1_000)
    _bar(conn, 2_000, high=101.0, low=80.0)

    row = load_giveback_rows(conn)[0]
    assert row.r_unit == pytest.approx(10.0)
    assert row.mfe_r == pytest.approx(2.0)


def test_unmeasurable_row_is_kept_and_counted(conn: duckdb.DuckDBPyConnection) -> None:
    """A hold window covering no bars is a data gap, not a zero excursion."""
    _signal(conn, candle_ts_ms=1_000, filled_at_ms=3_000)  # no ohlcv rows at all

    rows = load_giveback_rows(conn)
    assert len(rows) == 1
    assert rows[0].mfe_r is None and rows[0].giveback_r is None
    assert not rows[0].measurable
    assert summarise(rows, label="x").unmeasurable == 1


def test_zero_risk_row_is_excluded(conn: duckdb.DuckDBPyConnection) -> None:
    """R is undefined when nothing is risked — the resolver's own convention."""
    _signal(conn, entry=100.0, sl=100.0)
    _bar(conn, 2_000, high=110.0, low=90.0)

    assert load_giveback_rows(conn) == []


# --- cost basis -------------------------------------------------------------


def test_giveback_subtracts_the_restated_outcome(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """MFE_R is raw price; outcome_r must be put on one basis before subtracting."""
    pre_parity = _PARITY_MS - 86_400_000
    _signal(conn, outcome_r=-1.0, candle_ts_ms=1_000, filled_at_ms=pre_parity)
    _bar(conn, 2_000, high=110.0, low=90.0)

    row = load_giveback_rows(conn)[0]
    restated = restate_on_resolution_clock(-1.0, 100.0, 90.0, pre_parity)
    assert restated < -1.0  # drag makes a pre-parity loss worse on the net basis
    assert row.outcome_r == pytest.approx(restated)
    assert row.giveback_r == pytest.approx(1.0 - restated)

    raw = load_giveback_rows(conn, restate_cost_basis=False)[0]
    assert raw.outcome_r == pytest.approx(-1.0)


def test_restatement_splits_on_the_resolution_clock_not_the_candle() -> None:
    """Candle time smears the basis step across the resolution lag and
    manufactures a phantom era two months early."""
    assert restate_on_resolution_clock(1.0, 100.0, 90.0, _PARITY_MS) == 1.0
    assert restate_on_resolution_clock(1.0, 100.0, 90.0, _PARITY_MS - 1) < 1.0


# --- reported quantities ----------------------------------------------------


def test_primary_population_is_conditional_on_x(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _signal(conn, signal_id="reached", candle_ts_ms=1_000, filled_at_ms=3_000)
    _bar(conn, 2_000, high=120.0, low=99.0)  # MFE 2.0R
    conn.execute(
        "INSERT INTO signal_alert_outcomes VALUES "
        "('flat','ETHUSDT','1h','bos','long',5000,5000,100.0,90.0,120.0,2.0,3,'',"
        f"'loss',-1.0,{_POST})"
    )
    conn.execute(
        "INSERT INTO ohlcv VALUES ('ETHUSDT','1h',6000,100.0,102.0,90.0,100.0,1.0,0.5)"
    )

    rows = load_giveback_rows(conn)
    assert len(rows) == 2
    assert summarise(rows, label="p", x_r=1.0).n_measured == 1
    assert summarise(rows, label="all", x_r=None).n_measured == 2


def test_intrabar_ambiguity_is_flagged(conn: duckdb.DuckDBPyConnection) -> None:
    """One bar touching both the stop and the >=X extreme cannot be ordered."""
    _signal(conn, candle_ts_ms=1_000, filled_at_ms=2_000)
    _bar(conn, 2_000, high=115.0, low=89.0)  # reaches +1.5R and the 90.0 stop

    row = load_giveback_rows(conn)[0]
    assert row.intrabar_ambiguous
    assert summarise([row], label="p").intrabar_ambiguous == 1


def test_min_licensable_bar_refuses_a_null_below_the_ci(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """No bar at or below max(|ci_lo|, |ci_hi|) can license a negative claim."""
    s = summarise(
        [
            GivebackRow(
                signal_id=f"s{i}",
                symbol="BTCUSDT",
                tf="1h",
                strategy="bos",
                direction="long",
                candle_ts_ms=i,
                outcome_filled_at_ms=i + 1,
                entry_price=100.0,
                sl_price=90.0,
                outcome="loss",
                outcome_r=-1.0,
                r_unit=10.0,
                sl_pct=0.1,
                bars=2,
                mfe_r=1.5,
                giveback_r=0.5 + (i % 3) * 0.1,
                intrabar_ambiguous=False,
            )
            for i in range(40)
        ],
        label="p",
        ci=True,
        n_boot=200,
    )
    assert s.ci_lo is not None and s.ci_hi is not None
    assert s.min_licensable_bar == pytest.approx(max(abs(s.ci_lo), abs(s.ci_hi)))


def test_summarise_by_keeps_thin_cells(conn: duckdb.DuckDBPyConnection) -> None:
    """Dropping a thin cell hides the thinness that makes it unreadable."""
    _signal(conn, signal_id="a", strategy="bos", candle_ts_ms=1_000, filled_at_ms=2_000)
    _bar(conn, 2_000, high=120.0, low=99.0)

    cells = summarise_by(load_giveback_rows(conn), lambda r: r.strategy)
    assert [c.label for c in cells] == ["bos"]
    assert cells[0].n_measured == 1


def test_sl_pct_bucket_isolates_the_flat_two_percent_mass() -> None:
    def _row(sl_pct: float) -> GivebackRow:
        return GivebackRow(
            signal_id="s",
            symbol="BTCUSDT",
            tf="1h",
            strategy="bos",
            direction="long",
            candle_ts_ms=0,
            outcome_filled_at_ms=1,
            entry_price=100.0,
            sl_price=98.0,
            outcome="loss",
            outcome_r=-1.0,
            r_unit=2.0,
            sl_pct=sl_pct,
            bars=1,
            mfe_r=0.0,
            giveback_r=1.0,
            intrabar_ambiguous=False,
        )

    assert sl_pct_bucket(_row(0.020)) == "~2% (flat-SL detectors)"
    assert sl_pct_bucket(_row(0.005)) == "<1%"
    assert sl_pct_bucket(_row(0.060)) == ">4%"
