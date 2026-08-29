"""backtest_runs + backtest_trades upserts/queries."""

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any

import duckdb
import pandas as pd

from analytics.backtest.live_parity_config import LiveParityConfig, live_parity_key


def _insert_sql(table: str, row: dict[str, Any], view: str) -> str:
    """Build an ``INSERT OR REPLACE`` that names its target columns.

    ⚠ **Never insert into these tables positionally.** ``INSERT ... SELECT``
    maps the select list onto the table's columns BY POSITION, and the column
    order of ``backtest_runs`` differs between a live database and a fresh one:
    ``long_total_r`` / ``short_total_r`` / ``volume_suppress`` are created inline
    by ``init_schema`` while ``adr_suppress_threshold`` / ``recovery_factor``
    arrive through the ALTER migration, so they land in creation order on a
    fresh DB and in migration order on a database that predates the CREATE.

    Measured 2026-08-25: on a fresh DB an ``adr_suppress_threshold`` of 0.8 was
    read back out of ``long_total_r``, five columns wide — production was
    correct and every reclone, in-memory test DB and ``make preflight`` clone
    was silently wrong, which is why no gate ever went red on it. Naming the
    columns makes the order irrelevant, and deriving both lists from one dict
    means adding a column cannot reintroduce the skew.
    """
    cols = ", ".join(row)
    return f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM {view}"


def _detector_params_suffix(params: Mapping[str, float | int]) -> str:
    """Format sorted ``k=v`` pairs for the run_id key.

    Rejects ``bool`` values — ``bool`` is an ``int`` subclass, so an unguarded
    numeric check would silently accept ``True``/``False`` as ``1``/``0`` and
    hash/store them with no trace they were ever boolean. Every detector
    keyword this repo has (``lookback``, ``tolerance_pct``, ``swing_n``) is
    genuinely numeric; a caller passing a flag here has the wrong axis.
    """
    for k, v in params.items():
        if isinstance(v, bool):
            raise TypeError(f"detector_params[{k!r}] must be int or float, not bool")
    return ",".join(f"{k}={params[k]}" for k in sorted(params))


