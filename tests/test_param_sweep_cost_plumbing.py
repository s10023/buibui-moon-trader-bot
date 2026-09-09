"""ST128 — the WFO path must carry production's cost model and stop floor.

`analytics/param_sweep.py` is what `/wfo-sweep` and `/param-audit` run, and
AGENTS.md calls that the trusted production source of `tp_r`. It threaded
`fee_pct` to `run_backtest` and silently dropped `min_sl_pct` and
`slippage_pct`, so every live `tp_r` was fitted on a book with no stop floor
paying only 71% of real round-trip drag.

The defect was a SILENT DEFAULT, not a wrong value, so the guarantee here is
structural: the args are required through the chain (a dropped call site is a
TypeError, not a zero), and `test_every_run_backtest_call_carries_cost_and_floor`
fails when a NEW call site forgets — which is the recurrence shape, and the one
a behavioural test on today's call sites cannot see.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from analytics.param_sweep import (
    _audit_strategy_worker,
    _cost_defaults,
    _sweep_grid_worker,
    run_param_sweep,
    run_strategy_audit,
)

_REQUIRED = ("min_sl_pct", "slippage_pct")


def _calls_to(source: Path, callee: str) -> list[ast.Call]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == callee
    ]


class TestCallSiteCompleteness:
    """Source-level: no `run_backtest` call in the WFO path may omit either arg."""

    def test_every_run_backtest_call_carries_cost_and_floor(self) -> None:
        path = Path("analytics/param_sweep.py")
        calls = _calls_to(path, "run_backtest")
        assert calls, "expected run_backtest call sites in param_sweep.py"

        missing = [
            (c.lineno, arg)
            for c in calls
            for arg in _REQUIRED
            if arg not in {k.arg for k in c.keywords}
        ]
        assert not missing, (
            f"{path} has run_backtest call sites missing cost/floor args: {missing}. "
            "A silent default here re-opens ST128 — every live tp_r would again be "
            "fitted on a floorless, slippage-free book."
        )

    def test_production_cli_forwards_both(self) -> None:
        """`buibui param-sweep` / `param-audit` is the path that moves the TOMLs."""
        path = Path("cli/param.py")
        for callee in ("_run", "run_strategy_audit"):
            for call in _calls_to(path, callee):
                passed = {k.arg for k in call.keywords}
                for arg in _REQUIRED:
                    assert arg in passed, (
                        f"{path}:{call.lineno} calls {callee}() without {arg}"
                    )


class TestRequiredNotDefaulted:
    """A default of 0.0 is the defect itself, so these must be required."""

    @pytest.mark.parametrize(
        "fn",
        [
            _sweep_grid_worker,
            run_param_sweep,
            _audit_strategy_worker,
            run_strategy_audit,
        ],
    )
    def test_args_have_no_default(self, fn: Any) -> None:
        params = inspect.signature(fn).parameters
        for arg in _REQUIRED:
            assert arg in params, f"{fn.__name__} does not accept {arg}"
            assert params[arg].default is inspect.Parameter.empty, (
                f"{fn.__name__}.{arg} has a default; a silent zero is exactly ST128"
            )


class TestDefaultsComeFromTheToml:
    """Restating the constants is what let the sweep drift from production."""

    def test_cost_defaults_match_the_toml_and_are_non_zero(self) -> None:
        fee, slippage, floor = _cost_defaults()
        assert (fee, slippage, floor) == (0.0005, 0.0002, 0.005)

    def test_slippage_is_a_real_share_of_drag(self) -> None:
        """Dropping slippage understated round-trip drag by ~29%."""
        fee, slippage, _ = _cost_defaults()
        understated = (2 * fee) / (2 * (fee + slippage))
        assert understated == pytest.approx(0.714, abs=0.01)


class TestForwardedToTheEngine:
    """Behavioural: the values actually land on run_backtest, not just the signature."""

    def test_sweep_worker_forwards_values(self) -> None:
        seen: list[dict[str, Any]] = []

        def fake(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs)
            raise _Stop

        with (
            patch("analytics.param_sweep.run_backtest", side_effect=fake),
            pytest.raises(_Stop),
        ):
            _sweep_grid_worker(
                params={"tp_r": 2.0},
                ohlcv_is=_df(),
                signals_is=_df(),
                ohlcv_oos=_df(),
                signals_oos=_df(),
                symbol="BTCUSDT",
                timeframe="1h",
                strategy="bos",
                fee_pct=0.0005,
                min_sl_pct=0.005,
                slippage_pct=0.0002,
                is_min=1,
            )

        assert seen and seen[0]["min_sl_pct"] == 0.005
        assert seen[0]["slippage_pct"] == 0.0002


class _Stop(Exception):
    """Abort the worker after the first engine call — we only inspect kwargs."""


def _df() -> Any:
    import pandas as pd

    return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
