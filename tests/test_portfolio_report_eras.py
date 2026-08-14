"""The era check inside the P1 replay report.

The load-bearing case is the NOT RUN line: a silently-skipped check reads exactly
like a passed one, so the absence must be visible in the output.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from analytics.eras import EraBoundary
from portfolio.book import BookResult, SizedTrade
from portfolio.report import format_report
from portfolio.sizing import SizingConfig


def _ms(day: str) -> int:
    return int(
        datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000
    )


def _trade(entry_idx: int) -> SizedTrade:
    return SizedTrade(
        signal_id=f"s{entry_idx}",
        symbol="BTCUSDT",
        tf="15m",
        strategy="bos",
        direction="long",
        entry_idx=entry_idx,
        exit_idx=entry_idx + 1,
        r_eff=1.0,
        g_vol=1.0,
        g_regime=1.0,
        rc_fixed=1.0,
        rc_comp=1.0,
        pnl_fixed=1.0,
        pnl_comp=1.0,
        realized_r=1.0,
        regime="trend",
    )


def _result() -> BookResult:
    index = np.array(
        [_ms("2026-06-10"), _ms("2026-06-20"), _ms("2026-06-30")], dtype=np.int64
    )
    return BookResult(
        daily_index=index,
        capital=10_000.0,
        pnl_fixed=np.zeros(3),
        pnl_comp=np.zeros(3),
        sized=[_trade(0), _trade(1), _trade(2)],
        skipped=[],
    )


def test_omitting_boundaries_reports_not_run_rather_than_staying_silent() -> None:
    out = format_report(_result(), SizingConfig())

    assert "era check: NOT RUN" in out
    assert "CLEAN" not in out, "a skipped check must never read as a passed one"


def test_supplying_boundaries_reports_the_straddle() -> None:
    inside = EraBoundary(
        ts_ms=_ms("2026-06-15"),
        label="N8 watermark fix",
        scope="ledger",
        source="git",
        ref="abc1234",
    )

    out = format_report(_result(), SizingConfig(), [inside])

    assert "STRADDLES 1 boundaries" in out
    assert "N8 watermark fix" in out
    assert "NOT RUN" not in out


def test_a_sample_inside_one_era_reads_clean() -> None:
    """Negative control for the test above — same sample, boundary moved outside."""
    outside = EraBoundary(
        ts_ms=_ms("2026-01-01"),
        label="long before",
        scope="ledger",
        source="git",
        ref="abc1234",
    )

    out = format_report(_result(), SizingConfig(), [outside])

    assert "era check: CLEAN" in out
    assert "STRADDLES" not in out


def test_entry_idx_is_resolved_through_daily_index_not_used_as_a_timestamp() -> None:
    """`SizedTrade.entry_idx` is a position, not ms; using it raw would read as 1970."""
    boundary = EraBoundary(
        ts_ms=_ms("2026-06-15"),
        label="mid-sample",
        scope="ledger",
        source="git",
        ref="abc1234",
    )

    out = format_report(_result(), SizingConfig(), [boundary])

    assert "2026-06-10 to 2026-06-30" in out, "timestamps must come from daily_index"