def _backtest_run_id(
    symbol: str,
    timeframe: str,
    strategy: str,
    days: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
    smt_trend_filter: int,
    secondary_symbol: str | None,
    adr_suppress_threshold: float | None = None,
    volume_suppress: bool | None = None,
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    tp_r_long: float | None = None,
    tp_r_short: float | None = None,
    volume_suppress_long: bool | None = None,
    volume_suppress_short: bool | None = None,
    adr_exempt: bool = False,
    atr_sl_floor: bool = False,
    live_parity: str | None = None,
    detector_params: Mapping[str, float | int] | None = None,
    writer: str = "sweep",
) -> str:
    """Return a deterministic 16-char hex ID for a backtest param combination.

    Optional suffixes are appended only when set so existing run_ids are
    unchanged (None = flag not applied, same hash as before these columns).

    ``writer`` namespaces the ID by **who wrote the row**, and it is load-bearing
    rather than cosmetic. This hash covers only backtest *parameters*, and
    :func:`upsert_backtest_run` issues ``INSERT OR REPLACE`` — so when the live
    signal-watch gate resolved the same ``sl_pct``/``tp_r`` as a swept cell (i.e.
    the *chosen* cell, the one that matters), the 15-minute daemon silently
    replaced the swept row. Measured 2026-08-12: **415 rows overwritten, 331 whose
    stored aggregate disagreed with their own trades** (`smt_divergence/15m` read 8
    trades against 1070 stored), and **53% of rated `tue_thu` cells** owned by the
    live gate instead of the deliberate sweep.

    ``"sweep"`` is the **unsuffixed default on purpose**: sweep rows are the
    validated evidence, and ``backtest_cache`` keys derive from ``run_id``
    (``analytics/signal/_common.py``). Keeping the default hash byte-identical
    means historical sweep rows stay addressable and the live cache is not
    invalidated — only the other writers move to their own namespaces.

    ⚠ **A namespacing fix closes the axis it was written for and nothing else.**
    ``writer`` fixed WHO wrote the row and left every engine axis unnamespaced:
    until ST86 ``upsert_backtest_run`` forwarded 11 of these arguments and knew
    nothing of ``live_parity``, so a ``tp_r`` retune or an ``atr_sl_multiplier``
    sweep landing in the TOML silently overwrote the rows measured under the old
    value. Every argument here changes what the engine produces, so **any new
    engine knob must be added to this key in the same PR that adds it.**

    ``detector_params`` (ST104 P1) namespaces a detector-level retune (e.g.
    eqh_eql's ``lookback``/``tolerance_pct``/``swing_n``) so it cannot collide
    with the default-param row for the same symbol/tf/strategy/day_filter.
    None or empty leaves the key — and therefore every historical run_id —
    byte-identical, matching every other optional suffix here.
    """
    key = f"{symbol}|{timeframe}|{strategy}|{days}|{sl_pct}|{tp_r}|{fee_pct}|{day_filter}|{smt_trend_filter}|{secondary_symbol}"
    if adr_suppress_threshold is not None:
        key += f"|adr:{adr_suppress_threshold}"
    if volume_suppress:
        key += "|vol_suppress"
    if min_sl_pct > 0.0:
        key += f"|min_sl:{min_sl_pct}"
    if atr_sl_multiplier is not None:
        key += f"|atr_sl:{atr_sl_multiplier}"
    if tp_r_long is not None:
        key += f"|tp_long:{tp_r_long}"
    if tp_r_short is not None:
        key += f"|tp_short:{tp_r_short}"
    if volume_suppress_long:
        key += "|vol_sup_l"
    if volume_suppress_short:
        key += "|vol_sup_s"
    if adr_exempt:
        key += "|adr_exempt"
    if atr_sl_floor:
        key += "|atr_floor"
    if live_parity:
        key += f"|lp:{live_parity}"
    if detector_params:
        key += f"|dp:{_detector_params_suffix(detector_params)}"
    if writer != "sweep":
        key += f"|writer:{writer}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def upsert_backtest_run(
    conn: duckdb.DuckDBPyConnection,
    result: Any,
    days: int,
    data_start_ms: int,
    data_end_ms: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
    smt_trend_filter: int,
    secondary_symbol: str | None = None,
    sweep_id: str | None = None,
    adr_suppress_threshold: float | None = None,
    volume_suppress: bool | None = None,
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    tp_r_long: float | None = None,
    tp_r_short: float | None = None,
    volume_suppress_long: bool | None = None,
    volume_suppress_short: bool | None = None,
    adr_exempt: bool = False,
    atr_sl_floor: bool = False,
    live_parity: LiveParityConfig | None = None,
    detector_params: Mapping[str, float | int] | None = None,
    writer: str = "sweep",
) -> str:
    """Insert or replace a backtest aggregate result row.

    result must be a BacktestResult instance.
    Returns the run_id so the caller can link backtest_trades rows.

    ``writer`` identifies the caller so two writers cannot collide on one row —
    see :func:`_backtest_run_id`. Pass ``"live"`` from the signal-watch gate,
    ``"single"`` from a single-combo run and ``"ui"`` from the web API; the sweep
    keeps the default.

    Every engine axis is forwarded to the ID (ST86). ``live_parity`` and
    ``adr_exempt`` are additionally STORED, because namespacing alone leaves a
    row unable to say what it ran under: the parity question is exactly what a
    stored ``tp_r``'s provenance turns on, and an ADR-exempt run is otherwise
    indistinguishable from an unthresholded one, both landing at
    ``adr_suppress_threshold IS NULL``.

    ``detector_params`` (ST104 P1) is likewise both namespaced (via
    ``_backtest_run_id``) and STORED, as JSON text with sorted keys, so a
    retuned row can say what it ran under rather than only being addressable
    by a different hash. The read path excludes non-null ``detector_params``
    from rated/production selection (`analytics.recalibrate_lib._build_run_filter`)
    so a study can never silently mix into the live ratings.
    """
    live_parity_str = live_parity_key(live_parity)
    run_id = _backtest_run_id(
        result.symbol,
        result.timeframe,
        result.strategy,
        days,
        sl_pct,
        tp_r,
        fee_pct,
        day_filter,
        smt_trend_filter,
        secondary_symbol,
        adr_suppress_threshold,
        volume_suppress,
        min_sl_pct,
        atr_sl_multiplier,
        tp_r_long,
        tp_r_short,
        volume_suppress_long,
        volume_suppress_short,
        adr_exempt,
        atr_sl_floor,
        live_parity_str,
        detector_params,
        writer=writer,
    )
    row: dict[str, Any] = {
        "run_id": run_id,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "strategy": result.strategy,
        "data_start_ms": data_start_ms,
        "data_end_ms": data_end_ms,
        "days": days,
        "sl_pct": sl_pct,
        "tp_r": tp_r,
        "fee_pct": fee_pct,
        "day_filter": day_filter,
        "smt_trend_filter": smt_trend_filter,
        "secondary_symbol": secondary_symbol,
        "total_signals": len(result.trades),
        "closed_trades": len(result.closed_trades),
        "win_count": result.win_count,
        "loss_count": result.loss_count,
        "win_rate": result.win_rate,
        "avg_r": result.avg_r,
        "total_r": result.total_r,
        "max_drawdown_r": result.max_drawdown_r,
        "run_at_ms": int(time.time() * 1000),
        "sweep_id": sweep_id,
        "adr_suppress_threshold": adr_suppress_threshold,
        "long_closed_trades": len(result.long_closed_trades),
        "long_win_count": result.long_win_count,
        "long_win_rate": result.long_win_rate,
        "long_avg_r": result.long_avg_r,
        "short_closed_trades": len(result.short_closed_trades),
        "short_win_count": result.short_win_count,
        "short_win_rate": result.short_win_rate,
        "short_avg_r": result.short_avg_r,
        "long_total_r": result.long_total_r,
        "short_total_r": result.short_total_r,
        "recovery_factor": result.recovery_factor,
        "volume_suppress": volume_suppress,
        "live_parity": live_parity_str,
        "adr_exempt": adr_exempt,
        "detector_params": (
            json.dumps({k: detector_params[k] for k in sorted(detector_params)})
            if detector_params
            else None
        ),
    }
    df = pd.DataFrame([row])
    conn.register("_bt_run_upsert_df", df)
    try:
        conn.execute(_insert_sql("backtest_runs", row, "_bt_run_upsert_df"))
    finally:
        conn.unregister("_bt_run_upsert_df")
    return run_id


