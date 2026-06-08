"""Pure recalibration logic — maps backtest_runs data to confidence star ratings.

No module-level side effects. No DB writes. No network calls.
"""

import re
import statistics
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

import duckdb
import pandas as pd

from analytics.research_guards import deflated_sharpe_ratio

# A 5★ cell whose Deflated Sharpe falls below this is overfit-suspect (spec §3;
# matches the sweep commit-gate threshold in analytics/sweep_guard.py).
DSR_SUSPECT_THRESHOLD = 0.95

# Minimum scoreable trades for a cell to receive a DSR and to join the trial
# family. Below this, a per-trade Sharpe is too noisy to be meaningful — a tiny-n
# low-dispersion cell can produce an extreme Sharpe that, via the (non-robust)
# cross-trial variance, inflates the expected-max-Sharpe benchmark and collapses
# every cell's DSR to ~0. Matches audit_guard.DEFAULT_MIN_N / sweep_guard's floor.
MIN_DSR_TRADES = 30


def _build_run_filter(
    day_filter: str | None,
    adr_suppress_threshold: float | None,
) -> tuple[str, list[str | float]]:
    """Build the shared backtest_runs WHERE-tail (day_filter + ADR scope).

    Returns ``(sql_tail, params)`` where ``sql_tail`` is appended after
    ``WHERE closed_trades > 0``. Mirrors the exact scoping used by both the
    rating aggregation and the DSR annotation so they always read the same runs.
    """
    sql = ""
    params: list[str | float] = []
    if day_filter is not None:
        sql += " AND day_filter = ?"
        params.append(day_filter)
    if adr_suppress_threshold is not None:
        # CAST(? AS REAL): the column is 32-bit REAL; comparing a 64-bit Python
        # float directly fails (0.8 → 0.800000012...). Exempt-strategy runs are
        # stored with NULL threshold and must still be included.
        sql += (
            " AND (adr_suppress_threshold = CAST(? AS REAL) "
            "OR adr_suppress_threshold IS NULL)"
        )
        params.append(adr_suppress_threshold)
    else:
        sql += " AND adr_suppress_threshold IS NULL"
    return sql, params


