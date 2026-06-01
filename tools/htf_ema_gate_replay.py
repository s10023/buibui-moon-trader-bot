"""F8 HTF EMA gate ablation — is the directional bias gate net-positive?

Replays the F8 HTF EMA directional gate (`config/strategy_params.toml
[bias.htf_ema]`; live logic `analytics/signal/gates.py::_apply_htf_ema_gate`)
against the permissive baseline in `backtest_trades`. For every trade the gate
would suppress in hard mode (direction opposes the per-strategy HTF EMA slope),
computes avg_r on the suppressed subset.

This is the go/no-go for the "EMA per-TF / multi-anchor" design exploration
(`docs/redesign/buibui-ema-per-tf-anchor-design.md`): there is no point laddering
the anchor TF if the gate it tunes is dropping winners. The headline cut is the
SUPPRESSED aggregate; the per-(timeframe × direction) rollup tests whether the
gate's value varies with the SIGNAL's TF — the premise of TF-proportional
anchoring.

Validity: `backtest_trades` is a permissive baseline (no `[live_parity]` block in
any config → `LiveParityConfig.f8_htf_ema=False`), so the F8-dropped subset is
present in the table rather than pre-filtered out — same assumption as
`tools/regime_gate_replay.py`.

Decision rule (mirrors the regime replay):
  * Suppressed avg_r ≤ 0 with n ≥ 100 AND kept avg_r > suppressed → gate earns
    its keep; anchor-laddering is a legitimate (if incremental) refinement.
  * Suppressed avg_r > 0 with n ≥ 100 → gate is dropping winners; the EMA-per-TF
    thread is moot until F8 itself is softened/flipped/removed.

Usage:
    PYTHONPATH=. poetry run python tools/htf_ema_gate_replay.py [--db PATH]
        [--config config/strategy_params.toml] [--out cells.csv]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

import duckdb
import pandas as pd

from analytics.signal_config import load_signal_config
from analytics.store import DEFAULT_DB_PATH
from analytics.strategies import STRATEGY_REGISTRY
from analytics.strategies._shared import compute_ema

_MIN_TRADES_FOR_DECISION = 100

# Timeframe → milliseconds, for resolving the last CLOSED anchor candle at entry.
_TF_MS: dict[str, int] = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
}


def _load_trades(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """All closed trades — F8 applies to every strategy (default 4h anchor)."""
    return conn.execute(
        """
        SELECT strategy, symbol, timeframe, direction, entry_time, pnl_r
        FROM backtest_trades
        WHERE outcome != 'open' AND pnl_r IS NOT NULL
        """
    ).df()


def _slope_series(
    conn: duckdb.DuckDBPyConnection, symbol: str, tf: str, period: int, slb: int
) -> pd.Series:
    """EMA slope fraction per anchor candle, indexed by open_time.

    slope[t] = (ema[t] - ema[t - slb]) / ema[t - slb], matching
    `compute_htf_ema_slope` evaluated at each closed candle. NaN during warmup.
    """
    df = conn.execute(
        "SELECT open_time, close FROM ohlcv WHERE symbol = ? AND timeframe = ? "
        "ORDER BY open_time",
        [symbol, tf],
    ).df()
    if df.empty:
        return pd.Series(dtype="float64")
    ema = compute_ema(df["close"], period)
    then = ema.shift(slb)
    slope = (ema - then) / then.replace(0.0, pd.NA)
    return pd.Series(slope.to_numpy(), index=df["open_time"].to_numpy())


def _last_closed_open(entry_time_ms: int, tf_ms: int) -> int:
    """open_time of the most recent CLOSED anchor candle at entry (live iloc[-2])."""
    bin_open = entry_time_ms - (entry_time_ms % tf_ms)
    return int(bin_open - tf_ms)


def annotate_suppression(
    trades: pd.DataFrame, conn: duckdb.DuckDBPyConnection, cfg_bias: object
) -> pd.DataFrame:
    """Mark each trade suppressed/kept by replaying the F8 gate per trade."""
    bias = cfg_bias
    deadband: float = bias.htf_ema_deadband_pct  # type: ignore[attr-defined]
    known = set(STRATEGY_REGISTRY)

    # Resolve the per-strategy anchor onto every trade.
    def _anchor(strategy: str) -> tuple[str, int, int]:
        a = bias.htf_ema_anchor(strategy)  # type: ignore[attr-defined]
        return a.tf, a.period, a.slope_lookback

    anchors = {s: _anchor(s) for s in trades["strategy"].unique() if s in known}
    trades = trades[trades["strategy"].isin(anchors)].copy()
    trades["anchor_tf"] = trades["strategy"].map(lambda s: anchors[s][0])
    trades["anchor_period"] = trades["strategy"].map(lambda s: anchors[s][1])
    trades["anchor_slb"] = trades["strategy"].map(lambda s: anchors[s][2])

    parts: list[pd.DataFrame] = []
    group_cols = ["symbol", "anchor_tf", "anchor_period", "anchor_slb"]
    for key, g in trades.groupby(group_cols, sort=False):
        symbol, atf, period, slb = (
            cast(str, key[0]),
            cast(str, key[1]),
            cast(int, key[2]),
            cast(int, key[3]),
        )
        series = _slope_series(conn, symbol, atf, period, slb)
        tf_ms = _TF_MS[str(atf)]
        g = g.copy()
        keys = g["entry_time"].apply(lambda t, _m=tf_ms: _last_closed_open(int(t), _m))
        g["slope"] = keys.map(series) if not series.empty else pd.NA
        parts.append(g)
    out = pd.concat(parts, ignore_index=True) if parts else trades

    slope = out["slope"]
    has_opinion = slope.notna() & (slope.abs() >= deadband)
    opposing = ((slope > 0) & (out["direction"] == "short")) | (
        (slope < 0) & (out["direction"] == "long")
    )
    out["suppressed"] = has_opinion & opposing
    return out


def aggregate(trades: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    grouped = trades.groupby([*by, "suppressed"], sort=False, observed=True)
    agg = grouped.agg(
        n=("pnl_r", "size"),
        avg_r=("pnl_r", "mean"),
        win_rate=("pnl_r", lambda s: (s > 0).mean()),
    ).reset_index()
    return agg.sort_values(
        ["suppressed", *by], ascending=[False, *[True] * len(by)]
    ).reset_index(drop=True)


def _wavg(df: pd.DataFrame) -> tuple[int, float]:
    n = int(df["n"].sum())
    if n == 0:
        return 0, 0.0
    return n, float((df["avg_r"] * df["n"]).sum() / n)


def render(trades: pd.DataFrame) -> str:
    lines: list[str] = []

    # Headline: global suppressed vs kept.
    by_supp = aggregate(trades, [])
    sup = by_supp[by_supp["suppressed"]]
    kept = by_supp[~by_supp["suppressed"]]
    sup_n, sup_avg = _wavg(sup)
    kept_n, kept_avg = _wavg(kept)
    lines.append("=" * 64)
    lines.append("F8 HTF EMA GATE ABLATION — would-suppress subset realized avg_r")
    lines.append("=" * 64)
    lines.append(f"SUPPRESSED (gate drops): n={sup_n:>7}  avg_r={sup_avg:+.4f}")
    lines.append(f"KEPT       (gate allows): n={kept_n:>7}  avg_r={kept_avg:+.4f}")
    lines.append("")

    # Money cut for the EMA-per-TF question: suppressed avg_r by SIGNAL tf × dir.
    lines.append("Suppressed subset by signal timeframe × direction:")
    lines.append("-" * 64)
    lines.append(f"{'tf':<6} {'dir':<6} {'n':>8} {'avg_r':>9} {'win%':>6}")
    lines.append("-" * 64)
    tfdir = aggregate(trades, ["timeframe", "direction"])
    tfdir = tfdir[tfdir["suppressed"]].sort_values(["timeframe", "direction"])
    for _, r in tfdir.iterrows():
        lines.append(
            f"{r['timeframe']:<6} {r['direction']:<6} {r['n']:>8} "
            f"{r['avg_r']:>+9.4f} {r['win_rate'] * 100:>5.1f}%"
        )
    lines.append("")

    # Per-strategy suppressed rollup (which strategies the gate helps/hurts).
    lines.append("Suppressed subset by strategy (n ≥ 30):")
    lines.append("-" * 64)
    lines.append(f"{'strategy':<18} {'anchor':<6} {'n':>8} {'avg_r':>9} {'win%':>6}")
    lines.append("-" * 64)
    strat = aggregate(trades, ["strategy", "anchor_tf"])
    strat = strat[(strat["suppressed"]) & (strat["n"] >= 30)].sort_values("avg_r")
    for _, r in strat.iterrows():
        lines.append(
            f"{r['strategy']:<18} {r['anchor_tf']:<6} {r['n']:>8} "
            f"{r['avg_r']:>+9.4f} {r['win_rate'] * 100:>5.1f}%"
        )
    lines.append("")

    # Verdict.
    lines.append("Decision:")
    if sup_n < _MIN_TRADES_FOR_DECISION:
        lines.append(f"  HOLD — suppressed n={sup_n} < {_MIN_TRADES_FOR_DECISION}.")
    elif sup_avg > 0:
        lines.append(
            f"  GATE SUSPECT — suppressed avg_r={sup_avg:+.4f} > 0. F8 is dropping "
            "net-winning trades; EMA-per-TF anchoring is moot until F8 is "
            "softened/flipped. Investigate the per-tf/strategy rows above."
        )
    elif kept_avg > sup_avg:
        lines.append(
            f"  GATE EARNS KEEP — suppressed avg_r={sup_avg:+.4f} ≤ 0 and kept "
            f"avg_r={kept_avg:+.4f} > suppressed. Anchor-laddering is a legitimate "
            "incremental refinement; promote only OOS-positive per-tf anchors."
        )
    else:
        lines.append(
            f"  AMBIGUOUS — suppressed avg_r={sup_avg:+.4f}, kept avg_r={kept_avg:+.4f}."
        )
    return "\n".join(lines)


def split_is_oos(
    trades: pd.DataFrame, oos_frac: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split trades by entry_time: latest `oos_frac` fraction → out-of-sample.

    oos_frac <= 0 → all in-sample, empty OOS. Deterministic time split (no
    shuffling) so the OOS window is a genuine forward holdout.
    """
    if oos_frac <= 0 or trades.empty:
        return trades, trades.iloc[0:0]
    cutoff = trades["entry_time"].quantile(1.0 - oos_frac)
    is_df = trades[trades["entry_time"] < cutoff]
    oos_df = trades[trades["entry_time"] >= cutoff]
    return is_df, oos_df


