"""Regime gate backtest replay — answers the soft→hard flip question.

Replays the v2 Phase 2 regime gate (`config/strategy_params.toml [bias.regime]`)
against historical `backtest_trades`. For each trade the gate would have
suppressed in hard mode, computes avg_r on the suppressed subset.

This is the empirical substitute for the original "wait 2 weeks in soft
mode" plan: same flip-decision data, derived from history rather than
forward observation.

Decision rule (matching the original soft-mode criteria):
  * Suppressed avg_r ≤ 0 with n ≥ 100 → flip is justified.
  * Kept avg_r > suppressed avg_r → gate concentrates edge correctly.
  * Suppressed avg_r > 0 with n ≥ 100 → gate is dropping winners; redo.

Usage:
    PYTHONPATH=. poetry run python tools/regime_gate_replay.py [--db PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

from analytics.regime import Regime, classify_series
from analytics.signal_config import load_signal_config
from analytics.store import DEFAULT_DB_PATH
from analytics.strategies import STRATEGY_REGISTRY

# Live gate uses 4h candles regardless of signal TF (per config/strategy_params.toml).
_REGIME_TF = "4h"
_FOUR_HOURS_MS = 4 * 60 * 60 * 1000

# Decision rule thresholds (kept in sync with soft-mode flip criteria).
_MIN_TRADES_FOR_DECISION = 100


def _load_trades(
    conn: duckdb.DuckDBPyConnection, strategies: list[str]
) -> pd.DataFrame:
    """All closed trades for the strategies the gate could suppress."""
    if not strategies:
        return pd.DataFrame()
    placeholders = ",".join(["?"] * len(strategies))
    return conn.execute(
        f"""
        SELECT strategy, symbol, timeframe, direction, entry_time, pnl_r
        FROM backtest_trades
        WHERE strategy IN ({placeholders})
          AND outcome != 'open'
          AND pnl_r IS NOT NULL
        """,
        strategies,
    ).df()


def _load_4h_ohlcv(conn: duckdb.DuckDBPyConnection, symbol: str) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT open_time, high, low, close
        FROM ohlcv
        WHERE symbol = ? AND timeframe = ?
        ORDER BY open_time
        """,
        [symbol, _REGIME_TF],
    ).df()


def _regime_at_entry(entry_time_ms: int) -> int:
    """Return the open_time of the most recent CLOSED 4h candle at entry_time.

    Mirrors the live gate's `iloc[-2]` rule: skip the in-progress candle and
    use the previous one's regime label.
    """
    bin_open = entry_time_ms - (entry_time_ms % _FOUR_HOURS_MS)
    return bin_open - _FOUR_HOURS_MS


