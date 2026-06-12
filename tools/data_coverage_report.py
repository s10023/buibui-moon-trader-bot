"""Data coverage report (N3 acceptance artifact).

Read-only against analytics.db: per (symbol x timeframe) row counts, date
range, expected-bar count and gap %, plus funding coverage and lifecycle
status. Output is markdown (optionally CSV) — committed to docs/audits/ after
a universe backfill.

Run via: PYTHONPATH=. poetry run python tools/data_coverage_report.py [--db analytics.db] [--csv out.csv]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

TF_MS: dict[str, int] = {
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def ohlcv_coverage(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per (symbol, timeframe): n, first/last day, expected bars, gap_pct."""
    df = conn.execute(
        "SELECT symbol, timeframe, COUNT(*) AS n, "
        "MIN(open_time) AS first_ms, MAX(open_time) AS last_ms, "
        "to_timestamp(MIN(open_time)/1000)::DATE AS first_day, "
        "to_timestamp(MAX(open_time)/1000)::DATE AS last_day "
        "FROM ohlcv GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
    ).df()

    expected: list[int | None] = []
    gap_pct: list[float | None] = []
    for _, row in df.iterrows():
        tf_ms = TF_MS.get(str(row["timeframe"]))
        if tf_ms is None:
            expected.append(None)
            gap_pct.append(None)
            continue
        exp = int((int(row["last_ms"]) - int(row["first_ms"])) // tf_ms) + 1
        expected.append(exp)
        gap_pct.append(
            round(1.0 - float(row["n"]) / float(exp), 4) if exp > 0 else None
        )
    df["expected"] = pd.Series(expected, dtype="object")
    df["gap_pct"] = gap_pct
    return df


def funding_coverage(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per symbol: funding row count + date range."""
    return conn.execute(
        "SELECT symbol, COUNT(*) AS n, "
        "to_timestamp(MIN(funding_time)/1000)::DATE AS first_day, "
        "to_timestamp(MAX(funding_time)/1000)::DATE AS last_day "
        "FROM funding_rates GROUP BY symbol ORDER BY symbol"
    ).df()


def lifecycle_table(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Lifecycle rows with onboard date rendered."""
    return conn.execute(
        "SELECT symbol, status, "
        "to_timestamp(onboard_ms/1000)::DATE AS onboarded, "
        "to_timestamp(delisted_noted_ms/1000)::DATE AS delisted_noted "
        "FROM symbol_lifecycle ORDER BY symbol"
    ).df()


def _md_table(df: pd.DataFrame) -> str:
    # Manual pipe-table renderer: emits the repo's markdownlint-conformant
    # spaced `| --- |` delimiters (pandas to_markdown emits `|:---|`).
    if df.empty:
        return "(no rows)\n"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |")
    return "\n".join(lines) + "\n"


def format_report(
    ohlcv: pd.DataFrame,
    funding: pd.DataFrame,
    lifecycle: pd.DataFrame | None,
) -> str:
    """Render the markdown coverage report."""
    parts = [
        "# Data coverage report",
        "",
        f"Symbols: {ohlcv['symbol'].nunique() if not ohlcv.empty else 0} · "
        f"OHLCV rows: {int(ohlcv['n'].sum()) if not ohlcv.empty else 0:,}",
        "",
        "## OHLCV coverage",
        "",
        _md_table(
            ohlcv[
                [
                    "symbol",
                    "timeframe",
                    "n",
                    "first_day",
                    "last_day",
                    "expected",
                    "gap_pct",
                ]
            ]
            if not ohlcv.empty
            else ohlcv
        ),
        "## Funding coverage",
        "",
        _md_table(funding),
    ]
    if lifecycle is not None and not lifecycle.empty:
        parts += ["## Symbol lifecycle", "", _md_table(lifecycle)]
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="analytics.db")
    parser.add_argument("--csv", default=None, help="Also write the OHLCV table as CSV")
    args = parser.parse_args()

    conn = duckdb.connect(args.db, read_only=True)
    try:
        ohlcv = ohlcv_coverage(conn)
        funding = funding_coverage(conn)
        life = lifecycle_table(conn)
    finally:
        conn.close()
    print(format_report(ohlcv, funding, life))
    if args.csv:
        ohlcv.to_csv(Path(args.csv), index=False)


if __name__ == "__main__":
    main()
