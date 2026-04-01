"""Pure recalibration logic — maps backtest_runs data to confidence star ratings.

No module-level side effects. No DB writes. No network calls.
"""

import re
from collections.abc import Mapping
from pathlib import Path

import duckdb
import pandas as pd


def get_backtest_win_rates(
    conn: duckdb.DuckDBPyConnection,
    day_filter: str | None = None,
) -> pd.DataFrame:
    """Query backtest_runs grouped by (strategy, tf), return win_rate, avg_r, total_trades.

    Groups across all symbols for each (strategy, timeframe) combination.
    Only the latest run per (strategy, timeframe, symbol) is used — older runs
    from previous param sweeps are excluded to avoid polluting the ratings.
    Only includes rows where closed_trades > 0.
    If day_filter is provided, only runs saved with that day_filter value are used.
    Returns a DataFrame with columns:
        strategy, timeframe, total_trades, win_rate, avg_r
    """
    # Fetch raw rows and deduplicate in Python to avoid ROW_NUMBER() window
    # functions, which segfault in DuckDB 1.5.x on Python 3.11.
    day_filter_clause = "AND day_filter = ?" if day_filter is not None else ""
    params = [day_filter] if day_filter is not None else []
    cursor = conn.execute(
        f"SELECT strategy, timeframe, symbol, run_at_ms, closed_trades, win_count, avg_r "
        f"FROM backtest_runs "
        f"WHERE closed_trades > 0 {day_filter_clause}",
        params,
    )
    rows = cursor.fetchall()
    if not rows:
        return pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )

    raw = pd.DataFrame(
        rows,
        columns=[
            "strategy",
            "timeframe",
            "symbol",
            "run_at_ms",
            "closed_trades",
            "win_count",
            "avg_r",
        ],
    )
    # Keep only the latest run per (strategy, timeframe, symbol)
    raw = raw.sort_values("run_at_ms", ascending=False).drop_duplicates(
        subset=["strategy", "timeframe", "symbol"]
    )
    # Aggregate across symbols
    agg = (
        raw.groupby(["strategy", "timeframe"], sort=True)
        .agg(
            total_trades=("closed_trades", "sum"),
            win_count_sum=("win_count", "sum"),
            avg_r=("avg_r", "mean"),
        )
        .reset_index()
    )
    agg["win_rate"] = (agg["win_count_sum"] / agg["total_trades"]).round(4)
    agg["avg_r"] = agg["avg_r"].round(4)
    agg["total_trades"] = agg["total_trades"].astype(int)
    return agg[["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]]


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
    day_filter: str | None = None,
) -> dict[str, dict[str, int]]:
    """Return {strategy: {tf: stars}} for strategies with sufficient data.

    Each (strategy, timeframe) is rated independently from the backtest DB.
    Strategies with fewer total trades than min_trades for a given TF are excluded.
    If day_filter is provided, only runs saved with that day_filter value are used.
    """
    df = get_backtest_win_rates(conn, day_filter=day_filter)
    if df.empty:
        return {}

    result: dict[str, dict[str, int]] = {}
    for _, row in df.iterrows():
        strategy = str(row["strategy"])
        tf = str(row["timeframe"])
        total = int(row["total_trades"])
        avg_r = float(row["avg_r"])
        stars = win_rate_to_stars(avg_r, total, min_trades)
        if stars is not None:
            if strategy not in result:
                result[strategy] = {}
            result[strategy][tf] = stars

    return result


def _fmt_confidence_value(value: dict[str, int] | int) -> str:
    """Format a confidence value as a Python literal for source patching."""
    if isinstance(value, int):
        return str(value)
    items = ", ".join(f'"{k}": {v}' for k, v in sorted(value.items()))
    return "{" + items + "}"


def _get_old_stars(old_val: dict[str, int] | int, tf: str) -> int:
    """Resolve old confidence value for a given TF."""
    if isinstance(old_val, int):
        return old_val
    return old_val.get(tf, old_val.get("default", 3))


def format_recalibration_report(
    old_ratings: dict[str, dict[str, int] | int],
    new_ratings: dict[str, dict[str, int]],
    win_rates: pd.DataFrame,
) -> str:
    """Human-readable diff table showing old vs new star ratings per TF.

    win_rates must be the DataFrame returned by get_backtest_win_rates().
    Strategies not present in new_ratings (insufficient data) are listed separately.
    """
    star = lambda n: "★" * n + "☆" * (5 - n)  # noqa: E731

    # Build lookup: (strategy, tf) → (total_trades, avg_r, win_rate)
    tf_stats: dict[tuple[str, str], tuple[int, float, float]] = {}
    if not win_rates.empty:
        for _, row in win_rates.iterrows():
            key = (str(row["strategy"]), str(row["timeframe"]))
            tf_stats[key] = (
                int(row["total_trades"]),
                float(row["avg_r"]),
                float(row["win_rate"]),
            )

    all_strategies = sorted(set(old_ratings) | set(new_ratings))

    lines: list[str] = []
    lines.append("═" * 80)
    lines.append("Confidence Star Recalibration Report (Per TF)")
    lines.append("═" * 80)
    lines.append(
        f"  {'Strategy':<22} {'TF':<5} {'Old':>6} {'New':>6} {'Trades':>8} {'WinRate':>8} {'AvgR':>7}  Change"
    )
    lines.append("─" * 80)

    changed: list[str] = []
    unchanged: list[str] = []
    no_data: list[str] = []

    for strat in all_strategies:
        old_val = old_ratings.get(strat, 3)
        new_tf_map = new_ratings.get(strat)

        if new_tf_map is None:
            no_data.append(strat)
            lines.append(
                f"  {strat:<22} {'—':<5} {star(_get_old_stars(old_val, '?')):>6}  {'(no data)':>6}"
            )
            continue

        for tf in sorted(new_tf_map):
            old_stars = _get_old_stars(old_val, tf)
            new_stars = new_tf_map[tf]
            stats = tf_stats.get((strat, tf))
            if stats is not None:
                trades_str = str(stats[0])
                win_rate_str = f"{stats[2]:.1%}"
                avg_r_str = f"{stats[1]:+.3f}"
            else:
                trades_str = "—"
                win_rate_str = "—"
                avg_r_str = "—"

            if new_stars != old_stars:
                direction = "▲" if new_stars > old_stars else "▼"
                changed.append(f"{strat}/{tf}")
                lines.append(
                    f"  {strat:<22} {tf:<5} {star(old_stars):>6} {star(new_stars):>6}"
                    f" {trades_str:>8} {win_rate_str:>8} {avg_r_str:>7}"
                    f"  {direction} {old_stars}→{new_stars}"
                )
            else:
                unchanged.append(f"{strat}/{tf}")
                lines.append(
                    f"  {strat:<22} {tf:<5} {star(old_stars):>6} {star(new_stars):>6}"
                    f" {trades_str:>8} {win_rate_str:>8} {avg_r_str:>7}  ="
                )

    lines.append("─" * 80)
    lines.append(
        f"  Changed: {len(changed)}  Unchanged: {len(unchanged)}  No data: {len(no_data)}"
    )

    if changed:
        lines.append(f"\n  Strategy/TF combos that would change: {', '.join(changed)}")

    return "\n".join(lines)


def write_confidence_to_db(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    ratings: dict[str, dict[str, int]],
    win_rates: pd.DataFrame,
    day_filter: str | None = None,
) -> None:
    """Upsert confidence star ratings to the DB for a specific config.

    Replaces write_confidence_to_source for per-config star storage.
    config_name: TOML stem, e.g. 'signal_watch', 'signal_watch_weekdays'.
    day_filter: stored alongside stars so backtest rows can JOIN without a UI selector.
    """
    from analytics.data_store import upsert_confidence_ratings

    upsert_confidence_ratings(
        conn, config_name, ratings, win_rates, day_filter=day_filter
    )


def write_confidence_to_source(
    updates: Mapping[str, dict[str, int] | int],
    source_path: Path,
) -> list[str]:
    """Patch confidence values in indicators_lib.py for each strategy in updates.

    Accepts either a plain int (applies to all TFs) or a per-TF dict
    (e.g. {"default": 2, "4h": 4}).

    Finds each StrategySpec block by strategy key and replaces its confidence value.
    Returns a list of strategy names that were successfully patched.

    The pattern matched per strategy:
        "strategy_name": StrategySpec(
            ...
            confidence=N,        ← int form, replaced
            confidence={...},    ← dict form, replaced
    """
    content = source_path.read_text()
    patched: list[str] = []

    for strategy, value in updates.items():
        replacement_str = _fmt_confidence_value(value)
        pattern = re.compile(
            r'("' + re.escape(strategy) + r'":\s*StrategySpec\(.*?)'
            r"(confidence=)(\d+|\{[^}]*\})",
            re.DOTALL,
        )

        def _replacer(m: re.Match[str], _repl: str = replacement_str) -> str:
            return m.group(1) + m.group(2) + _repl

        new_content, count = pattern.subn(_replacer, content)
        if count:
            content = new_content
            patched.append(strategy)

    if patched:
        source_path.write_text(content)

    return patched
