#!/usr/bin/env python
"""ST9 / H11 SL-horizon audit (read-only).

Six candle detectors hard-code ``sl_pct = 0.02`` at every timeframe. This tool
re-resolves the same signals under an a-priori ATR-multiplier grid and emits a
pre-committed SUSPECT / CONFIRMED-BAD / NO-DIFFERENCE / INSUFFICIENT verdict per
(strategy x timeframe), via :mod:`analytics.sl_horizon`.

Substrate roles (pre-committed): LIVE ``signal_alert_outcomes`` is the GATE;
``backtest_trades`` corroborates. One Holm family per substrate, never pooled.

Read-only: no DB writes, no config edits, no engine change.

Run: ``PYTHONPATH=. poetry run python tools/sl_horizon_audit.py``
(wrapped by ``make buibui-sl-horizon-audit``).
"""

from __future__ import annotations

import argparse  # noqa: F401 (reserved for a later task's CLI entry point)
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.sl_horizon import (  # noqa: E402
    BASELINE_ARM,
    FAMILY,
    SIGNAL_KEY,  # noqa: F401 (reserved for a later task)
    SLGridConfig,
    arm_label,
    atr_by_open_time,
    baseline_levels,
    counterfactual_levels,
    resolve_arm,
    window_for_signal,
)
from analytics.store import DEFAULT_DB_PATH  # noqa: E402, F401 (later task)
from analytics.store.market_data import get_ohlcv  # noqa: E402
from analytics.strategies._registry import DETECTOR_REGISTRY  # noqa: E402

