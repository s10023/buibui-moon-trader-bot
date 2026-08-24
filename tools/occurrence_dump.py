#!/usr/bin/env python
"""ST63 occurrence dump — read-only, per-strategy, and deliberately VERDICT-FREE.

One row per strategy fire, tagged with the M1 indicator state that held **as of
its own entry**, so the operator can eyeball which conditions separate the good
fires from the bad ("only look at long signal A when above some vwap/MA").

⚠ **THE DUMP IS THE PRODUCT. THE GATE IS NOT BUILDABLE, AND THAT SPLIT IS WHAT
SAVES THIS ITEM.** Conditioning axes are 6-for-6-plus-one-amended NO in this
repo, and trial count dominates n: 1 -> 320 trials moves the DSR bar 21x
(+0.049R -> +1.035R against a corpus best of +1.196R). A per-strategy x
per-axis-state scan is therefore *structurally unreachable as a gated search* —
this tool prints its own cell count so that is visible rather than assumed.
Nothing here emits BUILD/AVOID/KEEP/KILL, and ``tests/test_occurrence_dump.py``
pins that absence. Do not wire a filter off this output without a
pre-registered single construction. SoT ST63.

Reuse, not re-implementation: the entry loaders (which carry the
``backtest_trades`` cross-run dedup — the table has a ~5.11x duplication
factor), the market loader and ``tag_trades`` all come from the H8 audit. A
fourth copy of that plumbing is the specific thing this file avoids.

Substrate roles are inherited from H8 and are NOT re-decided here:
``backtest_trades`` = primary, ``signal_alert_outcomes`` (live) = corroboration
only — thin, irregularly sampled, and the one-way GOLDEN-signal loop means live
outcomes must never feed a live filter.

Run:
    PYTHONPATH=. poetry run python tools/occurrence_dump.py --out dump.csv
(wrapped by ``make buibui-occurrence-dump``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.indicator_condition import _AXES, tag_trades  # noqa: E402
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from tools.indicator_condition_audit import (  # noqa: E402
    _DEFAULT_D1_HISTORY_BARS,
    _DEFAULT_H1_HISTORY_BARS,
    _load_entries,
    _load_indicator_market,
)

# Highest timeframe first — the operator's framing is an explicit top-down walk.
# Anything not listed sorts last, alphabetically, rather than being dropped.
_TF_ORDER: tuple[str, ...] = ("1w", "1d", "4h", "1h", "15m", "5m", "1m")

# Vocabulary this tool must never emit. Mirrored by the test that pins it.
_VERDICT_WORDS: tuple[str, ...] = ("BUILD", "AVOID", "KEEP", "KILL", "DEMOTE")


def tf_rank(tf: str) -> int:
    """Highest timeframe first. Unknown timeframes share the last rank and are
    then separated alphabetically by the ``tf`` column itself in every sort."""
    try:
        return _TF_ORDER.index(tf)
    except ValueError:
        return len(_TF_ORDER)


def build_occurrence_rows(tagged: pd.DataFrame) -> pd.DataFrame:
    """``tagged`` -> the dump frame: one row per fire, ordered highest-TF down.

    Column order is stable (identity, outcome, then the axes in ``_AXES``
    order) because this CSV is read by eye and diffed across runs.
    """
    if tagged.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "tf",
                "strategy",
                "direction",
                "entry_time",
                "entry_utc",
                "pnl_r",
                *_AXES,
            ]
        )
    out = tagged.copy()
    out["entry_utc"] = pd.to_datetime(out["entry_time"], unit="ms", utc=True)
    for axis in _AXES:
        if axis not in out.columns:
            out[axis] = None
    cols = [
        "symbol",
        "tf",
        "strategy",
        "direction",
        "entry_time",
        "entry_utc",
        "pnl_r",
        *_AXES,
    ]
    out = out[cols]
    out["_k"] = [tf_rank(str(t)) for t in out["tf"]]
    out = out.sort_values(
        ["_k", "tf", "strategy", "direction", "symbol", "entry_time"], kind="stable"
    )
    return out.drop(columns=["_k"]).reset_index(drop=True)


def summarize(rows: pd.DataFrame, *, min_n: int) -> pd.DataFrame:
    """Per (tf, strategy, direction, axis, state): n, avg_r, win_rate, delta.

    ``delta`` is avg_r minus the (tf, strategy, direction) base rate — plain
    descriptive arithmetic, deliberately NOT a verdict and deliberately not
    gated, CI'd or corrected. It is the number the eye wants; the trial count
    printed alongside it is why it cannot be acted on directly.

    ``min_n`` filters this SUMMARY only. The dump CSV always stays complete, so
    a thin cell is hidden from the eye but never from the file.
    """
    if rows.empty:
        return pd.DataFrame(
            columns=[
                "tf",
                "strategy",
                "direction",
                "axis",
                "state",
                "n",
                "avg_r",
                "win_rate",
                "base_r",
                "delta",
            ]
        )
    base = (
        rows.groupby(["tf", "strategy", "direction"], sort=False)["pnl_r"]
        .mean()
        .rename("base_r")
        .reset_index()
    )
    recs: list[dict[str, object]] = []
    for axis in _AXES:
        if axis not in rows.columns:
            continue
        sub = rows[rows[axis].notna()]
        if sub.empty:
            continue
        grouped = sub.groupby(
            ["tf", "strategy", "direction", axis], sort=False, dropna=True
        )["pnl_r"]
        for (tf, strat, direction, state), series in grouped:
            recs.append(
                {
                    "tf": str(tf),
                    "strategy": str(strat),
                    "direction": str(direction),
                    "axis": axis,
                    "state": str(state),
                    "n": int(series.size),
                    "avg_r": float(series.mean()),
                    "win_rate": float((series > 0).mean()),
                }
            )
    if not recs:
        return pd.DataFrame(
            columns=[
                "tf",
                "strategy",
                "direction",
                "axis",
                "state",
                "n",
                "avg_r",
                "win_rate",
                "base_r",
                "delta",
            ]
        )
    df = pd.DataFrame(recs).merge(base, on=["tf", "strategy", "direction"], how="left")
    df["delta"] = df["avg_r"] - df["base_r"]
    df = df[df["n"] >= min_n]
    df["_k"] = [tf_rank(str(t)) for t in df["tf"]]
    df = df.sort_values(
        ["_k", "tf", "strategy", "direction", "axis", "state"], kind="stable"
    )
    return df.drop(columns=["_k"]).reset_index(drop=True)


def format_report(
    rows: pd.DataFrame,
    summary: pd.DataFrame,
    *,
    source: str,
    min_n: int,
    n_cells_total: int,
) -> str:
    """Human-readable report. Carries the no-verdict contract in its own header."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("ST63 OCCURRENCE DUMP — DIAGNOSTIC ONLY, NO VERDICT")
    lines.append("=" * 78)
    lines.append(
        "This is a description of what fired where, not a recommendation. No cell "
        "here is gated,\nCI'd or multiplicity-corrected. Do NOT wire a filter off "
        "it without a pre-registered\nsingle construction — a scan over these cells "
        "is unreachable by trial count alone."
    )
    lines.append("")
    lines.append(
        f"source            {source}"
        + (
            "  (corroboration only — never gate-deciding)"
            if source == "live"
            else "  (primary)"
        )
    )
    lines.append(f"fires dumped      {len(rows):,}")
    lines.append(
        f"cells (all n)     {n_cells_total:,}   <- the trial count if this were ever searched"
    )
    lines.append(
        f"cells shown       {len(summary):,}   (n >= {min_n}; the CSV keeps all of them)"
    )
    if not rows.empty:
        lines.append(
            f"span              {rows['entry_utc'].min():%Y-%m-%d} -> {rows['entry_utc'].max():%Y-%m-%d}"
        )
        lines.append(
            f"strategies        {rows['strategy'].nunique()}   timeframes {rows['tf'].nunique()}   symbols {rows['symbol'].nunique()}"
        )
    lines.append("")
    if summary.empty:
        lines.append(
            "(no cell reaches the minimum n — widen --since-days or lower --min-n)"
        )
        return "\n".join(lines)
    hdr = f"{'tf':>4}  {'strategy':<22} {'dir':<5} {'axis':<14} {'state':<12} {'n':>6} {'avg_r':>8} {'win%':>6} {'base_r':>8} {'delta':>8}"
    for tf_key, tf_grp in summary.groupby("tf", sort=False):
        tf = str(tf_key)
        lines.append(f"--- {tf} " + "-" * (len(hdr) - len(tf) - 5))
        lines.append(hdr)
        for _, r in tf_grp.iterrows():
            lines.append(
                f"{str(r['tf']):>4}  {str(r['strategy']):<22} {str(r['direction']):<5} "
                f"{str(r['axis']):<14} {str(r['state']):<12} {int(r['n']):>6} "
                f"{float(r['avg_r']):>8.4f} {float(r['win_rate']) * 100:>5.1f}% "
                f"{float(r['base_r']):>8.4f} {float(r['delta']):>+8.4f}"
            )
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="ST63: dump one row per strategy fire with its M1 context. No verdict."
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument("--source", choices=["live", "backtest"], default="backtest")
    p.add_argument(
        "--timeframes", nargs="*", default=None, help="filter fires by their own tf"
    )
    p.add_argument(
        "--strategies", nargs="*", default=None, help="filter to these strategies"
    )
    p.add_argument("--since-days", type=int, default=None)
    p.add_argument(
        "--min-n",
        type=int,
        default=20,
        help="summary-only floor; the CSV keeps everything",
    )
    p.add_argument("--d1-history-bars", type=int, default=_DEFAULT_D1_HISTORY_BARS)
    p.add_argument("--h1-history-bars", type=int, default=_DEFAULT_H1_HISTORY_BARS)
    p.add_argument("--out", type=Path, default=None, help="write the per-fire CSV here")
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
    entries = _load_entries(args.db, args.source, since_ms)
    if args.timeframes:
        entries = entries[entries["tf"].isin(args.timeframes)]
    if args.strategies:
        entries = entries[entries["strategy"].isin(args.strategies)]
    entries = entries.reset_index(drop=True)
    if entries.empty:
        print("no entries for that scope — nothing to dump", file=sys.stderr)
        return 1
    market = _load_indicator_market(
        args.db, entries, d1_bars=args.d1_history_bars, h1_bars=args.h1_history_bars
    )
    tagged = tag_trades(entries, market)
    rows = build_occurrence_rows(tagged)
    n_cells_total = len(summarize(rows, min_n=1))
    summary = summarize(rows, min_n=args.min_n)
    print(
        format_report(
            rows,
            summary,
            source=args.source,
            min_n=args.min_n,
            n_cells_total=n_cells_total,
        )
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        rows.to_csv(args.out, index=False)
        print(f"[saved] {args.out}  ({len(rows):,} fires)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
