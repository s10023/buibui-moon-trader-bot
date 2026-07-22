"""Tests for the ST9/H11 SL-horizon audit driver."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.store.schema import init_schema  # noqa: E402
from tools.sl_horizon_audit import (  # noqa: E402
    TF_MS,
    FidelityReport,
    check_fidelity,
    load_live_signals,
)


@pytest.fixture()
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def test_tf_ms_covers_the_four_audited_timeframes() -> None:
    assert TF_MS["15m"] == 15 * 60_000
    assert TF_MS["1h"] == 60 * 60_000
    assert TF_MS["4h"] == 4 * 60 * 60_000
    assert TF_MS["1d"] == 24 * 60 * 60_000


def test_load_live_signals_returns_only_family_rows_with_resolved_outcomes(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    conn.execute(
        """
        INSERT INTO signal_alert_outcomes
            (signal_id, symbol, tf, strategy, direction, fired_at_ms,
             candle_ts_ms, entry_price, sl_price, tp_price, rr_ratio,
             confidence_at_fire, tags, outcome, outcome_r, outcome_filled_at_ms)
        VALUES
            ('a', 'BTCUSDT', '1h', 'pin_bar',   'long', 1, 1000, 100.0, 98.0, 106.0,
             3.0, 3, NULL, 'win',  3.0, 2000),
            ('b', 'BTCUSDT', '1h', 'fvg',       'long', 1, 1000, 100.0, 98.0, 106.0,
             3.0, 3, NULL, 'win',  3.0, 2000),
            ('c', 'BTCUSDT', '1h', 'engulfing', 'long', 1, 1000, 100.0, 98.0, 106.0,
             3.0, 3, NULL, 'open', NULL, NULL)
        """
    )
    got = load_live_signals(conn)
    assert list(got["signal_id"]) == ["a"]


def test_check_fidelity_passes_when_replay_matches_stored() -> None:
    replayed = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "net_r": [1.0, -1.0, 1.0, -1.0],
            "outcome": ["win", "loss", "win", "loss"],
        }
    )
    stored = replayed.rename(columns={"net_r": "stored_r", "outcome": "stored_outcome"})
    report = check_fidelity(replayed, stored, tolerance_r=0.02, min_agreement=0.95)
    assert isinstance(report, FidelityReport)
    assert report.passed is True


def test_check_fidelity_fails_on_a_systematic_offset() -> None:
    replayed = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "net_r": [1.5, -0.5, 1.5, -0.5],
            "outcome": ["win", "loss", "win", "loss"],
        }
    )
    stored = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "stored_r": [1.0, -1.0, 1.0, -1.0],
            "stored_outcome": ["win", "loss", "win", "loss"],
        }
    )
    report = check_fidelity(replayed, stored, tolerance_r=0.02, min_agreement=0.95)
    assert report.passed is False
    assert any("avg_r" in r for r in report.reasons)


def test_check_fidelity_fails_on_outcome_disagreement() -> None:
    replayed = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "net_r": [1.0, -1.0, 1.0, -1.0],
            "outcome": ["win", "win", "loss", "loss"],
        }
    )
    stored = pd.DataFrame(
        {
            "strategy": ["pin_bar"] * 4,
            "tf": ["1h"] * 4,
            "key": [1, 2, 3, 4],
            "stored_r": [1.0, -1.0, 1.0, -1.0],
            "stored_outcome": ["win", "loss", "win", "loss"],
        }
    )
    report = check_fidelity(replayed, stored, tolerance_r=0.02, min_agreement=0.95)
    assert report.passed is False
    assert any("agreement" in r for r in report.reasons)
