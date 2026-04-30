"""backtest_combos + backtest_cross_tf_combos table accessors."""

import time
from typing import Any

import duckdb
import pandas as pd


def upsert_combo_run(
    conn: duckdb.DuckDBPyConnection,
    combo: "Any",  # ComboBacktestResult — avoid circular import
    days: int,
    data_start_ms: int,
    data_end_ms: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
) -> str:
    """Persist a ComboBacktestResult aggregate row and return the combo_id."""
    from analytics.backtest_lib import ComboBacktestResult

    assert isinstance(combo, ComboBacktestResult)
    r = combo.result

    run_at_ms = int(time.time() * 1000)
    # combo_id excludes run_at_ms so re-running overwrites the previous result
    # for the same (symbol, tf, pair, window, day_filter) instead of duplicating.
    combo_id = (
        f"{r.symbol}|{r.timeframe}|{combo.strategy_a}+{combo.strategy_b}"
        f"|w{combo.window}|{day_filter}"
    )

    rf = r.recovery_factor if r.max_drawdown_r > 0 else None

    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_combos (
            combo_id, symbol, timeframe, strategy_a, strategy_b, window_candles,
            data_start_ms, data_end_ms, days, sl_pct, tp_r, fee_pct, day_filter,
            total_signals, closed_trades, win_count, win_rate, avg_r, total_r,
            max_drawdown_r, recovery_factor,
            long_closed_trades, long_win_rate, long_avg_r,
            short_closed_trades, short_win_rate, short_avg_r,
            run_at_ms
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?
        )
        """,
        [
            combo_id,
            r.symbol,
            r.timeframe,
            combo.strategy_a,
            combo.strategy_b,
            combo.window,
            data_start_ms,
            data_end_ms,
            days,
            sl_pct,
            tp_r,
            fee_pct,
            day_filter,
            len(r.trades),
            len(r.closed_trades),
            r.win_count,
            r.win_rate,
            r.avg_r,
            r.total_r,
            r.max_drawdown_r,
            rf,
            len(r.long_closed_trades),
            r.long_win_rate,
            r.long_avg_r,
            len(r.short_closed_trades),
            r.short_win_rate,
            r.short_avg_r,
            run_at_ms,
        ],
    )
    return combo_id


def list_combo_runs(
    conn: duckdb.DuckDBPyConnection,
) -> "pd.DataFrame":
    """Return all backtest_combos rows sorted newest-first."""

    return conn.execute("SELECT * FROM backtest_combos ORDER BY run_at_ms DESC").df()


def get_combo_lookup(
    conn: duckdb.DuckDBPyConnection,
) -> "dict[tuple[str, str, frozenset[str]], dict[str, Any]]":
    """Build a lookup dict for live co-fire detection from backtest_combos.

    Keyed by (symbol, timeframe, frozenset({strategy_a, strategy_b})) → the row
    with the highest avg_r for that pair (across all day_filter values).

    Uses list_combo_runs which already deduplicates to the latest run per combo_id.
    Returns an empty dict when no combo runs have been saved yet.
    """
    df = list_combo_runs(conn)
    if df.empty:
        return {}
    lookup: dict[tuple[str, str, frozenset[str]], dict[str, Any]] = {}
    for row in df.to_dict("records"):
        key: tuple[str, str, frozenset[str]] = (
            str(row["symbol"]),
            str(row["timeframe"]),
            frozenset({str(row["strategy_a"]), str(row["strategy_b"])}),
        )
        avg_r = float(row["avg_r"])
        if key not in lookup or avg_r > lookup[key]["avg_r"]:
            lookup[key] = {
                "avg_r": avg_r,
                "win_rate": float(row["win_rate"]),
                "closed_trades": int(row["closed_trades"]),
                "strategy_a": str(row["strategy_a"]),
                "strategy_b": str(row["strategy_b"]),
            }
    return lookup


def upsert_cross_tf_combo_run(
    conn: duckdb.DuckDBPyConnection,
    combo: "Any",
    days: int,
    data_start_ms: int,
    data_end_ms: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
) -> str:
    """Persist a CrossTfComboBacktestResult and return the combo_id.

    combo_id is stable across re-runs: same (symbol, tf_htf, tf_ltf,
    strategy_htf, strategy_ltf, window_hours, day_filter) always overwrites
    the previous result via INSERT OR REPLACE.
    """
    from analytics.backtest_lib import CrossTfComboBacktestResult

    assert isinstance(combo, CrossTfComboBacktestResult)
    r = combo.result

    run_at_ms = int(time.time() * 1000)
    wh = combo.window_hours
    wh_str = f"{wh:.1f}".rstrip("0").rstrip(".")
    combo_id = (
        f"{r.symbol}|{combo.tf_htf}+{combo.tf_ltf}"
        f"|{combo.strategy_htf}+{combo.strategy_ltf}"
        f"|w{wh_str}h|{day_filter}"
    )

    rf = r.recovery_factor if r.max_drawdown_r > 0 else None

    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_cross_tf_combos (
            combo_id, symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf,
            window_hours, data_start_ms, data_end_ms, days, sl_pct, tp_r,
            fee_pct, day_filter, total_signals, closed_trades, win_count,
            win_rate, avg_r, total_r, max_drawdown_r, recovery_factor,
            long_closed_trades, long_win_rate, long_avg_r,
            short_closed_trades, short_win_rate, short_avg_r,
            run_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            combo_id,
            r.symbol,
            combo.tf_htf,
            combo.tf_ltf,
            combo.strategy_htf,
            combo.strategy_ltf,
            combo.window_hours,
            data_start_ms,
            data_end_ms,
            days,
            sl_pct,
            tp_r,
            fee_pct,
            day_filter,
            len(r.trades),
            len(r.closed_trades),
            r.win_count,
            r.win_rate,
            r.avg_r,
            r.total_r,
            r.max_drawdown_r,
            rf,
            len(r.long_closed_trades),
            r.long_win_rate,
            r.long_avg_r,
            len(r.short_closed_trades),
            r.short_win_rate,
            r.short_avg_r,
            run_at_ms,
        ],
    )
    return combo_id


def list_cross_tf_combo_runs(
    conn: duckdb.DuckDBPyConnection,
) -> "pd.DataFrame":
    """Return all backtest_cross_tf_combos rows sorted newest-first."""
    return conn.execute(
        "SELECT * FROM backtest_cross_tf_combos ORDER BY run_at_ms DESC"
    ).df()


def get_cross_tf_combo_lookup(
    conn: duckdb.DuckDBPyConnection,
) -> "dict[tuple[str, str, str, str, str], dict[str, Any]]":
    """Build a lookup dict for live cross-TF co-fire detection.

    Keyed by (symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf) → the row
    with the highest avg_r for that ordered pair (across all day_filter values).
    The key is ordered (not frozenset) because HTF/LTF roles are distinct.

    Returns an empty dict when no cross-TF combo runs have been saved yet.
    """
    df = list_cross_tf_combo_runs(conn)
    if df.empty:
        return {}
    lookup: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in df.to_dict("records"):
        key: tuple[str, str, str, str, str] = (
            str(row["symbol"]),
            str(row["tf_htf"]),
            str(row["tf_ltf"]),
            str(row["strategy_htf"]),
            str(row["strategy_ltf"]),
        )
        avg_r = float(row["avg_r"])
        if key not in lookup or avg_r > lookup[key]["avg_r"]:
            lookup[key] = {
                "avg_r": avg_r,
                "win_rate": float(row["win_rate"]),
                "closed_trades": int(row["closed_trades"]),
                "strategy_htf": str(row["strategy_htf"]),
                "strategy_ltf": str(row["strategy_ltf"]),
                "tf_htf": str(row["tf_htf"]),
                "tf_ltf": str(row["tf_ltf"]),
                "window_hours": float(row["window_hours"]),
            }
    return lookup