def get_backtest_win_rates(
    conn: duckdb.DuckDBPyConnection,
    day_filter: str | None = None,
    adr_suppress_threshold: float | None = None,
) -> pd.DataFrame:
    """Query backtest_runs grouped by (strategy, tf), return win_rate, avg_r, total_trades.

    Groups across all symbols for each (strategy, timeframe) combination.
    Only the latest run per (strategy, timeframe, symbol) is used — older runs
    from previous param sweeps are excluded to avoid polluting the ratings.
    Only includes rows where closed_trades > 0.
    If day_filter is provided, only runs saved with that day_filter value are used.
    adr_suppress_threshold: when None (default) uses only runs with no ADR gate
    (adr_suppress_threshold IS NULL); when a float, uses only runs saved with that
    exact threshold. This mirrors the day_filter pattern so recalibration always
    uses runs from the same execution context as the live config.
    Returns a DataFrame with columns:
        strategy, timeframe, total_trades, win_rate, avg_r
    """
    # Fetch raw rows and deduplicate in Python to avoid ROW_NUMBER() window
    # functions, which segfault in DuckDB 1.5.x on Python 3.11.
    filter_sql, params = _build_run_filter(day_filter, adr_suppress_threshold)
    cursor = conn.execute(
        f"SELECT strategy, timeframe, symbol, run_at_ms, closed_trades, win_count, avg_r, "
        f"long_closed_trades, long_win_count, long_avg_r, "
        f"short_closed_trades, short_win_count, short_avg_r "
        f"FROM backtest_runs "
        f"WHERE closed_trades > 0{filter_sql}",
        params,
    )
    rows = cursor.fetchall()
    if not rows:
        return pd.DataFrame(
            columns=[
                "strategy",
                "timeframe",
                "total_trades",
                "win_rate",
                "avg_r",
                "long_total_trades",
                "long_win_rate",
                "long_avg_r",
                "short_total_trades",
                "short_win_rate",
                "short_avg_r",
            ]
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
            "long_closed_trades",
            "long_win_count",
            "long_avg_r",
            "short_closed_trades",
            "short_win_count",
            "short_avg_r",
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
            long_total_trades=("long_closed_trades", "sum"),
            long_win_count_sum=("long_win_count", "sum"),
            long_avg_r=("long_avg_r", "mean"),
            short_total_trades=("short_closed_trades", "sum"),
            short_win_count_sum=("short_win_count", "sum"),
            short_avg_r=("short_avg_r", "mean"),
        )
        .reset_index()
    )
    agg["win_rate"] = (agg["win_count_sum"] / agg["total_trades"]).round(4)
    agg["avg_r"] = agg["avg_r"].round(4)
    agg["total_trades"] = agg["total_trades"].astype(int)
    # Directional win rates — guard against zero-trade denominator
    long_n = agg["long_total_trades"].replace(0, float("nan"))
    short_n = agg["short_total_trades"].replace(0, float("nan"))
    agg["long_win_rate"] = (agg["long_win_count_sum"] / long_n).round(4)
    agg["short_win_rate"] = (agg["short_win_count_sum"] / short_n).round(4)
    agg["long_avg_r"] = agg["long_avg_r"].round(4)
    agg["short_avg_r"] = agg["short_avg_r"].round(4)
    agg["long_total_trades"] = agg["long_total_trades"].fillna(0).astype(int)
    agg["short_total_trades"] = agg["short_total_trades"].fillna(0).astype(int)
    return agg[
        [
            "strategy",
            "timeframe",
            "total_trades",
            "win_rate",
            "avg_r",
            "long_total_trades",
            "long_win_rate",
            "long_avg_r",
            "short_total_trades",
            "short_win_rate",
            "short_avg_r",
        ]
    ]


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
    adr_suppress_threshold: float | None = None,
) -> dict[str, dict[str, int]]:
    """Return {strategy: {tf: stars}} for strategies with sufficient data.

    Each (strategy, timeframe) is rated independently from the backtest DB.
    Strategies with fewer total trades than min_trades for a given TF are excluded.
    If day_filter is provided, only runs saved with that day_filter value are used.
    adr_suppress_threshold: mirrors the day_filter pattern — only runs saved with that
    exact threshold are used (default None uses runs with adr_suppress_threshold IS NULL).
    """
    df = get_backtest_win_rates(
        conn, day_filter=day_filter, adr_suppress_threshold=adr_suppress_threshold
    )
    if df.empty:
        return {}

    result: dict[str, dict[str, int]] = {}
    for row in df.to_dict("records"):
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


def compute_directional_ratings(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    day_filter: str | None = None,
    adr_suppress_threshold: float | None = None,
) -> dict[str, dict[str, dict[str, int]]]:
    """Return {strategy: {tf: {"long": stars, "short": stars}}} from backtest DB.

    Uses a lower default min_trades than compute_recalibrated_ratings (5 vs 10)
    because directional splits have fewer trades than the combined total.
    Directions with fewer than min_trades are omitted (not rated).
    """
    df = get_backtest_win_rates(
        conn, day_filter=day_filter, adr_suppress_threshold=adr_suppress_threshold
    )
    if df.empty:
        return {}

    result: dict[str, dict[str, dict[str, int]]] = {}
    for row in df.to_dict("records"):
        strategy = str(row["strategy"])
        tf = str(row["timeframe"])
        dir_map: dict[str, int] = {}
        for direction, total_col, avg_r_col in [
            ("long", "long_total_trades", "long_avg_r"),
            ("short", "short_total_trades", "short_avg_r"),
        ]:
            total = int(row[total_col]) if not pd.isna(row[total_col]) else 0
            avg_r_raw = row[avg_r_col]
            if total < min_trades or pd.isna(avg_r_raw):
                continue
            stars = win_rate_to_stars(float(avg_r_raw), total, min_trades)
            if stars is not None:
                dir_map[direction] = stars
        if dir_map:
            if strategy not in result:
                result[strategy] = {}
            result[strategy][tf] = dir_map

    return result


def _sharpe(returns: list[float]) -> float | None:
    """Per-trade Sharpe ``mean / stdev(ddof=1)``. None when undefined (<2 trades
    or zero dispersion) — such a cell cannot be deflated and is annotated NULL."""
    if len(returns) < 2:
        return None
    sd = statistics.stdev(returns)
    if sd == 0.0:
        return None
    return statistics.fmean(returns) / sd


