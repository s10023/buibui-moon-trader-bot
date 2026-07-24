#!/usr/bin/env python
"""H8 M1 indicator-state conditioning audit — read-only.

Tags every historical trade with the M1 brief-indicator state (EMA
stack/slope, regime, Bollinger squeeze/%B, anchored-VWAP distance,
volume-profile value-area position, price-action character, Monday-range)
that held **as of its own entry** and emits a pre-committed
BUILD / AVOID / NO-EDGE / INSUFFICIENT verdict per (axis-state x direction)
via :mod:`analytics.indicator_condition` (block-bootstrap CI + Holm haircut +
a two-sample with-vs-without lift CI + DSR/PBO family stamps).

Substrate roles (pre-committed, spec §3): ``backtest_trades`` = primary (gate
-deciding); ``signal_alert_outcomes`` (live) = corroboration only — thin and
irregularly sampled, never gate-deciding.

Unlike ``tools/warning_value_audit.py`` this driver keys trades off
``entry_time`` (not ``signal_time``) — the M1 states must be re-derived as of
the moment the position was actually taken (design doc §6), and always from
the symbol's 1d + 1h OHLCV regardless of the trade's own strategy timeframe
(mirrors the brief panel). ``_load_market`` is reused verbatim from
``warning_value_audit`` (called twice, once per timeframe, via a synthetic
entries frame); the entry loaders here are local because their column
selection differs (entry_time vs signal_time).

Read-only (``duckdb.connect(..., read_only=True)``); no engine/schema/golden
change. Run:
    PYTHONPATH=. poetry run python tools/indicator_condition_audit.py
(wrapped by ``make buibui-indicator-condition-audit``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.indicator_condition import (  # noqa: E402
    _AXES,
    ConditionVerdict,
    IndicatorConditionConfig,
    build_condition_cells,
    evaluate_conditions,
    tag_trades,
)
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.strategies._registry import STRATEGY_REGISTRY  # noqa: E402
from tools.warning_value_audit import _load_market  # noqa: E402

SourceResult = tuple[list[ConditionVerdict], pd.DataFrame, int]

_DEFAULT_D1_HISTORY_BARS = 300  # >= 200 for EmaState.stack + regime's 90d window
_DEFAULT_H1_HISTORY_BARS = 1500  # ~62d, covers the 60d volume-profile window


# --------------------------------------------------------------------------- #
# source normalization (pure) — keyed on entry_time, NOT signal_time          #
# --------------------------------------------------------------------------- #


def normalize_live(df: pd.DataFrame) -> pd.DataFrame:
    """``signal_alert_outcomes`` rows -> the common entry frame.

    No real "entry_time" column exists for live rows; ``candle_ts_ms`` (the
    alert's own candle) is the best available proxy — consistent with this
    source being corroboration-only, never gate-deciding.
    """
    out = pd.DataFrame(
        {
            "symbol": df["symbol"],
            "tf": df["tf"],
            "strategy": df["strategy"],
            "direction": df["direction"],
            "entry_time": df["candle_ts_ms"],
            "pnl_r": df["outcome_r"],
        }
    )
    return out.dropna(subset=["entry_time", "pnl_r"]).reset_index(drop=True)


def normalize_backtest(df: pd.DataFrame) -> pd.DataFrame:
    """``backtest_trades`` rows -> the common entry frame, deduped across runs.

    Dedup on (symbol, tf, strategy, direction, entry_time) keeping the
    lexicographically-latest ``run_id`` (mirrors
    ``warning_value_audit.normalize_backtest``).
    """
    out = pd.DataFrame(
        {
            "run_id": df["run_id"],
            "symbol": df["symbol"],
            "tf": df["timeframe"],
            "strategy": df["strategy"],
            "direction": df["direction"],
            "entry_time": df["entry_time"],
            "pnl_r": df["pnl_r"],
        }
    ).dropna(subset=["entry_time", "pnl_r"])
    out = out.sort_values("run_id", kind="stable").drop_duplicates(
        subset=["symbol", "tf", "strategy", "direction", "entry_time"], keep="last"
    )
    return out.drop(columns=["run_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# DB front door (read-only)                                                    #
# --------------------------------------------------------------------------- #


def _load_entries(db: Path, src: str, since_ms: int | None) -> pd.DataFrame:
    with duckdb.connect(str(db), read_only=True) as conn:
        if src == "live":
            q = (
                "SELECT symbol, tf, strategy, direction, candle_ts_ms, outcome_r "
                "FROM signal_alert_outcomes "
                "WHERE outcome_r IS NOT NULL AND candle_ts_ms IS NOT NULL"
            )
            if since_ms is not None:
                q += f" AND candle_ts_ms >= {since_ms}"
            return normalize_live(conn.execute(q).df())
        q = (
            "SELECT run_id, symbol, timeframe, strategy, direction, "
            "entry_time, pnl_r "
            "FROM backtest_trades WHERE pnl_r IS NOT NULL"
        )
        if since_ms is not None:
            q += f" AND entry_time >= {since_ms}"
        return normalize_backtest(conn.execute(q).df())


def _load_indicator_market(
    db: Path,
    entries: pd.DataFrame,
    *,
    d1_bars: int,
    h1_bars: int,
) -> dict[tuple[str, str], pd.DataFrame]:
    """1d + 1h OHLCV per distinct symbol, regardless of the trade's own tf.

    Reuses ``warning_value_audit._load_market`` verbatim by handing it a
    synthetic entries frame with ``tf`` forced to "1d" / "1h" and
    ``entry_time`` renamed to the ``ts_ms`` column it expects — its fetch
    window is one continuous [tmin-margin, tmax+1bar] block per symbol, so
    every trade between the earliest and latest gets whatever history that
    block covers (only the very earliest trades in the dataset are margin-
    limited to ``{d1,h1}_bars``).
    """
    base = entries.rename(columns={"entry_time": "ts_ms"})[["symbol", "ts_ms"]]
    market: dict[tuple[str, str], pd.DataFrame] = {}
    market.update(_load_market(db, base.assign(tf="1d"), d1_bars))
    market.update(_load_market(db, base.assign(tf="1h"), h1_bars))
    return market


# --------------------------------------------------------------------------- #
# per-strategy-TYPE diagnostic (reported, NOT gate-deciding — spec §5)         #
# --------------------------------------------------------------------------- #


def _with_strategy_type(tagged: pd.DataFrame) -> pd.DataFrame:
    out = tagged.copy()
    out["strategy_type"] = out["strategy"].map(
        lambda s: STRATEGY_REGISTRY[s].strategy_type if s in STRATEGY_REGISTRY else ""
    )
    return out


def diagnostic_by_strategy_type(
    tagged: pd.DataFrame, cfg: IndicatorConditionConfig
) -> dict[str, list[ConditionVerdict]]:
    """One independent gate family per strategy-type slice. Diagnostic color
    only — never feeds the headline pooled verdict."""
    if tagged.empty or "strategy_type" not in tagged.columns:
        return {}
    out: dict[str, list[ConditionVerdict]] = {}
    for stype, sub in tagged.groupby("strategy_type", sort=True):
        label = str(stype) if str(stype) else "(unclassified)"
        cells = build_condition_cells(sub, axes=_AXES)
        out[label] = evaluate_conditions(cells, cfg)
    return out


# --------------------------------------------------------------------------- #
# report                                                                       #
# --------------------------------------------------------------------------- #


def _fmt(x: float | None) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:+.3f}"


def _sort_key(v: ConditionVerdict) -> tuple[int, str, str, str]:
    order = {"BUILD": 0, "AVOID": 0, "NO-EDGE": 1, "INSUFFICIENT": 2}
    return (order.get(v.verdict, 3), v.axis, v.state, v.direction)


def _verdict_table(verdicts: list[ConditionVerdict]) -> list[str]:
    lines = [
        "| axis | state | dir | n_with | n_without | avg_with | avg_without "
        "| lift | lift lo | lift hi | dsr | pbo | verdict |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
        "| --- | --- |",
    ]
    for v in sorted(verdicts, key=_sort_key):
        lines.append(
            f"| {v.axis} | {v.state} | {v.direction} | {v.n_with} | {v.n_without} "
            f"| {_fmt(v.avg_r_with)} | {_fmt(v.avg_r_without)} | {_fmt(v.lift)} "
            f"| {_fmt(v.lift_lo)} | {_fmt(v.lift_hi)} | {_fmt(v.dsr)} | {_fmt(v.pbo)} "
            f"| {v.verdict} |"
        )
    return lines


def format_report(
    results: dict[str, SourceResult],
    diagnostics: dict[str, dict[str, list[ConditionVerdict]]],
    *,
    min_n: int,
    cfg: IndicatorConditionConfig,
) -> str:
    lines: list[str] = []
    lines.append(
        "# H8 M1 indicator-state conditioning audit — does the M1 indicator "
        "state predict avg_r beyond the base rate?"
    )
    lines.append("")
    lines.append(
        f"Generated by `tools/indicator_condition_audit.py` (read-only). "
        f"Params: min_n={min_n}, bar=±{cfg.bar}R, alpha={cfg.alpha}, "
        f"n_boot={cfg.n_boot}, seed={cfg.seed}, dsr_floor={cfg.dsr_floor}, "
        f"pbo_ceil={cfg.pbo_ceil}."
    )
    lines.append("")
    lines.append(
        "Pre-committed semantics (design doc §7): BUILD = the with-state "
        "slice is reliably positive AND beats the without-state slice "
        "(audit_guard DISABLE + lift CI excludes 0 + DSR/PBO family gate). "
        "AVOID = the with-state slice is reliably negative and worse "
        "(audit_guard ENABLE). NO-EDGE = well-powered, no gate-grade effect. "
        "INSUFFICIENT = n < min_n. backtest_trades = primary substrate "
        "(gate-deciding); signal_alert_outcomes (live) = corroboration only."
    )
    lines.append("")
    lines.append("## Headline (backtest primary, pooled across strategies)")
    lines.append("")
    bt = results.get("backtest")
    if bt is None:
        lines.append("- (backtest source not run — no primary verdict)")
    else:
        build = [
            f"{v.axis}/{v.state}/{v.direction}" for v in bt[0] if v.verdict == "BUILD"
        ]
        avoid = [
            f"{v.axis}/{v.state}/{v.direction}" for v in bt[0] if v.verdict == "AVOID"
        ]
        if build:
            lines.append("- BUILD: " + ", ".join(build))
        if avoid:
            lines.append("- AVOID: " + ", ".join(avoid))
        if not build and not avoid:
            lines.append(
                "- NO-EDGE / INSUFFICIENT across every pre-registered axis — "
                "conditional-edge = NO holds on the M1 axes too"
            )
    for src, (verdicts, tagged, n_dropped) in results.items():
        lines.append("")
        lines.append(f"## Source: {src} ({len(tagged)} tagged, {n_dropped} dropped)")
        lines.append("")
        lines.extend(_verdict_table(verdicts))
        diag = diagnostics.get(src, {})
        if diag:
            lines.append("")
            lines.append(f"### Diagnostic per strategy-type ({src}, NOT gate-deciding)")
            lines.append("")
            for stype, tverdicts in sorted(diag.items()):
                interesting = [v for v in tverdicts if v.verdict in ("BUILD", "AVOID")]
                if not interesting:
                    continue
                lines.append(f"#### {stype}")
                lines.append("")
                lines.extend(_verdict_table(interesting))
                lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="H8: does the M1 indicator state predict avg_r beyond the base rate?"
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument("--source", choices=["live", "backtest", "both"], default="both")
    p.add_argument(
        "--timeframes", nargs="*", default=None, help="filter entries by their own tf"
    )
    p.add_argument("--since-days", type=int, default=None)
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--bar", type=float, default=0.05)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-boot", type=int, default=2000)
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--d1-history-bars", type=int, default=_DEFAULT_D1_HISTORY_BARS)
    p.add_argument("--h1-history-bars", type=int, default=_DEFAULT_H1_HISTORY_BARS)
    p.add_argument("--out", type=Path, default=None)
    return p


def main() -> int:
    args = build_parser().parse_args()
    since_ms: int | None = None
    if args.since_days is not None:
        since_ms = int(
            (
                pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=args.since_days)
            ).timestamp()
            * 1000
        )
    cfg = IndicatorConditionConfig(
        bar=args.bar,
        alpha=args.alpha,
        min_n=args.min_n,
        n_boot=args.n_boot,
        seed=args.seed,
    )
    sources = ["backtest", "live"] if args.source == "both" else [args.source]
    results: dict[str, SourceResult] = {}
    diagnostics: dict[str, dict[str, list[ConditionVerdict]]] = {}
    for src in sources:
        entries = _load_entries(args.db, src, since_ms)
        if args.timeframes:
            entries = entries[entries["tf"].isin(args.timeframes)]
        if entries.empty:
            print(f"[warn] no entries for source={src}", file=sys.stderr)
            continue
        market = _load_indicator_market(
            args.db,
            entries,
            d1_bars=args.d1_history_bars,
            h1_bars=args.h1_history_bars,
        )
        tagged = tag_trades(entries, market)
        n_dropped = int(tagged[list(_AXES)].isna().all(axis=1).sum())
        cells = build_condition_cells(tagged, axes=_AXES)
        verdicts = evaluate_conditions(cells, cfg)
        results[src] = (verdicts, tagged, n_dropped)
        diagnostics[src] = diagnostic_by_strategy_type(_with_strategy_type(tagged), cfg)
    if not results:
        print("no data — nothing to evaluate", file=sys.stderr)
        return 1
    report = format_report(results, diagnostics, min_n=args.min_n, cfg=cfg)
    print(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)
        print(f"[saved] {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
