"""Prune old backtest runs from analytics.db.

Keeps whichever is more recent:
  - runs from the last DAYS days (default 7)
  - the most recent MAX_ROWS runs (default 200)

Cascade-deletes matching rows from backtest_trades (no FK enforcement in DuckDB).
"""

import sys
import time

import duckdb

DB_PATH = "analytics.db"
DAYS = 7
MAX_RUNS_PER_COMBO = 10  # per (strategy, symbol, timeframe, day_filter)


def main() -> None:
    conn = duckdb.connect(DB_PATH)

    now_ms = int(time.time() * 1000)
    cutoff_date_ms = now_ms - DAYS * 86_400 * 1_000

    total_runs: int = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]  # type: ignore[index]

    # Rows older than DAYS days
    date_delete_ids: list[str] = [
        r[0]
        for r in conn.execute(
            "SELECT run_id FROM backtest_runs WHERE run_at_ms < ?", [cutoff_date_ms]
        ).fetchall()
    ]

    # Rows beyond the MAX_RUNS_PER_COMBO most recent per (strategy, symbol, timeframe, day_filter)
    combo_delete_ids: list[str] = [
        r[0]
        for r in conn.execute(
            f"""
            SELECT run_id FROM (
                SELECT run_id, ROW_NUMBER() OVER (
                    PARTITION BY strategy, symbol, timeframe, day_filter
                    ORDER BY run_at_ms DESC
                ) AS rn
                FROM backtest_runs
            ) WHERE rn > {MAX_RUNS_PER_COMBO}
            """
        ).fetchall()
    ]

    # Delete only rows that fail BOTH safeguards:
    # a run is kept if it's within 7 days OR within the top-10 for its combo
    to_delete = set(date_delete_ids) & set(combo_delete_ids)

    if not to_delete:
        print(
            f"Nothing to prune — {total_runs} runs in DB, all within last {DAYS}d or top {MAX_RUNS_PER_COMBO} per combo."
        )
        conn.close()
        return

    # Preview
    preview = conn.execute(
        f"""
        SELECT strategy, symbol, timeframe, day_filter,
               strftime(to_timestamp(run_at_ms / 1000), '%Y-%m-%d %H:%M') AS run_at,
               avg_r, closed_trades
        FROM backtest_runs
        WHERE run_id IN ({",".join("?" * len(to_delete))})
        ORDER BY run_at_ms DESC
        """,
        list(to_delete),
    ).fetchall()

    trade_count: int = conn.execute(
        f"SELECT COUNT(*) FROM backtest_trades WHERE run_id IN ({','.join('?' * len(to_delete))})",
        list(to_delete),
    ).fetchone()[0]  # type: ignore[index]

    print(f"\nCurrent DB: {total_runs} backtest runs")
    print(f"Will DELETE: {len(to_delete)} runs + {trade_count} trades")
    print(f"Will KEEP:   {total_runs - len(to_delete)} runs\n")
    print(
        f"{'Strategy':<25} {'Symbol':<8} {'TF':<5} {'DayFilter':<10} {'RunAt':<17} {'AvgR':>6} {'Trades':>7}"
    )
    print("-" * 82)
    for row in preview:
        strategy, symbol, tf, day_filter, run_at, avg_r, trades = row
        print(
            f"{strategy:<25} {symbol:<8} {tf:<5} {day_filter:<10} {run_at:<17} {avg_r:>6.3f} {trades:>7}"
        )

    print()
    answer = input(f"Delete these {len(to_delete)} runs? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted — no changes made.")
        conn.close()
        return

    conn.execute(
        f"DELETE FROM backtest_trades WHERE run_id IN ({','.join('?' * len(to_delete))})",
        list(to_delete),
    )
    conn.execute(
        f"DELETE FROM backtest_runs WHERE run_id IN ({','.join('?' * len(to_delete))})",
        list(to_delete),
    )
    conn.close()

    remaining = total_runs - len(to_delete)
    print(f"\nDone. {len(to_delete)} runs deleted, {remaining} remain.")
    print()
    print(
        "Reminder: if you're unsure whether important runs were removed, re-run and save:"
    )
    print("  make buibui-backtest SAVE=1 CONFIG=config/signal_watch.toml")
    print("  make buibui-recalibrate CONFIG=config/signal_watch.toml APPLY=1")


if __name__ == "__main__":
    main()
    sys.exit(0)
