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


def test_pct_of_months_none_when_zero_prior_months() -> None:
    """M2: zero completed prior months must not fabricate a p50 — None."""
    start = datetime(2026, 2, 1, tzinfo=UTC)
    df = _daily(5, start, 0.5)  # all 5 bars are in the current month
    as_of = int(datetime(2026, 2, 10, tzinfo=UTC).timestamp() * 1000)
    ctx, _ = build_monthly_context(completed_1d=df, as_of_ms=as_of)
    assert ctx is not None
    assert ctx.n_months == 0
    assert ctx.pct_of_months is None


def test_elapsed_frac_uses_completed_bar_count_not_as_of_day() -> None:
    """Item 6: mtd_elapsed_frac must describe the SAME window as
    mtd_return_pct (completed bars), not the calendar day-of-month — those
    diverge whenever completed_1d has fewer bars for the current month than
    as_of.day (e.g. the local sync hasn't caught up to "today" yet)."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    df = _daily(400, start, 0.5)  # last bar = 2026-02-04 (4 Feb bars)
    as_of = int(datetime(2026, 2, 10, tzinfo=UTC).timestamp() * 1000)  # day 10
    ctx, _ = build_monthly_context(completed_1d=df, as_of_ms=as_of)
    assert ctx is not None
    days_in_feb_2026 = 28
    assert abs(ctx.mtd_elapsed_frac - 4 / days_in_feb_2026) < 1e-9


def test_partial_first_month_in_window_is_dropped() -> None:
    """I3: the oldest month in the fetch window may start mid-month (fetch
    truncation, or the symbol was listed mid-month) — that's not a
    COMPLETED month's return, so it must be excluded from the population."""
    start = datetime(2025, 1, 15, tzinfo=UTC)  # NOT day 1 — partial Jan 2025
    df = _daily(400, start, 0.5)  # last bar = 2026-02-18
    as_of = int(datetime(2026, 2, 25, tzinfo=UTC).timestamp() * 1000)
    ctx, _ = build_monthly_context(completed_1d=df, as_of_ms=as_of)
    assert ctx is not None
    ts = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    current_ym = datetime.fromtimestamp(as_of / 1000, tz=UTC).strftime("%Y-%m")
    prior_yms = sorted(
        y for y in ts.dt.strftime("%Y-%m").unique().tolist() if y != current_ym
    )
    # The oldest prior month (2025-01, starting day 15) is dropped; every
    # other prior month in this contiguous-daily fixture starts on day 1.
    assert ctx.n_months == len(prior_yms) - 1