def annotate_regime_4h(
    trades: pd.DataFrame, conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    """Attach `regime` (Regime label) per trade based on 4h classification.

    Trades whose 4h regime cannot be resolved (insufficient history, cache miss)
    are labelled `unknown` — matching the live gate's fall-open behaviour.
    """
    parts: list[pd.DataFrame] = []
    for symbol, group in trades.groupby("symbol", sort=False):
        ohlcv = _load_4h_ohlcv(conn, str(symbol))
        if ohlcv.empty:
            g = group.copy()
            g["regime"] = "unknown"
            parts.append(g)
            continue
        ohlcv["regime"] = classify_series(ohlcv, _REGIME_TF)
        lookup = ohlcv.set_index("open_time")["regime"]
        g = group.copy()
        g["regime_lookup_key"] = g["entry_time"].apply(_regime_at_entry)
        g["regime"] = g["regime_lookup_key"].map(lookup).fillna("unknown")
        g = g.drop(columns=["regime_lookup_key"])
        parts.append(g)
    return (
        pd.concat(parts, ignore_index=True)
        if parts
        else trades.assign(regime="unknown")
    )


def annotate_suppression(
    trades: pd.DataFrame, regime_allowed_fn: object
) -> pd.DataFrame:
    """Mark each trade as suppressed/kept by replaying the gate logic.

    `regime_allowed_fn` matches BiasConfig.regime_allowed signature:
      (strategy, strategy_type, regime) -> bool
    """
    out = trades.copy()
    types = {n: s.strategy_type for n, s in STRATEGY_REGISTRY.items()}

    def _is_suppressed(row: pd.Series) -> bool:
        strategy = str(row["strategy"])
        regime: Regime = row["regime"]
        if regime == "unknown":
            return False  # falls open
        strategy_type = types.get(strategy, "")
        return not regime_allowed_fn(strategy, strategy_type, regime)  # type: ignore[operator]

    out["suppressed"] = out.apply(_is_suppressed, axis=1)
    return out


def aggregate(trades: pd.DataFrame) -> pd.DataFrame:
    """Per-(strategy × regime × suppressed) avg_r table."""
    grouped = trades.groupby(
        ["strategy", "regime", "suppressed"], sort=False, observed=True
    )
    agg = grouped.agg(
        n=("pnl_r", "size"),
        avg_r=("pnl_r", "mean"),
        win_rate=("pnl_r", lambda s: (s > 0).mean()),
    ).reset_index()
    return agg.sort_values(
        ["suppressed", "strategy", "regime"], ascending=[False, True, True]
    ).reset_index(drop=True)


def render_verdict(agg: pd.DataFrame) -> str:
    """Print per-cell table + global flip verdict."""
    lines: list[str] = []
    lines.append("Per-cell (strategy × regime × suppressed):")
    lines.append("-" * 78)
    lines.append(
        f"{'strategy':<18} {'regime':<10} {'supp':<6} {'n':>7} {'avg_r':>8} {'win%':>6}"
    )
    lines.append("-" * 78)
    for _, row in agg.iterrows():
        lines.append(
            f"{row['strategy']:<18} {row['regime']:<10} "
            f"{'YES' if row['suppressed'] else 'no':<6} "
            f"{row['n']:>7} {row['avg_r']:>+8.4f} {row['win_rate'] * 100:>5.1f}%"
        )
    lines.append("-" * 78)

    suppressed = agg[agg["suppressed"]]
    kept = agg[~agg["suppressed"]]
    if not suppressed.empty:
        weighted_avg_r = (suppressed["avg_r"] * suppressed["n"]).sum() / suppressed[
            "n"
        ].sum()
        total_n = int(suppressed["n"].sum())
        lines.append("")
        lines.append(f"SUPPRESSED aggregate: n={total_n}  avg_r={weighted_avg_r:+.4f}")
    if not kept.empty:
        weighted_avg_r_k = (kept["avg_r"] * kept["n"]).sum() / kept["n"].sum()
        total_n_k = int(kept["n"].sum())
        lines.append(
            f"KEPT aggregate:       n={total_n_k}  avg_r={weighted_avg_r_k:+.4f}"
        )

    lines.append("")
    lines.append("Decision:")
    if suppressed.empty or int(suppressed["n"].sum()) < _MIN_TRADES_FOR_DECISION:
        lines.append(
            f"  HOLD — insufficient suppressed trades (need n ≥ {_MIN_TRADES_FOR_DECISION})."
        )
    else:
        sup_avg = (suppressed["avg_r"] * suppressed["n"]).sum() / suppressed["n"].sum()
        kept_avg = (
            (kept["avg_r"] * kept["n"]).sum() / kept["n"].sum()
            if not kept.empty
            else 0.0
        )
        if sup_avg <= 0 and kept_avg > sup_avg:
            lines.append(
                f"  FLIP justified — suppressed avg_r={sup_avg:+.4f} ≤ 0 "
                f"and kept avg_r={kept_avg:+.4f} > suppressed."
            )
        elif sup_avg > 0:
            lines.append(
                f"  DO NOT FLIP — suppressed avg_r={sup_avg:+.4f} > 0. "
                "Gate is dropping winners; redesign needed."
            )
        else:
            lines.append(
                f"  AMBIGUOUS — suppressed avg_r={sup_avg:+.4f}, kept avg_r={kept_avg:+.4f}. "
                "Investigate per-cell."
            )

    return "\n".join(lines)


def run(db_path: Path, config_path: Path) -> tuple[pd.DataFrame, str]:
    cfg = load_signal_config(config_path)
    if not cfg.bias.regime_enabled:
        raise SystemExit(
            f"{config_path}: [bias.regime].enabled is false — nothing to replay."
        )

    # Strategies the gate could suppress: any strategy whose type is NOT enabled
    # in at least one regime, plus per_strategy overrides.
    suppressible: set[str] = set()
    for name, spec in STRATEGY_REGISTRY.items():
        # Default mapping vs override.
        if name in cfg.bias.regime_per_strategy:
            allowed = cfg.bias.regime_per_strategy[name]
        else:
            allowed = cfg.bias.regime_enabled_regimes.get(spec.strategy_type, [])
        if {"trend", "range", "high_vol"} - set(allowed):
            suppressible.add(name)

    conn = duckdb.connect(str(db_path), read_only=True)
    trades = _load_trades(conn, sorted(suppressible))
    if trades.empty:
        raise SystemExit(
            f"No backtest_trades for suppressible strategies {sorted(suppressible)}."
        )

    trades = annotate_regime_4h(trades, conn)
    trades = annotate_suppression(trades, cfg.bias.regime_allowed)
    agg = aggregate(trades)
    return agg, render_verdict(agg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(DEFAULT_DB_PATH),
        help=f"Path to analytics.db (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/strategy_params.toml"),
        help="Signal config to read [bias.regime] from (default: strategy_params.toml).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional CSV output path for the per-cell aggregate.",
    )
    args = parser.parse_args(argv)

    agg, verdict = run(args.db, args.config)
    print(verdict)
    if args.out is not None:
        agg.to_csv(args.out, index=False)
        print(f"\nWrote per-cell CSV: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
