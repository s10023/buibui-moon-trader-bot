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

from analytics.research_guards import GATE_DSR, deflated_sharpe_ratio
from analytics.signal_config import SignalWatchConfig, declared_cells
from analytics.store.backtest_trades import load_backtest_trades

# A 5-star cell whose Deflated Sharpe falls below this is overfit-suspect (spec
# section 3). Derived from the published gate rather than restated: this line held its
# own 0.95 under a comment saying it "matches the sweep commit-gate threshold", which
# is agreement by coincidence — ST134.
DSR_SUSPECT_THRESHOLD = GATE_DSR

# Minimum scoreable trades for a cell to receive a DSR and to join the trial
# family. Below this, a per-trade Sharpe is too noisy to be meaningful — a tiny-n
# low-dispersion cell can produce an extreme Sharpe that, via the (non-robust)
# cross-trial variance, inflates the expected-max-Sharpe benchmark and collapses
# every cell's DSR to ~0. Matches audit_guard.DEFAULT_MIN_N / sweep_guard's floor.
MIN_DSR_TRADES = 30

# Minimum per-trade R dispersion for a cell to receive a DSR and to join the
# trial family. MIN_DSR_TRADES gates COUNT, and count alone is not enough: a cell
# whose trades all resolved at the same R clears it, then earns a Sharpe in the
# hundreds because the denominator is ~0. That Sharpe is not a signal — it says
# every trade hit the same stop. Measured over ST63's 1,766-cell family, 105
# cells (5.9%) sit under this floor and take the family's sr_variance from
# 0.1022 to 9.79e26, twenty-seven orders of magnitude, which makes any DSR over
# the raw family meaningless rather than merely noisy. The floor is insensitive
# between 0.05 and 0.10 (both leave 702 cells), so the threshold is not doing the
# work — excluding the degenerate tail is.
# → docs/audits/2026-08-24-st63-occurrence-dump-power-pricing.md
MIN_DSR_SD = 0.05


def _build_run_filter(
    day_filter: str | None,
    adr_suppress_threshold: float | None,
) -> tuple[str, list[str | float]]:
    """Build the shared backtest_runs WHERE-tail (day_filter + ADR + parity scope).

    Returns ``(sql_tail, params)`` where ``sql_tail`` is appended after
    ``WHERE closed_trades > 0``. Mirrors the exact scoping used by both the
    rating aggregation and the DSR annotation so they always read the same runs.
    """
    sql = ""
    params: list[str | float] = []
    # ST86: a live-parity run is a different book — it answers "how would this
    # cell have scored under the live gates", not "how did it score". Excluding
    # it by default keeps a `--live-parity --save` sweep out of the rating
    # population, where a sweep_id plus a newer run_at_ms would let it outrank
    # the deliberate sweep it was compared against.
    sql += " AND (live_parity IS NULL)"
    # ST104 P1: a detector_params run is a different book too — it answers
    # "how would this cell have scored under a retuned detector", not "how
    # did it score". Excluding it by default keeps a pre-registered retune
    # study (e.g. eqh_eql's lookback/tolerance_pct/swing_n) out of the rating
    # population, the same way a live-parity run already is.
    sql += " AND (detector_params IS NULL)"
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


def select_rated_run_ids(
    conn: duckdb.DuckDBPyConnection,
    day_filter: str | None = None,
    adr_suppress_threshold: float | None = None,
) -> list[str]:
    """Return one ``backtest_runs.run_id`` per (strategy, timeframe, symbol).

    **Sweep first, then recency**: a row carrying a ``sweep_id`` outranks any
    non-sweep row and ``run_at_ms`` only breaks ties within a class. That ordering
    is load-bearing — the live signal-watch gate writes a fresh row every 15
    minutes, so plain recency hands the deliberate sweep's cell to the daemon
    (measured: 53% of rated ``tue_thu`` cells). See ``_backtest_run_id``'s
    ``writer`` argument.

    Exists so the ranking has **one** implementation rather than a copy per
    caller. It previously lived inline in :func:`compute_dsr_ratings`, and the
    weekly decay review's out-of-tree copy had already drifted back to
    recency-only — reading a different run population than the gate it audits.
    Any new consumer must call this, not re-derive it.
    """
    filter_sql, params = _build_run_filter(day_filter, adr_suppress_threshold)
    run_rows = conn.execute(
        f"SELECT run_id, strategy, timeframe, symbol, run_at_ms, sweep_id "
        f"FROM backtest_runs "
        f"WHERE closed_trades > 0{filter_sql}",
        params,
    ).fetchall()

    best: dict[tuple[str, str, str], tuple[int, int, str]] = {}
    for run_id, strategy, tf, symbol, run_at_ms, sweep_id in run_rows:
        key = (str(strategy), str(tf), str(symbol))
        rank = (1 if sweep_id is not None else 0, int(run_at_ms))
        cur = best.get(key)
        if cur is None or rank > (cur[0], cur[1]):
            best[key] = (rank[0], rank[1], str(run_id))
    return [v[2] for v in best.values()]


