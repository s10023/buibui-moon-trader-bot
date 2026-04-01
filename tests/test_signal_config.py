"""Tests for analytics/signal_config.py — pure config loader."""

import tomllib
from pathlib import Path

import pytest

from analytics.signal_config import (
    SignalWatchConfig,
    StrategyOverride,
    SymbolOverride,
    _day_filter_to_weekdays,
    load_signal_config,
)


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "signal_watch.toml"
    p.write_text(content)
    return p


class TestSignalWatchConfigDefaults:
    def test_defaults(self) -> None:
        cfg = SignalWatchConfig()
        assert cfg.symbols is None
        assert cfg.timeframes == ["4h"]
        assert cfg.strategies is None
        assert cfg.telegram is False
        assert cfg.min_sl_pct == 0.0
        assert cfg.tp_r == 2.0
        assert cfg.sl_pct == 0.02
        assert cfg.state_file == "signal_state.json"
        assert cfg.smt_pairs == {}
        assert cfg.day_filter == "off"
        assert cfg.smt_trend_filter == 1


class TestDayFilterToWeekdays:
    def test_off_returns_none(self) -> None:
        assert _day_filter_to_weekdays("off") is None

    def test_weekdays_returns_mon_fri(self) -> None:
        assert _day_filter_to_weekdays("weekdays") == [0, 1, 2, 3, 4]

    def test_tue_thu_returns_tue_wed_thu(self) -> None:
        assert _day_filter_to_weekdays("tue_thu") == [1, 2, 3]

    def test_unknown_string_returns_none(self) -> None:
        assert _day_filter_to_weekdays("unknown") is None


class TestLoadSignalConfig:
    def test_minimal_config(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, 'timeframes = ["15m", "1h"]\ntelegram = true\n')
        cfg = load_signal_config(p)
        assert cfg.timeframes == ["15m", "1h"]
        assert cfg.telegram is True
        # unset fields fall back to defaults
        assert cfg.symbols is None
        assert cfg.min_sl_pct == 0.0

    def test_full_config(self, tmp_path: Path) -> None:
        content = """
symbols = ["BTCUSDT", "ETHUSDT"]
timeframes = ["15m", "1h", "4h", "1d"]
strategies = ["fvg", "bos"]
telegram = true
min_sl_pct = 0.01
tp_r = 3.0
sl_pct = 0.015
state_file = "my_state.json"

[smt_pairs]
BTCUSDT = "ETHUSDT"
ETHUSDT = "BTCUSDT"
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.symbols == ["BTCUSDT", "ETHUSDT"]
        assert cfg.timeframes == ["15m", "1h", "4h", "1d"]
        assert cfg.strategies == ["fvg", "bos"]
        assert cfg.telegram is True
        assert cfg.min_sl_pct == 0.01
        assert cfg.tp_r == 3.0
        assert cfg.sl_pct == 0.015
        assert cfg.state_file == "my_state.json"
        assert cfg.smt_pairs == {"BTCUSDT": "ETHUSDT", "ETHUSDT": "BTCUSDT"}

    def test_full_config_day_filter_string_modes(self, tmp_path: Path) -> None:
        for mode in ("off", "weekdays", "tue_thu"):
            content = f'day_filter = "{mode}"\n'
            p = _write_toml(tmp_path, content)
            cfg = load_signal_config(p)
            assert cfg.day_filter == mode

    def test_full_config_smt_trend_filter(self, tmp_path: Path) -> None:
        content = "smt_trend_filter = 0\n"
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.smt_trend_filter == 0

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_signal_config(tmp_path / "nonexistent.toml")

    def test_invalid_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.toml"
        p.write_text("timeframes = [broken")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_signal_config(p)

    def test_invalid_smt_pairs_not_table(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, 'smt_pairs = "not a table"\n')
        with pytest.raises(ValueError, match="smt_pairs must be a TOML table"):
            load_signal_config(p)

    def test_accepts_path_object(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, "telegram = true\n")
        cfg = load_signal_config(p)
        assert cfg.telegram is True

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, "telegram = false\n")
        cfg = load_signal_config(str(p))
        assert cfg.telegram is False

    def test_default_config_file_is_valid(self) -> None:
        """The committed config/signal_watch.toml must parse without errors."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        assert cfg.timeframes == ["15m", "1h", "4h", "1d"]
        assert cfg.telegram is True
        assert cfg.min_sl_pct == 0.005

    def test_strategy_timeframes_parsed(self, tmp_path: Path) -> None:
        content = '[strategy_timeframes]\ntrend_day = ["4h", "1d"]\nmarubozu = ["1d"]\n'
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.strategy_timeframes == {
            "trend_day": ["4h", "1d"],
            "marubozu": ["1d"],
        }

    def test_strategy_timeframes_defaults_to_empty(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, "telegram = true\n")
        cfg = load_signal_config(p)
        assert cfg.strategy_timeframes == {}

    def test_invalid_strategy_timeframes_not_table(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, 'strategy_timeframes = "not a table"\n')
        with pytest.raises(
            ValueError, match="strategy_timeframes must be a TOML table"
        ):
            load_signal_config(p)

    def test_signal_watch_toml_has_trend_day_restriction(self) -> None:
        """signal_watch.toml must declare trend_day restricted to 4h/1d via TOML."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        assert "trend_day" in cfg.strategy_timeframes
        assert cfg.strategy_timeframes["trend_day"] == ["4h", "1d"]

    def test_preset_configs_are_valid(self) -> None:
        """All three named preset TOML files must parse without errors."""
        config_dir = Path(__file__).parent.parent / "config"
        for name in ("scalping.toml", "swing.toml", "conservative.toml"):
            cfg = load_signal_config(config_dir / name)
            assert cfg.timeframes, f"{name}: timeframes must not be empty"
            assert cfg.strategies, f"{name}: strategies must not be empty"


class TestBacktestFilterConfigPerTf:
    def test_effective_min_trades_uses_per_tf_override(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = BacktestFilterConfig(min_trades=20, min_trades_per_tf={"4h": 10, "1d": 5})
        assert cfg.effective_min_trades("4h") == 10
        assert cfg.effective_min_trades("1d") == 5

    def test_effective_min_trades_falls_back_to_global(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = BacktestFilterConfig(min_trades=20, min_trades_per_tf={"4h": 10})
        assert cfg.effective_min_trades("1h") == 20

    def test_effective_min_trades_empty_per_tf(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = BacktestFilterConfig(min_trades=15)
        assert cfg.effective_min_trades("15m") == 15
        assert cfg.effective_min_trades("1d") == 15

    def test_load_signal_config_parses_per_tf_keys(self, tmp_path: Path) -> None:
        content = """
