from __future__ import annotations

import pytest

from analytics.forecast.config import ForecastConfig


def test_defaults() -> None:
    cfg = ForecastConfig()
    assert cfg.speeds == (
        (8, 32, 5.3),
        (16, 64, 3.75),
        (32, 128, 2.65),
        (64, 256, 1.91),
    )
    assert cfg.vol_span == 32
    assert cfg.fdm == 1.25
    assert cfg.cap == 20.0
    assert cfg.vol_target_annual == 0.20
    assert cfg.fee_pct == 0.0005
    assert cfg.slippage_pct == 0.0002
    assert cfg.gov_window == 64
    assert cfg.g_min == 0.5
    assert cfg.g_max == 1.5
    assert cfg.annualization_days == 365.0


def test_min_history_is_longest_slow_plus_vol_span() -> None:
    cfg = ForecastConfig()
    # longest slow span (256) + vol span (32)
    assert cfg.min_history == 288


def test_from_toml_reads_backtest_costs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "cfg.toml"
    p.write_text("[backtest]\nfee_pct = 0.001\nslippage_bps = 4.0\n")
    cfg = ForecastConfig.from_toml(p)
    assert cfg.fee_pct == 0.001
    assert cfg.slippage_pct == 0.0004  # 4 bps


def test_from_toml_missing_backtest_uses_defaults(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "cfg.toml"
    p.write_text("[other]\nx = 1\n")
    cfg = ForecastConfig.from_toml(p)
    assert cfg.fee_pct == 0.0005
    assert cfg.slippage_pct == 0.0002


def test_weights_defaults_to_none() -> None:
    assert ForecastConfig().weights is None


def test_weights_length_mismatch_raises() -> None:
    # default speeds has 4 entries; 2 weights must raise
    with pytest.raises(ValueError):
        ForecastConfig(weights=(1.0, 1.0))


def test_weights_matching_length_ok() -> None:
    cfg = ForecastConfig(weights=(1.0, 1.0, 1.0, 1.0))
    assert cfg.weights == (1.0, 1.0, 1.0, 1.0)
