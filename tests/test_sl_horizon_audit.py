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

from analytics.sl_horizon import BASELINE_ARM, SLGridConfig, arm_label  # noqa: E402
from analytics.store.schema import init_schema  # noqa: E402
from tools.sl_horizon_audit import (  # noqa: E402
    NO_TIME_STOP_BARS,
    TF_MS,
    FidelityReport,
    check_fidelity,
    load_live_signals,
    load_stored_backtest_trades,
    resolve_live_arms,
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


def test_no_time_stop_bars_is_large_enough_to_never_bind() -> None:
    # The engine has no expiry; the fidelity replay must not introduce one.
    assert NO_TIME_STOP_BARS >= 100_000


def test_load_stored_backtest_trades_dedups_across_runs(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    # trade_id is a NOT NULL primary key in the real schema (not in the brief's
    # column list) — added here, one unique value per row.
    rows = [
        (
            "t1",
            "r1",
            "BTCUSDT",
            "1h",
            "pin_bar",
            "long",
            1000,
            1100,
            100.0,
            98.0,
            106.0,
            1200,
            106.0,
            "win",
            3.0,
        ),
        # same signal, later run_id — only this one should survive
        (
            "t2",
            "r2",
            "BTCUSDT",
            "1h",
            "pin_bar",
            "long",
            1000,
            1100,
            100.0,
            98.0,
            106.0,
            1200,
            98.0,
            "loss",
            -1.0,
        ),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO backtest_trades (trade_id, run_id, symbol, timeframe, "
            "strategy, direction, signal_time, entry_time, entry_price, sl_price, "
            "tp_price, exit_time, exit_price, outcome, pnl_r) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            list(r),
        )
    got = load_stored_backtest_trades(conn, symbols=["BTCUSDT"], timeframes=["1h"])
    assert len(got) == 1
    assert got.iloc[0]["stored_outcome"] == "loss"
    assert {"strategy", "tf", "key", "stored_r", "stored_outcome"} <= set(got.columns)


def test_load_stored_backtest_trades_key_is_unique_per_signal(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    for sig_time in (1000, 2000):
        conn.execute(
            "INSERT INTO backtest_trades (trade_id, run_id, symbol, timeframe, "
            "strategy, direction, signal_time, entry_time, entry_price, sl_price, "
            "tp_price, exit_time, exit_price, outcome, pnl_r) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                f"t-{sig_time}",
                "r1",
                "BTCUSDT",
                "1h",
                "pin_bar",
                "long",
                sig_time,
                sig_time + 100,
                100.0,
                98.0,
                106.0,
                sig_time + 200,
                106.0,
                "win",
                3.0,
            ],
        )
    got = load_stored_backtest_trades(conn, symbols=["BTCUSDT"], timeframes=["1h"])
    assert got["key"].nunique() == 2


def test_resolve_live_arms_produces_one_row_per_alert_per_arm() -> None:
    cfg = SLGridConfig(multipliers=(1.0, 2.0))
    alerts = pd.DataFrame(
        [
            {
                "signal_id": "a",
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "pin_bar",
                "direction": "long",
                "candle_ts_ms": 2_000,
                "entry_price": 100.0,
                "sl_price": 98.0,
                "tp_price": 106.0,
                "rr_ratio": 3.0,
                "outcome": "win",
                "outcome_r": 3.0,
            }
        ]
    )
    n = 40
    ohlcv = pd.DataFrame(
        {
            "open_time": [1_000 * i for i in range(n)],
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": [10.0] * n,
        }
    )
    rows = resolve_live_arms(
        alerts,
        {("BTCUSDT", "1h"): ohlcv},
        cfg=cfg,
        tp_r_for=lambda *_: 3.0,
    )
    assert set(rows["arm"]) == {BASELINE_ARM, arm_label(1.0), arm_label(2.0)}
    assert len(rows) == 3


def test_resolve_live_arms_baseline_uses_the_stored_sl() -> None:
    """The live baseline must reproduce the alert, so it uses the STORED sl_price."""
    cfg = SLGridConfig(multipliers=(1.0,))
    alerts = pd.DataFrame(
        [
            {
                "signal_id": "a",
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "pin_bar",
                "direction": "long",
                "candle_ts_ms": 2_000,
                "entry_price": 100.0,
                "sl_price": 97.0,
                "tp_price": 109.0,
                "rr_ratio": 3.0,
                "outcome": "win",
                "outcome_r": 3.0,
            }
        ]
    )
    n = 40
    ohlcv = pd.DataFrame(
        {
            "open_time": [1_000 * i for i in range(n)],
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": [10.0] * n,
        }
    )
    rows = resolve_live_arms(
        alerts, {("BTCUSDT", "1h"): ohlcv}, cfg=cfg, tp_r_for=lambda *_: 3.0
    )
    baseline = rows[rows["arm"] == BASELINE_ARM].iloc[0]
    # stored SL is 3% away, not the nominal 2% — the replay must honour it.
    assert baseline["sl_dist_pct"] == pytest.approx(0.03)
