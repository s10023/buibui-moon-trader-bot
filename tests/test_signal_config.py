"""Tests for analytics/signal_config.py — pure config loader."""

import tomllib
from pathlib import Path

import pytest

from analytics.signal_config import SignalWatchConfig, load_signal_config


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
        assert cfg.cooldown_seconds == 3600.0
        assert cfg.state_file == "signal_state.json"
        assert cfg.smt_pairs == {}
        assert cfg.day_filter is False
        assert cfg.smt_trend_filter == 1


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
cooldown_seconds = 7200.0
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
        assert cfg.cooldown_seconds == 7200.0
        assert cfg.state_file == "my_state.json"
        assert cfg.smt_pairs == {"BTCUSDT": "ETHUSDT", "ETHUSDT": "BTCUSDT"}

    def test_full_config_day_filter_and_smt_trend_filter(self, tmp_path: Path) -> None:
        content = "day_filter = true\nsmt_trend_filter = 0\n"
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.day_filter is True
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
        assert cfg.min_sl_pct == 0.01