def sample_run_times(
    conn: duckdb.DuckDBPyConnection,
    day_filter: str | None = None,
    adr_suppress_threshold: float | None = None,
) -> list[int]:
    """`run_at_ms` of every run this scope scores — the era key for a BACKTEST sample.

    ⚠ **Not `entry_time`, and the difference is a category error, not a refinement.**
    A backtest row's `entry_time` is *simulated market time*: a run executed today
    over 2025-09 bars stamps 2025-09 on rows produced by today's code. Every trade in
    one run shares one code version, so the era a backtest row belongs to is fixed by
    **when the run was saved**. Keying on `entry_time` instead silently compares bar
    timestamps against code-change dates — it reported 64% of this sample as
    "pre-dating the first boundary" purely because the OHLCV is older than the repo.

    The live outcome ledger is the opposite case: an alert fired under whatever code
    was live at that moment, so there the fire time IS the era key.
    """
    run_ids = select_rated_run_ids(conn, day_filter, adr_suppress_threshold)
    if not run_ids:
        return []
    placeholders = ",".join("?" * len(run_ids))
    rows = conn.execute(
        f"SELECT run_at_ms FROM backtest_runs "
        f"WHERE run_id IN ({placeholders}) AND run_at_ms IS NOT NULL",
        run_ids,
    ).fetchall()
    return [int(row[0]) for row in rows]


