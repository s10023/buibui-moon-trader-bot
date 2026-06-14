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


from portfolio.sizing import (  # noqa: E402
    apply_caps,
    cluster_of,
    effective_risk_fraction,
    position_size,
    regime_multiplier,
    risk_per_unit,
    vol_governor,
)


def test_risk_per_unit_and_position_size() -> None:
    assert risk_per_unit(100.0, 95.0) == pytest.approx(5.0)
    assert risk_per_unit(95.0, 100.0) == pytest.approx(5.0)
    # risk_capital 25 at 5/unit => 5 units
    assert position_size(25.0, 100.0, 95.0) == pytest.approx(5.0)
    assert position_size(25.0, 100.0, 100.0) == 0.0  # zero risk => no position


def test_vol_governor_clamps_and_cold_start() -> None:
    cfg = SizingConfig()
    # realized vol == target => g_vol 1.0
    assert vol_governor(0.20, cfg) == pytest.approx(1.0)
    # hot book (realized 0.40 vs target 0.20) => shrink, clamped at floor 0.5
    assert vol_governor(0.40, cfg) == pytest.approx(0.5)
    # cold book (realized 0.05) => expand, clamped at ceiling 1.5
    assert vol_governor(0.05, cfg) == pytest.approx(1.5)
    # undefined / non-positive vol => neutral 1.0 (cold start)
    assert vol_governor(0.0, cfg) == pytest.approx(1.0)
    assert vol_governor(float("nan"), cfg) == pytest.approx(1.0)


def test_regime_multiplier() -> None:
    cfg = SizingConfig()
    assert regime_multiplier("high_vol", cfg) == pytest.approx(0.5)
    assert regime_multiplier("trend", cfg) == pytest.approx(1.0)
    assert regime_multiplier(None, cfg) == pytest.approx(1.0)
    off = SizingConfig(apply_high_vol_halving=False)
    assert regime_multiplier("high_vol", off) == pytest.approx(1.0)


def test_effective_risk_fraction() -> None:
    cfg = SizingConfig()  # r_base 0.0025
    assert effective_risk_fraction(cfg, g_vol=1.0, g_regime=1.0) == pytest.approx(
        0.0025
    )
    assert effective_risk_fraction(cfg, g_vol=1.5, g_regime=0.5) == pytest.approx(
        0.0025 * 1.5 * 0.5
    )


def test_cluster_of() -> None:
    cfg = SizingConfig()
    # majors share one cluster id; non-majors get their own singleton
    assert cluster_of("BTCUSDT", cfg) == cluster_of("ETHUSDT", cfg)
    assert cluster_of("DOGEUSDT", cfg) == "DOGEUSDT"
    assert cluster_of("BTCUSDT", cfg) != "BTCUSDT"


def test_apply_caps_scales_down_to_fit() -> None:
    cfg = SizingConfig()  # r_open_max 0.02, r_cluster_max 0.01
    # plenty of headroom => unchanged
    assert apply_caps(
        0.0025, symbol="BTCUSDT", open_risk_total=0.0, open_risk_cluster=0.0, cfg=cfg
    ) == pytest.approx(0.0025)
    # cluster nearly full => scaled to remaining headroom
    assert apply_caps(
        0.0025,
        symbol="BTCUSDT",
        open_risk_total=0.005,
        open_risk_cluster=0.009,
        cfg=cfg,
    ) == pytest.approx(0.001)
    # total cap binds before cluster
    assert apply_caps(
        0.0025,
        symbol="DOGEUSDT",
        open_risk_total=0.0195,
        open_risk_cluster=0.0,
        cfg=cfg,
    ) == pytest.approx(0.0005)


def test_apply_caps_skip_floor() -> None:
    cfg = SizingConfig()  # skip_floor_frac 0.1 => floor 0.00025
    # headroom below floor => skip (0.0)
    assert (
        apply_caps(
            0.0025,
            symbol="BTCUSDT",
            open_risk_total=0.0199,
            open_risk_cluster=0.0,
            cfg=cfg,
        )
        == 0.0
    )