[backtest]
mode = "soft"
min_trades = 20
min_trades_15m = 30
min_trades_4h = 10
min_trades_1d = 5
"""
        p = tmp_path / "w.toml"
        p.write_text(content)
        cfg = load_signal_config(p)
        assert cfg.backtest.min_trades_per_tf == {"15m": 30, "4h": 10, "1d": 5}
        assert cfg.backtest.effective_min_trades("15m") == 30
        assert cfg.backtest.effective_min_trades("4h") == 10
        assert cfg.backtest.effective_min_trades("1d") == 5
        assert cfg.backtest.effective_min_trades("1h") == 20  # falls back to global

    def test_signal_watch_toml_has_per_tf_min_trades(self) -> None:
        """The committed signal_watch.toml must define per-TF min_trades overrides."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # [backtest] section uses directional counts (longs or shorts only)
        # recalibrated from backtest_runs DB p25 directional counts (200d window)
        assert cfg.backtest.effective_min_trades("15m") == 20
        assert cfg.backtest.effective_min_trades("4h") == 5
        assert cfg.backtest.effective_min_trades("1d") == 2


class TestStrategyOverride:
    def test_effective_tp_r_tf_specific(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "bos": StrategyOverride(tp_r=2.5, tp_r_per_tf={"4h": 2.5, "1h": 2.0}),
            },
        )
        assert cfg.effective_tp_r("bos", "BTCUSDT", "4h") == 2.5
        assert cfg.effective_tp_r("bos", "BTCUSDT", "1h") == 2.0

    def test_effective_tp_r_strategy_wide(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={"engulfing": StrategyOverride(tp_r=3.0)},
        )
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "1h") == 3.0
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "4h") == 3.0

    def test_effective_tp_r_falls_back_to_global(self) -> None:
        cfg = SignalWatchConfig(tp_r=2.0, strategy_params={})
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "1h") == 2.0

    def test_effective_tp_r_tf_specific_overrides_strategy_wide(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "pin_bar": StrategyOverride(tp_r=3.0, tp_r_per_tf={"4h": 2.5})
            },
        )
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "4h") == 2.5  # TF-specific wins
        assert (
            cfg.effective_tp_r("pin_bar", "BTCUSDT", "1h") == 3.0
        )  # strategy-wide fallback
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "1d") == 3.0

    def test_effective_sl_pct_tf_specific(self) -> None:
        cfg = SignalWatchConfig(
            sl_pct=0.02,
            strategy_params={
                "orb": StrategyOverride(sl_pct_per_tf={"1h": 0.015}),
            },
        )
        assert cfg.effective_sl_pct("orb", "BTCUSDT", "1h") == 0.015
        assert cfg.effective_sl_pct("orb", "BTCUSDT", "4h") == 0.02  # global fallback

    def test_effective_sl_pct_no_override(self) -> None:
        cfg = SignalWatchConfig(sl_pct=0.025)
        assert cfg.effective_sl_pct("fvg", "BTCUSDT", "1h") == 0.025

    def test_load_strategy_params_sub_table(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.engulfing]
