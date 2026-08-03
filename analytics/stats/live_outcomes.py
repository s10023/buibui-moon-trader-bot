"""Live signal-alert outcome statistics (cross-symbol).

Graduates the read-only CLI stop-gap ``tools/live_outcomes_report.py`` into the
Stats page. Reads the live ``signal_alert_outcomes`` ledger (populated by the
signal daemon's outcome writer + backfill worker) and returns:

- a roll-up: total / resolved / open mix + win/loss/expired counts (all-time
  by default, optionally scoped to one symbol — ``open_no_tp`` is a
  data-integrity gauge that should read 0 after the outcome-ledger SL/TP
  fallback fix);
- per-(strategy, tf, direction) win-rate / avg-R cells (windowed by ``days``);
- a per-strategy roll-up (windowed by ``days``), ordered by avg-R desc.

Unlike the per-symbol StatsBundle cards this aggregates across all symbols by
default, so it lives in its own router and is never cached. Empty ledger is a
valid state (no alerts fired yet) — returns a zero roll-up rather than
raising.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import duckdb


@dataclass
class LiveOutcomesRollup:
    """All-time integrity + outcome roll-up over the ledger."""

    total_rows: int
    resolved: int
    open: int
    open_no_tp: int  # open AND tp_price IS NULL — should be 0 post-fix
    wins: int
    losses: int
    expired: int


@dataclass
class LiveOutcomeCell:
    """Per-(strategy, tf, direction) resolved-trade breakdown."""

    strategy: str
    tf: str
    direction: str
    n: int
    wins: int
    losses: int
    expired: int
    win_rate: float | None  # wins / (wins + losses); expired excluded
    avg_r: float | None  # mean outcome_r over all resolved rows in the cell


@dataclass
class LiveOutcomeStrategyRow:
    """Per-strategy resolved-trade roll-up across TFs and directions."""

    strategy: str
    n: int
    wins: int
    losses: int
    expired: int
    win_rate: float | None
    avg_r: float | None


@dataclass
class LiveOutcomeSymbolRow:
    """One symbol chip: the symbol and its all-time alert count."""

    symbol: str
    n: int


@dataclass
class OpenPosition:
    """One unresolved ledger row — the alert is still live."""

    signal_id: str
    symbol: str
    strategy: str
    tf: str
    direction: str
    fired_at_ms: int
    entry_price: float | None
    sl_price: float | None
    tp_price: float | None


@dataclass
class MarkedOpenPosition:
    """An open position with current-price arithmetic attached.

    ``unrealized_r`` is GROSS of costs, unlike the resolved ``outcome_r`` in
    the tables, which is net of fee + slippage + funding. Netting an open
    position would mean accruing funding to the current moment; the UI labels
    this column gross instead.
    """

    position: OpenPosition
    mark: float | None
    unrealized_r: float | None
    dist_sl_pct: float | None
    dist_tp_pct: float | None


@dataclass
class LiveOutcomesResult:
    """Full live-outcomes payload for the Stats card."""

    days: int  # window applied to cells/by_strategy (0 = all time)
    min_n: int  # minimum resolved rows per cell/strategy
    rollup: LiveOutcomesRollup
    cells: list[LiveOutcomeCell]
    by_strategy: list[LiveOutcomeStrategyRow]
    symbols: list[LiveOutcomeSymbolRow]  # chip list — always global, all-time


def compute_live_outcomes(
    conn: duckdb.DuckDBPyConnection,
    days: int = 30,
    min_n: int = 1,
    *,
    symbol: str | None = None,
    now_ms: int | None = None,
) -> LiveOutcomesResult:
    """Compute the live-outcomes roll-up + breakdowns.

    ``days`` windows only the per-cell / per-strategy tables (0 = all time).

    ``now_ms`` anchors that window. ``None`` (the default) uses the wall clock
    and is byte-identical to the pre-parameter behaviour, which is what the API
    router wants. Callers that reconstruct a past moment MUST pass their own
    clock: the F2 card takes an explicit ``now_ms`` so ``--as-of`` is
    reproducible, and a wall-clock cutoff would annotate a past-dated card with
    outcomes recorded *after* its as-of date — look-ahead in the one surface
    built for determinism. Harmless while ``days`` was 0 (no cutoff is computed
    at all); a live bug the moment a non-zero window became the default. Note it
    bounds the cell / per-strategy tables only — the roll-up stays all-time by
    design, as it already did for ``days``, so an as-of caller must read
    ``cells`` (which is what the card does), not ``rollup``.

    ``symbol`` scopes the roll-up AND both tables to one symbol; ``None`` (the
    default, and the UI's ALL chip) is the global view and is byte-identical to
    the pre-symbol-filter behaviour. Under a symbol filter ``open_no_tp``
    reports that symbol's integrity rather than the ledger's.

    ``symbols`` (the chip list) is always global and all-time, unaffected by
    both arguments, so chips never disappear or churn as filters change.

    Never raises on empty data — returns a zero roll-up.
    """
    sym_where = ""
    sym_params: tuple[object, ...] = ()
    if symbol:
        sym_where = "WHERE symbol = ?"
        sym_params = (symbol,)

    totals = conn.execute(
        f"""
        SELECT
          COUNT(*)                                     AS total_rows,
          COUNT(*) FILTER (WHERE outcome IS NOT NULL)  AS resolved,
          COUNT(*) FILTER (WHERE outcome IS NULL)      AS open_rows,
          COUNT(*) FILTER (WHERE outcome IS NULL
                           AND tp_price IS NULL)       AS open_no_tp,
          COUNT(*) FILTER (WHERE outcome = 'win')      AS wins,
          COUNT(*) FILTER (WHERE outcome = 'loss')     AS losses,
          COUNT(*) FILTER (WHERE outcome = 'expired')  AS expired
        FROM signal_alert_outcomes
        {sym_where}
        """,
        sym_params,
    ).fetchone()

    rollup = LiveOutcomesRollup(
        total_rows=int(totals[0]) if totals else 0,
        resolved=int(totals[1]) if totals else 0,
        open=int(totals[2]) if totals else 0,
        open_no_tp=int(totals[3]) if totals else 0,
        wins=int(totals[4]) if totals else 0,
        losses=int(totals[5]) if totals else 0,
        expired=int(totals[6]) if totals else 0,
    )

    where = "WHERE outcome IS NOT NULL"
    params: list[object] = []
    if symbol:
        where += " AND symbol = ?"
        params.append(symbol)
    if days > 0:
        anchor_ms = int(time.time() * 1000) if now_ms is None else now_ms
        cutoff_ms = anchor_ms - days * 86_400_000
        where += " AND fired_at_ms >= ?"
        params.append(cutoff_ms)
    if now_ms is not None:
        # An explicit clock means "reconstruct the ledger as of this instant",
        # so it needs an UPPER bound too. Moving only the lower bound would
        # still let rows fired after the as-of date through, since the window
        # is otherwise one-sided — the look-ahead this parameter exists to
        # stop. Applied independently of `days` so `days=0` + `now_ms` means
        # all-time-up-to-the-clock rather than all-time-including-the-future.
        # Unreachable for the wall-clock default: no row is fired in the
        # future, so the API router stays byte-identical.
        where += " AND fired_at_ms <= ?"
        params.append(now_ms)

    cell_rows = conn.execute(
        f"""
        SELECT
          strategy, tf, direction,
          COUNT(*)                                  AS n,
          COUNT(*) FILTER (WHERE outcome='win')     AS wins,
          COUNT(*) FILTER (WHERE outcome='loss')    AS losses,
          COUNT(*) FILTER (WHERE outcome='expired') AS expired,
          AVG(CASE WHEN outcome='win'  THEN 1.0
                   WHEN outcome='loss' THEN 0.0 END) AS win_rate,
          AVG(outcome_r)                            AS avg_r
        FROM signal_alert_outcomes
        {where}
        GROUP BY strategy, tf, direction
        HAVING COUNT(*) >= ?
        ORDER BY strategy, tf, direction
        """,
        (*params, min_n),
    ).fetchall()

    cells = [
        LiveOutcomeCell(
            strategy=str(s),
            tf=str(tf),
            direction=str(direction),
            n=int(n),
            wins=int(wins),
            losses=int(losses),
            expired=int(expired),
            win_rate=None if win_rate is None else float(win_rate),
            avg_r=None if avg_r is None else float(avg_r),
        )
        for (s, tf, direction, n, wins, losses, expired, win_rate, avg_r) in cell_rows
    ]

    strat_rows = conn.execute(
        f"""
        SELECT
          strategy,
          COUNT(*) AS n,
          COUNT(*) FILTER (WHERE outcome='win')     AS wins,
          COUNT(*) FILTER (WHERE outcome='loss')    AS losses,
          COUNT(*) FILTER (WHERE outcome='expired') AS expired,
          AVG(CASE WHEN outcome='win'  THEN 1.0
                   WHEN outcome='loss' THEN 0.0 END) AS win_rate,
          AVG(outcome_r)                            AS avg_r
        FROM signal_alert_outcomes
        {where}
        GROUP BY strategy
        HAVING COUNT(*) >= ?
        ORDER BY avg_r DESC NULLS LAST, strategy
        """,
        (*params, min_n),
    ).fetchall()

    by_strategy = [
        LiveOutcomeStrategyRow(
            strategy=str(s),
            n=int(n),
            wins=int(wins),
            losses=int(losses),
            expired=int(expired),
            win_rate=None if win_rate is None else float(win_rate),
            avg_r=None if avg_r is None else float(avg_r),
        )
        for (s, n, wins, losses, expired, win_rate, avg_r) in strat_rows
    ]

    symbol_rows = conn.execute(
        """
        SELECT symbol, COUNT(*) AS n
        FROM signal_alert_outcomes
        GROUP BY symbol
        ORDER BY n DESC, symbol ASC
        """
    ).fetchall()

    symbols = [
        LiveOutcomeSymbolRow(symbol=str(sym), n=int(n)) for (sym, n) in symbol_rows
    ]

    return LiveOutcomesResult(
        days=days,
        min_n=min_n,
        rollup=rollup,
        cells=cells,
        by_strategy=by_strategy,
        symbols=symbols,
    )


def open_positions(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbol: str | None = None,
) -> list[OpenPosition]:
    """Return unresolved ledger rows, newest first.

    Pure DB read — no price, no network, no clock.
    """
    where = "WHERE outcome IS NULL"
    params: list[object] = []
    if symbol:
        where += " AND symbol = ?"
        params.append(symbol)

    rows = conn.execute(
        f"""
        SELECT signal_id, symbol, strategy, tf, direction, fired_at_ms,
               entry_price, sl_price, tp_price
        FROM signal_alert_outcomes
        {where}
        ORDER BY fired_at_ms DESC
        """,
        tuple(params),
    ).fetchall()

    return [
        OpenPosition(
            signal_id=str(sid),
            symbol=str(sym),
            strategy=str(strategy),
            tf=str(tf),
            direction=str(direction),
            fired_at_ms=int(fired_at_ms),
            entry_price=None if entry is None else float(entry),
            sl_price=None if sl is None else float(sl),
            tp_price=None if tp is None else float(tp),
        )
        for (
            sid,
            sym,
            strategy,
            tf,
            direction,
            fired_at_ms,
            entry,
            sl,
            tp,
        ) in rows
    ]


def mark_open_positions(
    rows: Sequence[OpenPosition],
    marks: Mapping[str, float],
) -> list[MarkedOpenPosition]:
    """Attach mark price, gross unrealized R, and SL/TP distances.

    Pure: no clock, no network, no DB. Every derived field degrades to ``None``
    rather than raising, so a missing price or a degenerate stop never breaks
    the panel.
    """
    marked: list[MarkedOpenPosition] = []

    for pos in rows:
        mark = marks.get(pos.symbol)
        mark_valid = mark is not None and mark > 0
        unrealized_r: float | None = None
        dist_sl_pct: float | None = None
        dist_tp_pct: float | None = None

        # A missing or zero mark is a bad tick — nothing derived is meaningful.
        if mark_valid:
            assert mark is not None  # narrow for mypy; mark_valid implies not None
            if pos.entry_price is not None and pos.sl_price is not None:
                risk = abs(pos.entry_price - pos.sl_price)
                if risk > 0:
                    gain = (
                        mark - pos.entry_price
                        if pos.direction == "long"
                        else pos.entry_price - mark
                    )
                    unrealized_r = gain / risk
            if pos.sl_price is not None:
                dist_sl_pct = abs(mark - pos.sl_price) / mark
            if pos.tp_price is not None:
                dist_tp_pct = abs(mark - pos.tp_price) / mark

        marked.append(
            MarkedOpenPosition(
                position=pos,
                mark=mark if mark_valid else None,
                unrealized_r=unrealized_r,
                dist_sl_pct=dist_sl_pct,
                dist_tp_pct=dist_tp_pct,
            )
        )

    return marked
