"""Tests for portfolio.sizing — SizingConfig defaults/from_toml + pure sizing math."""

from pathlib import Path

import pytest

from portfolio.sizing import SizingConfig


def test_sizing_config_defaults() -> None:
    cfg = SizingConfig()
    assert cfg.capital == 10_000.0
    assert cfg.r_base == pytest.approx(0.0025)
    assert cfg.vol_target_annual == pytest.approx(0.20)
    assert cfg.vol_window_days == 30
    assert cfg.g_vol_min == 0.5 and cfg.g_vol_max == 1.5
    assert cfg.r_open_max == pytest.approx(0.02)
    assert cfg.r_cluster_max == pytest.approx(0.01)
    assert cfg.high_vol_risk_mult == 0.5
    assert cfg.apply_high_vol_halving is True
    assert cfg.annualization_days == pytest.approx(365.0)
    assert ("BTCUSDT", "ETHUSDT", "SOLUSDT") in cfg.clusters


def test_sizing_config_from_toml_overrides(tmp_path: Path) -> None:
    p = tmp_path / "p.toml"
    p.write_text(
        "[portfolio]\n"
        "capital = 25000\n"
        "r_base = 0.005\n"
        "vol_target_annual = 0.15\n"
        'clusters = [["BTCUSDT", "ETHUSDT"]]\n'
    )
    cfg = SizingConfig.from_toml(p)
    assert cfg.capital == 25_000.0
    assert cfg.r_base == pytest.approx(0.005)
    assert cfg.vol_target_annual == pytest.approx(0.15)
    assert cfg.clusters == (("BTCUSDT", "ETHUSDT"),)
    # unspecified keys keep defaults
    assert cfg.r_open_max == pytest.approx(0.02)


def test_sizing_config_from_toml_missing_block_is_defaults(
    tmp_path: Path,
) -> None:
    p = tmp_path / "empty.toml"
    p.write_text("[other]\nx = 1\n")
    cfg = SizingConfig.from_toml(p)
    assert cfg == SizingConfig()
