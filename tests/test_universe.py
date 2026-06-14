"""Tests for analytics/universe.py — research-universe config loader."""

from pathlib import Path

import pytest

from analytics.universe import DEFAULT_UNIVERSE_PATH, load_universe


def _write_toml(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


class TestLoadUniverse:
    def test_loads_symbols_list(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path / "universe.toml",
            '[universe]\nselected_at = "2026-06-12"\n'
            'criterion = "test"\nsymbols = ["BTCUSDT", "ETHUSDT"]\n',
        )
        assert load_universe(p) == ["BTCUSDT", "ETHUSDT"]

    def test_uppercases_strips_and_dedupes(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path / "universe.toml",
            '[universe]\nsymbols = [" btcusdt", "BTCUSDT", "ethusdt "]\n',
        )
        assert load_universe(p) == ["BTCUSDT", "ETHUSDT"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_universe(tmp_path / "nope.toml")

    def test_empty_symbols_raises(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path / "universe.toml", "[universe]\nsymbols = []\n")
        with pytest.raises(ValueError, match="symbols"):
            load_universe(p)

    def test_missing_universe_block_raises(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path / "universe.toml", "[other]\nx = 1\n")
        with pytest.raises(ValueError, match="symbols"):
            load_universe(p)

    def test_non_string_entry_raises(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path / "universe.toml", "[universe]\nsymbols = [42]\n")
        with pytest.raises(ValueError, match="invalid symbol"):
            load_universe(p)

    def test_default_path_points_at_committed_config(self) -> None:
        # The committed config/universe.toml must load through the default path.
        assert Path("config/universe.toml") == DEFAULT_UNIVERSE_PATH
        symbols = load_universe()
        assert "BTCUSDT" in symbols
        assert 10 <= len(symbols) <= 30
