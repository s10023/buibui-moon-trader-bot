#!/usr/bin/env python
"""Faithful per-strategy structural entry-simulation harness (read-only).

The escalation from the structural level-hold touch-decay kill-test
(``docs/audits/2026-06-26-structural-level-hold-touch-decay.md``). That audit
found first touches of structural zones run further favorably than repeat
touches **in excursion space** (``mfe_atr``, gross of costs). This tool converts
that premium into **cost-netted realized avg_r** with real entries/stops and
emits a pre-committed de-biased **BUILD / NO-EDGE** verdict per
(zone_type × direction).

Entries/stops/TP are resolved through the production backtest engine
(``analytics/backtest/engine.run_backtest``) so the cost model
(``net_R = raw − fee − slippage − funding``), next-bar-open entry, and SL/TP
candle scan match live exactly. The gate keys on a pre-committed headline config
(``--headline-tp-r`` × ``--headline-sl-model``) evaluated SEPARATELY for every tf
passed via ``--timeframes`` — there is no cross-tf pooling, and a BUILD on one tf
does NOT imply a BUILD on another. Each tf gets its own gate table + tp_r ×
sl_model sensitivity table; DSR / PBO over that grid deflate each tf's own
headline. The live ledger cannot isolate first touches (cooldown removes
repeats) — it is shown only as blended context.

Read-only — no DB writes, no engine change.

Run:
    PYTHONPATH=. poetry run python tools/structural_entry_sim_audit.py \
        --timeframes 1d --min-n 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.backtest_runner import _build_funding_series_by_symbol  # noqa: E402
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.market_data import get_ohlcv  # noqa: E402
from analytics.structural_entry_sim import (  # noqa: E402
    SL_MODELS,
    StructuralBuildVerdict,
    build_realized_table,
    evaluate_build,
)
from analytics.universe import load_universe  # noqa: E402

DEFAULT_ZONE_TYPES = [
    "fvg",
    "eqh_eql",
    "bos",
]  # ob / fib opt-in (family / walk-forward cost)
DEFAULT_TFS = [
    "1d"
]  # 4h is O(n^2) in the extractors — pass explicitly for the robustness run
DEFAULT_TP_R = [1.0, 1.5, 2.0, 3.0]
DEFAULT_OUT = (
    REPO_ROOT / "docs" / "audits" / "2026-06-26-structural-entry-sim-harness.md"
)

_LIVE_STRATEGY_TO_ZONE = {
    "fvg": "fvg",
    "order_block": "ob",
    "eqh_eql": "eqh_eql",
    "bos": "bos",
    "fib_golden_zone": "fib",
    "liquidity_sweep": "eqh_eql",
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


def _load_funding(db: Path, symbols: list[str]) -> dict[str, pd.Series]:
    far_future = 4_102_444_800_000
    with duckdb.connect(str(db), read_only=True) as conn:
        return _build_funding_series_by_symbol(conn, symbols, 0, far_future)


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


def _headline(verdicts: list[StructuralBuildVerdict]) -> tuple[str, str]:
    """Per-tf headline: decision + rationale for one tf's verdict list."""
    built = [f"{v.zone_type}/{v.direction}" for v in verdicts if v.decision == "BUILD"]
    if built:
        return (
            "BUILD",
            "First-touch is a positive-EV, cost-netted tradable entry "
            "(boot-CI>0, Holm, n≥MinTRL, DSR/PBO) on: "
            + ", ".join(built)
            + " → build a `structural_touch` detector for these cells (live-OOS gated).",
        )
    if any(v.decision == "NO-EDGE" for v in verdicts):
        return (
            "NO-EDGE",
            "No powered (zone_type × direction) cell clears the de-biased BUILD bar in "
            "realized R — the excursion premium does not survive entry/stop/cost. "
            "XS-solo stays the deploy core.",
        )
    return (
        "INSUFFICIENT",
        "No cell reaches min_n first touches at the headline config → underpowered.",
    )