def get_backtest_win_rates(
    conn: duckdb.DuckDBPyConnection,
    day_filter: str | None = None,
    adr_suppress_threshold: float | None = None,
) -> pd.DataFrame:
    """Query backtest_runs grouped by (strategy, tf), return win_rate, avg_r, total_trades.

    Groups across all symbols for each (strategy, timeframe) combination.
    One run per (strategy, timeframe, symbol) is used, ranked **sweep-first, then
    recency**: a row carrying a ``sweep_id`` outranks any non-sweep row, and
    ``run_at_ms`` only breaks ties within a class. Older sweeps are still excluded.
    A cell with no sweep row is rated from whatever rows it has.
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
        f"short_closed_trades, short_win_count, short_avg_r, sweep_id "
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
            "sweep_id",
        ],
    )
    # Keep one run per (strategy, timeframe, symbol): a SWEEP row outranks any
    # non-sweep row, and recency only breaks ties within a class.
    #
    # Recency alone was the bug. The live gate writes every 15 minutes, so its row
    # is essentially always the newest — 53% of rated `tue_thu` cells were sourced
    # from the daemon's short-window backtest rather than the deliberate, validated
    # sweep. Writer identity stops the two COLLIDING; without this they merely
    # coexist and the daemon still wins. A cell with no sweep row is still rated
    # from what it has.
    raw["_is_sweep"] = raw["sweep_id"].notna()
    raw = raw.sort_values(["_is_sweep", "run_at_ms"], ascending=False).drop_duplicates(
        subset=["strategy", "timeframe", "symbol"]
    )
    # Weight each symbol's avg_r by its own trade count before aggregating.
    #
    # An unweighted mean gives a 3-trade symbol the same say as a 300-trade one,
    # and `win_rate_to_stars` has a boundary at exactly 0.0 (avg_r < 0 -> 1 star).
    # `win_rate` below was already trade-weighted (sum of wins / sum of trades);
    # only the R columns were not, so the two halves of the same row disagreed
    # about what a symbol was worth.
    #
    # Measured 2026-08-19 in the three PRODUCTION scopes -- each config's own
    # day_filter + adr_suppress_threshold, i.e. the rows whose stars actually get
    # written -- over 555 cells (combined + long + short): median gap 0.0138R,
    # p90 0.2636R, max 1.2749R, with 39 cells crossing the sign and 60 changing
    # star rating (fvg/1d +0.2397 -> -0.4878, i.e. 3 stars -> 1).
    #
    # Scope is what makes this visible: a scope-free call (no day_filter, which
    # also restricts to adr IS NULL) reads 0 sign flips over 70 cells. Measure
    # this in the production scope or it looks like a rounding change.
    #
    # A row whose avg_r is NULL carries zero weight in BOTH numerator and
    # denominator: it must not drag the mean toward 0.0, and it must not inflate
    # the divisor either.
    for r_col, n_col in (
        ("avg_r", "closed_trades"),
        ("long_avg_r", "long_closed_trades"),
        ("short_avg_r", "short_closed_trades"),
    ):
        n = pd.to_numeric(raw[n_col], errors="coerce").fillna(0.0)
        r = pd.to_numeric(raw[r_col], errors="coerce")
        raw[f"_{r_col}_rw"] = (r * n).fillna(0.0)
        raw[f"_{r_col}_rn"] = n.where(r.notna(), 0.0)

    # Aggregate across symbols
    agg = (
        raw.groupby(["strategy", "timeframe"], sort=True)
        .agg(
            total_trades=("closed_trades", "sum"),
            win_count_sum=("win_count", "sum"),
            avg_r_rw=("_avg_r_rw", "sum"),
            avg_r_rn=("_avg_r_rn", "sum"),
            long_total_trades=("long_closed_trades", "sum"),
            long_win_count_sum=("long_win_count", "sum"),
            long_avg_r_rw=("_long_avg_r_rw", "sum"),
            long_avg_r_rn=("_long_avg_r_rn", "sum"),
            short_total_trades=("short_closed_trades", "sum"),
            short_win_count_sum=("short_win_count", "sum"),
            short_avg_r_rw=("_short_avg_r_rw", "sum"),
            short_avg_r_rn=("_short_avg_r_rn", "sum"),
        )
        .reset_index()
    )
    # Zero weight means no data at all -> NaN, matching the old all-NaN mean.
    for r_col in ("avg_r", "long_avg_r", "short_avg_r"):
        agg[r_col] = agg[f"{r_col}_rw"] / agg[f"{r_col}_rn"].replace(0.0, float("nan"))
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


def _sharpe(returns: list[float], *, min_sd: float = MIN_DSR_SD) -> float | None:
    """Per-trade Sharpe ``mean / stdev(ddof=1)``.

    None when undefined (<2 trades) or when dispersion is below ``min_sd`` — such
    a cell cannot be deflated and is annotated NULL. ``min_sd=0.0`` still rejects
    exactly-zero dispersion, because that Sharpe does not exist rather than being
    untrustworthy; pass it to reproduce the pre-ST66 result.
    """
    if len(returns) < 2:
        return None
    sd = statistics.stdev(returns)
    if sd == 0.0 or sd < min_sd:
        return None
    return statistics.fmean(returns) / sd


def _scope_dsr(
    pools: dict[tuple[str, str], list[float]],
    min_trades: int,
    min_sd: float = MIN_DSR_SD,
) -> dict[tuple[str, str], float | None]:
    """Deflated Sharpe per cell, deflated against the family of all cells' Sharpes.

    The trial family (N + variance) is the per-recalibrate-pass cell set for one
    direction scope — an **N-FLOOR** on the true search effort (spec §5): the real
    N spans every sweep that ever produced these runs, so this DSR is *optimistic*.
    A cell joins the family and receives a DSR only if it clears BOTH floors:
    ``>= min_trades`` scoreable trades and ``>= min_sd`` dispersion. Cells failing
    either are annotated None so their noisy or degenerate Sharpe cannot poison the
    deflation benchmark (see MIN_DSR_TRADES and MIN_DSR_SD — count and dispersion
    are separate failure modes, and the count floor does not imply the other).
    """
    sharpes = {
        key: (_sharpe(rets, min_sd=min_sd) if len(rets) >= min_trades else None)
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
    min_sd: float = MIN_DSR_SD,
) -> dict[str, dict[str, dict[str, float | None]]]:
    """Return ``{strategy: {tf: {"combined"|"long"|"short": dsr}}}`` from per-trade R.

    Pools ``backtest_trades.pnl_r`` over the same latest-run-per-(strategy, tf, symbol)
    set the star ratings use (identical day_filter / ADR scoping), computes each cell's
    Sharpe, and deflates it against the per-pass cell family (see :func:`_scope_dsr`).
    A high-star / low-DSR cell is overfit-suspect. Cells/directions with fewer than
    ``min_trades`` scoreable trades, or under ``min_sd`` dispersion, are annotated
    ``None`` (too noisy or too degenerate to deflate reliably, and excluded from the
    family); ``{}`` when there are no runs or trades.
    """
    run_ids = select_rated_run_ids(conn, day_filter, adr_suppress_threshold)
    if not run_ids:
        return {}

    trades = load_backtest_trades(
        conn,
        columns=("strategy", "timeframe", "direction", "pnl_r"),
        run_ids=run_ids,
        closed_only=True,
        dedup_on=None,
    )
    trade_rows = list(trades.itertuples(index=False, name=None))
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

    dsr_combined = _scope_dsr(combined, min_trades, min_sd)
    dsr_long = _scope_dsr(longs, min_trades, min_sd)
    dsr_short = _scope_dsr(shorts, min_trades, min_sd)

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
    likely overfit despite a high star rating — and an "Unscoreable" line listing
    ★≥4 cells whose DSR is None (under MIN_DSR_TRADES scoreable trades, so no
    deflation was possible). The second line exists because omitting those cells
    made an unrun check look like a passed one: the 2026-08-11 decay review found
    42 of 66 rated cells — 21 of the 30 5★ — silently absent from a warning
    written for exactly them. Absence from "Suspect" is not a clean bill.
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
        unscoreable: list[str] = []
        for strat in sorted(new_ratings):
            for tf in sorted(new_ratings[strat]):
                stars = new_ratings[strat][tf]
                if stars < 4:
                    continue
                dsr = dsr_ratings.get(strat, {}).get(tf, {}).get("combined")
                if dsr is None:
                    unscoreable.append(f"{strat}/{tf}")
                elif dsr < DSR_SUSPECT_THRESHOLD:
                    suspect.append(f"{strat}/{tf} (DSR {dsr:.2f})")
        if suspect:
            lines.append(
                f"\n  ⚠ Suspect (★≥4 but DSR<{DSR_SUSPECT_THRESHOLD:.2f}, likely "
                f"overfit): {', '.join(suspect)}"
            )
        if unscoreable:
            lines.append(
                f"\n  ⚠ Unscoreable (★≥4 but DSR undefined — under {MIN_DSR_TRADES} "
                f"scoreable trades, so the overfit check above never ran on these "
                f"{len(unscoreable)} cell(s); absent from Suspect is NOT clean): "
                f"{', '.join(unscoreable)}"
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


# Share of one config's rating rows that ``prune_undeclared_ratings`` may delete
# before it refuses. **This is a tripwire on the resolver, not a policy knob.**
# The failure mode worth catching is a declaration resolver that under-reports
# what a config declares — the 2026-08-12 measurement read ``strategy_timeframes``
# with no fallback to ``cfg.timeframes`` and inflated the orphan count 117 → 194.
# Such a bug always presents as *mass* deletion, so a share ceiling converts the
# one catastrophic-and-silent outcome into a loud one.
DEFAULT_PRUNE_MAX_SHARE = 0.5

# Below this many rating rows the share ceiling is not applied at all. The
# ceiling is *evidence about a resolver*, and a 1-of-1 or 3-of-4 table carries
# none — it would fire on every small or freshly-seeded config while catching no
# real bug. A production config carries 130-180 rows, so this never binds there.
DEFAULT_PRUNE_MIN_ROWS = 20


class PruneThresholdExceeded(RuntimeError):
    """Raised when an undeclared-rating prune would remove an implausible share.

    Carries the counts so the caller can print them; nothing has been deleted
    when this is raised.
    """

    def __init__(
        self, config_name: str, n_undeclared: int, n_total: int, max_share: float
    ) -> None:
        self.config_name = config_name
        self.n_undeclared = n_undeclared
        self.n_total = n_total
        self.max_share = max_share
        super().__init__(
            f"refusing to prune {n_undeclared} of {n_total} rating row(s) for "
            f"'{config_name}' ({n_undeclared / n_total:.0%} > "
            f"{max_share:.0%} ceiling). Nothing was deleted. Either the config "
            f"genuinely shed this many cells — re-run with a raised ceiling "
            f"after eyeballing `tools/dead_surface_check.py` — or the "
            f"declaration resolver is under-reporting, which is the bug this "
            f"guard exists to catch."
        )


def rating_direction_key(direction: str) -> str | None:
    """Map a stored ``confidence_ratings.direction`` onto a declaration key.

    ``long``/``short`` narrow per direction; anything else — notably the legacy
    ``combined`` rows the direction migration backfilled — describes the cell
    irrespective of direction and is judged against the base declaration.

    Lives here, beside the code that WRITES ``confidence_ratings``, so the
    reader in ``tools/dead_surface_check.py`` and the pruner below cannot fork
    into two hand-mirrored copies. That has already happened once in this
    codebase (``get_backtest_win_rates`` vs ``compute_dsr_ratings``), and a fork
    here would let a cell read as declared to the checker and undeclared to the
    pruner — i.e. a silent deletion of a live rating.
    """
    return direction if direction in ("long", "short") else None


def prune_undeclared_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    cfg: SignalWatchConfig,
    max_share: float = DEFAULT_PRUNE_MAX_SHARE,
    min_rows: int = DEFAULT_PRUNE_MIN_ROWS,
) -> int:
    """Delete this config's rating rows for cells it no longer declares.

    ``recalibrate`` rebuilds ratings from historical ``backtest_runs`` with no
    notion of what the configs currently declare, and writes with an upsert — so
    a cell dropped from a config keeps its stars *and collects a fresh timestamp
    on a stale value at every refresh*. Measured 2026-08-13: orphan rows carried
    the same newest ``updated_at`` as clean rows, so nothing about one looks
    wrong. Nothing else in the pipeline removes them; ``prune_stale_ratings``
    matches on ``day_filter`` only and has no declaration check.

    Scoping needs no notion of "declared by another config": ratings are keyed
    by ``config_name``, so a row for a cell *this* config does not declare is
    dead weight for *this* daemon regardless of what the others declare — and
    each of those carries its own row for the cell.

    Raises :class:`PruneThresholdExceeded` without deleting anything when the
    undeclared share exceeds ``max_share``. Returns the number of rows removed.
    """
    rows = conn.execute(
        "SELECT strategy, tf, direction FROM confidence_ratings WHERE config_name = ?",
        [config_name],
    ).fetchall()
    if not rows:
        return 0

    declared: dict[str | None, set[tuple[str, str]]] = {
        key: set(declared_cells(cfg, key)) for key in (None, "long", "short")
    }
    undeclared = [
        (str(strategy), str(tf), str(direction))
        for strategy, tf, direction in rows
        if (str(strategy), str(tf))
        not in declared[rating_direction_key(str(direction))]
    ]
    if not undeclared:
        return 0

    if len(rows) >= min_rows and len(undeclared) / len(rows) > max_share:
        raise PruneThresholdExceeded(config_name, len(undeclared), len(rows), max_share)

    conn.executemany(
        "DELETE FROM confidence_ratings "
        "WHERE config_name = ? AND strategy = ? AND tf = ? AND direction = ?",
        [[config_name, s, tf, d] for s, tf, d in undeclared],
    )
    return len(undeclared)


class UnratedPruneThresholdExceeded(RuntimeError):
    """Raised when an unrated-rating prune would remove an implausible share.

    Deliberately NOT :class:`PruneThresholdExceeded`. That exception carries a
    field named ``n_undeclared`` and remediation advice pointing at the
    declaration resolver, and neither describes this pruner — reusing it would
    hand the reader a confidently wrong diagnosis at the one moment they are
    reading an alarm.

    The failure this guard catches is a **collapsed pool**: measured
    2026-08-20, omitting ``adr_suppress_threshold`` from
    ``get_backtest_win_rates`` returns 0 rows for two of the three configs, so
    every cell falls below its trade floor, the pass rates nothing, and an
    unguarded delete removes every live rating for that config in one silent
    pass. The daemon then logs "No confidence ratings found" at INFO on its
    next restart and nothing else anywhere reports a problem.

    Nothing has been deleted when this is raised.
    """

    def __init__(
        self, config_name: str, n_unrated: int, n_total: int, max_share: float
    ) -> None:
        self.config_name = config_name
        self.n_unrated = n_unrated
        self.n_total = n_total
        self.max_share = max_share
        super().__init__(
            f"refusing to prune {n_unrated} of {n_total} rating row(s) for "
            f"'{config_name}' ({n_unrated / n_total:.0%} > "
            f"{max_share:.0%} ceiling). Nothing was deleted. Either the pool "
            f"genuinely shed this many cells — re-run with a raised ceiling "
            f"after eyeballing the recalibration report above — or the rating "
            f"pass was scoped wrongly and read an empty pool, which is the bug "
            f"this guard exists to catch: check that day_filter and "
            f"adr_suppress_threshold match the config."
        )


def prune_unrated_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    ratings: dict[str, dict[str, int]],
    directional_ratings: dict[str, dict[str, dict[str, int]]] | None,
    max_share: float = DEFAULT_PRUNE_MAX_SHARE,
    min_rows: int = DEFAULT_PRUNE_MIN_ROWS,
) -> int:
    """Delete this config's rating rows the CURRENT pass produced no rating for.

    ``compute_recalibrated_ratings`` omits a strategy under ``min_trades`` and
    ``compute_directional_ratings`` omits a direction under its own floor — but
    **omission is an upsert with no delete counterpart**, so a cell that stays
    *declared* while falling below the floor keeps its last rating forever.
    Neither existing pruner reaches it: ``prune_stale_ratings`` matches on
    ``day_filter`` and ``prune_undeclared_ratings`` on declaration, and such a
    cell is current on both. Measured 2026-08-20 (ST58): 24 of 288 rows frozen,
    oldest stamped 2026-04-02, 24 of 24 still declared.

    This is not cosmetic. ``analytics/signal/gates.py`` drops the
    lower-confidence side when both directions fire on one candle, so a frozen
    star can silence the correct direction — measured worst case, a
    ``weekdays ema/4h/long`` frozen at 4 stars against a fresh 1-star short on
    a cell whose current pool reads avg_r -1.02.

    Call this **once per config**, over the union of all three directions, with
    the two dicts the pass just wrote. Never call it from inside
    ``upsert_confidence_ratings``, which runs once per direction: a pass that
    saw only ``long`` would delete every ``combined`` and ``short`` row.

    The key is three-part. ``direction`` must be in it, because a cell can have
    a freshly rated ``long`` and an unrated ``short`` in the same pass — a
    ``(strategy, tf)`` key would keep both or drop both.

    Raises :class:`UnratedPruneThresholdExceeded` without deleting anything when
    the unrated share exceeds ``max_share``. Returns the number of rows removed.
    """
    rows = conn.execute(
        "SELECT strategy, tf, direction FROM confidence_ratings WHERE config_name = ?",
        [config_name],
    ).fetchall()
    if not rows:
        return 0

    keep: set[tuple[str, str, str]] = set()
    for strategy, tf_map in ratings.items():
        for tf in tf_map:
            keep.add((strategy, tf, "combined"))
    for strategy, dir_tf_map in (directional_ratings or {}).items():
        for tf, stars_map in dir_tf_map.items():
            # Iterate what the dict actually holds: a pass can rate one
            # direction and omit the other, which is the ST58 shape itself.
            for direction in stars_map:
                keep.add((strategy, tf, direction))

    unrated = [
        (str(strategy), str(tf), str(direction))
        for strategy, tf, direction in rows
        if (str(strategy), str(tf), str(direction)) not in keep
    ]
    if not unrated:
        return 0

    if len(rows) >= min_rows and len(unrated) / len(rows) > max_share:
        raise UnratedPruneThresholdExceeded(
            config_name, len(unrated), len(rows), max_share
        )

    conn.executemany(
        "DELETE FROM confidence_ratings "
        "WHERE config_name = ? AND strategy = ? AND tf = ? AND direction = ?",
        [[config_name, s, tf, d] for s, tf, d in unrated],
    )
    return len(unrated)


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
    content = source_path.read_text(encoding="utf-8")
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
        source_path.write_text(content, encoding="utf-8")

    return patched
