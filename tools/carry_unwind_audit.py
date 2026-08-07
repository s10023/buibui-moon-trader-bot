#!/usr/bin/env python3
"""H15 — USD/JPY carry-unwind state tag audit.

Spec: docs/superpowers/specs/2026-08-04-h15-usdjpy-carry-unwind-design.md

Runs the pre-committed gate over two panels:
  forward — BTCUSDT daily log return / causal trailing 30d vol  (decides)
  ledger  — per-UTC-day mean R of backtest/live trades          (secondary)

The two panels use DIFFERENT bars because their observations have different
units: BAR_VOL = 0.02 sigma-units vs BAR_LEDGER = 0.05 R. Passing the R bar to
the forward panel makes every verdict unreachable — spec Sec.6, and precisely
the H8 defect (a directional metric fed the wrong-scale threshold silently
makes a verdict unreachable rather than failing loudly).

Read-only over ``analytics.db`` UNLESS ``--refresh`` is passed, in which case
the ONLY write this tool ever performs is an upsert into the additive
``fx_prices`` table (created here with ``CREATE TABLE IF NOT EXISTS`` if
absent; never any other table; never a backtest or recalibrate run against
the live DB). Run:
    PYTHONPATH=. poetry run python tools/carry_unwind_audit.py [--refresh]
(wrapped by ``make buibui-carry-unwind-audit``).
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
from analytics.fx_carry import (  # noqa: E402
    MAG_NEUTRAL,
    RUN_EQ2,
    RUN_GE3,
    RUN_LE1,
    YEN_STRONG,
    YEN_WEAK,
    build_weekly_from_daily,
    carry_family_key,
    expand_to_days,
    label_magnitude_states,
    label_run_states,
    yen_strength_runs,
)
from analytics.research_guards import min_track_record_length  # noqa: E402
from analytics.state_audit import (  # noqa: E402
    ALPHA,
    MIN_N,
    MINTRL_CONFIDENCE,
    build_state_cells,
    cell_sharpe,
    collapse_to_daily,
    evaluate_states,
    family_dsr,
    family_pbo,
    sign_agrees_early_late,
)
from analytics.store import DEFAULT_DB_PATH, _upsert  # noqa: E402
from analytics.venue_fetch import fetch_yahoo_daily  # noqa: E402

_DAY_MS = 86_400_000

BAR_VOL = 0.02  # sigma-units, forward panel  (0.065%/day, 0.38 ann-Sharpe)
BAR_LEDGER = 0.05  # R-units, ledger panel — unchanged from H14
VOL_WINDOW = 30  # causal trailing realized-vol window, days
FX_START_MS = 1_483_228_800_000  # 2017-01-01, z-score warm-up margin only

_FX_SYMBOL = "JPY=X"

# (axis name, weekly state column) — never crossed; each is fed to
# build_state_cells SEPARATELY so one axis's complement slice never absorbs
# rows tagged under the other axis (the same discipline H14's _build_cells
# uses for level vs change).
_AXES: tuple[tuple[str, str], ...] = (("run", "run_state"), ("magnitude", "mag_state"))

_TRADE_QUERIES: dict[str, str] = {
    "backtest": (
        "SELECT entry_time, direction, pnl_r FROM backtest_trades "
        "WHERE pnl_r IS NOT NULL"
    ),
    "live": (
        "SELECT candle_ts_ms AS entry_time, direction, outcome_r AS pnl_r "
        "FROM signal_alert_outcomes WHERE outcome_r IS NOT NULL"
    ),
}

_VERDICT_ORDER = {"BUILD": 0, "AVOID": 0, "NO-EDGE": 1, "INSUFFICIENT": 2}

_EFFECTIVE_FINDING_NOTE = (
    "NOTE: each axis has 3 mutually exclusive states, so its 3 cell-vs-complement\n"
    "tests are ~2 effective comparisons, not 3. A cell count is not a finding count."
)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# --refresh: the ONLY write path (fx_prices upsert, nothing else)             #
# --------------------------------------------------------------------------- #


def refresh_fx_prices(db: Path) -> None:
    """Fetch JPY=X daily closes from Yahoo and upsert into ``fx_prices``.

    Never touches any other table: creates ``fx_prices`` with ``CREATE TABLE
    IF NOT EXISTS`` if absent, then performs the one upsert via the sealed
    ``_upsert`` helper (explicit register/unregister in try/finally — never
    switch this to the implicit ``FROM df`` replacement scan, which triggers
    a DuckDB heap-corruption bug).
    """
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    print(
        f"[refresh] fetching Yahoo {_FX_SYMBOL} daily closes, "
        f"{_iso(FX_START_MS)} -> {_iso(end_ms)}",
        file=sys.stderr,
    )
    fx = fetch_yahoo_daily(_FX_SYMBOL, FX_START_MS, end_ms)
    print(f"[refresh]   {_FX_SYMBOL}: {len(fx)} daily closes", file=sys.stderr)
    payload = fx.assign(symbol=_FX_SYMBOL)[["symbol", "open_time", "close"]]

    with duckdb.connect(str(db)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fx_prices (
                symbol    TEXT   NOT NULL,
                open_time BIGINT NOT NULL,
                close     DOUBLE NOT NULL,
                PRIMARY KEY (symbol, open_time)
            )
            """
        )
        _upsert(conn, payload, "fx_prices", "symbol, open_time, close")
    print(f"[refresh] upserted {len(payload)} rows into fx_prices", file=sys.stderr)


