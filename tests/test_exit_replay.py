"""Tests for the pluggable exit-replay engine (exit spec §4)."""

import numpy as np
import pytest

from analytics.exits.policies import ExitPolicyConfig, composite, fixed
from analytics.exits.replay import ExitOutcome, replay_exits


def _w(
    highs: list[float], lows: list[float], closes: list[float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.array(highs, dtype=np.float64),
        np.array(lows, dtype=np.float64),
        np.array(closes, dtype=np.float64),
    )


def _run(
    window: tuple[np.ndarray, np.ndarray, np.ndarray],
    policy: ExitPolicyConfig,
    *,
    direction: str = "long",
    entry: float = 100.0,
    sl_price: float = 98.0,
) -> ExitOutcome:
    # long default: entry 100, sl 98 -> risk 2; 1R=102, TP@3R=106, BE=100
    h, lo, c = window
    return replay_exits(
        h, lo, c, direction=direction, entry=entry, sl_price=sl_price, policy=policy
    )


class TestFixedBaseline:
    def test_pure_tp_win(self) -> None:
        r = _run(_w([106], [100], [105]), fixed(tp_r=3.0, max_hold_bars=10))
        assert r.outcome == "win"
        assert r.realized_r == pytest.approx(3.0)
        assert r.exit_bar == 0

    def test_pure_sl_loss(self) -> None:
        r = _run(_w([101], [98], [99]), fixed(tp_r=3.0, max_hold_bars=10))
        assert r.outcome == "loss"
        assert r.realized_r == pytest.approx(-1.0)

    def test_time_expiry_marks_to_market(self) -> None:
        w = _w([101, 101, 101], [99, 99, 99], [100.5, 100.5, 100.5])
        r = _run(w, fixed(tp_r=3.0, max_hold_bars=3))
        assert r.outcome == "expired"
        assert r.realized_r == pytest.approx(0.25)  # (100.5-100)/2
        assert r.exit_bar == 2

    def test_adverse_first_on_sl_tp_same_bar(self) -> None:
        # bar spans both SL and TP -> conservative loss
        r = _run(_w([106], [98], [102]), fixed(tp_r=3.0, max_hold_bars=10))
        assert r.outcome == "loss"
        assert r.realized_r == pytest.approx(-1.0)


class TestComposite:
    P = composite(tp_r=3.0, max_hold_bars=10, time_stop_bars=10)

    def test_partial_then_runner_to_breakeven(self) -> None:
        # bar0 hits 1R (partial 50% + arm BE), bar1 fades to entry (BE stop)
        r = _run(_w([102, 101], [100, 100], [101, 100]), self.P)
        assert r.partial_taken
        assert r.outcome == "breakeven"
        assert r.realized_r == pytest.approx(0.5)  # 0.5*1 + 0.5*0
        assert r.exit_bar == 1

    def test_partial_then_runner_to_tp(self) -> None:
        r = _run(_w([102, 106], [100, 101], [101, 105]), self.P)
        assert r.partial_taken
        assert r.outcome == "win"
        assert r.realized_r == pytest.approx(0.5 * 1.0 + 0.5 * 3.0)  # 2.0
        assert r.exit_bar == 1

    def test_be_arming_has_no_same_bar_lookahead(self) -> None:
        # bar0 reaches 1R (arms BE) AND dips to entry the same bar; BE must NOT
        # stop on bar0 (effective next bar) -> runner survives to TP on bar1.
        r = _run(_w([102, 106], [100, 101], [101, 105]), self.P)
        assert r.exit_bar == 1
        assert r.realized_r == pytest.approx(2.0)

    def test_time_stop_fires_before_max_hold(self) -> None:
        p = composite(tp_r=3.0, max_hold_bars=10, time_stop_bars=2)
        r = _run(_w([101] * 5, [99] * 5, [100.5] * 5), p)
        assert r.outcome == "expired"
        assert not r.partial_taken
        assert r.realized_r == pytest.approx(0.25)
        assert r.exit_bar == 1


class TestShortSymmetry:
    def test_short_tp_win(self) -> None:
        # short: entry 100, sl 102 (risk 2); TP@3R = 94
        r = _run(
            _w([100], [94], [95]),
            fixed(tp_r=3.0, max_hold_bars=10),
            direction="short",
            entry=100.0,
            sl_price=102.0,
        )
        assert r.outcome == "win"
        assert r.realized_r == pytest.approx(3.0)


class TestGuards:
    def test_zero_risk_raises(self) -> None:
        with pytest.raises(ValueError):
            _run(
                _w([100], [100], [100]),
                fixed(tp_r=3.0, max_hold_bars=10),
                sl_price=100.0,
            )

    def test_empty_window_raises(self) -> None:
        with pytest.raises(ValueError):
            _run(_w([], [], []), fixed(tp_r=3.0, max_hold_bars=10))


class TestFineBarResolution:
    # long: entry 100, sl 98 -> 1R=102, TP@3R=106, BE=100
    _TIE = _w([106.5], [97.5], [101])

    def test_no_callback_is_adverse_first_and_counts_the_tie(self) -> None:
        r = _run(self._TIE, fixed(tp_r=3.0, max_hold_bars=10))
        assert r.outcome == "loss"
        assert (r.ambiguous_bars, r.resolved_bars) == (1, 0)

    def test_callback_returning_none_stays_adverse_first(self) -> None:
        h, lo, c = self._TIE
        r = replay_exits(
            h,
            lo,
            c,
            direction="long",
            entry=100.0,
            sl_price=98.0,
            policy=fixed(tp_r=3.0, max_hold_bars=10),
            fine_bars=lambda i: None,
        )
        assert r.outcome == "loss"
        assert (r.ambiguous_bars, r.resolved_bars) == (1, 0)

    def test_fine_bars_resolve_stop_first(self) -> None:
        h, lo, c = self._TIE
        fine = _w([101, 106.5], [97.5, 99], [98, 106])
        r = replay_exits(
            h,
            lo,
            c,
            direction="long",
            entry=100.0,
            sl_price=98.0,
            policy=fixed(tp_r=3.0, max_hold_bars=10),
            fine_bars=lambda i: fine,
        )
        assert r.outcome == "loss"
        assert r.resolved_bars == 1

    def test_breakeven_arms_inside_the_fine_walk(self) -> None:
        # +1R first (partial + arm), then the next fine bar falls through BE.
        h, lo, c = _w([103], [97.5], [99])
        fine = _w([102.5, 101], [100.5, 97.5], [102, 99])
        pol = composite(tp_r=3.0, max_hold_bars=10, time_stop_bars=10)
        r = replay_exits(
            h,
            lo,
            c,
            direction="long",
            entry=100.0,
            sl_price=98.0,
            policy=pol,
            fine_bars=lambda i: fine,
        )
        assert r.outcome == "breakeven"
        assert r.realized_r == pytest.approx(0.5)  # 0.5*1R + 0.5*0R
        assert r.residual_ties == 0

    def test_residual_fine_tie_is_counted_and_adverse_first(self) -> None:
        h, lo, c = self._TIE
        fine = _w([106.5], [97.5], [101])
        r = replay_exits(
            h,
            lo,
            c,
            direction="long",
            entry=100.0,
            sl_price=98.0,
            policy=fixed(tp_r=3.0, max_hold_bars=10),
            fine_bars=lambda i: fine,
        )
        assert r.outcome == "loss"
        assert (r.resolved_bars, r.residual_ties) == (1, 1)
