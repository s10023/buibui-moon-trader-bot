"""The one read path into ``backtest_trades`` (#985).

Every reader of the table goes through :func:`load_backtest_trades`, so run
selection and cross-run dedup have one implementation. Before this there were
three copies of one dedup and five different selection rules, and they had
already drifted (#949).

Two ways to choose runs, and a caller names exactly one:

* ``run_ids=`` — the caller chose the runs. Rated readers take them from
  ``recalibrate_lib.select_rated_run_ids``; a study reading its own sweep passes
  that sweep's ids. The loader returns exactly those runs' rows.
* pooled (no ``run_ids``) — every admissible run. A detector with a
  ``[[detector_floor]]`` in ``config/eras.toml`` is admitted only from runs saved
  at or after its floor whose trade layer is clean (their closed rows equal
  their own ``closed_trades``). The clean-run half is not optional: until #987
  the writer never deleted a run's old rows, so a run re-saved after a
  causality fix still carried its pre-fix signals. Measured on the 2026-10-09
  snapshot, the floor alone admitted 4,233 leaked ``bos`` rows. Detectors with no
  floor are admitted from every run.

Dedup is on by default: one row per key, from the most recently saved run, with
``run_id`` breaking a tie. The old rule kept the lexicographically last
``run_id``, which is a hash, so the surviving row was effectively arbitrary.

⚠ Dedup runs in pandas. DuckDB window functions have segfaulted on this table.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import duckdb
import pandas as pd

from analytics.eras import DetectorFloor, detector_floors

#: The cross-run identity of a trade: the same entry seen by several saved runs.
ENTRY_KEY: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "strategy",
    "direction",
    "entry_time",
)
#: The same identity keyed on the signal bar, for readers that work in signal time.
SIGNAL_KEY: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "strategy",
    "direction",
    "signal_time",
)

TRADE_COLUMNS: frozenset[str] = frozenset(
    {
        "trade_id",
        "run_id",
        "symbol",
        "timeframe",
        "strategy",
        "direction",
        "signal_time",
        "entry_time",
        "entry_price",
        "sl_price",
        "tp_price",
        "exit_time",
        "exit_price",
        "outcome",
        "pnl_r",
        "low_volume",
        "volume_spike",
    }
)

#: ``backtest_runs`` columns a reader may ask for alongside its trades.
RUN_COLUMNS: frozenset[str] = frozenset(
    {
        "run_at_ms",
        "sweep_id",
        "day_filter",
        "sl_pct",
        "tp_r",
        "fee_pct",
        "detector_params",
    }
)


def _in_clause(column: str, values: Sequence[str]) -> tuple[str, list[str]]:
    return f"{column} IN ({', '.join('?' * len(values))})", list(values)


def _closed_count_join(
    floors: Mapping[str, DetectorFloor], run_id_col: str
) -> tuple[str, list[object]]:
    """``LEFT JOIN`` exposing ``c.n_closed``, each floored run's stored closed rows."""
    in_sql, in_params = _in_clause("strategy", sorted(floors))
    join = (
        "LEFT JOIN (SELECT run_id, COUNT(*) AS n_closed FROM backtest_trades "
        f"WHERE outcome <> 'open' AND pnl_r IS NOT NULL AND {in_sql} "
        f"GROUP BY run_id) c ON c.run_id = {run_id_col}"
    )
    return join, list(in_params)


def _floor_clause(
    floors: Mapping[str, DetectorFloor], strategy_col: str = "t.strategy"
) -> tuple[str, list[object]]:
    """SQL admitting a trade only from a floored detector's clean, post-floor run."""
    if not floors:
        return "", []
    names = sorted(floors)
    cases = " ".join("WHEN ? THEN ?" for _ in names)
    params: list[object] = []
    for name in names:
        params += [name, floors[name].since_ms]
    in_sql, in_params = _in_clause(strategy_col, names)
    clause = (
        f"(NOT {in_sql} OR ("
        f"r.run_at_ms >= CASE {strategy_col} {cases} END "
        "AND r.closed_trades = COALESCE(c.n_closed, 0)))"
    )
    return clause, [*in_params, *params]