def _combined_headline(
    verdicts_by_tf: dict[str, list[StructuralBuildVerdict]],
) -> tuple[str, str]:
    """Overall status across ALL requested tfs — never claims a tf it didn't test.

    Deliberately does NOT collapse a per-tf disagreement into a single BUILD/
    NO-EDGE — a mixed outcome (e.g. BUILD on 1d, NO-EDGE on 4h) is reported as
    MIXED so a verdict is never claimed to "hold" on a tf that contradicts it.
    """
    per_tf = {tf: _headline(vs)[0] for tf, vs in verdicts_by_tf.items()}
    statuses = set(per_tf.values())
    tfs = list(verdicts_by_tf)

    if statuses == {"BUILD"}:
        return (
            "BUILD",
            "BUILD holds independently on every requested timeframe ("
            + ", ".join(tfs)
            + ") — see the per-tf breakdown below for each tf's own cells.",
        )
    if "BUILD" in statuses:
        build_tfs = [tf for tf in tfs if per_tf[tf] == "BUILD"]
        other = [f"{tf}={per_tf[tf]}" for tf in tfs if per_tf[tf] != "BUILD"]
        return (
            "MIXED",
            "BUILD holds ONLY on "
            + ", ".join(build_tfs)
            + "; does NOT replicate on "
            + ", ".join(other)
            + ". Do not report this as a single cross-tf BUILD — read the "
            "per-tf breakdown; a `structural_touch` detector would have to be "
            "scoped to the tf(s) that actually clear the gate.",
        )
    if statuses == {"NO-EDGE"}:
        return (
            "NO-EDGE",
            "No powered cell on any requested tf ("
            + ", ".join(tfs)
            + ") clears the de-biased BUILD bar — XS-solo stays the deploy core.",
        )
    return (
        "INSUFFICIENT",
        "No cell on any requested tf ("
        + ", ".join(tfs)
        + ") reaches min_n first touches at the headline config → underpowered.",
    )


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:+.3f}"


