#!/usr/bin/env python3
"""Extract frozen OHLCV fixture files for regression testing.

Run once to seed tests/fixtures/ — re-run only when intentionally refreshing
the fixture window (triggers a mass golden-file regeneration).

Usage:
    poetry run python scripts/extract_regression_fixture.py

Output:
    tests/fixtures/btc_15m_200d.parquet
    tests/fixtures/btc_1h_200d.parquet
    tests/fixtures/btc_4h_200d.parquet
    tests/fixtures/btc_1d_200d.parquet
    tests/fixtures/btc_funding_200d.parquet
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

# Repo root is one level up from scripts/
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.data_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    get_funding_rates,
    get_ohlcv,
)

SYMBOL = "BTCUSDT"
SINCE = "2025-09-12"  # canonical anchor — matches `--since` backfill date
TIMEFRAMES = ["15m", "1h", "4h", "1d"]
OUTPUT_DIR = REPO_ROOT / "tests" / "fixtures"


def main() -> None:
    db_path = REPO_ROOT / DEFAULT_DB_PATH
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    since_ms = int(
        datetime.strptime(SINCE, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000
    )
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        for tf in TIMEFRAMES:
            df = get_ohlcv(conn, SYMBOL, tf, since_ms, now_ms)
            if df.empty:
                print(f"WARNING: no data for {SYMBOL} {tf} — skipping", file=sys.stderr)
                continue
            # Save string columns as object so pyarrow doesn't re-encode them
            # as large_string on reload (which creates extra ExtensionBlocks
            # that make iloc row-access very slow inside detector loops).
            for col in ("symbol", "timeframe"):
                if col in df.columns:
                    df[col] = df[col].astype(object)
            out = OUTPUT_DIR / f"btc_{tf}_200d.parquet"
            df.to_parquet(out, index=False)
            print(f"  wrote {out.name}  ({len(df)} rows)")

        # Funding-rate fixture (P0b) — lets the regression goldens reflect the
        # full net_R (raw − fee − slippage − funding). One stamp / 8h; the
        # window is a superset of the OHLCV span so every trade's funding
        # accrual resolves.
        fdf = get_funding_rates(conn, SYMBOL, since_ms, now_ms)
        if fdf.empty:
            print(
                f"WARNING: no funding for {SYMBOL} — funding fixture skipped",
                file=sys.stderr,
            )
        else:
            fdf["symbol"] = fdf["symbol"].astype(object)
            fout = OUTPUT_DIR / "btc_funding_200d.parquet"
            fdf.to_parquet(fout, index=False)
            print(f"  wrote {fout.name}  ({len(fdf)} rows)")
    finally:
        conn.close()

    print(f"\nFixtures written to {OUTPUT_DIR}/")
    print("Next: poetry run pytest tests/test_regression.py --update-golden -v")


if __name__ == "__main__":
    main()
