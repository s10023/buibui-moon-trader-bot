#!/usr/bin/env python
"""Structural level-hold touch-decay kill-test (read-only).

Tests the user thesis "a structural level weakens each time it is tested": does
a zone hold better (higher forward favorable excursion) on its FIRST touch than
on repeat touches? Repeat touches do not exist in the stored ledgers (the engine
writes one trade per zone; the live cooldown dedups repeats), so they are
regenerated from raw OHLCV + ``analytics/zones_lib.py`` geometry.

This is the CHEAP kill-test (excursion-space, no entry simulation). A robust
positive escalates to a faithful per-strategy entry-simulation harness.

Substrate / rigor (decided in the design): the backtest/OHLCV path is the
de-biased measurement — first-vs-repeat mean ``mfe_atr`` lift, with an
early/late time-split (the lift must hold in BOTH halves), a two-sample
bootstrap CI clearing ``+bar``, and a Holm haircut across the (zone_type ×
direction) family. The live ledger cannot gate touch-decay (cooldown removes
repeats) — it is shown only as blended per-strategy context.

Read-only — no DB writes, no engine change. See the design plan and
``docs/audits/2026-06-26-structural-level-hold-touch-decay.md``.

Run:
    PYTHONPATH=. poetry run python tools/structural_touch_decay_audit.py \
        --timeframes 1d 4h --min-n 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.market_data import get_ohlcv  # noqa: E402
from analytics.structural_touch import (  # noqa: E402
    TouchDecayVerdict,
    build_touch_table,
    evaluate_touch_decay,
)
from analytics.universe import load_universe  # noqa: E402

DEFAULT_ZONE_TYPES = ["fvg", "ob", "eqh_eql", "bos"]  # fib opt-in (walk-forward cost)
DEFAULT_TFS = ["1d", "4h"]
DEFAULT_OUT = (
    REPO_ROOT / "docs" / "audits" / "2026-06-26-structural-level-hold-touch-decay.md"
)

# Live strategy name -> kill-test zone_type (for the blended live-context table).
_LIVE_STRATEGY_TO_ZONE = {
    "fvg": "fvg",
    "order_block": "ob",
    "eqh_eql": "eqh_eql",
    "bos": "bos",
    "fib_golden_zone": "fib",
    "liquidity_sweep": "eqh_eql",  # swept liquidity level ~ eqh_eql / bos
}


def _load_ohlcv(
    db: Path, symbols: list[str], tfs: list[str]
) -> dict[tuple[str, str], pd.DataFrame]:
    """Full-history OHLCV per (symbol, tf). Empty frames are skipped."""
    out: dict[tuple[str, str], pd.DataFrame] = {}
    far_future = 4_102_444_800_000  # 2100-01-01
    with duckdb.connect(str(db), read_only=True) as conn:
        for symbol in symbols:
            for tf in tfs:
                df = get_ohlcv(conn, symbol, tf, 0, far_future)
                if not df.empty:
                    out[(symbol, tf)] = df
    return out


def _live_context(db: Path) -> pd.DataFrame:
    """Blended live avg_r per structural strategy/direction (context only)."""
    with duckdb.connect(str(db), read_only=True) as conn:
        rows = conn.execute(
            "SELECT strategy, direction, COUNT(*) AS n, AVG(outcome_r) AS avg_r "
            "FROM signal_alert_outcomes "
            "WHERE outcome <> 'open' AND outcome_r IS NOT NULL "
            "GROUP BY strategy, direction"
        ).df()
    return rows[rows["strategy"].isin(_LIVE_STRATEGY_TO_ZONE)].copy()


def _headline(verdicts: list[TouchDecayVerdict]) -> tuple[str, str]:
    if any(v.decision == "DECAY-CONFIRMED" for v in verdicts):
        cells = [
            f"{v.zone_type}/{v.direction}"
            for v in verdicts
            if v.decision == "DECAY-CONFIRMED"
        ]
        return (
            "DECAY-CONFIRMED",
            "First-touch beats repeat-touch (time-split-robust, Holm, CI>bar) on: "
            + ", ".join(cells)
            + " → escalate to the faithful per-strategy harness.",
        )
    if any(v.decision == "NO-DECAY" for v in verdicts):
        return (
            "NO-DECAY",
            "No powered (zone_type × direction) cell shows a robust first-vs-repeat "
            "lift → thesis weakened; XS-solo stays the deploy core.",
        )
    return (
        "INSUFFICIENT",
        "No cell reaches min_n on both first and repeat touches → underpowered; "
        "re-run as history / the live ledger grows.",
    )


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:+.3f}"


def _gate_table(verdicts: list[TouchDecayVerdict]) -> list[str]:
    lines = [
        "| zone × dir | n_first | n_rep | mfe_first | mfe_rep | lift | lift CI | "
        "Holm p | split | decision |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | :-: | --- |",
    ]
    order = {"DECAY-CONFIRMED": 0, "NO-DECAY": 1, "INSUFFICIENT": 2}
    for v in sorted(verdicts, key=lambda v: (order.get(v.decision, 9), v.zone_type)):
        ci = f"[{_fmt(v.ci_lo)}, {_fmt(v.ci_hi)}]" if v.ci_lo is not None else "—"
        holm = "—" if v.holm_p is None else f"{v.holm_p:.3f}"
        lines.append(
            f"| {v.zone_type}/{v.direction} | {v.n_first} | {v.n_repeat} | "
            f"{_fmt(v.mfe_first)} | {_fmt(v.mfe_repeat)} | {_fmt(v.lift)} | {ci} | "
            f"{holm} | {'✓' if v.split_ok else '·'} | **{v.decision}** |"
        )
    return lines


def _gradient_table(table: pd.DataFrame, *, min_n: int) -> list[str]:
    """Uncorrected mean mfe_atr by touch-index bucket (1 / 2 / 3+) × zone × dir."""
    if table.empty:
        return ["*(no touches)*"]
    t = table.copy()
    t["bucket"] = t["touch_index"].apply(
        lambda i: "1" if i == 1 else ("2" if i == 2 else "3+")
    )
    grp = (
        t.groupby(["zone_type", "direction", "bucket"])
        .agg(n=("mfe_atr", "size"), mfe=("mfe_atr", "mean"), held=("held", "mean"))
        .reset_index()
    )
    grp = grp[grp["n"] >= min_n]
    lines = [
        "| zone × dir | touch | n | mean mfe_atr | held-rate |",
        "| --- | :-: | ---: | ---: | ---: |",
    ]
    for _, r in grp.sort_values(["zone_type", "direction", "bucket"]).iterrows():
        lines.append(
            f"| {r['zone_type']}/{r['direction']} | {r['bucket']} | {int(r['n'])} | "
            f"{r['mfe']:+.3f} | {r['held']:.2f} |"
        )
    return lines


def _live_table(live: pd.DataFrame) -> list[str]:
    if live.empty:
        return ["*(no live structural rows)*"]
    lines = [
        "Blended over ALL touches (live cooldown removes repeats — cannot split "
        "first-vs-repeat; context only).",
        "",
        "| strategy | dir | n | avg_r |",
        "| --- | --- | ---: | ---: |",
    ]
    for _, r in live.sort_values("n", ascending=False).iterrows():
        lines.append(
            f"| {r['strategy']} | {r['direction']} | {int(r['n'])} | {r['avg_r']:+.3f} |"
        )
    return lines


def _report(
    *,
    verdicts: list[TouchDecayVerdict],
    table: pd.DataFrame,
    live: pd.DataFrame,
    args: argparse.Namespace,
) -> str:
    verdict, rationale = _headline(verdicts)
    n_touches = len(table)
    out = [
        "# Structural level-hold touch-decay kill-test",
        "",
        "**Date:** 2026-06-26  ·  **Status:** read-only measurement (no engine change)",
        "",
        f"## Headline verdict: **{verdict}**",
        "",
        rationale,
        "",
        "Pre-committed gate (locked before running): per (zone_type × direction) "
        f"cell, `n_first ≥ {args.min_n}` and `n_repeat ≥ {args.min_n}`; the "
        "first−repeat mean-`mfe_atr` lift's bootstrap-CI lower bound `> +"
        f"{args.bar}`; Holm-adjusted two-sided `p < {args.alpha}`; AND the lift "
        "positive in BOTH early/late time-split halves. Substrate = "
        "`backtest`/OHLCV (the live ledger cannot gate — cooldown removes "
        "repeats).",
        "",
        f"Params: `tfs={args.timeframes}`  `zone_types={args.zone_types}`  "
        f"`window={args.window}`  `band_atr_frac={args.band_atr_frac}`  "
        f"`min_gap_bars={args.min_gap_bars}`  `n_boot={args.n_boot}`  "
        f"`seed={args.seed}`. Touches indexed: **{n_touches}**.",
        "",
        "## Primary gate (per zone_type × direction)",
        "",
        *_gate_table(verdicts),
        "",
        "## Exploratory — touch-index gradient (uncorrected, not gate-deciding)",
        "",
        *_gradient_table(table, min_n=args.min_n),
        "",
        "## Live context — blended structural avg_r",
        "",
        *_live_table(live),
        "",
        "---",
        "",
        "*Kill-test in excursion-space (forward ATR-normalized MFE/MAE per touch, "
        "no entry simulation). A DECAY-CONFIRMED cell motivates the faithful "
        "per-strategy harness; NO-DECAY weakens the thesis. fib is opt-in "
        "(`--zone-types … fib`) due to its walk-forward cost.*",
        "",
    ]
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Structural level-hold touch-decay kill-test (read-only)"
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument("--universe", type=Path, default=None, help="universe TOML path")
    p.add_argument("--timeframes", nargs="+", default=DEFAULT_TFS)
    p.add_argument("--zone-types", nargs="+", default=DEFAULT_ZONE_TYPES)
    p.add_argument("--window", type=int, default=24, help="forward excursion bars")
    p.add_argument("--band-atr-frac", type=float, default=0.25)
    p.add_argument("--min-gap-bars", type=int, default=1)
    p.add_argument("--fib-step", type=int, default=5)
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--bar", type=float, default=0.10)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-boot", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p


def main() -> int:
    args = build_parser().parse_args()
    symbols = load_universe(args.universe) if args.universe else load_universe()
    print(f"Loading OHLCV: {len(symbols)} symbols × {args.timeframes} ...", flush=True)
    bars = _load_ohlcv(args.db, symbols, args.timeframes)
    print(
        f"Building touch table over {len(bars)} (symbol, tf) frames "
        f"(zone_types={args.zone_types}) ...",
        flush=True,
    )
    # Build per (symbol, tf) so progress is observable (extraction is O(n^2) on
    # higher-frequency tfs); the per-frame tables are independent and concat to
    # exactly the single-call result.
    parts: list[pd.DataFrame] = []
    for i, ((symbol, tf), df) in enumerate(bars.items(), start=1):
        part = build_touch_table(
            {(symbol, tf): df},
            args.zone_types,
            window=args.window,
            band_atr_frac=args.band_atr_frac,
            min_gap_bars=args.min_gap_bars,
            fib_step=args.fib_step,
        )
        parts.append(part)
        print(
            f"  [{i}/{len(bars)}] {symbol} {tf}: {len(df)} bars -> {len(part)} touches",
            flush=True,
        )
    table = (
        pd.concat(parts, ignore_index=True)
        if parts
        else build_touch_table({}, args.zone_types, window=args.window)
    )
    verdicts = evaluate_touch_decay(
        table,
        min_n=args.min_n,
        bar=args.bar,
        alpha=args.alpha,
        n_boot=args.n_boot,
        seed=args.seed,
    )
    live = _live_context(args.db)
    report = _report(verdicts=verdicts, table=table, live=live, args=args)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    verdict, rationale = _headline(verdicts)
    print(f"\nHeadline verdict: {verdict}\n{rationale}\n")
    for v in sorted(verdicts, key=lambda v: v.zone_type):
        print(
            f"  {v.zone_type}/{v.direction}: {v.decision}  "
            f"(n_first={v.n_first} n_rep={v.n_repeat} lift={_fmt(v.lift)})"
        )
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
