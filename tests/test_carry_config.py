"""Tests for CarryConfig."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from analytics.carry.config import CarryConfig
from analytics.forecast.config import ForecastConfig


def test_defaults() -> None:
    cfg = CarryConfig()
    assert cfg.carry_spans == (1, 5, 20, 60)
    assert cfg.carry_scalar == 30.0
    assert cfg.fdm == 1.25
    assert cfg.cross_sectional is True
    assert isinstance(cfg.sleeve_cfg, ForecastConfig)


def test_pass_throughs_match_sleeve_cfg() -> None:
    cfg = CarryConfig()
    assert cfg.annualization_days == cfg.sleeve_cfg.annualization_days
    assert cfg.cap == cfg.sleeve_cfg.cap
    assert cfg.vol_span == cfg.sleeve_cfg.vol_span
    assert cfg.vol_target_annual == cfg.sleeve_cfg.vol_target_annual
    assert cfg.fee_pct == cfg.sleeve_cfg.fee_pct
    assert cfg.slippage_pct == cfg.sleeve_cfg.slippage_pct
    assert cfg.gov_window == cfg.sleeve_cfg.gov_window
    assert cfg.g_min == cfg.sleeve_cfg.g_min
    assert cfg.g_max == cfg.sleeve_cfg.g_max


def test_empty_spans_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        CarryConfig(carry_spans=())


def test_span_below_one_rejected() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        CarryConfig(carry_spans=(0, 5))


def test_frozen() -> None:
    cfg = CarryConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.carry_scalar = 99.0  # type: ignore[misc]
