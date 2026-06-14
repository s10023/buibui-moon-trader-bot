"""Tests for portfolio.book — caps, concurrency, dual-basis daily MTM curves."""

import numpy as np
import pytest

from portfolio.book import LedgerTrade, PaperBook
from portfolio.sizing import SizingConfig

_DAY = 86_400_000


def _grid(n_days: int) -> np.ndarray:
    return np.arange(0, n_days * _DAY, _DAY, dtype=np.int64)


def _flat_close(n_days: int, price: float) -> np.ndarray:
    return np.full(n_days, price, dtype=np.float64)


def test_same_day_trade_banks_realized_on_exit_day() -> None:
    cfg = SizingConfig()  # r_base 0.0025, capital 10_000
    grid = _grid(5)
    close = {"BTCUSDT": _flat_close(5, 100.0)}
    # entry and exit both on day 2; win +2R
    trades = [
        LedgerTrade(
            signal_id="s1",
            symbol="BTCUSDT",
            tf="15m",
            strategy="fvg",
            direction="long",
            entry_ts_ms=2 * _DAY + 1,
            exit_ts_ms=2 * _DAY + 5,
            entry_price=100.0,
            sl_price=95.0,
            outcome="win",
            realized_r=2.0,
        )
    ]
    book = PaperBook(cfg, grid, close, regime_by_signal=None)
    res = book.run(trades)
    # risk_capital_fixed = 0.0025 * 10_000 = 25; pnl = 25 * 2 = 50, banked day 2+
    assert res.pnl_fixed[1] == pytest.approx(0.0)
    assert res.pnl_fixed[2] == pytest.approx(50.0)
    assert res.pnl_fixed[4] == pytest.approx(50.0)
    assert len(res.sized) == 1 and not res.skipped


def test_multi_day_long_marks_to_market() -> None:
    cfg = SizingConfig()
    grid = _grid(6)
    # price rises 100 -> 110 over the hold; risk_per_unit = 5
    close = {"BTCUSDT": np.array([100, 100, 105, 110, 110, 110], dtype=np.float64)}
    trades = [
        LedgerTrade(
            signal_id="s1",
            symbol="BTCUSDT",
            tf="1h",
            strategy="bos",
            direction="long",
            entry_ts_ms=1 * _DAY + 1,
            exit_ts_ms=3 * _DAY + 1,
            entry_price=100.0,
            sl_price=95.0,
            outcome="win",
            realized_r=2.0,
        )
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    rc = 25.0  # 0.0025 * 10_000
    # day1 mark: (100-100)/5 = 0R -> 0; day2 mark: (105-100)/5 = +1R -> +25
    assert res.pnl_fixed[1] == pytest.approx(0.0)
    assert res.pnl_fixed[2] == pytest.approx(rc * 1.0)
    # exit day 3 snaps to realized +2R -> +50, held thereafter
    assert res.pnl_fixed[3] == pytest.approx(rc * 2.0)
    assert res.pnl_fixed[5] == pytest.approx(rc * 2.0)


def test_short_marks_invert_sign() -> None:
    cfg = SizingConfig()
    grid = _grid(4)
    close = {"BTCUSDT": np.array([100, 100, 95, 90], dtype=np.float64)}
    trades = [
        LedgerTrade(
            signal_id="s1",
            symbol="BTCUSDT",
            tf="1h",
            strategy="bos",
            direction="short",
            entry_ts_ms=1 * _DAY + 1,
            exit_ts_ms=3 * _DAY + 1,
            entry_price=100.0,
            sl_price=105.0,
            outcome="win",
            realized_r=2.0,
        )
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    # short: day2 (100-95)/5 = +1R favorable -> +25
    assert res.pnl_fixed[2] == pytest.approx(25.0)


def test_cluster_cap_scales_down_third_major() -> None:
    cfg = (
        SizingConfig()
    )  # r_cluster_max 0.01; three majors at 0.0025 each = 0.0075 < 0.01
    grid = _grid(3)
    close = {s: _flat_close(3, 100.0) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")}
    # four concurrent majors: 4th must be capped (0.0075 used, headroom 0.0025)
    trades = []
    for i, sym in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT", "BTCUSDT")):
        trades.append(
            LedgerTrade(
                signal_id=f"s{i}",
                symbol=sym,
                tf="1h",
                strategy="bos",
                direction="long",
                entry_ts_ms=1 * _DAY + i,
                exit_ts_ms=2 * _DAY,
                entry_price=100.0,
                sl_price=95.0,
                outcome="loss",
                realized_r=-1.0,
            )
        )
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    # first three at full r_eff, fourth scaled to remaining cluster headroom 0.0025 -> 0.0025
    # (cluster headroom exactly equals r_base here, so it stays full but caps still applied)
    assert len(res.sized) == 4
    assert res.sized[3].r_eff == pytest.approx(0.0025)


def test_open_risk_cap_skips_when_headroom_below_floor() -> None:
    cfg = SizingConfig(clusters=())  # no clusters -> only the 2% total cap binds
    grid = _grid(3)
    close = {f"C{i}USDT": _flat_close(3, 100.0) for i in range(10)}
    # 8 concurrent at 0.0025 = 0.02 -> total cap full; 9th has 0 headroom -> skip
    trades = []
    for i in range(9):
        trades.append(
            LedgerTrade(
                signal_id=f"s{i}",
                symbol=f"C{i}USDT",
                tf="1h",
                strategy="bos",
                direction="long",
                entry_ts_ms=1 * _DAY + i,
                exit_ts_ms=2 * _DAY,
                entry_price=100.0,
                sl_price=95.0,
                outcome="loss",
                realized_r=-1.0,
            )
        )
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    assert len(res.sized) == 8
    assert res.skipped and res.skipped[-1][0] == "s8"


def test_zero_risk_trade_skipped() -> None:
    cfg = SizingConfig()
    grid = _grid(3)
    close = {"BTCUSDT": _flat_close(3, 100.0)}
    trades = [
        LedgerTrade(
            signal_id="s1",
            symbol="BTCUSDT",
            tf="1h",
            strategy="bos",
            direction="long",
            entry_ts_ms=1 * _DAY,
            exit_ts_ms=2 * _DAY,
            entry_price=100.0,
            sl_price=100.0,
            outcome="loss",
            realized_r=-1.0,
        )
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    assert not res.sized and res.skipped[0] == ("s1", "zero_risk")


def test_compounding_curve_diverges_from_fixed_after_pnl() -> None:
    cfg = SizingConfig()
    grid = _grid(4)
    close = {"BTCUSDT": _flat_close(4, 100.0)}
    # two sequential wins; second sizes off grown equity on the comp basis
    trades = [
        LedgerTrade(
            "s1",
            "BTCUSDT",
            "15m",
            "fvg",
            "long",
            0 * _DAY + 1,
            0 * _DAY + 2,
            100.0,
            95.0,
            "win",
            4.0,
        ),
        LedgerTrade(
            "s2",
            "BTCUSDT",
            "15m",
            "fvg",
            "long",
            2 * _DAY + 1,
            2 * _DAY + 2,
            100.0,
            95.0,
            "win",
            4.0,
        ),
    ]
    res = PaperBook(cfg, grid, close, regime_by_signal=None).run(trades)
    # comp 2nd trade risk_capital > fixed because equity grew after trade 1
    assert res.pnl_comp[-1] > res.pnl_fixed[-1]