def upsert_backtest_trades(
    conn: duckdb.DuckDBPyConnection,
    result: Any,
    run_id: str,
) -> None:
    """Insert or replace per-trade rows for a backtest run.

    result must be a BacktestResult instance.
    Skips if result.trades is empty.
    """
    if not result.trades:
        return
    rows = [
        {
            "trade_id": f"{run_id}:{t.signal_time}",
            "run_id": run_id,
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "strategy": result.strategy,
            "direction": t.direction,
            "signal_time": t.signal_time,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "sl_price": t.sl_price,
            "tp_price": t.tp_price,
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "outcome": t.outcome,
            "pnl_r": t.pnl_r,
            "low_volume": bool(getattr(t, "low_volume", False)),
            "volume_spike": bool(getattr(t, "volume_spike", False)),
        }
        for t in result.trades
    ]
    df = pd.DataFrame(rows)
    conn.register("_bt_trades_upsert_df", df)
    try:
        conn.execute(_insert_sql("backtest_trades", rows[0], "_bt_trades_upsert_df"))
    finally:
        conn.unregister("_bt_trades_upsert_df")


def list_backtest_runs(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return the latest backtest_run per (symbol, timeframe, strategy, day_filter), newest first.

    Attaches calibrated star ratings from confidence_ratings by matching on
    (strategy, timeframe, day_filter) so each row shows the correct per-config stars.

    ``live_parity`` and ``adr_exempt`` join the partition for the same reason
    ``adr_suppress_threshold`` is already in it (ST86): once those runs stop
    colliding on one ``run_id`` they coexist, and a partition blind to an axis
    picks between two different books on recency alone. ``detector_params``
    (ST104 P1) joins it for the identical reason: once namespaced, a retuned
    study row and the default-param row for the same symbol/tf/strategy/
    day_filter no longer collide on ``run_id`` — without this the newer of
    the two would silently hide the other from this listing on recency
    alone, exactly the failure this docstring already warns against.

    ⚠ ``adr_exempt`` is COALESCEd because the migration creates a value boundary
    the partition would otherwise read as a real axis: every row written before
    ST86 has it NULL (the column was added empty) and every row after stores
    FALSE, and a window partition treats those as different groups — so the raw
    column returns each cell once per era instead of once. Uniform NULL today is
    why no gate catches it; it appears only as post-migration rows accrue.
    ``live_parity`` and ``detector_params`` need no such tolerance: a legacy
    row and a non-parity/non-retuned row are both NULL and already share a
    partition, and a parity or retuned run is a different book that MUST
    keep its own.
    """
    return conn.execute(
        "SELECT b.run_id, b.symbol, b.timeframe, b.strategy, b.days, b.sl_pct, b.tp_r, "
        "b.fee_pct, b.day_filter, b.closed_trades, b.win_count, b.loss_count, b.win_rate, "
        "b.avg_r, b.total_r, b.max_drawdown_r, b.recovery_factor, b.sweep_id, b.run_at_ms, "
        "b.long_closed_trades, b.long_win_count, b.long_win_rate, b.long_avg_r, b.long_total_r, "
        "b.short_closed_trades, b.short_win_count, b.short_win_rate, b.short_avg_r, b.short_total_r, "
        "b.adr_suppress_threshold, b.live_parity, b.adr_exempt, b.detector_params, "
        "cr.stars, cr_long.long_stars, cr_short.short_stars "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY symbol, timeframe, strategy, day_filter, "
        "                 adr_suppress_threshold, live_parity, detector_params, "
        "                 COALESCE(adr_exempt, FALSE) "
        "    ORDER BY run_at_ms DESC"
        "  ) AS rn FROM backtest_runs"
        ") b "
        "LEFT JOIN ("
        "  SELECT strategy, tf, day_filter, MAX(stars) AS stars "
        "  FROM confidence_ratings "
        "  WHERE direction = 'combined' AND day_filter IS NOT NULL "
        "  GROUP BY strategy, tf, day_filter"
        ") cr ON cr.strategy = b.strategy AND cr.tf = b.timeframe AND cr.day_filter = b.day_filter "
        "LEFT JOIN ("
        "  SELECT strategy, tf, day_filter, MAX(stars) AS long_stars "
        "  FROM confidence_ratings "
        "  WHERE direction = 'long' AND day_filter IS NOT NULL "
        "  GROUP BY strategy, tf, day_filter"
        ") cr_long ON cr_long.strategy = b.strategy AND cr_long.tf = b.timeframe "
        "  AND cr_long.day_filter = b.day_filter "
        "LEFT JOIN ("
        "  SELECT strategy, tf, day_filter, MAX(stars) AS short_stars "
        "  FROM confidence_ratings "
        "  WHERE direction = 'short' AND day_filter IS NOT NULL "
        "  GROUP BY strategy, tf, day_filter"
        ") cr_short ON cr_short.strategy = b.strategy AND cr_short.tf = b.timeframe "
        "  AND cr_short.day_filter = b.day_filter "
        "WHERE b.rn = 1 "
        "ORDER BY b.run_at_ms DESC"
    ).df()


def get_win_rate_by_strategy(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return win rate aggregated per strategy across all saved backtest runs.

    Only includes combos with at least 20 closed trades (same gate as sweep table).
    Ordered by win_rate_pct descending.
    """
    return conn.execute("""
        SELECT
            strategy,
            SUM(closed_trades)                                                  AS total_closed,
            SUM(win_count)                                                      AS total_wins,
            ROUND(SUM(win_count) * 100.0 / NULLIF(SUM(closed_trades), 0), 1)   AS win_rate_pct,
            ROUND(AVG(avg_r), 3)                                                AS mean_avg_r,
            COUNT(*)                                                            AS combos_run
        FROM backtest_runs
        WHERE closed_trades >= 20
          AND adr_suppress_threshold IS NULL
          AND live_parity IS NULL
          AND detector_params IS NULL
        GROUP BY strategy
        ORDER BY win_rate_pct DESC
    """).df()