def render_is_oos(trades: pd.DataFrame, oos_frac: float) -> str:
    types = {n: s.strategy_type for n, s in STRATEGY_REGISTRY.items()}
    trades = trades.copy()
    trades["type"] = trades["strategy"].map(types)
    is_df, oos_df = split_is_oos(trades, oos_frac)
    lines = ["", f"IS/OOS short-side check (oos_frac={oos_frac}):", "-" * 64]
    lines.append(f"{'type':<14} {'IS short_r':>11} {'OOS short_r':>12} {'verdict':>10}")
    lines.append("-" * 64)

    def _short_r(df: pd.DataFrame, typ: str) -> float | None:
        sub = df[
            (df["type"] == typ) & (df["suppressed"]) & (df["direction"] == "short")
        ]
        return float(sub["pnl_r"].mean()) if len(sub) else None

    for typ in sorted({t for t in types.values() if isinstance(t, str)}):
        is_r, oos_r = _short_r(is_df, typ), _short_r(oos_df, typ)
        if is_r is None or oos_r is None:
            verdict = "n/a"
        elif is_r > 0 and oos_r > 0:
            verdict = "RELAX"
        else:
            verdict = "KEEP"
        is_s = f"{is_r:+.4f}" if is_r is not None else "  --  "
        oos_s = f"{oos_r:+.4f}" if oos_r is not None else "  --  "
        lines.append(f"{typ:<14} {is_s:>11} {oos_s:>12} {verdict:>10}")
    return "\n".join(lines)


def run(
    db_path: Path, config_path: Path, oos_frac: float = 0.0
) -> tuple[pd.DataFrame, str]:
    cfg = load_signal_config(config_path)
    if not cfg.bias.htf_ema_enabled:
        raise SystemExit(f"{config_path}: [bias.htf_ema].enabled is false.")
    conn = duckdb.connect(str(db_path), read_only=True)
    trades = _load_trades(conn)
    if trades.empty:
        raise SystemExit("No closed backtest_trades found.")
    trades = annotate_suppression(trades, conn, cfg.bias)
    verdict = render(trades)
    if oos_frac > 0:
        verdict = verdict + render_is_oos(trades, oos_frac)
    return trades, verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    parser.add_argument(
        "--config", type=Path, default=Path("config/strategy_params.toml")
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--oos-frac",
        type=float,
        default=0.0,
        help="Fraction of latest trades held out for OOS short-side check.",
    )
    args = parser.parse_args(argv)
    trades, verdict = run(args.db, args.config, args.oos_frac)
    print(verdict)
    if args.out is not None:
        aggregate(trades, ["strategy", "timeframe", "direction", "anchor_tf"]).to_csv(
            args.out, index=False
        )
        print(f"\nWrote per-cell CSV: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