def _fmt_p(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def _gate_table(verdicts: list[StructuralBuildVerdict]) -> list[str]:
    lines = [
        "| zone × dir | n_first | n_rep | first_avg_r | boot CI | Holm p | MinTRL | "
        "DSR | PBO | decay lift | split | decision |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | :-: | --- |",
    ]
    order = {"BUILD": 0, "NO-EDGE": 1, "INSUFFICIENT": 2}
    for v in sorted(verdicts, key=lambda v: (order.get(v.decision, 9), v.zone_type)):
        ci = f"[{_fmt(v.boot_lo)}, {_fmt(v.boot_hi)}]" if v.boot_lo is not None else "—"
        mintrl = "—" if v.mintrl is None else f"{v.mintrl:.0f}"
        lines.append(
            f"| {v.zone_type}/{v.direction} | {v.n_first} | {v.n_repeat} | "
            f"{_fmt(v.first_avg_r)} | {ci} | {_fmt_p(v.holm_p)} | {mintrl} | "
            f"{_fmt_p(v.dsr)} | {_fmt_p(v.pbo)} | {_fmt(v.decay_lift)} | "
            f"{'✓' if v.time_split_ok else '·'} | **{v.decision}** |"
        )
    return lines


def _sensitivity_table(
    table: pd.DataFrame, *, headline_tf: str, min_n: int
) -> list[str]:
    """First-touch net avg_r across the tp_r × sl_model grid (robustness)."""
    if table.empty:
        return ["*(no trades)*"]
    first = table[(table["touch_index"] == 1) & (table["tf"] == headline_tf)]
    grp = (
        first.groupby(["zone_type", "direction", "tp_r", "sl_model"])
        .agg(
            n=("pnl_r", "size"), avg_r=("pnl_r", "mean"), gross=("pnl_r_gross", "mean")
        )
        .reset_index()
    )
    grp = grp[grp["n"] >= min_n]
    if grp.empty:
        return ["*(no powered cells at headline tf)*"]
    lines = [
        f"First-touch net avg_r at tf=`{headline_tf}` (n≥{min_n}); gross in parens.",
        "",
        "| zone × dir | tp_r | sl_model | n | net avg_r | gross |",
        "| --- | ---: | --- | ---: | ---: | ---: |",
    ]
    for _, r in grp.sort_values(
        ["zone_type", "direction", "tp_r", "sl_model"]
    ).iterrows():
        lines.append(
            f"| {r['zone_type']}/{r['direction']} | {r['tp_r']:.1f} | {r['sl_model']} | "
            f"{int(r['n'])} | {r['avg_r']:+.3f} | {r['gross']:+.3f} |"
        )
    return lines


def _live_table(live: pd.DataFrame) -> list[str]:
    if live.empty:
        return ["*(no live structural rows)*"]
    lines = [
        "Blended over ALL touches (live cooldown removes repeats — cannot isolate "
        "first-touch; context only).",
        "",
        "| strategy | dir | n | avg_r |",
        "| --- | --- | ---: | ---: |",
    ]
    for _, r in live.sort_values("n", ascending=False).iterrows():
        lines.append(
            f"| {r['strategy']} | {r['direction']} | {int(r['n'])} | {r['avg_r']:+.3f} |"
        )
    return lines


def _caveats() -> list[str]:
    """Static methodological caveats — valid regardless of the verdict."""
    return [
        "- **Judge robustness by the WIDER stops, not the tightest.** `structural`"
        " (far-edge) stops are the tightest and inflate R-multiples; an edge is only"
        " real if it survives `atr_floor` (0.5·ATR min risk) and `fixed_atr`"
        " (1·ATR) — read the sensitivity table for the conservative rows.",
        "- **BUILD here is NOT live-confirmed.** This is the de-biased *backtest*"
        " substrate. The live ledger cannot isolate first touches (cooldown removes"
        " repeats) AND fires a different, filtered population — the blended live rows"
        " above can even point the other way at tiny n. Any detector built from a"
        " BUILD cell stays gated on live-OOS as the ledger grows.",
        "- **First touches are not iid.** Overlapping zones across 25 symbols share"
        " market-wide moves; the block bootstrap mitigates serial correlation but"
        " DSR/PBO at large n read as *well-powered*, not *risk-free* — a DSR of 1.000"
        " / PBO of 0.000 is mostly sample size.",
        "- **Entry is unconditional.** Every first touch is taken — no regime / trend"
        " / level-quality filter. A deployable detector needs entry filters and may"
        " behave differently (better or worse) than this unconditional average.",
        "- **Resolution = SL/TP, no max-hold.** Realized R closes on the first SL or"
        " TP touch; still-open touches are excluded from the resolved population.",
    ]


def _tf_section(
    tf: str, verdicts: list[StructuralBuildVerdict], table: pd.DataFrame, *, min_n: int
) -> list[str]:
    """One tf's gate table + sensitivity table (both scoped to this tf only)."""
    verdict, rationale = _headline(verdicts)
    return [
        f"### tf=`{tf}` — headline: **{verdict}**",
        "",
        rationale,
        "",
        f"#### Primary gate (per zone_type × direction, tf=`{tf}`)",
        "",
        *_gate_table(verdicts),
        "",
        f"#### Robustness — tp_r × sl_model sensitivity (tf=`{tf}`, reported, "
        "not gate-deciding)",
        "",
        *_sensitivity_table(table, headline_tf=tf, min_n=min_n),
        "",
    ]


def _report(
    *,
    verdicts_by_tf: dict[str, list[StructuralBuildVerdict]],
    table: pd.DataFrame,
    live: pd.DataFrame,
    args: argparse.Namespace,
) -> str:
    verdict, rationale = _combined_headline(verdicts_by_tf)
    tf_sections: list[str] = []
    for tf in verdicts_by_tf:
        tf_sections.extend(_tf_section(tf, verdicts_by_tf[tf], table, min_n=args.min_n))
    out = [
        "# Faithful per-strategy structural entry-sim harness",
        "",
        "**Date:** 2026-06-26  ·  **Status:** read-only measurement (no engine change)",
        "",
        f"## Headline verdict: **{verdict}**",
        "",
        rationale,
        "",
        "Pre-committed BUILD gate (locked before running): on the **headline config** "
        f"(`tp_r={args.headline_tp_r}` × `sl_model={args.headline_sl_model}`), "
        "evaluated SEPARATELY for each tf below (no cross-tf pooling), first-touch "
        "(`touch_index==1`) net realized R must clear `n_first ≥ "
        f"{args.min_n}`, a block-bootstrap CI lower bound `> {args.bar}`, "
        f"a Holm-adjusted `p < {args.alpha}` across that tf's (zone_type × direction) "
        "family, `n_first ≥ MinTRL(0.95)`, AND `DSR ≥ 0.95 ∧ PBO ≤ 0.5` over that tf's "
        "tp_r × sl_model trial family. The first−repeat decay lift is secondary "
        "corroboration. Substrate = backtest/OHLCV (the live ledger cannot gate — "
        "cooldown removes repeats).",
        "",
        f"Params: `tfs={args.timeframes}`  `zone_types={args.zone_types}`  "
        f"`tp_r_grid={args.tp_r_grid}`  `sl_models={args.sl_models}`  "
        f"`fee_bps={args.fee_bps}`  `slippage_bps={args.slippage_bps}`  "
        f"`n_boot={args.n_boot}`  `seed={args.seed}`  "
        f"`touch_geometry={'legacy-formation' if args.legacy_touch_geometry else 'confirmed'}`"
        f". Resolved trades (all tfs): "
        f"**{len(table)}**.",
        "",
        "## Per-timeframe breakdown",
        "",
        *tf_sections,
        "## Live context — blended structural avg_r",
        "",
        *_live_table(live),
        "",
        "## Interpretation & caveats (always read before acting)",
        "",
        *_caveats(),
        "",
        "---",
        "",
        "*Realized R through real next-bar-open entries, structural / ATR stops, and "
        "`tp_r × risk` targets, net of fees + slippage + funding via the production "
        "engine. A BUILD verdict motivates a `structural_touch` detector (still "
        "live-OOS gated), scoped to the tf(s) that actually clear the gate; NO-EDGE "
        "on a tf closes the thread for that tf. ob / fib zone types are opt-in via "
        "`--zone-types`.*",
        "",
    ]
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Faithful per-strategy structural entry-sim harness (read-only)"
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument("--universe", type=Path, default=None, help="universe TOML path")
    p.add_argument("--timeframes", nargs="+", default=DEFAULT_TFS)
    p.add_argument("--zone-types", nargs="+", default=DEFAULT_ZONE_TYPES)
    p.add_argument("--tp-r-grid", nargs="+", type=float, default=DEFAULT_TP_R)
    p.add_argument("--sl-models", nargs="+", default=list(SL_MODELS))
    p.add_argument("--headline-tp-r", type=float, default=2.0)
    p.add_argument("--headline-sl-model", default="atr_floor")
    p.add_argument(
        "--headline-tf",
        nargs="+",
        default=None,
        help="tf(s) to gate on; default = --timeframes (every requested tf gets "
        "its own gate + tables — no cross-tf pooling)",
    )
    p.add_argument("--fee-bps", type=float, default=5.0, help="per-leg taker fee (bps)")
    p.add_argument(
        "--slippage-bps", type=float, default=2.0, help="per-leg slippage (bps)"
    )
    p.add_argument("--band-atr-frac", type=float, default=0.25)
    p.add_argument("--min-gap-bars", type=int, default=1)
    p.add_argument("--fib-step", type=int, default=5)
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--bar", type=float, default=0.0)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-boot", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument(
        "--legacy-touch-geometry",
        action="store_true",
        help=(
            "index touches from zone FORMATION (pre-2026-08-18 behaviour) instead "
            "of from the bar the zone becomes knowable. Reproduces the original "
            "run; not tradable."
        ),
    )
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p


def main() -> int:
    args = build_parser().parse_args()
    symbols = load_universe(args.universe) if args.universe else load_universe()
    print(f"Loading OHLCV: {len(symbols)} symbols × {args.timeframes} ...", flush=True)
    bars = _load_ohlcv(args.db, symbols, args.timeframes)
    funding = _load_funding(args.db, symbols)
    print(
        f"Simulating entries over {len(bars)} (symbol, tf) frames "
        f"(zone_types={args.zone_types}, tp_r×sl_model="
        f"{len(args.tp_r_grid)}×{len(args.sl_models)}) ...",
        flush=True,
    )
    # Build per (symbol, tf) so progress is observable (extraction is O(n^2) on
    # higher-frequency tfs); per-frame tables concat to the single-call result.
    parts: list[pd.DataFrame] = []
    for i, ((symbol, tf), df) in enumerate(bars.items(), start=1):
        part = build_realized_table(
            {(symbol, tf): df},
            args.zone_types,
            tp_r_grid=args.tp_r_grid,
            sl_models=args.sl_models,
            fee_pct=args.fee_bps / 10_000.0,
            slippage_pct=args.slippage_bps / 10_000.0,
            funding_by_symbol=funding,
            band_atr_frac=args.band_atr_frac,
            min_gap_bars=args.min_gap_bars,
            fib_step=args.fib_step,
            respect_confirmation=not args.legacy_touch_geometry,
            require_outside_first=not args.legacy_touch_geometry,
        )
        parts.append(part)
        print(
            f"  [{i}/{len(bars)}] {symbol} {tf}: {len(df)} bars -> {len(part)} trades",
            flush=True,
        )
    table = (
        pd.concat(parts, ignore_index=True)
        if parts
        else build_realized_table(
            {}, args.zone_types, tp_r_grid=args.tp_r_grid, sl_models=args.sl_models
        )
    )
    headline_tfs = args.headline_tf if args.headline_tf else args.timeframes
    verdicts_by_tf: dict[str, list[StructuralBuildVerdict]] = {}
    for tf in headline_tfs:
        verdicts_by_tf[tf] = evaluate_build(
            table,
            headline_tp_r=args.headline_tp_r,
            headline_sl_model=args.headline_sl_model,
            headline_tf=tf,
            min_n=args.min_n,
            bar=args.bar,
            alpha=args.alpha,
            n_boot=args.n_boot,
            seed=args.seed,
        )
    live = _live_context(args.db)
    report = _report(verdicts_by_tf=verdicts_by_tf, table=table, live=live, args=args)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    verdict, rationale = _combined_headline(verdicts_by_tf)
    print(f"\nHeadline verdict: {verdict}\n{rationale}\n")
    for tf in headline_tfs:
        print(f"tf={tf}:")
        for v in sorted(verdicts_by_tf[tf], key=lambda v: v.zone_type):
            print(
                f"  {v.zone_type}/{v.direction}: {v.decision}  "
                f"(n_first={v.n_first} first_avg_r={_fmt(v.first_avg_r)} "
                f"dsr={_fmt_p(v.dsr)} pbo={_fmt_p(v.pbo)})"
            )
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
