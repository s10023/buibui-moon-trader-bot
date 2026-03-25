"""Pure recalibration logic — maps backtest_runs data to confidence star ratings.

No module-level side effects. No DB writes. No network calls.
"""

import duckdb
import pandas as pd


def get_backtest_win_rates(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Query backtest_runs grouped by (strategy, tf), return win_rate, avg_r, total_trades.

    Groups across all symbols for each (strategy, timeframe) combination.
    Only includes rows where closed_trades > 0.
    Returns a DataFrame with columns:
        strategy, timeframe, total_trades, win_rate, avg_r
    """
    return conn.execute("""
        SELECT
            strategy,
            timeframe,
            SUM(closed_trades)                                                AS total_trades,
            ROUND(SUM(win_count) * 1.0 / NULLIF(SUM(closed_trades), 0), 4)  AS win_rate,
            ROUND(AVG(avg_r), 4)                                             AS avg_r
        FROM backtest_runs
        WHERE closed_trades > 0
        GROUP BY strategy, timeframe
        ORDER BY strategy, timeframe
    """).df()


def win_rate_to_stars(
    avg_r: float, total_trades: int, min_trades: int = 10
) -> int | None:
    """Map avg_r to 1–5 stars. Returns None when total_trades < min_trades.

    Thresholds:
        avg_r < 0        → 1★
        0 <= avg_r < 0.2 → 2★
        0.2 <= avg_r < 0.5 → 3★
        0.5 <= avg_r < 0.9 → 4★
        avg_r >= 0.9     → 5★
    """
    if total_trades < min_trades:
        return None
    if avg_r < 0:
        return 1
    if avg_r < 0.2:
        return 2
    if avg_r < 0.5:
        return 3
    if avg_r < 0.9:
        return 4
    return 5


def compute_recalibrated_ratings(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 10,
) -> dict[str, int]:
    """Return {strategy_name: new_stars} for strategies with sufficient data.

    Aggregates avg_r across all (strategy, timeframe) groups, weighted by trade count.
    Strategies with fewer total trades than min_trades across all TFs are excluded.
    """
    df = get_backtest_win_rates(conn)
    if df.empty:
        return {}

    # Weighted-average avg_r across timeframes per strategy
    strategy_groups = df.groupby("strategy")
    result: dict[str, int] = {}

    for strategy, group in strategy_groups:
        total = int(group["total_trades"].sum())
        # weighted avg_r by trade count per TF
        weighted_avg_r = float(
            (group["avg_r"] * group["total_trades"]).sum() / total if total > 0 else 0.0
        )
        stars = win_rate_to_stars(weighted_avg_r, total, min_trades)
        if stars is not None:
            result[str(strategy)] = stars

    return result


def format_recalibration_report(
    old_ratings: dict[str, int],
    new_ratings: dict[str, int],
    win_rates: pd.DataFrame,
) -> str:
    """Human-readable diff table showing old vs new star ratings with win rate data.

    win_rates must be the DataFrame returned by get_backtest_win_rates().
    Strategies not present in new_ratings (insufficient data) are listed separately.
    """
    star = lambda n: "★" * n + "☆" * (5 - n)  # noqa: E731

    all_strategies = sorted(set(old_ratings) | set(new_ratings))

    # Build per-strategy summary row from win_rates DataFrame
    by_strategy: dict[str, tuple[int, float, float]] = {}
    if not win_rates.empty:
        for strat, grp in win_rates.groupby("strategy"):
            total = int(grp["total_trades"].sum())
            w_avg_r = float(
                (grp["avg_r"] * grp["total_trades"]).sum() / total if total > 0 else 0.0
            )
            w_win_rate = float(grp["win_rate"].mean())
            by_strategy[str(strat)] = (total, w_avg_r, w_win_rate)

    lines: list[str] = []
    lines.append("═" * 72)
    lines.append("Confidence Star Recalibration Report")
    lines.append("═" * 72)
    lines.append(
        f"  {'Strategy':<22} {'Old':>6} {'New':>6} {'Trades':>8} {'WinRate':>8} {'AvgR':>7}  Change"
    )
    lines.append("─" * 72)

    changed: list[str] = []
    unchanged: list[str] = []
    no_data: list[str] = []

    for strat in all_strategies:
        old = old_ratings.get(strat, 0)
        new = new_ratings.get(strat)

        stats = by_strategy.get(strat)
        if stats is not None:
            trades_str = str(stats[0])
            win_rate_str = f"{stats[2]:.1%}"
            avg_r_str = f"{stats[1]:+.3f}"
        else:
            trades_str = "—"
            win_rate_str = "—"
            avg_r_str = "—"

        if new is None:
            no_data.append(strat)
            lines.append(
                f"  {strat:<22} {star(old):>6}  {'(no data)':>6} {trades_str:>8} {win_rate_str:>8} {avg_r_str:>7}"
            )
        elif new != old:
            direction = "▲" if new > old else "▼"
            changed.append(strat)
            lines.append(
                f"  {strat:<22} {star(old):>6} {star(new):>6} {trades_str:>8} {win_rate_str:>8} {avg_r_str:>7}  {direction} {old}→{new}"
            )
        else:
            unchanged.append(strat)
            lines.append(
                f"  {strat:<22} {star(old):>6} {star(new):>6} {trades_str:>8} {win_rate_str:>8} {avg_r_str:>7}  ="
            )

    lines.append("─" * 72)
    lines.append(
        f"  Changed: {len(changed)}  Unchanged: {len(unchanged)}  No data: {len(no_data)}"
    )

    if changed:
        lines.append(f"\n  Strategies that would change: {', '.join(changed)}")

    return "\n".join(lines)
