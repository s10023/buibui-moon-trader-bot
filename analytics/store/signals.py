"""signals + signal_alert_outcomes table accessors."""

from typing import Any

import duckdb
import pandas as pd

# Split into the two facts a row carries, because they have DIFFERENT WRITERS and
# the split is what stops one clobbering the other -- see upsert_signal_outcome.
# The scanner writes fire-time facts every time it re-detects a candle;
# analytics/signal/outcome_backfill.py resolves the outcome LATER, by UPDATE.
_FIRE_COLUMNS = [
    "symbol",
    "tf",
    "strategy",
    "direction",
    "fired_at_ms",
    "candle_ts_ms",
    "entry_price",
    "sl_price",
    "tp_price",
    "rr_ratio",
    "confidence_at_fire",
    "tags",
]
_RESOLVED_COLUMNS = ["outcome", "outcome_r", "outcome_filled_at_ms"]
_OUTCOME_COLUMNS = ["signal_id", *_FIRE_COLUMNS, *_RESOLVED_COLUMNS]


def upsert_signals(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or ignore signal rows (conflicts on PK are silently skipped).

    df must have columns: symbol, timeframe, strategy, open_time, direction,
    entry_price, sl_price, reason, confidence, fired_at.
    Conflicts on (symbol, timeframe, strategy, open_time, direction) are ignored
    so that re-runs of the same scan cycle do not overwrite previously persisted
    signals with potentially different metadata.
    """
    if df.empty:
        return
    # Explicit register/unregister in try/finally — see _upsert docstring for why.
    conn.register("_signals_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO signals "
            "SELECT symbol, timeframe, strategy, open_time, direction, "
            "entry_price, sl_price, reason, confidence, fired_at "
            "FROM _signals_upsert_df"
        )
    finally:
        conn.unregister("_signals_upsert_df")


def get_signals_history(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Return persisted signal rows for (symbol, timeframe) in [start_ms, end_ms].

    Results are ordered by open_time descending (most recent first).
    """
    return conn.execute(
        "SELECT symbol, timeframe, strategy, open_time, direction, "
        "entry_price, sl_price, reason, confidence, fired_at "
        "FROM signals "
        "WHERE symbol = ? AND timeframe = ? AND open_time >= ? AND open_time <= ? "
        "ORDER BY open_time DESC",
        [symbol, timeframe, start_ms, end_ms],
    ).df()


def upsert_signal_outcome(conn: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> None:
    """Insert a signal row, or refresh its FIRE-TIME fields, never its outcome.

    The row dict must contain at minimum: signal_id, symbol, tf, strategy,
    direction, fired_at_ms.  All other fields are optional and default to NULL
    when omitted.

    ⚠ **A conflict updates only the columns the CALLER ACTUALLY SUPPLIED. An
    outcome field absent from `row` is left exactly as it was** (ST82, ported
    from wifey #157). This was `INSERT OR REPLACE` over all 16 columns until
    2026-09-03, and its sole caller -- `analytics/signal/scanner.py` -- passes no
    outcome fields at all, so `row.get()` handed them back as NULL: re-detecting
    an already-resolved candle BLANKED its result. `--catch-up` persists replayed
    closed candles, so that path is live rather than theoretical.

    Keying on PRESENCE rather than on the column's name is what keeps this a
    fix and not a trade: a caller that does pass an outcome still writes it, so
    no existing contract narrows, and a caller that passes `outcome=None` still
    blanks it -- an explicit request rather than an accident of `.get()`.

    ⚠ **The old docstring justified the REPLACE by saying the outcome is
    "backfilled later" through here. That was false, and checking it is what
    sized this fix.** The resolver never calls this function; it writes
    `SET outcome = ?, outcome_r = ?, outcome_filled_at_ms = ?` directly
    (`outcome_backfill.py:344`). So the replace semantics protected nothing and
    cost the blanking -- a claim that reads as a reason while being neither.

    The column blanked hardest is the one that matters most: `AGENTS.md` requires
    the cost-basis era split to be taken on `outcome_filled_at_ms` and never on
    `candle_ts_ms`, so a silent restatement moves the boundary of the two-basis
    ledger the OOS evidence base is drawn from.
    """
    values = [row.get(col) for col in _OUTCOME_COLUMNS]
    placeholders = ", ".join("?" * len(_OUTCOME_COLUMNS))
    # Fire-time columns always refresh; a resolved column only when the caller
    # supplied it. Derived from the lists rather than spelled out, so a column
    # added to the table cannot silently stop being refreshed -- or start
    # clobbering an outcome.
    refreshed = _FIRE_COLUMNS + [c for c in _RESOLVED_COLUMNS if c in row]
    updates = ", ".join(f"{col} = excluded.{col}" for col in refreshed)
    conn.execute(
        f"INSERT INTO signal_alert_outcomes ({', '.join(_OUTCOME_COLUMNS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (signal_id) DO UPDATE SET {updates}",
        values,
    )
