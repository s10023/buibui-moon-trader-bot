from __future__ import annotations

import analytics.xsrev as xsrev


def test_public_surface() -> None:
    expected = {
        "ReversalConfig",
        "XSBookResult",
        "combine_reversal_forecasts",
        "equity_curve",
        "reversal_forecast_matrix",
        "replay_xsrev",
        "replay_xsrev_trials",
        "run_xsrev_backtest",
        "scaled_reversal_forecast",
    }
    assert expected.issubset(set(xsrev.__all__))
    for name in expected:
        assert hasattr(xsrev, name)
