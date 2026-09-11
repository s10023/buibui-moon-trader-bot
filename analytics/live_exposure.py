"""How often a swept cell actually fires live.

ST134 section 5 item 2. An OOS ``avg_r`` delta is only half an effect size: the same
delta on a cell that fires daily and one that fires twice a year are different
decisions, and a report carrying only the delta cannot tell them apart.

Read-only against ``signal_alert_outcomes``.
"""

from __future__ import annotations

import duckdb

_MS_PER_WEEK = 7 * 86_400_000


def alerts_per_week(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    since_ms: int,
    now_ms: int,
) -> float:
    """Live alerts per week for one ``(symbol, timeframe, strategy)`` cell.

    ⚠ The ledger's timeframe column is ``tf``, while ``backtest_runs`` calls the same
    thing ``timeframe``. A query copied from the backtest side matches no rows and
    reads as a cell that never fires — silent, and in the direction that HIDES
    exposure, which is the direction that matters here.

    Returns ``0.0`` for an empty or zero-length window rather than raising, so a
    cell with no live history reports as unexposed rather than aborting the run.
    """
    span_ms = now_ms - since_ms
    if span_ms <= 0:
        return 0.0
    row = conn.execute(
        """
        SELECT COUNT(*) FROM signal_alert_outcomes
        WHERE symbol = ?
          AND tf = ?
          AND strategy = ?
          AND fired_at_ms >= ?
          AND fired_at_ms < ?
        """,
        [symbol, timeframe, strategy, since_ms, now_ms],
    ).fetchone()
    n = int(row[0]) if row is not None else 0
    return n * _MS_PER_WEEK / span_ms