TF_MS: dict[str, int] = {
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

#: (strategy, symbol, tf, direction) -> pinned tp_r
TpRLookup = Callable[[str, str, str, str], float]


@dataclass(frozen=True)
class FidelityReport:
    """Result of comparing a replayed baseline arm against a stored substrate."""

    passed: bool
    n_matched: int
    agreement: float
    worst_avg_r_delta: float
    reasons: list[str] = field(default_factory=list)


def load_live_signals(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Resolved live alerts for the six audited detectors.

    Only rows with a non-NULL ``outcome_r`` are returned — unresolved alerts have
    no stored result to compare against and cannot anchor the live fidelity check.
    """
    placeholders = ", ".join("?" for _ in FAMILY)
    return conn.execute(
        f"""
        SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms,
               entry_price, sl_price, tp_price, rr_ratio, outcome, outcome_r
        FROM signal_alert_outcomes
        WHERE strategy IN ({placeholders})
          AND outcome_r IS NOT NULL
        ORDER BY candle_ts_ms
        """,
        list(FAMILY),
    ).df()


def load_backtest_signals(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    timeframes: list[str],
) -> pd.DataFrame:
    """Re-detect the six detectors over full OHLCV history.

    Returns one row per detected signal with columns
    ``SIGNAL_KEY + ["sig_idx", "detector_sl_price"]``. Signals are re-detected
    rather than read from ``backtest_trades`` because the stored runs cover only
    3 symbols over ~10 months, which leaves the 1d cell unpowered.
    """
    records: list[dict[str, object]] = []
    for tf in timeframes:
        for symbol in symbols:
            ohlcv = get_ohlcv(conn, symbol, tf, 0, 2**62)
            if ohlcv.empty:
                continue
            position = {int(t): i for i, t in enumerate(ohlcv["open_time"].to_numpy())}
            for strategy in FAMILY:
                detector = DETECTOR_REGISTRY[strategy]
                signals = detector(ohlcv)
                if signals.empty:
                    continue
                for _, sig in signals.iterrows():
                    open_time = int(sig["open_time"])
                    sig_idx = position.get(open_time)
                    if sig_idx is None:
                        continue
                    records.append(
                        {
                            "symbol": symbol,
                            "tf": tf,
                            "strategy": strategy,
                            "direction": str(sig["direction"]),
                            "open_time": open_time,
                            "sig_idx": sig_idx,
                            "detector_sl_price": float(sig["sl_price"]),
                        }
                    )
    return pd.DataFrame(records)


def resolve_all_arms(
    signals: pd.DataFrame,
    ohlcv_by_key: dict[tuple[str, str], pd.DataFrame],
    *,
    cfg: SLGridConfig,
    convention: str,
    tp_r_for: TpRLookup,
) -> pd.DataFrame:
    """Resolve every signal under the baseline arm and each ``k`` arm.

    Returns long-format rows (one per signal x arm) carrying ``net_r``,
    ``outcome``, ``exit_bar``, ``sl_dist_pct`` and ``atr_pct``, ready for
    ``build_paired_table`` and ``describe_horizon``.
    """
    rows: list[dict[str, object]] = []
    for (symbol, tf), grp in signals.groupby(["symbol", "tf"], sort=True):
        ohlcv = ohlcv_by_key.get((str(symbol), str(tf)))
        if ohlcv is None or ohlcv.empty:
            continue
        max_hold = cfg.max_hold_bars_by_tf.get(str(tf), 48)
        atr_map = atr_by_open_time(ohlcv, grp["open_time"].tolist())

        for _, sig in grp.iterrows():
            open_time = int(sig["open_time"])
            atr = atr_map.get(open_time)
            if atr is None or atr <= 0.0:
                continue
            direction = str(sig["direction"])
            strategy = str(sig["strategy"])
            tp_r = tp_r_for(strategy, str(symbol), str(tf), direction)

            entry, highs, lows, closes = window_for_signal(
                ohlcv,
                sig_idx=int(sig["sig_idx"]),
                convention=convention,
                max_hold_bars=max_hold,
            )
            if len(highs) == 0 or not np.isfinite(entry):
                continue

            arms: list[tuple[str, tuple[float, float]]] = [
                (
                    BASELINE_ARM,
                    baseline_levels(
                        entry, direction, baseline_pct=cfg.baseline_pct, tp_r=tp_r
                    ),
                )
            ]
            for k in cfg.multipliers:
                arms.append(
                    (
                        arm_label(k),
                        counterfactual_levels(
                            entry, direction, atr=atr, k=k, tp_r=tp_r
                        ),
                    )
                )

            for arm, (sl_price, _tp_price) in arms:
                res = resolve_arm(
                    highs,
                    lows,
                    closes,
                    direction=direction,
                    entry=entry,
                    sl_price=sl_price,
                    tp_r=tp_r,
                    max_hold_bars=max_hold,
                    round_trip_cost_pct=cfg.round_trip_cost_pct,
                    funding_r=0.0,
                )
                if res is None:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "tf": tf,
                        "strategy": strategy,
                        "direction": direction,
                        "open_time": open_time,
                        "arm": arm,
                        "net_r": res.net_r,
                        "realized_r": res.realized_r,
                        "outcome": res.outcome,
                        "exit_bar": res.exit_bar,
                        "sl_dist_pct": res.sl_dist_pct,
                        "cost_r": res.cost_r,
                        "atr_pct": atr / entry,
                    }
                )
    return pd.DataFrame(rows)


def check_fidelity(
    replayed: pd.DataFrame,
    stored: pd.DataFrame,
    *,
    tolerance_r: float,
    min_agreement: float,
) -> FidelityReport:
    """Compare a replayed baseline arm against the stored substrate.

    Both conditions must hold, per (strategy x tf):
    ``|avg_r_replayed - avg_r_stored| <= tolerance_r`` AND matched-trade outcome
    agreement ``>= min_agreement``. The mean alone would hide offsetting
    per-trade errors; the agreement rate alone would hide a uniform shift.
    """
    merged = replayed.merge(stored, on=["strategy", "tf", "key"], how="inner")
    if merged.empty:
        return FidelityReport(
            passed=False,
            n_matched=0,
            agreement=0.0,
            worst_avg_r_delta=float("nan"),
            reasons=["no rows matched between replayed and stored substrates"],
        )

    reasons: list[str] = []
    worst = 0.0
    for (strategy, tf), grp in merged.groupby(["strategy", "tf"], sort=True):
        delta = abs(float(grp["net_r"].mean()) - float(grp["stored_r"].mean()))
        worst = max(worst, delta)
        if delta > tolerance_r:
            reasons.append(
                f"{strategy} {tf}: avg_r delta {delta:.4f} > tolerance {tolerance_r}"
            )

    agreement = float((merged["outcome"] == merged["stored_outcome"]).mean())
    if agreement < min_agreement:
        reasons.append(f"outcome agreement {agreement:.3f} < required {min_agreement}")

    return FidelityReport(
        passed=not reasons,
        n_matched=int(len(merged)),
        agreement=agreement,
        worst_avg_r_delta=worst,
        reasons=reasons,
    )
