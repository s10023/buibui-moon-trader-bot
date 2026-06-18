from __future__ import annotations

from pathlib import Path

from analytics.combine.config import CombineConfig
from analytics.forecast.config import ForecastConfig


def test_defaults_are_equal_risk_causal() -> None:
    cfg = CombineConfig()
    assert cfg.w_xs == 0.5
    assert cfg.w_trend == 0.5
    assert cfg.idm_mode == "causal"
    assert cfg.idm_window == 365
    assert cfg.idm_min_periods == 120
    assert cfg.idm_cap == 2.5
    assert cfg.apply_governor is True
    assert isinstance(cfg.sleeve_cfg, ForecastConfig)
    # the headline XS sleeve is the validated original (NOT dollar-neutral)
    assert cfg.sleeve_cfg.xs_dollar_neutral is False


def test_from_toml_picks_up_sleeve_costs(tmp_path: Path) -> None:
    toml = tmp_path / "p.toml"
    toml.write_text("[backtest]\nfee_pct = 0.0007\nslippage_bps = 3.0\n")
    cfg = CombineConfig.from_toml(toml)
    assert cfg.sleeve_cfg.fee_pct == 0.0007
    assert cfg.sleeve_cfg.slippage_pct == 3.0 / 10_000.0


def test_invalid_idm_mode_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="idm_mode"):
        CombineConfig(idm_mode="bogus")
