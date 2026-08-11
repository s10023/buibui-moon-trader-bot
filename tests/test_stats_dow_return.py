"""DOW median return + the standard error the Stats tab dims on.

Why the error bar exists at all: at the n this card runs (~52 weekdays in a 1y
window), SE measured 0.18-0.40% on BTCUSDT/365d against cell means of 0.07-0.91%
— comparable to or larger than the value it qualifies. The column was rendering
that in green and red with the same visual weight as the range column, which is
real signal; the mean and median even disagree on SIGN for four of the seven
weekdays. The DOW axis is separately a measured NO (the weekend/DOW cut failed the
three-leg gate at DSR 0.672), so nothing here should imply a tradeable direction.

The fixture is skewed on purpose. A symmetric one makes mean == median and would
pass against an implementation that computed the mean twice.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

import duckdb
import pandas as pd
import pytest

from analytics.data_store import init_schema
from analytics.stats.dow import compute_dow_patterns
from analytics.store.market_data import upsert_ohlcv

SYM = "BTCUSDT"
# Every Monday is +1% except one -20% crash: mean goes negative, median stays +1%.
QUIET_RET = 0.01
CRASH_RET = -0.20
N_MONDAYS = 8


def _monday_rows(returns: list[float]) -> pd.DataFrame:
    """One 1h bar per Monday at 12:00 UTC, each with the given close/open return.

    12:00 UTC keeps the bar on the same calendar DATE under both UTC and the
    Asia/Kuala_Lumpur session timezone DuckDB uses here.
    """
    now = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    monday = now - timedelta(days=(now.weekday()) % 7)
    rows = []
    for i, ret in enumerate(returns):
        ts = int((monday - timedelta(weeks=i)).timestamp() * 1000)
        close = 100.0 * (1.0 + ret)
        rows.append(
            {
                "symbol": SYM,
                "timeframe": "1h",
                "open_time": ts,
                "open": 100.0,
                "high": max(100.0, close),
                "low": min(100.0, close),
                "close": close,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
    return pd.DataFrame(rows)


def _mondays(returns: list[float]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    upsert_ohlcv(conn, _monday_rows(returns))
    return conn


def _mon(conn: duckdb.DuckDBPyConnection):  # type: ignore[no-untyped-def]
    row = next(r for r in compute_dow_patterns(conn, SYM, 3650).rows if r.dow == "Mon")
    return row


def test_median_return_ignores_the_crash_the_mean_cannot() -> None:
    rets = [CRASH_RET] + [QUIET_RET] * (N_MONDAYS - 1)
    row = _mon(_mondays(rets))
    assert row.avg_return_pct == pytest.approx(statistics.mean(rets))
    assert row.median_return_pct == pytest.approx(QUIET_RET)
    # The whole point: one drags negative, the other does not.
    assert row.avg_return_pct < 0 < row.median_return_pct


def test_stderr_matches_stddev_over_sqrt_n() -> None:
    rets = [CRASH_RET] + [QUIET_RET] * (N_MONDAYS - 1)
    row = _mon(_mondays(rets))
    expected = statistics.stdev(rets) / (len(rets) ** 0.5)
    assert row.return_stderr_pct == pytest.approx(expected)


def test_this_cell_is_noise_by_its_own_error_bar() -> None:
    """The dimming rule the UI applies: |mean| < 2.69 SE means 'no direction'.

    2.69 is Bonferroni for the SEVEN weekdays the card shows at once (alpha 0.05
    / 7, two-sided). An uncorrected bar is wrong here because the reader scans all
    seven cells and reacts to the largest — the textbook multiple-comparison setup.
    On BTCUSDT/365d an uncorrected 2 SE bar left Thursday coloured at 2.46 SE.
    """
    row = _mon(_mondays([CRASH_RET] + [QUIET_RET] * (N_MONDAYS - 1)))
    assert row.return_stderr_pct is not None
    assert abs(row.avg_return_pct) < 2.69 * row.return_stderr_pct


def test_a_real_direction_is_not_dimmed() -> None:
    """Non-vacuity: the rule must also let a genuine signal through.

    Without this, a rule that dimmed EVERYTHING would pass the test above and the
    column would go uniformly grey — the mirror failure of rendering noise in
    colour, and just as misleading.
    """
    row = _mon(_mondays([QUIET_RET] * N_MONDAYS))  # every Monday identical
    assert row.return_stderr_pct == pytest.approx(0.0)
    assert abs(row.avg_return_pct) > 2.69 * (row.return_stderr_pct or 0.0)


def test_stderr_is_none_at_n_equals_one() -> None:
    """Sample stddev is undefined at n=1.

    Must stay None rather than coerce to 0.0 — a zero-width error bar would make a
    single observation look infinitely significant, which is the exact inversion of
    what this field is for.
    """
    row = _mon(_mondays([QUIET_RET]))
    assert row.sample_days == 1
    assert row.return_stderr_pct is None