def floored_run_admission(
    floors: Mapping[str, DetectorFloor],
) -> tuple[tuple[str, list[object]], tuple[str, list[object]]]:
    """``((join_sql, params), (where_sql, params))`` flooring ``backtest_runs r``.

    The same admission the pooled read applies, for a caller that chooses runs
    rather than trades (``recalibrate_lib.select_rated_run_ids``, #993): a
    floored detector's run counts only if it was saved at or after the floor and
    its trade layer is clean.
    """
    if not floors:
        return ("", []), ("TRUE", [])
    return (
        _closed_count_join(floors, "r.run_id"),
        _floor_clause(floors, "r.strategy"),
    )


def load_backtest_trades(
    conn: duckdb.DuckDBPyConnection,
    *,
    columns: Sequence[str],
    run_ids: Sequence[str] | None = None,
    floors: Mapping[str, DetectorFloor] | None = None,
    strategies: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    closed_only: bool = False,
    not_null: Sequence[str] = ("pnl_r",),
    run_columns: Sequence[str] = (),
    dedup_on: Sequence[str] | None = ENTRY_KEY,
) -> pd.DataFrame:
    """``backtest_trades`` rows, selected and deduped by the one shared rule.

    ``columns`` are trade columns and ``run_columns`` are joined from
    ``backtest_runs``; the frame carries exactly those, in that order. Pass
    ``run_ids`` to read chosen runs, or leave it ``None`` for the pooled
    selection, whose floors default to ``config/eras.toml``. ``closed_only``
    drops ``outcome = 'open'``; ``not_null`` drops rows missing any named
    trade column. ``dedup_on=None`` keeps every row, which a reader of one run
    per cell needs so two signals entering on one bar both count.
    """
    unknown = [c for c in (*columns, *not_null) if c not in TRADE_COLUMNS]
    unknown += [c for c in run_columns if c not in RUN_COLUMNS]
    if dedup_on is not None:
        unknown += [c for c in dedup_on if c not in TRADE_COLUMNS]
    if unknown:
        raise ValueError(f"unknown backtest column(s): {sorted(set(unknown))}")
    if run_ids is not None and floors is not None:
        raise ValueError(
            "pass run_ids or floors, not both: chosen runs are not floored"
        )

    out_cols = list(dict.fromkeys(columns))
    run_cols = list(dict.fromkeys(run_columns))
    if not out_cols and not run_cols:
        raise ValueError("ask for at least one column")
    fetch = list(dict.fromkeys([*out_cols, *(dedup_on or ())]))
    select = [f"t.{c}" for c in fetch] + [f"r.{c} AS {c}" for c in run_cols]
    if dedup_on is not None:
        select += ["r.run_at_ms AS _run_at_ms", "t.run_id AS _run_id"]

    where = ["TRUE"]
    params: list[object] = []
    join_count = ""
    if run_ids is not None:
        if not run_ids:
            return pd.DataFrame(columns=[*out_cols, *run_cols])
        sql, p = _in_clause("t.run_id", run_ids)
        where.append(sql)
        params += p
    else:
        active = detector_floors() if floors is None else floors
        if active:
            join_count, join_params = _closed_count_join(active, "t.run_id")
            params += join_params
        clause, floor_params = _floor_clause(active)
        if clause:
            where.append(clause)
            params += floor_params
    for column, values in (
        ("strategy", strategies),
        ("symbol", symbols),
        ("timeframe", timeframes),
    ):
        if values is not None:
            if not values:
                return pd.DataFrame(columns=[*out_cols, *run_cols])
            sql, p = _in_clause(f"t.{column}", values)
            where.append(sql)
            params += p
    if closed_only:
        where.append("t.outcome <> 'open'")
    where += [f"t.{c} IS NOT NULL" for c in not_null]

    sql = (
        f"SELECT {', '.join(select)} FROM backtest_trades t "
        f"LEFT JOIN backtest_runs r ON r.run_id = t.run_id {join_count} "
        f"WHERE {' AND '.join(where)}"
    )
    df = conn.execute(sql, params).df()
    if dedup_on is not None:
        df = df.sort_values(
            ["_run_at_ms", "_run_id"], kind="stable", na_position="first"
        ).drop_duplicates(subset=list(dedup_on), keep="last")
        df = df.sort_index(kind="stable")
    return df[[*out_cols, *run_cols]].reset_index(drop=True)
