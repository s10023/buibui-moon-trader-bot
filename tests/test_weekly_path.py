"""H10 partial-path predictiveness — pure-library tests (no DB)."""

from datetime import date, timedelta

import numpy as np
import pytest

from analytics import weekly_path as wp

WEEK_BARS = 168


def _week(i: int) -> date:
    """Monday i weeks after 2020-01-06 (a Monday)."""
    return date(2020, 1, 6) + timedelta(weeks=i)


def _linear_path(start: float, end: float) -> tuple[float, ...]:
    """A 168-point path rising linearly from `start` to `end`."""
    return tuple(np.linspace(start, end, WEEK_BARS))


def test_signal_sign_reads_the_bar_before_hour() -> None:
    """Hour h means index h-1 — the close of the h-th completed bar."""
    path = [0.0] * WEEK_BARS
    path[23] = -5.0  # index 23 == hour 24
    path[24] = +5.0
    assert wp.signal_sign(path, 24) == -1.0
    assert wp.signal_sign(path, 25) == +1.0


def test_signal_sign_flat_week_is_zero() -> None:
    assert wp.signal_sign([0.0] * WEEK_BARS, 24) == 0.0


def test_remaining_return_spans_hour_to_close() -> None:
    path = [0.0] * WEEK_BARS
    path[23] = 2.0
    path[167] = 5.0
    assert wp.remaining_return(path, 24) == pytest.approx(3.0)


def test_remaining_return_at_last_hour_is_zero() -> None:
    path = list(_linear_path(0.0, 4.0))
    assert wp.remaining_return(path, 168) == pytest.approx(0.0)