tp_r = 3.0

[strategy_params.bos]
tp_r_4h = 2.5
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert "engulfing" in cfg.strategy_params
        assert cfg.strategy_params["engulfing"].tp_r == 3.0
        assert cfg.strategy_params["engulfing"].tp_r_per_tf == {}
        assert "bos" in cfg.strategy_params
        assert cfg.strategy_params["bos"].tp_r is None
        assert cfg.strategy_params["bos"].tp_r_per_tf == {"4h": 2.5}

    def test_load_strategy_params_inline_table(self, tmp_path: Path) -> None:
        content = "[strategy_params]\nengulfing = {tp_r = 3.0}\n"
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.strategy_params["engulfing"].tp_r == 3.0

    def test_load_strategy_params_defaults_to_empty(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, "telegram = true\n")
        cfg = load_signal_config(p)
        assert cfg.strategy_params == {}

    def test_signal_watch_weekdays_toml_strategy_params_parsed(self) -> None:
        """signal_watch_weekdays.toml strategy_params must be applied."""
        cfg_path = (
            Path(__file__).parent.parent / "config" / "signal_watch_weekdays.toml"
        )
        cfg = load_signal_config(cfg_path)
        # engulfing: per-TF overrides (updated during TP sweep)
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "1h") == 4.0
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "4h") == 3.5
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "1d") == 3.0
        # strategy not in params falls back to global
        assert cfg.effective_tp_r("funding_reversion", "BTCUSDT", "1h") == cfg.tp_r

    def test_signal_watch_toml_strategy_params_parsed(self) -> None:
        """signal_watch.toml (tue_thu) strategy_params (F5 WFO findings) must be applied."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # engulfing: TF-specific (15m=4.0, 1h=4.0, 4h=3.5); no per-symbol for BTC
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "1h") == 4.0
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "4h") == 3.5
        # pin_bar: TF-specific (15m=4.5, 1h=3.5, 4h=4.5)
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "15m") == 4.5
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "1h") == 3.5
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "4h") == 4.5
        # hammer_hanging_man: strategy-wide 4.0, 1h override 5.0
        assert cfg.effective_tp_r("hammer_hanging_man", "BTCUSDT", "15m") == 4.0
        assert cfg.effective_tp_r("hammer_hanging_man", "BTCUSDT", "1h") == 5.0
        # doji: SOLUSDT 15m falls back to TF-level 4.0 (no symbol override for SOL)
        assert cfg.effective_tp_r("doji", "SOLUSDT", "15m") == 4.0
        assert cfg.effective_tp_r("doji", "BTCUSDT", "1h") == 3.5
        # morning_evening_star: TF-specific (15m=3.5, 1h=4.0, 4h=5.0), 1d falls back
        assert cfg.effective_tp_r("morning_evening_star", "BTCUSDT", "15m") == 3.5
        assert cfg.effective_tp_r("morning_evening_star", "BTCUSDT", "1h") == 4.0
        assert cfg.effective_tp_r("morning_evening_star", "BTCUSDT", "4h") == 5.0
        # trend_day: 4h override 5.0, 1d falls back to global 3.0
        assert cfg.effective_tp_r("trend_day", "BTCUSDT", "4h") == 5.0
        assert cfg.effective_tp_r("trend_day", "BTCUSDT", "1d") == 3.0
        assert cfg.effective_tp_r("orb", "BTCUSDT", "1h") == 3.0
        # strategy not in params falls back to global
        assert cfg.effective_tp_r("fvg", "BTCUSDT", "1h") == cfg.tp_r


class TestEffectiveTpRPerSymbol:
    def test_symbol_tf_override_wins_over_strategy_tf(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "doji": StrategyOverride(
                    tp_r_per_tf={"15m": 4.0},
                    per_symbol={
                        "ETHUSDT": SymbolOverride(tp_r_per_tf={"15m": 4.5}),
                    },
                )
            },
        )
        # ETHUSDT symbol+TF override (4.5) wins over strategy-level TF override (4.0)
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        # BTCUSDT has no symbol override — falls back to strategy+TF level
        assert cfg.effective_tp_r("doji", "BTCUSDT", "15m") == 4.0

    def test_symbol_wide_override_wins_over_strategy_tf(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "hammer_hanging_man": StrategyOverride(
                    tp_r=4.0,
                    per_symbol={
                        "ETHUSDT": SymbolOverride(tp_r=5.0),
                    },
                )
            },
        )
        # ETHUSDT symbol-wide (5.0) wins
        assert cfg.effective_tp_r("hammer_hanging_man", "ETHUSDT", "1h") == 5.0
        assert cfg.effective_tp_r("hammer_hanging_man", "ETHUSDT", "15m") == 5.0
        # BTCUSDT falls back to strategy-wide
        assert cfg.effective_tp_r("hammer_hanging_man", "BTCUSDT", "1h") == 4.0

    def test_symbol_tf_wins_over_symbol_wide(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "doji": StrategyOverride(
                    per_symbol={
                        "ETHUSDT": SymbolOverride(tp_r=4.0, tp_r_per_tf={"15m": 4.5}),
                    },
                )
            },
        )
        # symbol+TF (4.5) wins over symbol-wide (4.0)
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        # Other TF falls back to symbol-wide
        assert cfg.effective_tp_r("doji", "ETHUSDT", "1h") == 4.0

    def test_unknown_symbol_falls_through_to_strategy_level(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "doji": StrategyOverride(
                    tp_r=3.0,
                    tp_r_per_tf={"15m": 4.0},
                    per_symbol={
                        "ETHUSDT": SymbolOverride(tp_r_per_tf={"15m": 4.5}),
                    },
                )
            },
        )
        # Unknown symbol SOLUSDT — no symbol override → falls to TF-level (4.0)
        assert cfg.effective_tp_r("doji", "SOLUSDT", "15m") == 4.0
        # Unknown symbol, unknown TF → strategy-wide (3.0)
        assert cfg.effective_tp_r("doji", "SOLUSDT", "1h") == 3.0

    def test_toml_round_trip_per_symbol(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.doji]
tp_r = 3.0
tp_r_15m = 4.0

[strategy_params.doji.ETHUSDT]
tp_r_15m = 4.5

[strategy_params.doji.BTCUSDT]
tp_r_15m = 3.5
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        # ETHUSDT symbol+TF override
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        # BTCUSDT symbol+TF override
        assert cfg.effective_tp_r("doji", "BTCUSDT", "15m") == 3.5
        # SOLUSDT — no symbol override → TF-level fallback
        assert cfg.effective_tp_r("doji", "SOLUSDT", "15m") == 4.0
        # ETHUSDT 1h — no symbol TF override, no symbol-wide → strategy-wide (3.0)
        assert cfg.effective_tp_r("doji", "ETHUSDT", "1h") == 3.0

    def test_signal_watch_toml_per_symbol_overrides_parsed(self) -> None:
        """signal_watch.toml per-symbol overrides (F5 sweep findings) must be applied."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # doji: BTCUSDT 15m → 3.5, ETHUSDT 15m → 4.5, SOLUSDT → TF fallback 4.0
        assert cfg.effective_tp_r("doji", "BTCUSDT", "15m") == 3.5
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        assert cfg.effective_tp_r("doji", "SOLUSDT", "15m") == 4.0
        # hammer_hanging_man: ETHUSDT 1h → 5.0 (symbol override), BTCUSDT → TF-level 5.0
        assert cfg.effective_tp_r("hammer_hanging_man", "ETHUSDT", "1h") == 5.0
        assert cfg.effective_tp_r("hammer_hanging_man", "BTCUSDT", "1h") == 5.0
        # fib_golden_zone: ETHUSDT 1h → 4.5 (note: only 4h is active via strategy_timeframes)
        assert cfg.effective_tp_r("fib_golden_zone", "ETHUSDT", "1h") == 4.5
        # morning_evening_star: ETHUSDT 15m → 4.0, BTCUSDT 15m → 3.5
        assert cfg.effective_tp_r("morning_evening_star", "ETHUSDT", "15m") == 4.0
        assert cfg.effective_tp_r("morning_evening_star", "BTCUSDT", "15m") == 3.5
        # engulfing: SOLUSDT 4h → 4.0, BTCUSDT 4h → strategy-TF 3.5
        assert cfg.effective_tp_r("engulfing", "SOLUSDT", "4h") == 4.0
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "4h") == 3.5
