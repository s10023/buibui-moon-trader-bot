"""Give-back / heat-and-run study — run the pre-registered 2026-08-17 measurement.

    poetry run python tools/giveback_study.py [--x 1.0] [--no-restate] [--boot 10000]

DESCRIPTIVE: reports a property of the live outcome ledger. It proposes no rule,
so it carries no trial count and no three-leg gate — and it licenses a REPORT,
never a negative verdict. Read ``analytics/giveback.py``'s docstring for the four
traps the measurement is built around.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.eras import load_boundaries, straddle_report  # noqa: E402
from analytics.giveback import (  # noqa: E402
    DEFAULT_X_R,
    GivebackRow,
    Summary,
    load_giveback_rows,
    sl_pct_bucket,
    summarise,
    summarise_by,
)
from analytics.store import DEFAULT_DB_PATH  # noqa: E402


def _fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}"


def _line(s: Summary) -> str:
    iqr = (
        "n/a"
        if s.iqr_giveback_r is None
        else f"[{s.iqr_giveback_r[0]:+.3f}, {s.iqr_giveback_r[1]:+.3f}]"
    )
    share = "n/a" if s.share_non_positive is None else f"{s.share_non_positive:6.1%}"
    cap = "n/a" if s.median_capture_ratio is None else f"{s.median_capture_ratio:+.3f}"
    return (
        f"  {s.label:<28s} n={s.n_measured:>5d}  <=0 {share}  "
        f"med_gb {_fmt(s.median_giveback_r, 3)}  IQR {iqr:>18s}  capture {cap}"
    )


def _report(rows: list[GivebackRow], *, x_r: float, n_boot: int) -> list[str]:
    out: list[str] = []
    primary = summarise(
        rows, label=f"MFE_R >= {x_r:.1f}R", x_r=x_r, ci=True, n_boot=n_boot
    )
    whole = summarise(rows, label="whole population", x_r=None)

    out.append(
        f"Population: {primary.n:,} resolved rows, {primary.unmeasurable:,} unmeasurable"
    )
    out.append("")
    out.append(
        f"PRIMARY — conditional on MFE_R >= {x_r:.1f}R  (n={primary.n_measured:,})"
    )
    out.append(
        f"  share finishing at outcome_r <= 0 : {primary.share_non_positive:.1%}"
    )
    out.append(
        f"  median giveback_R                 : {_fmt(primary.median_giveback_r)}"
    )
    if primary.iqr_giveback_r is not None:
        lo, hi = primary.iqr_giveback_r
        out.append(f"  IQR giveback_R                    : [{lo:+.4f}, {hi:+.4f}]")
    out.append(
        f"  median capture (outcome_r / MFE_R): {_fmt(primary.median_capture_ratio)}"
    )
    out.append(
        f"  bootstrap CI on median giveback_R : [{_fmt(primary.ci_lo)}, {_fmt(primary.ci_hi)}]"
    )
    bar = primary.min_licensable_bar
    out.append(
        "  smallest bar a null could clear   : "
        + ("n/a" if bar is None else f"{bar:.4f}R  (no null claim below this)")
    )
    out.append(
        f"  intrabar-ambiguous rows           : {primary.intrabar_ambiguous:,}"
        " (exit bar carried both the stop and the >=X extreme; order unknowable)"
    )
    out.append("")
    out.append("BESIDE IT, never the headline — whole population")
    out.append(_line(whole))
    out.append(
        "  ⚠ dominated by never-green losers, whose giveback_R is ~1.0 by construction"
    )
    out.append("")

    for title, key in (
        ("By stop width (the bias axis)", sl_pct_bucket),
        (
            "By timeframe (pooled — stop-width composition differs per TF)",
            lambda r: r.tf,
        ),
        ("By direction", lambda r: r.direction),
        ("By strategy", lambda r: r.strategy),
    ):
        out.append(f"{title}:")
        out += [_line(s) for s in summarise_by(rows, key, x_r=x_r)]
        out.append("")

    # The TF cut with stop width HELD CONSTANT. The pooled cut above cannot
    # separate "higher TF gives back more" from "higher TF carries a different
    # stop-width mix", and the pooled version is the one a reader quotes.
    flat = [r for r in rows if 0.019 <= r.sl_pct <= 0.021]
    out.append(
        f"By timeframe, stop width held at ~2% (n={len(flat):,} of {len(rows):,}):"
    )
    out += [_line(s) for s in summarise_by(flat, lambda r: r.tf, x_r=x_r)]
    out.append(
        "  ⚠ the stop is 2.000% at the MEDIAN on every TF, so it is far tighter"
        " relative to a 1d range than a 15m one; median bars held runs 96/24/10/2."
    )
    out.append(
        "  ⚠ read this gradient as the flat-2%-SL defect resurfacing, NOT as an"
        " independent property of give-back, and NOT as licence to widen a stop."
    )
    out.append("")

    out.append("Era straddle — every figure above is an average ACROSS these:")
    out += straddle_report(
        load_boundaries(scopes=("ledger",)),
        [r.candle_ts_ms for r in rows],
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--x", type=float, default=DEFAULT_X_R, help="pre-registered threshold in R"
    )
    ap.add_argument("--boot", type=int, default=10_000, help="bootstrap resamples")
    ap.add_argument(
        "--no-restate",
        action="store_true",
        help="reproduce the pre-2026-08-14 mixed cost basis (not like-for-like)",
    )
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args()

    conn = duckdb.connect(args.db, read_only=True)
    rows = load_giveback_rows(conn, x_r=args.x, restate_cost_basis=not args.no_restate)
    basis = (
        "MIXED (pre-parity gross + post-parity net)"
        if args.no_restate
        else "post-parity net"
    )
    print(f"cost basis: {basis}")
    print("\n".join(_report(rows, x_r=args.x, n_boot=args.boot)))


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    main()
