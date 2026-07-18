"""Tests for the pure parts of tools/warning_value_audit.py."""

from __future__ import annotations

import pandas as pd

from analytics.warning_audit import WARNING_KEYS, WarningVerdict
from tools.warning_value_audit import (
    exploratory_by_tf,
    format_report,
    normalize_backtest,
    normalize_live,
)


def _verdict(warning: str, direction: str, verdict: str) -> WarningVerdict:
    return WarningVerdict(
        warning=warning,
        direction=direction,
        n_warned=100,
        n_clean=400,
        avg_warned=-0.2,
        avg_clean=0.1,
        ci_lo=-0.3,
        ci_hi=-0.1,
        adj_pvalue=0.01,
        n_tests=8,
        lift_lo=-0.4,
        lift_hi=-0.2,
        raw_decision="ENABLE",
        verdict=verdict,
        reasons=[],
    )


class TestNormalizers:
    def test_normalize_live_shape(self) -> None:
        raw = pd.DataFrame(
            {
                "symbol": ["BTCUSDT", "ETHUSDT"],
                "tf": ["1h", "4h"],
                "strategy": ["fvg", "bos"],
                "direction": ["long", "short"],
                "candle_ts_ms": [1000, None],
                "outcome_r": [0.5, 1.0],
            }
        )
        out = normalize_live(raw)
        assert list(out.columns) == [
            "symbol",
            "tf",
            "strategy",
            "direction",
            "ts_ms",
            "r",
        ]
        assert len(out) == 1  # null candle_ts_ms dropped

    def test_normalize_backtest_dedups_across_runs(self) -> None:
        raw = pd.DataFrame(
            {
                "run_id": ["run_a", "run_b", "run_a"],
                "symbol": ["BTCUSDT"] * 3,
                "timeframe": ["1h"] * 3,
                "strategy": ["fvg"] * 3,
                "direction": ["long"] * 3,
                "signal_time": [1000, 1000, 2000],
                "pnl_r": [0.5, 0.9, -0.2],
            }
        )
        out = normalize_backtest(raw)
        assert len(out) == 2  # duplicate signal collapsed
        kept = out[out["ts_ms"] == 1000].iloc[0]
        assert kept["r"] == 0.9  # latest run_id wins
        assert "run_id" not in out.columns


class TestExploratory:
    def test_by_tf_rows(self) -> None:
        tagged = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"] * 4,
                "tf": ["1h", "1h", "4h", "4h"],
                "strategy": ["fvg"] * 4,
                "direction": ["long"] * 4,
                "ts_ms": [1, 2, 3, 4],
                "r": [1.0, -1.0, 0.5, 0.5],
            }
        )
        for key in WARNING_KEYS:
            tagged[key] = False
        tagged.loc[0, "w7_doji"] = True
        out = exploratory_by_tf(tagged)
        row = out[(out["warning"] == "w7_doji") & (out["tf"] == "1h")].iloc[0]
        assert row["n_warned"] == 1
        assert row["avg_warned"] == 1.0
        assert row["avg_clean"] == -1.0


class TestFormatReport:
    def test_headline_and_tables(self) -> None:
        verdicts = [
            _verdict("w7_doji", "long", "SUPPRESS-CANDIDATE"),
            _verdict("w1_marubozu", "short", "COSMETIC"),
        ]
        expl = pd.DataFrame(
            [
                {
                    "warning": "w7_doji",
                    "direction": "long",
                    "tf": "1h",
                    "n_warned": 10,
                    "avg_warned": -0.2,
                    "n_clean": 40,
                    "avg_clean": 0.1,
                    "lift": -0.3,
                }
            ]
        )
        report = format_report(
            {"backtest": (verdicts, expl, 500, 12)},
            min_n=30,
            bar=0.05,
            alpha=0.05,
            n_boot=100,
            seed=1,
        )
        assert "SUPPRESS-CANDIDATE: w7_doji/long" in report
        assert "| --- |" in report  # markdownlint-conformant delimiters
        assert "500 tagged" in report