# --------------------------------------------------------------------------- #
# read-only loaders                                                           #
# --------------------------------------------------------------------------- #


def load_fx_daily(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute(
        "SELECT open_time, close FROM fx_prices WHERE symbol = ? ORDER BY open_time",
        [_FX_SYMBOL],
    ).df()


def load_btc_daily(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute(
        "SELECT open_time, close FROM ohlcv "
        "WHERE symbol = 'BTCUSDT' AND timeframe = '1d' ORDER BY open_time"
    ).df()


def load_trades(conn: duckdb.DuckDBPyConnection, source: str) -> pd.DataFrame:
    return conn.execute(_TRADE_QUERIES[source]).df()


# --------------------------------------------------------------------------- #
# weekly state construction (analytics.fx_carry, unchanged)                   #
# --------------------------------------------------------------------------- #


def build_weekly_states(fx_daily: pd.DataFrame) -> pd.DataFrame:
    """FX daily closes -> ISO weeks, both axes labelled (spec Sec.4-5)."""
    weekly = build_weekly_from_daily(fx_daily)
    if weekly.empty:
        return weekly.assign(run_state=pd.Series(dtype=object)).assign(
            mag_state=pd.Series(dtype=object)
        )
    close = weekly["close"].astype("float64")
    return weekly.assign(
        run_state=label_run_states(yen_strength_runs(close)),
        mag_state=label_magnitude_states(close),
    )


def states_by_day_for_axis(
    weekly: pd.DataFrame, state_col: str, *, end_ms: int
) -> pd.Series:
    """The axis's state at every UTC day from the FX warm-up start through
    ``end_ms``, via :func:`expand_to_days` — the single causality primitive
    both panels below key off of ("known before day D 00:00 UTC", spec Sec.5).
    """
    start_day = FX_START_MS // _DAY_MS
    end_day = end_ms // _DAY_MS
    days = pd.Index(np.arange(start_day, end_day + 1, dtype="int64"), name="day")
    return expand_to_days(weekly, state_col, days)


# --------------------------------------------------------------------------- #
# forward panel: BTCUSDT daily log return / causal trailing 30d vol           #
# --------------------------------------------------------------------------- #


def vol_normalise(returns: pd.Series) -> pd.Series:
    """``r_t / sigma_{t-1}`` with a causal trailing ``VOL_WINDOW`` vol.

    The ``.shift(1)`` is the causality guarantee: today's return must not enter
    its own scaling denominator. Asserted by a perturbation test with a
    positive control — do not remove it as a warm-up convenience.
    """
    sigma = returns.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std(ddof=1).shift(1)
    return returns / sigma.replace(0.0, np.nan)


def build_forward_panel(btc: pd.DataFrame, states_by_day: pd.Series) -> pd.DataFrame:
    """One axis's long frame: ``day``, ``direction`` (constant ``"market"``),
    ``mean_r`` (vol-normalised BTC log return), ``state``.

    Called once per axis with that axis's own ``states_by_day`` (never a
    mixed frame) so ``build_state_cells``'s complement ("outside") slice for
    one axis's state never absorbs rows from the other axis — the same
    discipline H14's ``_build_cells`` uses when it collapses ``levels`` and
    ``changes`` into two separate frames before ever calling
    ``build_state_cells``.
    """
    df = btc.sort_values("open_time").reset_index(drop=True)
    log_close = df["close"].astype("float64").apply(np.log)
    ret = log_close.diff()
    z = vol_normalise(ret)
    day = pd.Index((df["open_time"].to_numpy() // _DAY_MS).astype("int64"))
    state = states_by_day.reindex(day).to_numpy()
    out = pd.DataFrame(
        {
            "day": day.to_numpy(),
            "direction": "market",
            "mean_r": z.to_numpy(),
            "state": state,
        }
    )
    return out.dropna(subset=["mean_r", "state"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# ledger panel: per-UTC-day mean R of backtest/live trades                    #
# --------------------------------------------------------------------------- #


def build_ledger_panel(trades: pd.DataFrame, states_by_day: pd.Series) -> pd.DataFrame:
    """One axis's day-collapsed ledger frame via the shared ``collapse_to_daily``
    (unchanged from H14 — its own one-day entry lag applies on top of the
    already-causal ``states_by_day``, so a trade sees a state that was
    knowable at least a full day before ``expand_to_days`` alone requires).
    """
    return collapse_to_daily(trades, states_by_day)


# --------------------------------------------------------------------------- #
# coverage + balance reporting (Step 6 gate — NOT optional)                   #
# --------------------------------------------------------------------------- #


def _coverage_header(name: str, day_min: int, day_max: int, n: int) -> str:
    return (
        f"[{name}] coverage: {_iso(day_min * _DAY_MS)} -> {_iso(day_max * _DAY_MS)} "
        f"({n} observations)."
    )


def _weekly_window(weekly: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    """FX weeks whose own calendar date falls inside the crypto-bounded audit
    window (spec Sec.3: "361 FX weeks, 2019-09-08 -> 2026-08-04").
    """
    btc_first = _iso(int(btc["open_time"].min()))
    btc_last = _iso(int(btc["open_time"].max()))
    return weekly[(weekly["end_date"] >= btc_first) & (weekly["end_date"] <= btc_last)]


def _run_balance_report(weekly: pd.DataFrame, btc: pd.DataFrame) -> str:
    """Weekly run-state balance over the crypto-bounded audit window — the
    spec Sec.3 sanity check (measured there as 82.0% / 10.0% / 8.0% over 361
    FX weeks). A big miss here means the tag pipeline is wrong and every
    verdict below is meaningless.
    """
    window = _weekly_window(weekly, btc)
    valid = window["run_state"].dropna()
    n = len(valid)
    if n == 0:
        return "[run balance] no labeled weeks in the crypto audit window"
    counts = valid.value_counts()
    parts = [
        f"{state}={int(counts.get(state, 0))} ({counts.get(state, 0) / n:.1%})"
        for state in (RUN_LE1, RUN_EQ2, RUN_GE3)
    ]
    return (
        f"[run balance] n={n} FX weeks: "
        + ", ".join(parts)
        + " -- spec Sec.3 measured 82.0% / 10.0% / 8.0%; a big miss means the "
        "tag pipeline is wrong, not that the sample is unusual."
    )


def _magnitude_balance_report(weekly: pd.DataFrame, btc: pd.DataFrame) -> str:
    window = _weekly_window(weekly, btc)
    valid = window["mag_state"].dropna()
    n = len(valid)
    if n == 0:
        return "[magnitude balance] no labeled weeks in the crypto audit window"
    counts = valid.value_counts()
    parts = [
        f"{state}={int(counts.get(state, 0))} ({counts.get(state, 0) / n:.1%})"
        for state in (YEN_STRONG, MAG_NEUTRAL, YEN_WEAK)
    ]
    return f"[magnitude balance] n={n} FX weeks: " + ", ".join(parts)


# --------------------------------------------------------------------------- #
# cell diagnostics + table (recomputed with the SAME private helpers          #
# evaluate_states uses, per amendments.md A3 — not reimplemented)             #
# --------------------------------------------------------------------------- #


def _cell_diagnostics(
    cells: list[AuditCell], cell_verdicts: list[CellVerdict]
) -> dict[str, dict[str, float | None]]:
    """DSR / PBO / MinTRL per cell, reported against the exact verdicts the
    decision was made from.

    ``cell_verdicts`` is passed IN rather than recomputed: this used to call
    ``evaluate_audit_cells`` with arguments identical to the call its own
    caller had just made on the same list, so the duplicated bootstrap could
    only ever reproduce the same answer. Threading the caller's verdicts
    through upgrades the guarantee from "same inputs" to "same object".
    """
    if not cells:
        return {}
    by_family: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(cells):
        by_family.setdefault(carry_family_key(c.label), []).append(i)

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
        family_idx = by_family[carry_family_key(cell.label)]
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
    verdict: str


def _build_rows(cells: list[AuditCell], *, bar: float) -> list[_Row]:
    if not cells:
        return []
    cell_verdicts = evaluate_audit_cells(cells, bar=bar, alpha=ALPHA, min_n=MIN_N)
    verdict_by_label = dict(evaluate_states(cells, carry_family_key, bar=bar))
    diagnostics = _cell_diagnostics(cells, cell_verdicts)

    rows: list[_Row] = []
    for cell, cv in zip(cells, cell_verdicts, strict=True):
        axis, direction = carry_family_key(cell.label)
        state = cell.label.split("|", 1)[0]
        diag = diagnostics[cell.label]
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
                stable=sign_agrees_early_late(list(cell.supp_r)),
                verdict=verdict_by_label[cell.label],
            )
        )
    return rows


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
        "| axis | state | dir | n_days | mean | ci_lo | ci_hi | adj_p | dsr | pbo "
        "| mintrl | stable | verdict |"
    )
    sep = "| --- " * 13 + "|"
    lines = [header, sep]
    for r in sorted(rows, key=_sort_key):
        lines.append(
            f"| {r.axis} | {r.state} | {r.direction} | {r.n_days} | {_fmt(r.mean_r)} "
            f"| {_fmt(r.ci_lo)} | {_fmt(r.ci_hi)} | {_fmt(r.adj_p)} | {_fmt(r.dsr)} "
            f"| {_fmt(r.pbo)} | {_fmt(r.mintrl)} | {r.stable} | {r.verdict} |"
        )
    return "\n".join(lines)


def _low_n_warnings(rows: list[_Row], min_n: int) -> list[str]:
    """Display-only sanity check against ``--min-n`` — never alters the
    pre-committed gate itself (that stays frozen at ``MIN_N``)."""
    return [
        f"[warn] {r.axis}/{r.state}/{r.direction}: n_days={r.n_days} < --min-n={min_n}"
        for r in rows
        if r.n_days < min_n
    ]


def _headline(rows: list[_Row]) -> str:
    build = [f"{r.axis}/{r.state}/{r.direction}" for r in rows if r.verdict == "BUILD"]
    avoid = [f"{r.axis}/{r.state}/{r.direction}" for r in rows if r.verdict == "AVOID"]
    if not build and not avoid:
        return "NO-EDGE / INSUFFICIENT across every pre-registered cell."
    parts = []
    if build:
        parts.append("BUILD: " + ", ".join(build))
    if avoid:
        parts.append("AVOID: " + ", ".join(avoid))
    return " | ".join(parts)


def _print_section(
    title: str, cells: list[AuditCell], *, bar: float, min_n: int
) -> None:
    if not cells:
        print(f"\n=== {title} ===\n(no cells survived the day-collapse)")
        return
    rows = _build_rows(cells, bar=bar)
    print(f"\n=== {title} (bar={bar}) ===")
    print(f"headline: {_headline(rows)}")
    for line in _low_n_warnings(rows, min_n):
        print(line)
    print(_render_table(rows))
    print(_EFFECTIVE_FINDING_NOTE)


# --------------------------------------------------------------------------- #
# risk descriptives — printed, never gated (spec Sec.6: gating them would     #
# silently enlarge the family)                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _RiskRow:
    axis: str
    state: str
    n: int
    vol: float
    downside_semidev: float
    worst_day: float


def build_risk_rows(
    btc: pd.DataFrame, states_by_axis: dict[str, pd.Series]
) -> list[_RiskRow]:
    """Per-state realized vol / downside semideviation / worst single day on
    the RAW (not vol-normalised) BTC daily log return — descriptive only.
    """
    df = btc.sort_values("open_time").reset_index(drop=True)
    log_close = df["close"].astype("float64").apply(np.log)
    ret = log_close.diff()
    day = pd.Index((df["open_time"].to_numpy() // _DAY_MS).astype("int64"))

    rows: list[_RiskRow] = []
    for axis_name, states_by_day in states_by_axis.items():
        state = states_by_day.reindex(day).to_numpy()
        frame = pd.DataFrame({"ret": ret.to_numpy(), "state": state}).dropna()
        for label in sorted(frame["state"].unique()):
            arr = frame.loc[frame["state"] == label, "ret"].to_numpy(dtype=np.float64)
            if arr.shape[0] < 2:
                continue
            downside = arr[arr < 0.0]
            semidev = float(np.sqrt(np.mean(downside**2))) if downside.size else 0.0
            rows.append(
                _RiskRow(
                    axis=axis_name,
                    state=str(label),
                    n=int(arr.shape[0]),
                    vol=float(np.std(arr, ddof=1)),
                    downside_semidev=semidev,
                    worst_day=float(np.min(arr)),
                )
            )
    return rows


def _render_risk_table(rows: list[_RiskRow]) -> str:
    header = "| axis | state | n | vol(daily) | downside_semidev | worst_day |"
    sep = "| --- " * 6 + "|"
    lines = [header, sep]
    for r in sorted(rows, key=lambda row: (row.axis, row.state)):
        lines.append(
            f"| {r.axis} | {r.state} | {r.n} | {r.vol:.4f} | {r.downside_semidev:.4f} "
            f"| {r.worst_day:.4f} |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "H15: does a USD/JPY yen-strength state (consecutive down-weeks, "
            "or z-scored 4-week return magnitude) condition BTCUSDT forward "
            "returns or this system's ledger avg_r?"
        )
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument("--source", choices=["forward", "ledger", "both"], default="both")
    p.add_argument(
        "--min-n",
        type=int,
        default=MIN_N,
        help=(
            f"display-only day-count sanity-check threshold (default {MIN_N}). "
            f"The pre-committed gate itself is frozen at min_n={MIN_N} "
            "(spec Sec.7) and is NOT altered by this flag."
        ),
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "fetch fresh JPY=X daily closes and upsert into fx_prices before "
            "auditing (the only write this tool ever performs), then reopen "
            "read-only"
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
        refresh_fx_prices(args.db)

    now_ms = int(datetime.now(UTC).timestamp() * 1000)

    with duckdb.connect(str(args.db), read_only=True) as conn:
        try:
            fx_daily = load_fx_daily(conn)
        except duckdb.CatalogException:
            fx_daily = pd.DataFrame(columns=["open_time", "close"])
        btc = load_btc_daily(conn)
        trades_by_source = (
            {src: load_trades(conn, src) for src in ("backtest", "live")}
            if args.source in ("ledger", "both")
            else {}
        )

    if fx_daily.empty:
        print(
            "[error] fx_prices is empty -- run with --refresh first",
            file=sys.stderr,
        )
        return 1
    if btc.empty:
        print(
            "[error] no BTCUSDT 1d OHLCV -- run analytics sync first", file=sys.stderr
        )
        return 1

    weekly = build_weekly_states(fx_daily)

    print(
        "\nH15 USD/JPY carry-unwind state audit -- does yen strength condition "
        "forward crypto returns or this system's ledger avg_r?"
    )
    print(f"source={args.source}")
    print(
        _coverage_header(
            "fx JPY=X",
            int(fx_daily["open_time"].min()) // _DAY_MS,
            int(fx_daily["open_time"].max()) // _DAY_MS,
            len(fx_daily),
        )
    )
    print(
        _coverage_header(
            "btc 1d",
            int(btc["open_time"].min()) // _DAY_MS,
            int(btc["open_time"].max()) // _DAY_MS,
            len(btc),
        )
    )
    print(_run_balance_report(weekly, btc))
    print(_magnitude_balance_report(weekly, btc))
    print(
        "\nPre-committed semantics (spec Sec.7-8): BUILD = this state's "
        "observations are reliably POSITIVE and clear the family gate "
        "(bootstrap CI clears +/-bar, Holm-adj p<0.05, n>=MinTRL(0.95), "
        "family DSR>=0.95, family PBO<=0.5, early/late sign agreement) -- "
        "audit_guard DISABLE, sign-inverted. AVOID = reliably NEGATIVE and "
        "clears the same gate -- audit_guard ENABLE (thesis-confirming). "
        "NO-EDGE = well-powered but clears neither side. INSUFFICIENT = "
        f"n < {MIN_N}. The forward panel (BTCUSDT vol-normalised return) is "
        "PRIMARY/gate-deciding; the ledger panel is secondary and inherits "
        "the frozen 22-detector family."
    )

    states_by_axis: dict[str, pd.Series] = {
        axis: states_by_day_for_axis(weekly, col, end_ms=now_ms) for axis, col in _AXES
    }

    if args.source in ("forward", "both"):
        cells: list[AuditCell] = []
        for axis, _col in _AXES:
            panel = build_forward_panel(btc, states_by_axis[axis])
            cells += build_state_cells(panel)
        _print_section(
            "panel: forward (BTCUSDT vol-normalised daily return)",
            cells,
            bar=BAR_VOL,
            min_n=args.min_n,
        )

    if args.source in ("ledger", "both"):
        for src in ("backtest", "live"):
            trades = trades_by_source[src]
            title = f"panel: ledger / source: {src} ({len(trades)} trades loaded)"
            if trades.empty:
                print(f"\n=== {title} ===\n(no trades for this source)")
                continue
            ledger_cells: list[AuditCell] = []
            for axis, _col in _AXES:
                panel = build_ledger_panel(trades, states_by_axis[axis])
                ledger_cells += build_state_cells(panel)
            _print_section(title, ledger_cells, bar=BAR_LEDGER, min_n=args.min_n)

    if args.source in ("forward", "both"):
        print("\n--- Risk descriptives (descriptive, not gated) ---")
        risk_rows = build_risk_rows(btc, states_by_axis)
        print(_render_risk_table(risk_rows))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
