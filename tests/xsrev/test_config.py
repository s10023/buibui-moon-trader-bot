from __future__ import annotations

import pytest

from analytics.forecast.config import ForecastConfig
from analytics.xsrev.config import ReversalConfig


def test_defaults_are_a_priori() -> None:
    cfg = ReversalConfig()
    assert cfg.formation_windows == (2, 3, 5, 7)
    assert cfg.reversal_scalar == 10.0
    assert cfg.fdm == 1.25
    # k=1 excluded from the headline family
    assert 1 not in cfg.formation_windows


def test_properties_delegate_to_sleeve_cfg() -> None:
    cfg = ReversalConfig(sleeve_cfg=ForecastConfig(vol_span=16, cap=15.0))
    assert cfg.vol_span == 16
    assert cfg.cap == 15.0


def test_empty_windows_rejected() -> None:
    with pytest.raises(ValueError):
        ReversalConfig(formation_windows=())


def test_zero_window_rejected() -> None:
    with pytest.raises(ValueError):
        ReversalConfig(formation_windows=(0, 2))
