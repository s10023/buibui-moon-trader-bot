#!/usr/bin/env python3
"""ST56 — run the pre-registered wick-anchor construction.

Pre-registration and power pricing:
``docs/audits/2026-08-20-st56-wick-fill-anchor-power.md``. ONE construction, ONE
trial, no parameter search. Both arms share the same fire set and the same legal
``i+2`` entry, so the only thing that differs between them is the stop geometry.

This is tracked code on purpose. ST28's sixth powered-null site sat in a
gitignored scratch driver, unreachable by every gate, grep and review surface
this repo has.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.forecast import effective_independent_series  # noqa: E402
from analytics.research_guards import (  # noqa: E402
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    passes_gate,
    per_period_sharpe,
)
from analytics.signal.resolvers import _resolve_sl_pct, _resolve_tp_r  # noqa: E402
from analytics.signal.scanner import _resolve_outcome_sl_tp  # noqa: E402
from analytics.signal_config import load_signal_config  # noqa: E402
from analytics.store import DEFAULT_DB_PATH, get_ohlcv  # noqa: E402
from analytics.strategies.wick_fills import detect_wick_fills  # noqa: E402
from analytics.wick_anchor import (  # noqa: E402
    classify_production_geometry,
    entry_index_for_fire,
    simulate_exit,
    wick_anchor_stop,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAMES = ("15m", "1h", "4h", "1d")
STRATEGY = "wick_fill"


@dataclass
class Drops:
    """Every fire that did not produce a paired trade, by reason."""

    no_legal_entry: int = 0
    wick_wrong_side: int = 0
    unresolved: int = 0

    @property
    def total(self) -> int:
        return self.no_legal_entry + self.wick_wrong_side + self.unresolved


def build_pairs(
    conn: duckdb.DuckDBPyConnection,
    *,
    sl_pct_global: float,
    tp_r_global: float,
    min_sl_pct: float,
    strategy_params: object,
) -> tuple[pd.DataFrame, Drops, int]:
    """Return (paired trades, drop report, total fires)."""
    rows: list[dict[str, object]] = []
    drops = Drops()
    fires = 0

    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            print(f"  ... {symbol} {tf}", file=sys.stderr, flush=True)
            df = get_ohlcv(conn, symbol, tf, 0, 2_000_000_000_000)
            if df.empty:
                continue
            signals = detect_wick_fills(df)
            if signals.empty:
                continue

            idx_of = {int(t): i for i, t in enumerate(df["open_time"].to_numpy())}
            highs = df["high"].to_numpy(dtype=float)
            lows = df["low"].to_numpy(dtype=float)
            opens = df["open"].to_numpy(dtype=float)
            times = df["open_time"].to_numpy()
            n_bars = len(df)

            sl_pct = _resolve_sl_pct(
                strategy_params,  # type: ignore[arg-type]
                STRATEGY,
                symbol,
                tf,
                sl_pct_global,
            )

            for _, sig in signals.iterrows():
                fires += 1
                fire_idx = idx_of.get(int(sig["open_time"]))
                if fire_idx is None:
                    drops.no_legal_entry += 1
                    continue
                entry_idx = entry_index_for_fire(fire_idx, n_bars)
                if entry_idx is None:
                    drops.no_legal_entry += 1
                    continue

                direction = str(sig["direction"])
                entry = float(opens[entry_idx])
                struct_sl = float(sig["sl_price"])
                tp_r = _resolve_tp_r(
                    strategy_params,  # type: ignore[arg-type]
                    STRATEGY,
                    symbol,
                    tf,
                    tp_r_global,
                    direction,
                )

                prod_sl, prod_tp = _resolve_outcome_sl_tp(
                    direction=direction,
                    entry=entry,
                    struct_sl=struct_sl,
                    struct_tp=0.0,
                    eff_sl_pct=sl_pct,
                    min_sl_pct=min_sl_pct,
                    tp_r=tp_r,
                )
                geometry = classify_production_geometry(
                    direction, entry, struct_sl, sl_pct, min_sl_pct
                )

                wick_sl = wick_anchor_stop(direction, entry, struct_sl)
                if wick_sl is None:
                    drops.wick_wrong_side += 1
                    continue
                wick_dist = abs(entry - wick_sl)
                wick_tp = (
                    entry + wick_dist * tp_r
                    if direction == "long"
                    else entry - wick_dist * tp_r
                )

                prod = simulate_exit(
                    highs, lows, entry_idx, direction, sl=prod_sl, tp=prod_tp, tp_r=tp_r
                )
                wick = simulate_exit(
                    highs, lows, entry_idx, direction, sl=wick_sl, tp=wick_tp, tp_r=tp_r
                )
                if prod.r is None or wick.r is None:
                    drops.unresolved += 1
                    continue

                rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": tf,
                        "direction": direction,
                        "day": pd.Timestamp(times[entry_idx], unit="ms").date(),
                        "geometry": geometry,
                        "tp_r": tp_r,
                        "prod_sl_pct": abs(entry - prod_sl) / entry * 100.0,
                        "wick_sl_pct": wick_dist / entry * 100.0,
                        "prod_r": prod.r,
                        "wick_r": wick.r,
                        "diff_r": wick.r - prod.r,
                        "ambiguous": prod.ambiguous or wick.ambiguous,
                        "prod_ambiguous": prod.ambiguous,
                        "wick_ambiguous": wick.ambiguous,
                        "diff_r_optimistic": float(wick.r_optimistic or 0.0)
                        - float(prod.r_optimistic or 0.0),
                    }
                )

    return pd.DataFrame(rows), drops, fires


def _skew(x: npt.NDArray[np.float64]) -> float:
    """Sample skewness. Computed here so the DSR call carries no pandas typing."""
    sd = float(np.std(x, ddof=1))
    if sd == 0.0:
        return 0.0
    return float(np.mean(((x - float(np.mean(x))) / sd) ** 3))


def _kurtosis(x: npt.NDArray[np.float64]) -> float:
    """NON-EXCESS kurtosis (normal = 3.0), matching ``probabilistic_sharpe_ratio``."""
    sd = float(np.std(x, ddof=1))
    if sd == 0.0:
        return 3.0
    return float(np.mean(((x - float(np.mean(x))) / sd) ** 4))


def _daily_matrix(pairs: pd.DataFrame) -> npt.NDArray[np.float64]:
    """(T_days, N_cells x 2 arms) — the honest selection family.

    One pre-registered construction means no parameter was searched, but a
    deployment still has to CHOOSE cells, and choosing is what PBO prices. The
    columns are therefore every (cell, arm), not every parameter setting.
    """
    frames: list[pd.Series] = []
    for (sym, tf, direction), cell in pairs.groupby(
        ["symbol", "timeframe", "direction"]
    ):
        for arm in ("prod_r", "wick_r"):
            s = cell.groupby("day")[arm].sum()
            s.name = f"{sym}:{tf}:{direction}:{arm}"
            frames.append(s)
    if not frames:
        return np.zeros((0, 0), dtype=float)
    wide = pd.concat(frames, axis=1).fillna(0.0).sort_index()
    return wide.to_numpy(dtype=float)


def report(
    pairs: pd.DataFrame, drops: Drops, fires: int, n_boot: int = 10_000
) -> list[str]:
    out = [
        "ST56 - wick-anchor construction (pre-registered, ONE trial)",
        "",
        f"  fires                {fires:,}",
        f"  paired trades        {len(pairs):,}",
        f"  dropped              {drops.total:,}"
        f"  (no legal entry {drops.no_legal_entry:,}"
        f" / wick wrong side {drops.wick_wrong_side:,}"
        f" / unresolved {drops.unresolved:,})",
    ]
    if pairs.empty:
        out.append("  NO PAIRED TRADES - nothing to report")
        return out

    drop_rate = drops.wick_wrong_side / fires if fires else 0.0
    out.append(
        f"  wick-anchor drop rate {drop_rate:.1%}  (Decision Log reverses at ~40%)"
    )
    out.append(
        f"  intrabar-ambiguous   {int(pairs['ambiguous'].sum()):,}"
        f"  (production {int(pairs['prod_ambiguous'].sum()):,}"
        f" / wick {int(pairs['wick_ambiguous'].sum()):,})"
    )
    out.append("")
    out.append("  production geometry on the paired set:")
    for label, n in pairs["geometry"].value_counts().items():
        out.append(f"    {str(label):<12} {n:>7,}  ({n / len(pairs):.1%})")

    prod = pairs["prod_r"].to_numpy(dtype=float)
    wick = pairs["wick_r"].to_numpy(dtype=float)
    diff = pairs["diff_r"].to_numpy(dtype=float)
    out += [
        "",
        f"  median stop width    production {pairs['prod_sl_pct'].median():.4f}%"
        f"   wick {pairs['wick_sl_pct'].median():.4f}%",
        f"  mean R  production   {prod.mean():+.4f}",
        f"  mean R  wick anchor  {wick.mean():+.4f}",
        f"  paired difference    {diff.mean():+.4f}  (sd {diff.std(ddof=1):.4f})",
    ]

    per_symbol = {
        str(sym): g.set_index("day")["diff_r"].groupby(level=0).sum()
        for sym, g in pairs.groupby("symbol")
    }
    n_eff, deflator = effective_independent_series(per_symbol)
    eff_n = int(round(len(diff) / (deflator**2)))
    out += [
        "",
        f"  n_eff {n_eff:.4f} / {len(per_symbol)} series, deflator {deflator:.4f}",
        f"  effective n          {eff_n:,}  (declared {len(diff):,})",
    ]

    sr = per_period_sharpe(diff)
    cells = [
        per_period_sharpe(g["diff_r"].to_numpy(dtype=float))
        for _, g in pairs.groupby(["symbol", "timeframe", "direction"])
        if len(g) >= 20 and g["diff_r"].std(ddof=1) > 0
    ]
    sr_variance = float(np.var(cells, ddof=1)) if len(cells) > 1 else 0.01
    dsr = deflated_sharpe_ratio(
        sr,
        eff_n,
        n_trials=1,
        sr_variance=sr_variance,
        skew=_skew(diff),
        kurtosis=_kurtosis(diff),
    )
    boot = block_bootstrap_ci(
        diff, stat_fn=lambda x: float(np.mean(x)), seed=7, n_boot=n_boot
    )
    mat = _daily_matrix(pairs)
    pbo = (
        cscv_pbo(mat).pbo if mat.shape[0] >= 28 and mat.shape[1] >= 2 else float("nan")
    )

    verdict = passes_gate(dsr, pbo, boot.lo)
    opt = pairs["diff_r_optimistic"].to_numpy(dtype=float)
    boot_opt = block_bootstrap_ci(
        opt, stat_fn=lambda x: float(np.mean(x)), seed=7, n_boot=n_boot
    )
    out += [
        "",
        "  THE GATE - DSR >= 0.95 AND PBO <= 0.5 AND boot_lo > 0",
        f"    per-trade Sharpe   {sr:+.6f}",
        f"    DSR                {dsr:.4f}",
        f"    PBO                {pbo:.4f}",
        f"    boot CI            [{boot.lo:+.4f}, {boot.hi:+.4f}]",
        "",
        f"  VERDICT              {'PASSES' if verdict else 'FAILS'} the three-leg gate",
        "",
        "  SENSITIVITY - not the verdict, a bound on the tie-break's contribution",
        "  (every ambiguous bar resolved as the WIN instead of the loss)",
        f"    paired difference  {opt.mean():+.4f}",
        f"    boot CI            [{boot_opt.lo:+.4f}, {boot_opt.hi:+.4f}]",
        f"    sign flips?        {'YES - the verdict rests on the tie-break' if opt.mean() > 0 else 'NO - the result survives its most hostile tie-break'}",
    ]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wick_anchor_study")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--config", default="config/signal_watch.toml")
    parser.add_argument(
        "--n-boot",
        type=int,
        default=10_000,
        help=(
            "bootstrap resamples. The default reproduces the filed audit and costs "
            "~10 minutes of the run at n=336k; drop it for a quick look, but a "
            "figure quoted anywhere must come from the default."
        ),
    )
    args = parser.parse_args(argv)

    cfg = load_signal_config(Path(args.config))
    conn = duckdb.connect(args.db, read_only=True)
    try:
        pairs, drops, fires = build_pairs(
            conn,
            sl_pct_global=cfg.sl_pct,
            tp_r_global=cfg.tp_r,
            min_sl_pct=cfg.min_sl_pct,
            strategy_params=cfg.strategy_params,
        )
    finally:
        conn.close()

    print("\n".join(report(pairs, drops, fires, n_boot=args.n_boot)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
