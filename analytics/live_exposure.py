"""How often a swept cell actually fires live.

ST134 section 5 item 2. An OOS ``avg_r`` delta is only half an effect size: the same
delta on a cell that fires daily and one that fires twice a year are different
decisions, and a report carrying only the delta cannot tell them apart.

Read-only against ``signal_alert_outcomes``.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

_MS_PER_DAY = 86_400_000
_MS_PER_WEEK = 7 * _MS_PER_DAY


@dataclass(frozen=True)
class AlertExposure:
    """A live alert rate plus the window it was actually measured over.

    ST134 Task 10's rider (raised in the Task 9 review, corrected after a
    Task 10 review finding — see :func:`alerts_per_week`'s docstring).
    ``window_start_ms`` can be LATER than the caller's ``since_ms``: it
    clamps to when the LEDGER as a whole started recording, never to this
    cell's own first alert, so it narrows a stale ``since_ms`` without
    inflating the rate of a cell that simply fires rarely.
    ``first_fired_ms`` carries the per-cell information separately, so a
    reader gets it without it distorting the estimator.
    """

    rate: float
    window_start_ms: int
    window_days: float
    first_fired_ms: int | None


def alerts_per_week(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    since_ms: int,
    now_ms: int,
) -> AlertExposure:
    """Live alerts per week for one ``(symbol, timeframe, strategy)`` cell.

    ⚠ The ledger's timeframe column is ``tf``, while ``backtest_runs`` calls the same
    thing ``timeframe``. A query copied from the backtest side matches no rows and
    reads as a cell that never fires — silent, and in the direction that HIDES
    exposure, which is the direction that matters here.

    ⚠ **The window start CLAMPS to the LEDGER's own earliest recorded row
    across EVERY symbol/tf/strategy, never to this cell's own first alert,
    when that postdates ``since_ms``.** ``since_ms`` is a backtest start
    date, not when the live daemon began recording at all — before that
    point no row could exist for ANY cell, so clamping the shared boundary
    narrows a stale ``since_ms`` without touching a genuinely sparse cell's
    own denominator.

    ⚠ **A per-cell clamp (this function's first version) is unconditional
    and therefore wrong, not merely imprecise.** This cell's own first
    alert is, by construction of the query that finds it, always
    ``>= since_ms`` — so "clamp when the cell's first alert postdates
    ``since_ms``" is a condition that is always true, and the clamp fires on
    every cell with at least one alert. That inflates the rate of the
    SPARSEST cells hardest: measured, a cell with one alert a day before
    ``now_ms`` over a 1095-day window read ``7.00``/wk under that
    (unconditional) clamp against a true ``~0.0064``/wk, and one whose only
    alert landed ten minutes before ``now_ms`` read ``1008``/wk. Clamping to
    the ledger's shared start instead cannot do this: the ledger start can
    only move the window boundary EARLIER than any one cell's own alerts,
    never later, so a rarely-firing cell keeps its true, wide denominator.

    ``first_fired_ms`` is reported per cell (``None`` with no alerts in the
    window) precisely so that information is still visible without feeding
    back into the rate the way the per-cell clamp did.

    Returns ``rate=0.0`` for an empty or zero-length window rather than
    raising, so a cell with no live history reports as unexposed rather than
    aborting the run.
    """
    span_ms = now_ms - since_ms
    if span_ms <= 0:
        return AlertExposure(
            rate=0.0, window_start_ms=since_ms, window_days=0.0, first_fired_ms=None
        )
    row = conn.execute(
        """
        SELECT COUNT(*), MIN(fired_at_ms) FROM signal_alert_outcomes
        WHERE symbol = ?
          AND tf = ?
          AND strategy = ?
          AND fired_at_ms >= ?
          AND fired_at_ms < ?
        """,
        [symbol, timeframe, strategy, since_ms, now_ms],
    ).fetchone()
    n = int(row[0]) if row is not None else 0
    first_fired_ms = int(row[1]) if row is not None and row[1] is not None else None

    # No filter at all: the ledger-WIDE earliest row, across every cell, is
    # the real boundary — before it, nothing could have been recorded for
    # ANYONE, this cell included.
    ledger_row = conn.execute(
        "SELECT MIN(fired_at_ms) FROM signal_alert_outcomes"
    ).fetchone()
    ledger_start_ms = (
        int(ledger_row[0])
        if ledger_row is not None and ledger_row[0] is not None
        else None
    )
    window_start_ms = (
        max(since_ms, ledger_start_ms) if ledger_start_ms is not None else since_ms
    )
    window_span_ms = now_ms - window_start_ms
    if window_span_ms <= 0:
        return AlertExposure(
            rate=0.0,
            window_start_ms=window_start_ms,
            window_days=0.0,
            first_fired_ms=first_fired_ms,
        )
    return AlertExposure(
        rate=n * _MS_PER_WEEK / window_span_ms,
        window_start_ms=window_start_ms,
        window_days=window_span_ms / _MS_PER_DAY,
        first_fired_ms=first_fired_ms,
    )
