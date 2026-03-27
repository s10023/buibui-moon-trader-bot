"""Tests for analytics/backtest_config.py."""

import tomllib
from pathlib import Path

import pytest

from analytics.backtest_config import BacktestSweepConfig, load_backtest_config

_MINIMAL_TOML = """\
symbols    = ["BTCUSDT", "ETHUSDT"]
timeframes = ["1h", "4h"]
strategies = ["fvg", "bos"]
days       = 60
sl_pct     = 0.015
tp_r       = 3.0
min_trades = 10
"""

_SMT_TOML = """\
symbols = ["BTCUSDT", "ETHUSDT"]

[smt_pairs]
BTCUSDT = "ETHUSDT"
ETHUSDT = "BTCUSDT"
"""

_PARTIAL_TOML = """\
symbols = ["BTCUSDT"]
"""


class TestBacktestSweepConfigDefaults:
    def test_load_defaults(self) -> None:
        cfg = BacktestSweepConfig()
        assert cfg.symbols is None
        assert cfg.timeframes == ["4h"]
        assert cfg.strategies is None
        assert cfg.days == 90
        assert cfg.sl_pct == 0.02
        assert cfg.tp_r == 2.0
        assert cfg.min_trades == 20
        assert cfg.smt_pairs == {}
        assert cfg.day_filter == "off"
        assert cfg.smt_trend_filter == 1


class TestLoadBacktestConfig:
    def test_load_from_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text(_MINIMAL_TOML)
        cfg = load_backtest_config(p)
        assert cfg.symbols == ["BTCUSDT", "ETHUSDT"]
        assert cfg.timeframes == ["1h", "4h"]
        assert cfg.strategies == ["fvg", "bos"]
        assert cfg.days == 60
        assert cfg.sl_pct == 0.015
        assert cfg.tp_r == 3.0
        assert cfg.min_trades == 10

    def test_load_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_backtest_config("/nonexistent/path/cfg.toml")

    def test_load_invalid_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.toml"
        p.write_text("symbols = [unclosed")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_backtest_config(p)

    def test_load_smt_pairs(self, tmp_path: Path) -> None:
        p = tmp_path / "smt.toml"
        p.write_text(_SMT_TOML)
        cfg = load_backtest_config(p)
        assert cfg.smt_pairs == {"BTCUSDT": "ETHUSDT", "ETHUSDT": "BTCUSDT"}

    def test_load_partial_toml_uses_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "partial.toml"
        p.write_text(_PARTIAL_TOML)
        cfg = load_backtest_config(p)
        assert cfg.symbols == ["BTCUSDT"]
        assert cfg.timeframes == ["4h"]
        assert cfg.strategies is None
        assert cfg.days == 90
        assert cfg.min_trades == 20
        assert cfg.smt_pairs == {}
        assert cfg.day_filter == "off"
        assert cfg.smt_trend_filter == 1

    def test_load_day_filter_string_modes(self, tmp_path: Path) -> None:
        for mode in ("off", "weekdays", "tue_thu"):
            p = tmp_path / f"cfg_{mode}.toml"
            p.write_text(f'symbols = []\nday_filter = "{mode}"\nsmt_trend_filter = 0\n')
            cfg = load_backtest_config(p)
            assert cfg.day_filter == mode
            assert cfg.smt_trend_filter == 0

    def test_load_per_tf_min_trades(self, tmp_path: Path) -> None:
        content = "min_trades = 20\nmin_trades_15m = 30\nmin_trades_4h = 10\nmin_trades_1d = 5\n"
        p = tmp_path / "cfg.toml"
        p.write_text(content)
        cfg = load_backtest_config(p)
        assert cfg.min_trades_per_tf == {"15m": 30, "4h": 10, "1d": 5}
        assert cfg.effective_min_trades("15m") == 30
        assert cfg.effective_min_trades("4h") == 10
        assert cfg.effective_min_trades("1d") == 5
        assert cfg.effective_min_trades("1h") == 20  # falls back to global

    def test_effective_min_trades_no_overrides(self) -> None:
        cfg = BacktestSweepConfig(min_trades=15)
        assert cfg.effective_min_trades("15m") == 15
        assert cfg.effective_min_trades("4h") == 15


class TestStrategyOverrideBacktestConfig:
    def test_effective_tp_r_tf_specific(self) -> None:
        from analytics.backtest_config import StrategyOverride

        cfg = BacktestSweepConfig(
            tp_r=2.0,
            strategy_params={"bos": StrategyOverride(tp_r_per_tf={"4h": 2.5})},
        )
        assert cfg.effective_tp_r("bos", "4h") == 2.5
        assert cfg.effective_tp_r("bos", "1h") == 2.0  # global fallback

    def test_effective_tp_r_strategy_wide(self) -> None:
        from analytics.backtest_config import StrategyOverride

        cfg = BacktestSweepConfig(
            tp_r=2.0,
            strategy_params={"engulfing": StrategyOverride(tp_r=3.0)},
        )
        assert cfg.effective_tp_r("engulfing", "1h") == 3.0
        assert cfg.effective_tp_r("engulfing", "4h") == 3.0
        assert cfg.effective_tp_r("fvg", "1h") == 2.0  # not in params

    def test_effective_tp_r_tf_beats_strategy_wide(self) -> None:
        from analytics.backtest_config import StrategyOverride

        cfg = BacktestSweepConfig(
            tp_r=2.0,
            strategy_params={
                "pin_bar": StrategyOverride(tp_r=3.0, tp_r_per_tf={"4h": 2.5})
            },
        )
        assert cfg.effective_tp_r("pin_bar", "4h") == 2.5
        assert cfg.effective_tp_r("pin_bar", "1h") == 3.0

    def test_effective_sl_pct_override(self) -> None:
        from analytics.backtest_config import StrategyOverride

        cfg = BacktestSweepConfig(
            sl_pct=0.02,
            strategy_params={"orb": StrategyOverride(sl_pct=0.015)},
        )
        assert cfg.effective_sl_pct("orb", "1h") == 0.015
        assert cfg.effective_sl_pct("fvg", "1h") == 0.02

    def test_load_strategy_params_sub_table(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.engulfing]
tp_r = 3.0

[strategy_params.bos]
tp_r_4h = 2.5
"""
        p = tmp_path / "cfg.toml"
        p.write_text(content)
        cfg = load_backtest_config(p)
        assert cfg.strategy_params["engulfing"].tp_r == 3.0
        assert cfg.strategy_params["bos"].tp_r_per_tf == {"4h": 2.5}

    def test_load_strategy_params_defaults_to_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text("symbols = ['BTCUSDT']\n")
        cfg = load_backtest_config(p)
        assert cfg.strategy_params == {}
