"""Tests for analytics/brief/monthly.py (conn-free adapter)."""

from datetime import UTC, datetime

import pandas as pd

from analytics.brief.monthly import build_monthly_context


def _daily(n_days: int, start: datetime, step: float) -> pd.DataFrame:
    rows = []
    for i in range(n_days):
        ts = int((start.timestamp() + i * 86_400) * 1000)
        close = 100.0 + i * step
        rows.append(
            {
                "open_time": ts,
                "open": close - step,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
            }
        )
    return pd.DataFrame(rows)


def test_none_on_empty_frame() -> None:
    """No daily bars -> no context, one note."""
    ctx, notes = build_monthly_context(
        completed_1d=pd.DataFrame(),
        as_of_ms=int(datetime(2026, 7, 21, tzinfo=UTC).timestamp() * 1000),
    )
    assert ctx is None
    assert len(notes) == 1


def test_month_to_date_return_and_range_position() -> None:
    """A rising month reports a positive MTD return and a high range position."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    df = _daily(400, start, 0.5)
    as_of = int(datetime(2026, 2, 5, tzinfo=UTC).timestamp() * 1000)
    ctx, notes = build_monthly_context(completed_1d=df, as_of_ms=as_of)
    assert ctx is not None
    assert ctx.mtd_return_pct > 0
    assert ctx.range_position is not None
    assert 0.0 <= ctx.range_position <= 1.0
    assert ctx.n_months > 0
    assert notes == []


def test_range_position_none_when_flat() -> None:
    """A month whose high equals its low reports range_position None."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    df = _daily(400, start, 0.0)
    df["high"] = df["close"]
    df["low"] = df["close"]
    as_of = int(datetime(2026, 2, 5, tzinfo=UTC).timestamp() * 1000)
    ctx, _ = build_monthly_context(completed_1d=df, as_of_ms=as_of)
    assert ctx is not None
    assert ctx.range_position is None