def _scope_dsr(
    pools: dict[tuple[str, str], list[float]],
    min_trades: int,
) -> dict[tuple[str, str], float | None]:
    """Deflated Sharpe per cell, deflated against the family of all cells' Sharpes.

    The trial family (N + variance) is the per-recalibrate-pass cell set for one
    direction scope — an **N-FLOOR** on the true search effort (spec §5): the real
    N spans every sweep that ever produced these runs, so this DSR is *optimistic*.
    Only cells with ``>= min_trades`` scoreable trades (and a defined Sharpe) join
    the family and receive a DSR; smaller / degenerate cells are annotated None so
    their noisy Sharpe cannot poison the deflation benchmark (see MIN_DSR_TRADES).
    """
    sharpes = {
        key: (_sharpe(rets) if len(rets) >= min_trades else None)
        for key, rets in pools.items()
    }
    family = [s for s in sharpes.values() if s is not None]
    out: dict[tuple[str, str], float | None] = {}
    for key, sr in sharpes.items():
        out[key] = (
            None
            if sr is None
            else deflated_sharpe_ratio(sr, len(pools[key]), trial_srs=family)
        )
    return out


def compute_dsr_ratings(
    conn: duckdb.DuckDBPyConnection,
    day_filter: str | None = None,
    adr_suppress_threshold: float | None = None,
    min_trades: int = MIN_DSR_TRADES,
) -> dict[str, dict[str, dict[str, float | None]]]:
    """Return ``{strategy: {tf: {"combined"|"long"|"short": dsr}}}`` from per-trade R.

    Pools ``backtest_trades.pnl_r`` over the same latest-run-per-(strategy, tf, symbol)
    set the star ratings use (identical day_filter / ADR scoping), computes each cell's
    Sharpe, and deflates it against the per-pass cell family (see :func:`_scope_dsr`).
    A high-star / low-DSR cell is overfit-suspect. Cells/directions with fewer than
    ``min_trades`` scoreable trades are annotated ``None`` (too noisy to deflate
    reliably, and excluded from the family); ``{}`` when there are no runs or trades.
    """
    filter_sql, params = _build_run_filter(day_filter, adr_suppress_threshold)
    run_rows = conn.execute(
        f"SELECT run_id, strategy, timeframe, symbol, run_at_ms FROM backtest_runs "
        f"WHERE closed_trades > 0{filter_sql}",
        params,
    ).fetchall()
    if not run_rows:
        return {}

    # Keep only the latest run per (strategy, timeframe, symbol) — mirrors
    # get_backtest_win_rates so DSR and the rated avg_r read the same trades.
    latest: dict[tuple[str, str, str], tuple[int, str]] = {}
    for run_id, strategy, tf, symbol, run_at_ms in run_rows:
        key = (str(strategy), str(tf), str(symbol))
        cur = latest.get(key)
        if cur is None or int(run_at_ms) > cur[0]:
            latest[key] = (int(run_at_ms), str(run_id))
    run_ids = [v[1] for v in latest.values()]

    placeholders = ",".join("?" * len(run_ids))
    trade_rows = conn.execute(
        f"SELECT strategy, timeframe, direction, pnl_r FROM backtest_trades "
        f"WHERE run_id IN ({placeholders}) AND outcome <> 'open' AND pnl_r IS NOT NULL",
        run_ids,
    ).fetchall()
    if not trade_rows:
        return {}

    combined: dict[tuple[str, str], list[float]] = defaultdict(list)
    longs: dict[tuple[str, str], list[float]] = defaultdict(list)
    shorts: dict[tuple[str, str], list[float]] = defaultdict(list)
    for strategy, tf, direction, pnl_r in trade_rows:
        cell = (str(strategy), str(tf))
        combined[cell].append(float(pnl_r))
        if direction == "long":
            longs[cell].append(float(pnl_r))
        elif direction == "short":
            shorts[cell].append(float(pnl_r))

    dsr_combined = _scope_dsr(combined, min_trades)
    dsr_long = _scope_dsr(longs, min_trades)
    dsr_short = _scope_dsr(shorts, min_trades)

    result: dict[str, dict[str, dict[str, float | None]]] = {}
    for strategy, tf in combined:
        result.setdefault(strategy, {})[tf] = {
            "combined": dsr_combined.get((strategy, tf)),
            "long": dsr_long.get((strategy, tf)),
            "short": dsr_short.get((strategy, tf)),
        }
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
    directional_ratings: dict[str, dict[str, dict[str, int]]] | None = None,
    dsr_ratings: dict[str, dict[str, dict[str, float | None]]] | None = None,
) -> str:
    """Human-readable diff table showing old vs new star ratings per TF.

    win_rates must be the DataFrame returned by get_backtest_win_rates().
    Strategies not present in new_ratings (insufficient data) are listed separately.
    When directional_ratings is provided, appends a directional breakdown section.
    When dsr_ratings is provided, appends a "Suspect" line listing high-conviction
    (★≥4) cells whose combined Deflated Sharpe is below DSR_SUSPECT_THRESHOLD —
    likely overfit despite a high star rating.
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

    # Build directional lookup: (strategy, tf) → (long_avg_r, short_avg_r)
    dir_stats: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    if not win_rates.empty and "long_avg_r" in win_rates.columns:
        for _, row in win_rates.iterrows():
            key = (str(row["strategy"]), str(row["timeframe"]))
            lar = row.get("long_avg_r")
            sar = row.get("short_avg_r")
            dir_stats[key] = (
                float(lar) if lar is not None and not pd.isna(lar) else None,
                float(sar) if sar is not None and not pd.isna(sar) else None,
            )

    all_strategies = sorted(set(old_ratings) | set(new_ratings))

    lines: list[str] = []
    lines.append("═" * 92)
    lines.append("Confidence Star Recalibration Report (Per TF)")
    lines.append("═" * 92)
    lines.append(
        f"  {'Strategy':<22} {'TF':<5} {'Old':>6} {'New':>6} {'Trades':>8} {'WinRate':>8} {'AvgR':>7}"
        f"  {'L★':>6} {'S★':>6}  Change"
    )
    lines.append("─" * 92)

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

            # Directional stars for this row
            dir_entry = (directional_ratings or {}).get(strat, {}).get(tf, {})
            long_s = dir_entry.get("long")
            short_s = dir_entry.get("short")
            long_star_str = star(long_s) if long_s is not None else "  — "
            short_star_str = star(short_s) if short_s is not None else "  — "

            if new_stars != old_stars:
                arrow = "▲" if new_stars > old_stars else "▼"
                changed.append(f"{strat}/{tf}")
                lines.append(
                    f"  {strat:<22} {tf:<5} {star(old_stars):>6} {star(new_stars):>6}"
                    f" {trades_str:>8} {win_rate_str:>8} {avg_r_str:>7}"
                    f"  {long_star_str:>6} {short_star_str:>6}"
                    f"  {arrow} {old_stars}→{new_stars}"
                )
            else:
                unchanged.append(f"{strat}/{tf}")
                lines.append(
                    f"  {strat:<22} {tf:<5} {star(old_stars):>6} {star(new_stars):>6}"
                    f" {trades_str:>8} {win_rate_str:>8} {avg_r_str:>7}"
                    f"  {long_star_str:>6} {short_star_str:>6}  ="
                )

    lines.append("─" * 92)
    lines.append(
        f"  Changed: {len(changed)}  Unchanged: {len(unchanged)}  No data: {len(no_data)}"
    )

    if changed:
        lines.append(f"\n  Strategy/TF combos that would change: {', '.join(changed)}")

    if dsr_ratings:
        suspect: list[str] = []
        for strat in sorted(new_ratings):
            for tf in sorted(new_ratings[strat]):
                stars = new_ratings[strat][tf]
                dsr = dsr_ratings.get(strat, {}).get(tf, {}).get("combined")
                if stars >= 4 and dsr is not None and dsr < DSR_SUSPECT_THRESHOLD:
                    suspect.append(f"{strat}/{tf} (DSR {dsr:.2f})")
        if suspect:
            lines.append(
                f"\n  ⚠ Suspect (★≥4 but DSR<{DSR_SUSPECT_THRESHOLD:.2f}, likely "
                f"overfit): {', '.join(suspect)}"
            )

    return "\n".join(lines)


def prune_stale_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    day_filter: str,
) -> int:
    """Delete confidence_ratings rows for this config whose stored day_filter
    no longer matches the current config's day_filter.

    A given config_name should always reflect one day_filter scope at a time;
    when the scope is changed (e.g. weekdays → mon_fri), the upsert path only
    refreshes (config, strategy, tf, direction) keys that produced fresh runs
    under the new scope, leaving the others as zombie rows. This helper deletes
    those zombies. Returns the number of rows removed.
    """
    stale = conn.execute(
        "SELECT COUNT(*) FROM confidence_ratings "
        "WHERE config_name = ? AND day_filter IS NOT NULL AND day_filter <> ?",
        [config_name, day_filter],
    ).fetchone()
    n_stale = int(stale[0]) if stale else 0
    if n_stale:
        conn.execute(
            "DELETE FROM confidence_ratings "
            "WHERE config_name = ? AND day_filter IS NOT NULL AND day_filter <> ?",
            [config_name, day_filter],
        )
    return n_stale


def _slice_dsr_map(
    dsr_ratings: dict[str, dict[str, dict[str, float | None]]] | None,
    direction: str,
) -> dict[str, dict[str, float]] | None:
    """Project the nested DSR ratings onto ``{strategy: {tf: dsr}}`` for one direction.

    Drops cells whose DSR for this direction is None (uncomputable) so the upsert
    writes NULL rather than a bogus value.
    """
    if not dsr_ratings:
        return None
    out: dict[str, dict[str, float]] = {}
    for strategy, tf_map in dsr_ratings.items():
        for tf, scope_map in tf_map.items():
            value = scope_map.get(direction)
            if value is not None:
                out.setdefault(strategy, {})[tf] = value
    return out or None


def write_confidence_to_db(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    ratings: dict[str, dict[str, int]],
    win_rates: pd.DataFrame,
    day_filter: str | None = None,
    directional_ratings: dict[str, dict[str, dict[str, int]]] | None = None,
    dsr_ratings: dict[str, dict[str, dict[str, float | None]]] | None = None,
) -> None:
    """Upsert confidence star ratings to the DB for a specific config.

    Writes combined stars (direction='combined') and, when directional_ratings is
    provided, also long/short directional stars.
    config_name: TOML stem, e.g. 'signal_watch', 'signal_watch_weekdays'.
    day_filter: stored alongside stars so backtest rows can JOIN without a UI selector.
    dsr_ratings: {strategy: {tf: {"combined"|"long"|"short": dsr}}} from
        compute_dsr_ratings() — each star is annotated with its Deflated Sharpe so a
        high-star / low-DSR (overfit-suspect) cell is visible downstream.
    """
    from analytics.data_store import upsert_confidence_ratings

    upsert_confidence_ratings(
        conn,
        config_name,
        ratings,
        win_rates,
        day_filter=day_filter,
        direction="combined",
        dsr_map=_slice_dsr_map(dsr_ratings, "combined"),
    )
    if not directional_ratings:
        return
    for direction, _total_col, avg_r_col, wr_col in [
        ("long", "long_total_trades", "long_avg_r", "long_win_rate"),
        ("short", "short_total_trades", "short_avg_r", "short_win_rate"),
    ]:
        dir_map: dict[str, dict[str, int]] = {}
        for strategy, tf_map in directional_ratings.items():
            for tf, stars_map in tf_map.items():
                if direction in stars_map:
                    if strategy not in dir_map:
                        dir_map[strategy] = {}
                    dir_map[strategy][tf] = stars_map[direction]
        if dir_map:
            upsert_confidence_ratings(
                conn,
                config_name,
                dir_map,
                win_rates,
                day_filter=day_filter,
                direction=direction,
                avg_r_col=avg_r_col,
                win_rate_col=wr_col,
                dsr_map=_slice_dsr_map(dsr_ratings, direction),
            )


def write_confidence_to_source(
    updates: Mapping[str, dict[str, int] | int],
    source_path: Path,
) -> list[str]:
    """Patch confidence values in ``source_path`` (the StrategySpec source,
    ``analytics/strategies/_registry.py``) for each strategy in updates.

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
