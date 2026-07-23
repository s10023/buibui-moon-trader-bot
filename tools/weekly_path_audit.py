"""H10 — partial-path predictiveness audit (read-only driver).

Spec: docs/superpowers/specs/2026-07-23-h10-partial-path-predictiveness-design.md

Does the week's normalized path at hour `h` predict the return from `h` to the
week's close, beyond drift? Gates ST6 ("is this a bullish week?" framing) and
ST7 (idea generation + invalidation).

Read-only: opens DuckDB with read_only=True, writes nothing.

    PYTHONPATH=. poetry run python tools/weekly_path_audit.py
    make buibui-weekly-path-audit
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

import duckdb

from analytics import weekly_path as wp
from analytics.stats.weekly_cone import week_records
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def load_symbol_weeks(
    conn: duckdb.DuckDBPyConnection,
    symbols: Sequence[str],
    *,
    now_ms: int | None = None,
) -> list[wp.SymbolWeek]:
    """Every symbol's completed-week population, via the cone's own rules.

    A symbol with no qualifying weeks contributes nothing rather than raising —
    new listings legitimately have no 14-week AWR warm-up yet.
    """
    out: list[wp.SymbolWeek] = []
    for symbol in symbols:
        for record in week_records(conn, symbol, now_ms=now_ms):
            out.append(
                wp.SymbolWeek(
                    symbol=symbol,
                    week=record.week,
                    norm_path=tuple(record.norm_path),
                )
            )
    return out


def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def render_report(
    verdicts: Sequence[wp.HourVerdict],
    stamps: wp.FamilyStamps,
    magnitude_rows: Sequence[wp.MagnitudeRow],
    curve: Sequence[wp.CurvePoint],
    *,
    label: str,
) -> str:
    lines: list[str] = []
    lines.append(f"## {label}")
    lines.append("")
    lines.append("Units are AWR per week (the week's own trailing AWR14).")
    lines.append("")
    lines.append(
        "| hour | verdict | n weeks | mean v | CI lo | CI hi | Holm p | early | late |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for v in verdicts:
        lines.append(
            f"| h{v.hour} | {v.verdict} | {v.n_weeks} | {_fmt(v.mean_v)} | "
            f"{_fmt(v.ci_lo)} | {_fmt(v.ci_hi)} | {_fmt(v.adj_pvalue)} | "
            f"{_fmt(v.early_mean)} | {_fmt(v.late_mean)} |"
        )
    lines.append("")
    lines.append(
        f"Family stamps ({stamps.n_trials} trials): best h"
        f"{stamps.best_hour} · Sharpe {_fmt(stamps.best_sharpe)} · "
        f"DSR {_fmt(stamps.dsr)} · PBO {_fmt(stamps.pbo)} · "
        f"MinTRL {_fmt(stamps.min_trl, 1)}"
    )
    lines.append("")

    if magnitude_rows:
        lines.append("### Magnitude terciles (reported, non-gating)")
        lines.append("")
        lines.append("| hour | tercile | n weeks | mean v |")
        lines.append("| --- | --- | --- | --- |")
        for r in magnitude_rows:
            lines.append(
                f"| h{r.hour} | {r.tercile} | {r.n_weeks} | {_fmt(r.mean_v)} |"
            )
        lines.append("")

    if curve:
        lines.append("### Hour curve (reported, non-gating) — every 12th hour")
        lines.append("")
        lines.append("| hour | n weeks | mean v |")
        lines.append("| --- | --- | --- |")
        for p in curve:
            if p.hour % 12 == 0:
                lines.append(f"| h{p.hour} | {p.n_weeks} | {_fmt(p.mean_v)} |")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="H10 partial-path predictiveness audit (read-only)"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--majors", default=",".join(_MAJORS), help="comma-separated breadth contrast"
    )
    parser.add_argument(
        "--skip-curve",
        action="store_true",
        help="skip the 168-hour descriptive curve (it is the slow part)",
    )
    args = parser.parse_args(argv)

    cfg = wp.DEFAULT_CONFIG
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]

    conn = duckdb.connect(args.db, read_only=True)
    try:
        universe = load_universe()
        cohorts: list[tuple[str, list[str]]] = [
            ("universe", universe),
            ("majors", majors),
            ("BTC only", ["BTCUSDT"]),
        ]
        sections: list[str] = []
        for label, symbols in cohorts:
            weeks = load_symbol_weeks(conn, symbols, now_ms=now_ms)
            if not weeks:
                sections.append(f"## {label}\n\nNo qualifying weeks.\n")
                continue
            verdicts = wp.evaluate_hours(weeks, cfg)
            stamps = wp.family_stamps(weeks, cfg)
            magnitude = [
                row for h in cfg.hours for row in wp.magnitude_breakdown(weeks, h, cfg)
            ]
            curve = [] if args.skip_curve else wp.hour_curve(weeks, cfg)
            sections.append(
                render_report(verdicts, stamps, magnitude, curve, label=label)
            )
    finally:
        conn.close()

    print("# H10 — partial-path predictiveness\n")
    print("\n".join(sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
