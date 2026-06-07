"""Tests for `tools/adr_threshold_audit.py` verdict wiring.

The masking math (`_compute_trade_ratios`) mirrors the live ADR gate and is
covered indirectly via `_filter_signals_by_adr`; these tests focus on the
bootstrap-CI + haircut verdicts now produced by `aggregate_sweep` /
`per_strategy_sweep` (P0a-2 sub-PR 2). Frames are pre-annotated with the
`_chasing` / `_ratio` columns the masking step would have produced.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from tools import adr_threshold_audit as adr

# Fast, deterministic bootstrap settings for the suite.
_KW: dict[str, Any] = {"n_boot": 1500, "seed": 7}


def _row(
    *,
    strategy: str = "bos",
    tf: str = "1h",
    direction: str = "long",
    pnl_r: float = 0.0,
    chasing: bool = True,
    ratio: float = 0.75,
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "tf": tf,
        "direction": direction,
        "pnl_r": pnl_r,
        "_chasing": chasing,
        "_ratio": ratio,
    }


def _noisy(mean: float, std: float, n: int, seed: int) -> list[float]:
    return [float(x) for x in np.random.default_rng(seed).normal(mean, std, n)]


class TestAggregateSweep:
    def test_enable_when_extra_suppressed_are_losers(self) -> None:
        # 40 chasing trades at ratio 0.75 (inside [0.70, 0.80)) that are losers
        # → tightening to 0.70 drops losers → ENABLE.
        supp = [_row(pnl_r=v, ratio=0.75) for v in _noisy(-0.6, 0.5, 40, 1)]
        kept = [
            _row(pnl_r=v, chasing=False, ratio=0.10) for v in _noisy(0.1, 0.5, 40, 2)
        ]
        df = pd.DataFrame(supp + kept)
        out = adr.aggregate_sweep(df, [0.70], 0.80, set(), 30, 0.05, **_KW)
        assert out.iloc[0]["verdict"] == "ENABLE"
        assert out.iloc[0]["ci_hi"] <= -0.05

    def test_disable_when_extra_suppressed_are_winners(self) -> None:
        supp = [_row(pnl_r=v, ratio=0.75) for v in _noisy(0.6, 0.5, 40, 3)]
        df = pd.DataFrame(supp)
        out = adr.aggregate_sweep(df, [0.70], 0.80, set(), 30, 0.05, **_KW)
        assert out.iloc[0]["verdict"] == "DISABLE"

    def test_insufficient_below_min_n(self) -> None:
        supp = [_row(pnl_r=v, ratio=0.75) for v in _noisy(-0.6, 0.5, 10, 4)]
        df = pd.DataFrame(supp)
        out = adr.aggregate_sweep(df, [0.70], 0.80, set(), 30, 0.05, **_KW)
        assert out.iloc[0]["verdict"] == "INSUFFICIENT"

    def test_exempt_strategies_excluded(self) -> None:
        supp = [
            _row(strategy="bos", pnl_r=v, ratio=0.75) for v in _noisy(-0.6, 0.5, 40, 5)
        ]
        df = pd.DataFrame(supp)
        out = adr.aggregate_sweep(df, [0.70], 0.80, {"bos"}, 30, 0.05, **_KW)
        # bos is exempt → nothing suppressed → INSUFFICIENT.
        assert out.iloc[0]["n_supp"] == 0
        assert out.iloc[0]["verdict"] == "INSUFFICIENT"


class TestPerStrategySweep:
    def test_long_short_verdicts_split(self) -> None:
        longs = [
            _row(direction="long", pnl_r=v, ratio=0.75)
            for v in _noisy(-0.6, 0.5, 40, 6)
        ]
        shorts = [
            _row(direction="short", pnl_r=v, ratio=0.75)
            for v in _noisy(0.6, 0.5, 40, 7)
        ]
        df = pd.DataFrame(longs + shorts)
        out = adr.per_strategy_sweep(df, 0.70, 0.80, set(), 30, 0.05, **_KW)
        verdicts = dict(zip(out["direction"], out["verdict"], strict=True))
        assert verdicts["long"] == "ENABLE"
        assert verdicts["short"] == "DISABLE"

    def test_empty_when_all_exempt(self) -> None:
        df = pd.DataFrame([_row(pnl_r=v) for v in _noisy(-0.6, 0.5, 40, 8)])
        out = adr.per_strategy_sweep(df, 0.70, 0.80, {"bos"}, 30, 0.05, **_KW)
        assert out.empty
