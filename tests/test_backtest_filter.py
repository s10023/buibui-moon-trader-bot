"""Tests for the backtest filter in signal_lib and related helpers."""

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd

from analytics.backtest_lib import BacktestResult, Trade
from analytics.signal_config import BacktestFilterConfig
from analytics.signal_lib import _backtest_summary, _compute_backtest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 20) -> pd.DataFrame:
    """Minimal OHLCV DataFrame with n rows."""
    base_ms = 1_700_000_000_000
    interval = 4 * 3600 * 1000
    times = [base_ms + i * interval for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": times,
            "open": [100.0] * n,
            "high": [105.0] * n,
            "low": [95.0] * n,
            "close": [102.0] * n,
            "volume": [1000.0] * n,
        }
    )


def _make_result(win: int, loss: int) -> BacktestResult:
    """Build a BacktestResult with given closed trade counts."""
    result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
    for _ in range(win):
        result.trades.append(
            Trade(
                signal_time=1,
                entry_time=2,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=104.0,
                exit_price=104.0,
                outcome="win",
            )
        )
    for _ in range(loss):
        result.trades.append(
            Trade(
                signal_time=1,
                entry_time=2,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=104.0,
                exit_price=98.0,
                outcome="loss",
            )
        )
    return result


# ---------------------------------------------------------------------------
# _backtest_summary
# ---------------------------------------------------------------------------


class TestBacktestSummary:
    def _cfg(self, min_trades: int = 5, days: int = 90) -> BacktestFilterConfig:
        return BacktestFilterConfig(mode="soft", days=days, min_trades=min_trades)

    def test_single_strategy_sufficient_trades(self) -> None:
        result = _make_result(win=6, loss=4)
        summary = _backtest_summary({"fvg": result}, ["fvg"], self._cfg())
        assert "60%" in summary
        assert "10 trades" in summary
        assert "📊 Backtest 90d:" in summary

    def test_single_strategy_insufficient_trades(self) -> None:
        result = _make_result(win=2, loss=1)
        summary = _backtest_summary({"fvg": result}, ["fvg"], self._cfg(min_trades=20))
        assert "n/a" in summary
        assert "3 trades" in summary

    def test_single_strategy_none_result(self) -> None:
        summary = _backtest_summary({"fvg": None}, ["fvg"], self._cfg())
        assert "n/a" in summary

    def test_multiple_strategies(self) -> None:
        results = {
            "fvg": _make_result(win=6, loss=4),
            "bos": _make_result(win=7, loss=3),
        }
        summary = _backtest_summary(results, ["fvg", "bos"], self._cfg())
        assert "fvg" in summary
        assert "bos" in summary
        assert "·" in summary

    def test_days_shown_in_output(self) -> None:
        result = _make_result(win=5, loss=5)
        summary = _backtest_summary({"fvg": result}, ["fvg"], self._cfg(days=180))
        assert "180d" in summary


# ---------------------------------------------------------------------------
# _compute_backtest
# ---------------------------------------------------------------------------


class TestComputeBacktest:
    def test_returns_none_for_insufficient_data(self) -> None:
        tiny_df = _make_ohlcv(n=2)
        result = _compute_backtest(
            tiny_df, "fvg", None, None, "BTCUSDT", "4h", 0.02, 2.0
        )
        assert result is None

    def test_returns_none_for_unknown_strategy(self) -> None:
        df = _make_ohlcv(n=20)
        result = _compute_backtest(
            df, "nonexistent_strategy", None, None, "BTCUSDT", "4h", 0.02, 2.0
        )
        assert result is None

    def test_excludes_current_candle(self) -> None:
        """Detector should only see ohlcv[:-1], not the full df."""
        df = _make_ohlcv(n=20)
        captured: list[Any] = []

        def fake_detector(ohlcv: pd.DataFrame) -> pd.DataFrame:
            captured.append(len(ohlcv))
            return pd.DataFrame(
                columns=["open_time", "direction", "sl_price", "reason"]
            )

        with patch.dict(
            "analytics.signal_lib.SIGNAL_REGISTRY",
            {"fvg": {"detector": fake_detector, "confidence": 4}},
        ):
            with patch.dict(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {"fvg": MagicMock(requires_funding=False, requires_secondary=False)},
            ):
                _compute_backtest(df, "fvg", None, None, "BTCUSDT", "4h", 0.02, 2.0)

        assert captured == [len(df) - 1]  # detector saw n-1 candles

    def test_returns_backtest_result_on_success(self) -> None:
        df = _make_ohlcv(n=20)
        signals = pd.DataFrame(
            {
                "open_time": [df["open_time"].iloc[5]],
                "direction": ["long"],
                "sl_price": [95.0],
                "reason": ["fvg_long"],
            }
        )

        with patch.dict(
            "analytics.signal_lib.SIGNAL_REGISTRY",
            {"fvg": {"detector": lambda _: signals, "confidence": 4}},
        ):
            with patch.dict(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {"fvg": MagicMock(requires_funding=False, requires_secondary=False)},
            ):
                result = _compute_backtest(
                    df, "fvg", None, None, "BTCUSDT", "4h", 0.02, 2.0
                )

        assert result is not None
        assert isinstance(result, BacktestResult)

    def test_returns_none_if_detector_raises(self) -> None:
        df = _make_ohlcv(n=20)

        def bad_detector(_: pd.DataFrame) -> pd.DataFrame:
            raise ValueError("boom")

        with patch.dict(
            "analytics.signal_lib.SIGNAL_REGISTRY",
            {"fvg": {"detector": bad_detector, "confidence": 4}},
        ):
            with patch.dict(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {"fvg": MagicMock(requires_funding=False, requires_secondary=False)},
            ):
                result = _compute_backtest(
                    df, "fvg", None, None, "BTCUSDT", "4h", 0.02, 2.0
                )

        assert result is None


# ---------------------------------------------------------------------------
# BacktestFilterConfig loading
# ---------------------------------------------------------------------------


class TestBacktestFilterConfig:
    def test_defaults(self) -> None:
        cfg = BacktestFilterConfig()
        assert cfg.mode == "soft"
        assert cfg.days == 90
        assert cfg.min_trades == 20
        assert cfg.filter_threshold == 0.45

    def test_load_from_toml(self, tmp_path: Any) -> None:
        from analytics.signal_config import load_signal_config

        p = tmp_path / "cfg.toml"
        p.write_text(
            "[backtest]\nmode = 'hard'\ndays = 60\nmin_trades = 10\nfilter_threshold = 0.5\n"
        )
        cfg = load_signal_config(p)
        assert cfg.backtest.mode == "hard"
        assert cfg.backtest.days == 60
        assert cfg.backtest.min_trades == 10
        assert cfg.backtest.filter_threshold == 0.5

    def test_missing_backtest_section_uses_defaults(self, tmp_path: Any) -> None:
        from analytics.signal_config import load_signal_config

        p = tmp_path / "cfg.toml"
        p.write_text("telegram = true\n")
        cfg = load_signal_config(p)
        assert cfg.backtest.mode == "soft"
        assert cfg.backtest.days == 90
