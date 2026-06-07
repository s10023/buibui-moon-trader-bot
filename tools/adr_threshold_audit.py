"""tools/adr_threshold_audit.py — T6 Phase A `adr_suppress_threshold` audit.

Sweeps candidate ADR thresholds against `backtest_trades` to decide whether
the global `bias.adr_suppress_threshold` (currently 0.80) should be tightened.

Replay-only caveat
==================
The current threshold (T_now = 0.80) already dropped every chasing-direction
trade with consumed_ratio >= 0.80 before they reached `backtest_trades`. So the
audit can only test **stricter** (lower) candidate thresholds T_cand < T_now —
those trades are still in the data and can be re-masked. Testing relaxation
(T_cand > T_now) would need a permissive baseline run (T6 engine work or a
one-off backtest with the threshold disabled).

Decision rule per cell (n_supp >= --min-n at this candidate). Each verdict needs
a bootstrap CI on the additionally-suppressed slice clearing the ±bar AND a Holm
multiple-testing-adjusted p-value < alpha (shared across the candidates / cells
tested in one run; see `analytics/audit_guard.py`):
  CI hi <= -bar  AND  p < alpha → ENABLE this candidate (tighten — late-ADR
                          chasing trades in [T_cand, T_now) are losers to drop).
  CI lo >= +bar  AND  p < alpha → DISABLE this candidate (keep T_now —
                          tightening would suppress winners).
  else                  → INSUFFICIENT.

This replaces the prior crude ±0.05R point-estimate bar (P0a-2 sub-PR 2).

The aggregate view (across non-exempt strategies) is the primary signal — the
global `adr_suppress_threshold` is the only knob the current schema exposes.

Usage:
  PYTHONPATH=. poetry run python tools/adr_threshold_audit.py \
      --config config/signal_watch.toml
  PYTHONPATH=. poetry run python tools/adr_threshold_audit.py \
      --config config/signal_watch_weekdays.toml --candidates 0.60,0.65,0.70,0.75
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics import audit_guard  # noqa: E402
from analytics.backtest_config import load_backtest_config  # noqa: E402
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from tools.gate_audit import _resolve_config_run_ids, load_trades  # noqa: E402

DEFAULT_CANDIDATES: tuple[float, ...] = (0.60, 0.65, 0.70, 0.75)


def _compute_trade_ratios(
    ohlcv_df: pd.DataFrame,
    trades_df: pd.DataFrame,
) -> pd.DataFrame:
    """Annotate each trade row with `_ratio` (consumed_ratio at signal candle)
    and `_chasing` (bool — same-direction as the day's move at signal time).

    Mirrors `analytics/signal/gates.py::_filter_signals_by_adr` bit-for-bit so
    the sweep stays in lock-step with the live gate. Trades whose signal candle
    is missing from ohlcv → `_ratio = NaN`, treated as pass-through.
    """
    if trades_df.empty:
        return trades_df.assign(_ratio=pd.Series(dtype=float), _chasing=False)

    df = ohlcv_df.copy()
    df["_date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.date
    df = df.sort_values("open_time")

    day_opens: pd.Series = df.groupby("_date")["open"].first()
    df["_cum_high"] = df.groupby("_date")["high"].cummax()
    df["_cum_low"] = df.groupby("_date")["low"].cummin()
    df["_day_open"] = df["_date"].map(day_opens)
    df["_today_range"] = (df["_cum_high"] - df["_cum_low"]) / df["_day_open"].where(
        df["_day_open"] > 0
    )

    daily = (
        df.groupby("_date")
        .agg(_dh=("high", "max"), _dl=("low", "min"), _do=("open", "first"))
        .sort_index()
    )
    daily["_dr"] = (daily["_dh"] - daily["_dl"]) / daily["_do"].where(daily["_do"] > 0)
    daily["_adr14"] = daily["_dr"].rolling(14, min_periods=1).mean()
    df["_adr14"] = df["_date"].map(daily["_adr14"])
    df["_consumed"] = df["_today_range"] / df["_adr14"].where(df["_adr14"] > 0)
    df["_mid"] = (df["_cum_high"] + df["_cum_low"]) / 2
    df["_move_up"] = (df["close"] > df["_mid"]).astype(float)

    consumed_map: dict[int, float] = dict(
        zip(df["open_time"].astype(int), df["_consumed"].astype(float), strict=False)
    )
    move_up_map: dict[int, float] = dict(
        zip(df["open_time"].astype(int), df["_move_up"], strict=False)
    )

    out = trades_df.copy()
    keys = out["signal_time"].astype(int)
    out["_ratio"] = keys.map(consumed_map)
    move_up = keys.map(move_up_map)
    out["_chasing"] = (
        ((move_up == 1.0) & (out["direction"] == "long"))
        | ((move_up == 0.0) & (out["direction"] == "short"))
    ).fillna(False)
    return out


def _annotate_trades(
    trades_df: pd.DataFrame,
    db_path: Path,
) -> pd.DataFrame:
    """Per-(symbol, tf) load OHLCV from DuckDB and tag every trade with ratio
    + chasing. Returns a frame indexed identically to `trades_df` plus columns
    `_ratio`, `_chasing`.
    """
    cache: dict[tuple[str, str], pd.DataFrame] = {}

    def load(symbol: str, tf: str) -> pd.DataFrame:
        key = (symbol, tf)
        if key not in cache:
            with duckdb.connect(str(db_path), read_only=True) as conn:
                cache[key] = conn.execute(
                    "SELECT open_time, open, high, low, close FROM ohlcv "
                    "WHERE symbol = ? AND timeframe = ? ORDER BY open_time",
                    [symbol, tf],
                ).fetchdf()
        return cache[key]

    parts: list[pd.DataFrame] = []
    for (symbol, tf), sub in trades_df.groupby(["symbol", "tf"], dropna=False):
        ohlcv = load(str(symbol), str(tf))
        parts.append(_compute_trade_ratios(ohlcv, sub))
    if not parts:
        return trades_df.assign(_ratio=pd.Series(dtype=float), _chasing=False)
    out = pd.concat(parts, axis=0).sort_index()
    return out


def _exempt_strategies(config_path: Path) -> set[str]:
    cfg = load_backtest_config(config_path)
    return {n for n, o in cfg.strategy_params.items() if o.adr_exempt}


def _current_threshold(config_path: Path) -> float:
    cfg = load_backtest_config(config_path)
    return cfg.adr_suppress_threshold or 0.80


def aggregate_sweep(
    df: pd.DataFrame,
    candidates: list[float],
    current_threshold: float,
    exempt: set[str],
    min_n: int,
    verdict_threshold: float,
    *,
    alpha: float = audit_guard.DEFAULT_ALPHA,
    n_boot: int = audit_guard.DEFAULT_N_BOOT,
    seed: int | None = audit_guard.DEFAULT_SEED,
) -> pd.DataFrame:
    """For each candidate < current_threshold, mask additionally-suppressed
    trades (chasing & ratio in [candidate, current_threshold) & non-exempt) and
    aggregate avg_r across the whole non-exempt population.

    Verdicts come from `analytics.audit_guard.evaluate_audit_cells`; the Holm
    haircut is shared across all candidates tested in this sweep.
    """
    df = df.copy()
    df["_pnl"] = pd.to_numeric(df["pnl_r"], errors="coerce")
    df = df.dropna(subset=["_pnl"])
    non_exempt = df[~df["strategy"].isin(exempt)]

    metas: list[tuple[float, pd.DataFrame]] = []
    cells: list[audit_guard.AuditCell] = []
    for cand in candidates:
        mask = (
            non_exempt["_chasing"]
            & non_exempt["_ratio"].notna()
            & (non_exempt["_ratio"] >= cand)
            & (non_exempt["_ratio"] < current_threshold)
        )
        supp = non_exempt[mask]
        kept = non_exempt[~mask]
        cells.append(
            audit_guard.AuditCell(label=str(cand), supp_r=supp["_pnl"].tolist())
        )
        metas.append((cand, kept))

    verdicts = audit_guard.evaluate_audit_cells(
        cells,
        bar=verdict_threshold,
        alpha=alpha,
        min_n=min_n,
        n_boot=n_boot,
        seed=seed,
        enable_concentrate=False,
    )

    rows = []
    for (cand, kept), v in zip(metas, verdicts, strict=True):
        n_kept = len(kept)
        kept_avg = kept["_pnl"].mean() if n_kept else float("nan")
        rows.append(
            {
                "candidate": cand,
                "n_supp": v.n_supp,
                "supp_avg_r": round(v.supp_avg, 4) if v.supp_avg is not None else None,
                "ci_lo": round(v.ci_lo, 4) if v.ci_lo is not None else None,
                "ci_hi": round(v.ci_hi, 4) if v.ci_hi is not None else None,
                "adj_p": round(v.adj_pvalue, 4) if v.adj_pvalue is not None else None,
                "n_kept": n_kept,
                "kept_avg_r": round(kept_avg, 4) if n_kept else None,
                "verdict": v.decision,
            }
        )
    return pd.DataFrame(rows)


def per_strategy_sweep(
    df: pd.DataFrame,
    candidate: float,
    current_threshold: float,
    exempt: set[str],
    min_n: int,
    verdict_threshold: float,
    *,
    alpha: float = audit_guard.DEFAULT_ALPHA,
    n_boot: int = audit_guard.DEFAULT_N_BOOT,
    seed: int | None = audit_guard.DEFAULT_SEED,
) -> pd.DataFrame:
    """Per-(strategy, tf, direction) view at one candidate threshold. Returns
    the same n_supp / supp_avg_r / CI / verdict columns as `aggregate_sweep`.

    The Holm haircut is shared across every (strategy, tf, direction) cell in
    this view.
    """
    df = df.copy()
    df["_pnl"] = pd.to_numeric(df["pnl_r"], errors="coerce")
    df = df.dropna(subset=["_pnl"])
    df = df[~df["strategy"].isin(exempt)]
    df["_suppressed"] = (
        df["_chasing"]
        & df["_ratio"].notna()
        & (df["_ratio"] >= candidate)
        & (df["_ratio"] < current_threshold)
    )

    metas: list[tuple[object, object, object, pd.DataFrame]] = []
    cells: list[audit_guard.AuditCell] = []
    for key, sub in df.groupby(["strategy", "tf", "direction"], dropna=False):
        assert isinstance(key, tuple)
        strategy, tf, direction = key
        supp = sub[sub["_suppressed"]]
        kept = sub[~sub["_suppressed"]]
        cells.append(
            audit_guard.AuditCell(
                label=f"{strategy} × {tf} × {direction}",
                supp_r=supp["_pnl"].tolist(),
            )
        )
        metas.append((strategy, tf, direction, kept))

    verdicts = audit_guard.evaluate_audit_cells(
        cells,
        bar=verdict_threshold,
        alpha=alpha,
        min_n=min_n,
        n_boot=n_boot,
        seed=seed,
        enable_concentrate=False,
    )

    rows = []
    for (strategy, tf, direction, kept), v in zip(metas, verdicts, strict=True):
        n_kept = len(kept)
        kept_avg = kept["_pnl"].mean() if n_kept else float("nan")
        rows.append(
            {
                "strategy": strategy,
                "tf": tf,
                "direction": direction,
                "n_kept": n_kept,
                "kept_avg_r": round(kept_avg, 4) if n_kept else None,
                "n_supp": v.n_supp,
                "supp_avg_r": round(v.supp_avg, 4) if v.supp_avg is not None else None,
                "ci_lo": round(v.ci_lo, 4) if v.ci_lo is not None else None,
                "ci_hi": round(v.ci_hi, 4) if v.ci_hi is not None else None,
                "adj_p": round(v.adj_pvalue, 4) if v.adj_pvalue is not None else None,
                "verdict": v.decision,
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["strategy", "tf", "direction"]).reset_index(drop=True)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="T6 Phase A `adr_suppress_threshold` sweep (replay-only).",
    )
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument(
        "--candidates",
        type=str,
        default=",".join(str(c) for c in DEFAULT_CANDIDATES),
        help="Comma-separated candidate thresholds < current threshold (default %(default)s).",
    )
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument(
        "--verdict-threshold",
        type=float,
        default=0.05,
        help="±bar (R) the bootstrap CI must clear for ENABLE/DISABLE.",
    )
    p.add_argument(
        "--alpha",
        type=float,
        default=audit_guard.DEFAULT_ALPHA,
        help="CI level + Holm-adjusted p-value significance cutoff (default %(default)s).",
    )
    p.add_argument(
        "--n-boot",
        type=int,
        default=audit_guard.DEFAULT_N_BOOT,
        help="Bootstrap resamples for the per-cell CI (default %(default)s).",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=audit_guard.DEFAULT_SEED,
        help="Bootstrap RNG seed for reproducible verdicts (default %(default)s).",
    )
    p.add_argument(
        "--per-strategy-at",
        type=float,
        default=None,
        help="Also print per-(strategy, tf, direction) view at this candidate "
        "(default = strictest candidate in --candidates).",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    candidates = sorted({float(x) for x in args.candidates.split(",")})
    current = _current_threshold(args.config)
    invalid = [c for c in candidates if c >= current]
    if invalid:
        print(
            f"ERROR: candidates must be < current threshold ({current}); "
            f"got invalid: {invalid}"
        )
        return 2

    exempt = _exempt_strategies(args.config)
    run_ids = _resolve_config_run_ids(args.db, args.config)
    if not run_ids:
        print(f"No sweep run_ids resolved for {args.config}")
        return 1
    df = load_trades(args.db, run_ids, since_ms=None)
    if df.empty:
        print(f"No trades for {args.config} sweep — nothing to audit.")
        return 1

    annotated = _annotate_trades(df, args.db)
    print(f"Config: {args.config}  (current threshold = {current})")
    print(f"Scope: {len(run_ids)} run_ids, {len(annotated)} trades")
    print(f"Exempt strategies (skipped): {sorted(exempt) if exempt else '(none)'}")
    print(f"Candidate thresholds: {candidates}")
    print(
        f"min_n = {args.min_n}, verdict bar = ±{args.verdict_threshold}R, "
        f"alpha = {args.alpha}, n_boot = {args.n_boot}"
    )
    print()

    agg = aggregate_sweep(
        annotated,
        candidates,
        current,
        exempt,
        args.min_n,
        args.verdict_threshold,
        alpha=args.alpha,
        n_boot=args.n_boot,
        seed=args.seed,
    )
    print("--- aggregate sweep (non-exempt population) ---")
    print(agg.to_string(index=False))
    print()

    per_at = args.per_strategy_at if args.per_strategy_at is not None else candidates[0]
    per = per_strategy_sweep(
        annotated,
        per_at,
        current,
        exempt,
        args.min_n,
        args.verdict_threshold,
        alpha=args.alpha,
        n_boot=args.n_boot,
        seed=args.seed,
    )
    print(f"--- per-(strategy, tf, direction) at candidate {per_at} ---")
    if per.empty:
        print("(empty)")
    else:
        print(per.to_string(index=False))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
