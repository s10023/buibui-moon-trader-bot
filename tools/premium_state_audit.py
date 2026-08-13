#!/usr/bin/env python
"""H14 Coinbase-premium market-state audit — read-only DB front door.

Does the Coinbase premium (US-spot demand vs Binance, peg-adjusted) condition
the avg_r of this system's EXISTING trades? Consumes the pure library
:mod:`analytics.venue_premium` (series construction, causal z-score, state
labels, per-day collapse, the pre-committed BUILD/AVOID/NO-EDGE/INSUFFICIENT
gate) and the additive :mod:`analytics.store.venue_prices` table. This driver
adds no statistics of its own beyond what the library already decided — the
DSR/PBO/MinTRL/stability numbers printed here are recomputed with the SAME
private helpers ``evaluate_premium_states`` uses internally (imported
directly, not reimplemented), so what is printed is provably what the
verdict was actually computed from (amendments.md A3).

Substrate roles (spec Sec.7, pre-committed): ``backtest_trades`` = primary,
gate-deciding (de-biased, deep). ``signal_alert_outcomes`` (live) =
corroboration only — thin and, before N6 catch-up, session-skewed — never
gate-deciding.

Read-only over ``analytics.db`` UNLESS ``--refresh`` is passed, in which case
the ONLY write this tool ever performs is ``upsert_venue_spot_daily`` into
the additive ``venue_spot_daily`` table (never any other table; never a
backtest or recalibrate run against the live DB — that has silently moved
star ratings before). Run:
    PYTHONPATH=. poetry run python tools/premium_state_audit.py [--refresh]
(wrapped by ``make buibui-premium-state-audit``).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.audit_guard import (  # noqa: E402
    DECISION_CONCENTRATE,
    DECISION_INSUFFICIENT,
    AuditCell,
    CellVerdict,
    evaluate_audit_cells,
)
from analytics.research_guards import min_track_record_length  # noqa: E402
from analytics.state_audit import family_pbo  # noqa: E402
from analytics.store import DEFAULT_DB_PATH, init_schema  # noqa: E402
from analytics.store.venue_prices import (  # noqa: E402
    get_venue_spot_daily,
    upsert_venue_spot_daily,
)
from analytics.venue_fetch import (  # noqa: E402
    fetch_binance_spot_daily,
    fetch_coinbase_daily,
)
from analytics.venue_premium import (  # noqa: E402
    ALPHA,
    BAR,
    LEVEL_DEPRESSED,
    LEVEL_ELEVATED,
    LEVEL_NEUTRAL,
    MIN_N,
    MINTRL_CONFIDENCE,
    VERDICT_AVOID,
    VERDICT_BUILD,
    VERDICT_INSUFFICIENT,
    VERDICT_NO_EDGE,
    _cell_family_key,  # noqa: SLF001 -- the paired lib's own family grouping, not reinvented
    build_premium_series,
    build_state_cells,
    causal_zscore,
    cell_sharpe,
    collapse_to_daily,
    evaluate_premium_states,
    family_dsr,
    label_changes,
    label_levels,
    sign_agrees_early_late,
)

_DAY_MS = 86_400_000
# 2017-01-01 predates all three series (Binance BTCUSDT starts 2017-08-17,
# Coinbase BTC-USD/USDT-USD later still); requesting from here is safe —
# Coinbase's candles endpoint returns an empty page (not an error) for any
# window before a product's own listing date, verified against the live
# endpoint during this task's implementation.
_REFRESH_START_MS = int(datetime(2017, 1, 1, tzinfo=UTC).timestamp() * 1000)

_BACKTEST_QUERY = (
    "SELECT entry_time, direction, pnl_r FROM backtest_trades WHERE pnl_r IS NOT NULL"
)
_LIVE_QUERY = (
    "SELECT candle_ts_ms AS entry_time, direction, outcome_r AS pnl_r "
    "FROM signal_alert_outcomes WHERE outcome_r IS NOT NULL"
)

_VERDICT_ORDER = {
    VERDICT_BUILD: 0,
    VERDICT_AVOID: 0,
    VERDICT_NO_EDGE: 1,
    VERDICT_INSUFFICIENT: 2,
}


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# --refresh: the ONLY write path (upsert_venue_spot_daily, nothing else)      #
# --------------------------------------------------------------------------- #


def refresh_venue_prices(db: Path) -> None:
    """Fetch the three venue daily-close series from their public keyless
    endpoints and upsert into the additive ``venue_spot_daily`` table.

    Never touches any other table: no backtest, no recalibrate, no schema
    change beyond ``init_schema``'s idempotent ``CREATE TABLE IF NOT EXISTS``.
    """
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    print(
        f"[refresh] fetching Coinbase BTC-USD / Binance BTCUSDT / Coinbase "
        f"USDT-USD daily closes, {_iso(_REFRESH_START_MS)} -> {_iso(end_ms)}",
        file=sys.stderr,
    )
    cb_btc = fetch_coinbase_daily("BTC-USD", _REFRESH_START_MS, end_ms)
    bn_btc = fetch_binance_spot_daily("BTCUSDT", _REFRESH_START_MS)
    cb_usdt = fetch_coinbase_daily("USDT-USD", _REFRESH_START_MS, end_ms)

    frames: list[pd.DataFrame] = []
    for venue, symbol, df in (
        ("coinbase", "BTC-USD", cb_btc),
        ("binance", "BTCUSDT", bn_btc),
        ("coinbase", "USDT-USD", cb_usdt),
    ):
        print(f"[refresh]   {venue}/{symbol}: {len(df)} daily closes", file=sys.stderr)
        frames.append(
            df.assign(venue=venue, symbol=symbol)[
                ["venue", "symbol", "open_time", "close"]
            ]
        )
    combined = pd.concat(frames, ignore_index=True)

    with duckdb.connect(str(db)) as conn:
        init_schema(conn)
        upsert_venue_spot_daily(conn, combined)
    print(
        f"[refresh] upserted {len(combined)} rows into venue_spot_daily",
        file=sys.stderr,
    )


# --------------------------------------------------------------------------- #
# read-only loaders                                                           #
# --------------------------------------------------------------------------- #


def _load_close_series(
    conn: duckdb.DuckDBPyConnection, venue: str, symbol: str
) -> pd.Series:
    """One venue/symbol's closes, indexed by day number (``open_time //
    86_400_000``) — the day-index convention every downstream helper shares.
    """
    df = get_venue_spot_daily(conn, venue, symbol)
    if df.empty:
        return pd.Series(dtype="float64")
    day = (df["open_time"].to_numpy() // _DAY_MS).astype("int64")
    s = pd.Series(df["close"].to_numpy(), index=pd.Index(day, name="day"))
    return s[~s.index.duplicated(keep="last")].sort_index()


def _load_trades(conn: duckdb.DuckDBPyConnection, source: str) -> pd.DataFrame:
    query = _BACKTEST_QUERY if source == "backtest" else _LIVE_QUERY
    return conn.execute(query).df()


# --------------------------------------------------------------------------- #
# sanity-check reporting (brief Step 2 — NOT optional)                        #
# --------------------------------------------------------------------------- #


def _coverage_header(series_name: str, series: pd.Series) -> str:
    valid = series.dropna()
    if valid.empty:
        return f"[{series_name}] coverage: EMPTY — nothing loaded (run with --refresh?)"
    lo, hi = int(valid.index.min()), int(valid.index.max())
    note = ""
    if series_name in ("prem_adj", "peg_dev"):
        note = (
            " NOTE: cannot start before 2021-05-04 — Coinbase USDT-USD does "
            "not exist earlier; do not mistake this for full history."
        )
    return (
        f"[{series_name}] coverage: {_iso(lo * _DAY_MS)} -> {_iso(hi * _DAY_MS)} "
        f"({len(valid)} daily observations).{note}"
    )


def _level_balance_report(levels: pd.Series) -> str:
    valid = levels.dropna()
    n = len(valid)
    if n == 0:
        return "[levels] no labeled days (empty series — run with --refresh?)"
    counts = valid.value_counts()
    parts = [
        f"{state}={int(counts.get(state, 0))} ({counts.get(state, 0) / n:.1%})"
        for state in (LEVEL_ELEVATED, LEVEL_NEUTRAL, LEVEL_DEPRESSED)
    ]
    return (
        f"[levels] n={n} labeled days: "
        + ", ".join(parts)
        + " -- elevated/depressed should each be roughly 10-20% on a "
        "causal +/-1 sigma z-score; a big miss means the z-score or "
        "collapse step is wrong, not that the data is unusual."
    )


# --------------------------------------------------------------------------- #
# per-trade "optimistic, decides nothing" comparison (brief Step 6)           #
# --------------------------------------------------------------------------- #


def _tag_trades_with_state(
    trades: pd.DataFrame, states: pd.Series, *, lag_days: int = 1
) -> pd.DataFrame:
    """Per-TRADE state tag — the same ``day - lag_days`` join
    :func:`analytics.venue_premium.collapse_to_daily` uses, WITHOUT its
    day-level averaging. Feeds only the optimistic per-trade comparison
    printed alongside the real (per-day) cell stats — never the gate.
    """
    if trades.empty:
        return trades.assign(
            day=pd.Series(dtype="int64"), state=pd.Series(dtype=object)
        )
    df = trades.copy()
    df["day"] = (df["entry_time"] // _DAY_MS).astype("int64")
    df["state"] = (df["day"] - lag_days).map(states)
    return df.dropna(subset=["state"])


def _per_trade_index(
    trades: pd.DataFrame, levels: pd.Series, changes: pd.Series
) -> dict[tuple[str, str], tuple[int, float]]:
    tagged = pd.concat(
        [
            _tag_trades_with_state(trades, levels),
            _tag_trades_with_state(trades, changes),
        ],
        ignore_index=True,
    )
    if tagged.empty:
        return {}
    grouped = (
        tagged.groupby(["state", "direction"])["pnl_r"]
        .agg(["count", "mean"])
        .reset_index()
    )
    out: dict[tuple[str, str], tuple[int, float]] = {}
    for state, direction, count, mean in grouped.itertuples(index=False, name=None):
        out[(str(state), str(direction))] = (int(count), float(mean))
    return out


# --------------------------------------------------------------------------- #
# cell family + diagnostics (recomputed with the SAME private helpers        #
# evaluate_premium_states uses, per amendments.md A3 — not reimplemented)    #
# --------------------------------------------------------------------------- #


def _build_cells(
    trades: pd.DataFrame, levels: pd.Series, changes: pd.Series
) -> list[AuditCell]:
    """Level cells + change cells, concatenated into ONE Holm-haircut family
    (evaluate_premium_states's own docstring: level(3) x direction(2) +
    change(2) x direction(2) = 10 cells on the primary series).
    """
    daily_level = collapse_to_daily(trades, levels)
    daily_change = collapse_to_daily(trades, changes)
    return build_state_cells(daily_level) + build_state_cells(daily_change)


def _cell_diagnostics(
    cells: list[AuditCell], cell_verdicts: list[CellVerdict]
) -> dict[str, dict[str, float | None]]:
    """DSR / PBO / MinTRL per cell — reported against the exact verdicts the
    decision was made from, not a parallel calculation that could silently
    drift from it.

    ``cell_verdicts`` is passed IN rather than recomputed. It used to call
    ``evaluate_audit_cells(cells, bar=BAR, ...)`` itself, with arguments
    identical to the call its own caller had just made on the same list — a
    duplicated bootstrap whose result could only ever match. Threading the
    caller's verdicts through *strengthens* the guarantee in the paragraph
    above from "same inputs, so same answer" to "literally the same object".

    ``None`` for INSUFFICIENT/CONCENTRATE cells, mirroring
    ``evaluate_premium_states``'s own short-circuit for those two decisions
    (it never computes DSR/PBO/MinTRL for them either).
    """
    if not cells:
        return {}
    by_family: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(cells):
        by_family.setdefault(_cell_family_key(c.label), []).append(i)

    out: dict[str, dict[str, float | None]] = {}
    for cell, cv in zip(cells, cell_verdicts, strict=True):
        if cv.decision in (DECISION_INSUFFICIENT, DECISION_CONCENTRATE):
            out[cell.label] = {"dsr": None, "pbo": None, "mintrl": None}
            continue
        supp = np.asarray(cell.supp_r, dtype=np.float64)
        sharpe = cell_sharpe(supp)
        mintrl = min_track_record_length(
            abs(sharpe), target_sr=0.0, confidence=MINTRL_CONFIDENCE
        )
        family_idx = by_family[_cell_family_key(cell.label)]
        family_arrays = [
            np.asarray(cells[j].supp_r, dtype=np.float64) for j in family_idx
        ]
        dsr = family_dsr(supp, family_arrays)
        pbo = family_pbo(family_arrays)
        out[cell.label] = {"dsr": dsr, "pbo": pbo, "mintrl": mintrl}
    return out


@dataclass(frozen=True)
class _Row:
    axis: str
    state: str
    direction: str
    n_days: int
    mean_r: float | None
    ci_lo: float | None
    ci_hi: float | None
    adj_p: float | None
    dsr: float | None
    pbo: float | None
    mintrl: float | None
    stable: bool
    n_trades: int
    mean_r_trades: float | None
    verdict: str


def _build_rows(
    cells: list[AuditCell], trades: pd.DataFrame, levels: pd.Series, changes: pd.Series
) -> list[_Row]:
    if not cells:
        return []
    cell_verdicts = evaluate_audit_cells(cells, bar=BAR, alpha=ALPHA, min_n=MIN_N)
    verdict_by_label = dict(evaluate_premium_states(cells))
    diagnostics = _cell_diagnostics(cells, cell_verdicts)
    per_trade = _per_trade_index(trades, levels, changes)

    rows: list[_Row] = []
    for cell, cv in zip(cells, cell_verdicts, strict=True):
        axis, direction = _cell_family_key(cell.label)
        state = cell.label.split("|", 1)[0]
        diag = diagnostics[cell.label]
        n_trades, mean_r_trades = per_trade.get((state, direction), (0, None))
        rows.append(
            _Row(
                axis=axis,
                state=state,
                direction=direction,
                n_days=cv.n_supp,
                mean_r=cv.supp_avg,
                ci_lo=cv.ci_lo,
                ci_hi=cv.ci_hi,
                adj_p=cv.adj_pvalue,
                dsr=diag["dsr"],
                pbo=diag["pbo"],
                mintrl=diag["mintrl"],
                # Step 7 / amendments.md A3 point 2: printed via the SAME
                # function that decides the gate, not a separate calc.
                stable=sign_agrees_early_late(list(cell.supp_r)),
                n_trades=n_trades,
                mean_r_trades=mean_r_trades,
                verdict=verdict_by_label[cell.label],
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# report                                                                       #
# --------------------------------------------------------------------------- #


def _fmt(x: float | None) -> str:
    if x is None:
        return "—"
    if np.isnan(x):
        return "—"
    if np.isinf(x):
        return "inf"
    return f"{x:+.3f}"


def _sort_key(r: _Row) -> tuple[int, str, str, str]:
    return (_VERDICT_ORDER.get(r.verdict, 3), r.axis, r.state, r.direction)


def _render_table(rows: list[_Row]) -> str:
    header = (
        "| axis | state | dir | n_days | mean_r | ci_lo | ci_hi | adj_p | dsr | pbo "
        "| mintrl | stable | n_trades(opt.) | mean_r_trades(opt.) | verdict |"
    )
    sep = "| --- " * 15 + "|"
    lines = [header, sep]
    for r in sorted(rows, key=_sort_key):
        lines.append(
            f"| {r.axis} | {r.state} | {r.direction} | {r.n_days} | {_fmt(r.mean_r)} "
            f"| {_fmt(r.ci_lo)} | {_fmt(r.ci_hi)} | {_fmt(r.adj_p)} | {_fmt(r.dsr)} "
            f"| {_fmt(r.pbo)} | {_fmt(r.mintrl)} | {r.stable} | {r.n_trades} "
            f"| {_fmt(r.mean_r_trades)} | {r.verdict} |"
        )
    return "\n".join(lines)


def _low_n_warnings(rows: list[_Row], min_n: int) -> list[str]:
    """Display-only sanity check against ``--min-n`` (see the flag's help
    text: it never alters the pre-committed gate itself)."""
    return [
        f"[warn] {r.axis}/{r.state}/{r.direction}: n_days={r.n_days} < --min-n={min_n}"
        for r in rows
        if r.n_days < min_n
    ]


def _headline(rows: list[_Row]) -> str:
    build = [
        f"{r.axis}/{r.state}/{r.direction}" for r in rows if r.verdict == VERDICT_BUILD
    ]
    avoid = [
        f"{r.axis}/{r.state}/{r.direction}" for r in rows if r.verdict == VERDICT_AVOID
    ]
    if not build and not avoid:
        return "NO-EDGE / INSUFFICIENT across every pre-registered cell."
    parts = []
    if build:
        parts.append("BUILD: " + ", ".join(build))
    if avoid:
        parts.append("AVOID: " + ", ".join(avoid))
    return " | ".join(parts)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "H14: does the Coinbase-premium market state condition avg_r of "
            "this system's existing trades, beyond the stored OHLCV?"
        )
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument(
        "--series", choices=["prem_adj", "prem_raw", "peg_dev"], default="prem_adj"
    )
    p.add_argument("--source", choices=["backtest", "live", "both"], default="both")
    p.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "fetch fresh Coinbase/Binance daily closes and upsert into "
            "venue_spot_daily before auditing (the only write this tool "
            "ever performs), then reopen read-only"
        ),
    )
    p.add_argument(
        "--min-n",
        type=int,
        default=MIN_N,
        help=(
            f"display-only day-count sanity-check threshold (default {MIN_N}). "
            f"The pre-committed gate itself is frozen at min_n={MIN_N} "
            "(spec Sec.7) and is NOT altered by this flag -- see analytics."
            "venue_premium.MIN_N."
        ),
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    print(f"DB: {args.db}")
    if args.min_n != MIN_N:
        print(
            f"[note] --min-n={args.min_n} is display-only; the pre-committed "
            f"gate stays frozen at min_n={MIN_N} (spec Sec.7)",
            file=sys.stderr,
        )

    if args.refresh:
        refresh_venue_prices(args.db)

    sources = ["backtest", "live"] if args.source == "both" else [args.source]
    with duckdb.connect(str(args.db), read_only=True) as conn:
        cb_btc = _load_close_series(conn, "coinbase", "BTC-USD")
        bn_btc = _load_close_series(conn, "binance", "BTCUSDT")
        cb_usdt = _load_close_series(conn, "coinbase", "USDT-USD")
        trades_by_source = {src: _load_trades(conn, src) for src in sources}

    if cb_btc.empty or bn_btc.empty or cb_usdt.empty:
        print(
            "[error] one or more venue_spot_daily series is empty -- run "
            "with --refresh first",
            file=sys.stderr,
        )
        return 1

    premiums = build_premium_series(cb_btc, bn_btc, cb_usdt)
    series = premiums[args.series]
    levels = label_levels(causal_zscore(series))
    changes = label_changes(series)

    print(
        "\nH14 Coinbase-premium market-state audit -- does venue-flow "
        "condition this system's avg_r?"
    )
    print(f"series={args.series}  source={args.source}")
    print(_coverage_header(args.series, series))
    print(_level_balance_report(levels))
    print(
        "\nPre-committed semantics (spec Sec.7): BUILD = this state's per-day "
        "mean R is reliably POSITIVE and clears the family gate (DSR>=0.95, "
        "PBO<=0.5, n_days>=MinTRL(0.95), early/late sign agreement) -- "
        "audit_guard DISABLE, sign-inverted. AVOID = reliably NEGATIVE and "
        "clears the same gate -- audit_guard ENABLE. NO-EDGE = the CI RULED OUT "
        "an effect at the bar (CI strictly inside +/-bar) but no gate-grade "
        "effect, or CONCENTRATE. INSUFFICIENT = everything else, INCLUDING a "
        f"cell with n_days >> {MIN_N} whose CI is simply too wide to decide. "
        "backtest_trades = primary/gate-deciding; "
        "signal_alert_outcomes (live) = corroboration only."
    )
    print(
        "Decision rule (spec Sec.8): BUILD surviving on prem_adj with "
        "prem_raw agreeing in sign -> file as a live-gate/size-governor "
        "hypothesis, NOT a detector. All cells NO-EDGE -> the sixth "
        "conditioning NO. BUILD on prem_raw but not prem_adj -> re-file as "
        "stablecoin-stress, not US-demand."
    )

    any_rows = False
    for src in sources:
        trades = trades_by_source[src]
        print(f"\n=== source: {src} ({len(trades)} trades loaded) ===")
        if trades.empty:
            print("(no trades for this source)")
            continue
        cells = _build_cells(trades, levels, changes)
        if not cells:
            print("(no cells survived the day-collapse)")
            continue
        rows = _build_rows(cells, trades, levels, changes)
        any_rows = True
        print(f"headline: {_headline(rows)}")
        for line in _low_n_warnings(rows, args.min_n):
            print(line)
        print(_render_table(rows))

    if not any_rows:
        print("\nno data -- nothing to evaluate", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
