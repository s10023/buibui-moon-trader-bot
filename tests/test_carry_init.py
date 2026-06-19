"""The carry package re-exports its public surface."""

from __future__ import annotations


def test_public_surface_importable() -> None:
    from analytics.carry import (  # noqa: F401
        CarryBookResult,
        CarryConfig,
        CarryReport,
        annualized_funding,
        carry_forecast_matrix,
        carry_gate_verdict,
        carry_leverage,
        combine_carry_forecasts,
        equity_curve,
        evaluate_carry,
        replay_carry,
        replay_carry_trials,
        run_carry_backtest,
        scaled_carry_forecast,
    )
