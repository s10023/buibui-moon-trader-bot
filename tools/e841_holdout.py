"""E841 — hedged violent-up continuation, out of universe (#948).

Implements ``docs/superpowers/specs/2026-10-08-e841-violent-up-holdout-preregistration.md``
§3-§5 and nothing else. The spec is FROZEN: a change to the construction, the gate
or the kill-switches after a holdout number is read is a new trial, not a fix.

Run order is the spec's, and the code enforces it rather than trusting the reader:

1. **K2 fidelity** — the raw trigger on the 25 universe perps must reproduce #920
   (1,079 symbol-days, 506 dates, +5.745% vs +1.146%). A difference stops the run
   unless ``--k2-explained`` records why.
2. **K1 power** — n and sd of the holdout book-day series, the holdout's n_eff
   (disclosure only), the in-universe hedged yardstick and ``tools/distil_power.py``
   at N = 3 with no ``--n-eff``. Printed before any mean of the holdout series is
   computed. A kill stops the run there and writes no series.
3. **Gate** — DSR at N = 3 and the block-bootstrap lower bound on E841's own
   series. PBO runs across round 1's columns, which do not exist yet, so it is
   reported PENDING and the book-day series is written for that later step.

Two readings of §3 are fixed here, before any holdout bar was read:

- **Held bars are D+1 … D+5.** A trigger on any of them, D+5 included, is skipped
  ("a symbol that triggers while already held is skipped").
- **Daily P&L is mark-to-market on entry notional** (§4: "each row is one day's
  mark-to-market P&L"). Both legs hold a fixed quantity from the D+1 open, so a
  leg's P&L on bar t is ``(C_t − C_{t−1}) / O_{D+1}`` (``O_{D+1}`` in place of
  ``C_{t−1}`` on D+1). The hedge basket is the mean of its components' such terms,
  each component sized equally AT ENTRY and never rebalanced. A position's daily
  rows therefore sum to ``N × (long 5d return − basket 5d return)`` before cost.

The 2026-10-08 bar must be CLOSED: ``sync`` stores the forming bar and re-fetches
it on the next run, so the driver refuses a panel in which any symbol lacks a bar
dated after the sample end.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

# Runnable as a bare script (`python tools/e841_holdout.py`), not only with
# PYTHONPATH=. — `tests/test_e841_holdout.py::test_bare_invocation_works` is the
# guarantee, not this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import numpy.typing as npt  # noqa: E402
import pandas as pd  # noqa: E402

from analytics.audit_guard import powered_null  # noqa: E402
from analytics.backtest_config import load_backtest_config  # noqa: E402
from analytics.forecast import effective_independent_series  # noqa: E402
from analytics.research_guards import (  # noqa: E402
    GATE_PBO,
    ann_sharpe,
    block_bootstrap_ci,
    deflated_sharpe_ratio,
    passes_gate,
    per_period_sharpe,
    required_sharpe,
)
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.universe import load_universe  # noqa: E402
from tools import distil_power  # noqa: E402

SPEC_PATH = Path(
    "docs/superpowers/specs/2026-10-08-e841-violent-up-holdout-preregistration.md"
)
COSTS_PATH = Path("config/strategy_params.toml")
VENUE = "binance"

# --- §3, frozen ---------------------------------------------------------------
SAMPLE_END = date(2026, 10, 8)
RET_MIN = 0.05
Z_MIN = 2.5
SD_WINDOW = 20
LISTING_SKIP_DAYS = 60
LIQ_WINDOW = 30
LIQ_FLOOR_USD = 5_000_000.0
HOLD_BARS = 5
HOLDOUT_SIZE = 402

# --- §4/§5 ---------------------------------------------------------------------
N_TRIALS = 3
SR_VARIANCE_FLOOR = 0.005
BOOT_BLOCK = HOLD_BARS
BOOT_SEED = 7  # the sleeves' seed (analytics/xsmom/report.py)
PERIODS_PER_YEAR = 365.0


@dataclass(frozen=True)
class K2Target:
    symbol_days: int = 1079
    dates: int = 506
    event_mean_pct: float = 5.745
    baseline_pct: float = 1.146


@dataclass(frozen=True)
class K2Result:
    symbol_days: int
    dates: int
    event_mean_pct: float
    baseline_pct: float

    def matches(self, target: K2Target) -> bool:
        """Counts exact; means at #920's printed precision (3 decimals)."""
        return (
            self.symbol_days == target.symbol_days
            and self.dates == target.dates
            and round(self.event_mean_pct, 3) == target.event_mean_pct
            and round(self.baseline_pct, 3) == target.baseline_pct
        )


@dataclass
class Position:
    symbol: str
    trigger_date: date
    sigma20: float
    notional: float
    hedge_n: int
    dates: list[date]
    gross_r: list[float]
    cost_r: list[float]

    @property
    def net_r(self) -> list[float]:
        return [g - c for g, c in zip(self.gross_r, self.cost_r, strict=True)]


@dataclass
class BuildStats:
    triggers: int = 0
    ineligible_listing: int = 0
    ineligible_liquidity: int = 0
    skipped_held: int = 0
    out_of_sample: int = 0
    positions: int = 0
    calendar_gaps: int = 0
    symbols_with_positions: int = 0
    notes: list[str] = field(default_factory=list)


# --- inputs ---------------------------------------------------------------------


def load_holdout_symbols(spec_path: Path = SPEC_PATH) -> list[str]:
    """Appendix A of the frozen spec — the one place the list lives."""
    text = spec_path.read_text(encoding="utf-8")
    block = text.split("## Appendix A", 1)[1].split("```text", 1)[1]
    block = block.split("```", 1)[0]
    syms = re.findall(r"[0-9A-Z]+USDT", block)
    if len(syms) != len(set(syms)):
        raise ValueError("Appendix A lists a symbol twice")
    return syms


@dataclass(frozen=True)
class Costs:
    fee_pct: float
    slippage_pct: float

    @property
    def per_leg_round_trip(self) -> float:
        """2(fee + slip): one leg's round trip, as a fraction of its notional."""
        return 2.0 * (self.fee_pct + self.slippage_pct)


def load_costs(path: Path = COSTS_PATH) -> Costs:
    """Production fee and slippage, read at run time and never restated."""
    cfg = load_backtest_config(path)
    return Costs(fee_pct=cfg.fee_pct, slippage_pct=cfg.slippage_pct)


def load_panel(
    conn: duckdb.DuckDBPyConnection, symbols: Sequence[str]
) -> dict[str, pd.DataFrame]:
    """1d bars per symbol from ``ohlcv_all`` on the pinned venue, oldest first."""
    df = conn.execute(
        """
        SELECT symbol, open_time, open, close, volume
        FROM ohlcv_all
        WHERE timeframe = '1d' AND venue = ? AND symbol IN (SELECT unnest(?))
        ORDER BY symbol, open_time
        """,
        [VENUE, list(symbols)],
    ).df()
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.date
    return {
        str(s): g.drop(columns=["symbol"]).reset_index(drop=True)
        for s, g in df.groupby("symbol", sort=True)
    }


def require_closed_and_cut(
    panel: dict[str, pd.DataFrame], sample_end: date = SAMPLE_END
) -> dict[str, pd.DataFrame]:
    """Refuse a panel whose sample-end bar may still be forming, then cut it there.

    ``sync`` re-fetches the last stored bar, so a bar dated after ``sample_end``
    proves the ``sample_end`` bar was overwritten after it closed.
    """
    unclosed = sorted(s for s, g in panel.items() if g["date"].max() <= sample_end)
    if unclosed:
        raise RuntimeError(
            f"{len(unclosed)} symbols have no bar after {sample_end}, so their "
            f"{sample_end} bar may be the forming one (first: {unclosed[:5]}). "
            "Sync 1d after it closes, then re-run."
        )
    return cut_at(panel, sample_end)


def cut_at(
    panel: dict[str, pd.DataFrame], sample_end: date = SAMPLE_END
) -> dict[str, pd.DataFrame]:
    """Drop every bar dated after ``sample_end``."""
    return {
        s: g[g["date"] <= sample_end].reset_index(drop=True) for s, g in panel.items()
    }


# --- §3 trigger and eligibility -------------------------------------------------


def signal_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Trigger and eligibility columns, each a function of bars ``<= D`` only.

    ``ret`` is row-based ``pct_change`` and ``sigma20`` the ddof-1 sd of the 20
    returns BEFORE D — exactly #920's ``rolling(20).std().shift(1)``.
    """
    out = df.copy()
    out["ret"] = out["close"].pct_change()
    out["sigma20"] = out["ret"].rolling(SD_WINDOW).std(ddof=1).shift(1)
    out["z"] = out["ret"] / out["sigma20"]
    out["trigger"] = (out["ret"] >= RET_MIN) & (out["z"] >= Z_MIN)
    first = out["date"].iloc[0] if len(out) else None
    out["days_listed"] = [(d - first).days for d in out["date"]] if first else []
    quote = out["volume"] * out["close"]
    out["quote_med30"] = quote.rolling(LIQ_WINDOW).median()
    out["listed_ok"] = out["days_listed"] >= LISTING_SKIP_DAYS
    out["liquid_ok"] = out["quote_med30"] >= LIQ_FLOOR_USD
    out["eligible"] = out["listed_ok"] & out["liquid_ok"] & out["sigma20"].notna()
    return out


# --- K2 -----------------------------------------------------------------------------


def k2_raw(panel: dict[str, pd.DataFrame]) -> K2Result:
    """The trigger run raw: no eligibility, no hedge, equal notional, close-to-close
    five-day return from D's close, per-date means — #920's measurement."""
    events: list[pd.DataFrame] = []
    base: list[pd.DataFrame] = []
    for g in panel.values():
        f = signal_frame(g)
        f["fwd5"] = f["close"].shift(-HOLD_BARS) / f["close"] - 1.0
        valid = f.dropna(subset=["fwd5"])
        base.append(valid[["date", "fwd5"]])
        events.append(valid.loc[valid["trigger"], ["date", "fwd5"]])
    ev = pd.concat(events)
    per_date = ev.groupby("date")["fwd5"].mean()
    baseline = pd.concat(base).groupby("date")["fwd5"].mean()
    return K2Result(
        symbol_days=len(ev),
        dates=len(per_date),
        event_mean_pct=float(per_date.mean() * 100.0),
        baseline_pct=float(baseline.mean() * 100.0),
    )


# --- §3 positions -----------------------------------------------------------------


@dataclass(frozen=True)
class HedgeBook:
    """Wide open/close frames of the hedge universe, indexed by calendar date."""

    opens: pd.DataFrame
    closes_ffill: pd.DataFrame

    @classmethod
    def from_panel(cls, panel: dict[str, pd.DataFrame]) -> HedgeBook:
        opens = pd.DataFrame(
            {s: g.set_index("date")["open"] for s, g in panel.items()}
        ).sort_index()
        closes = pd.DataFrame(
            {s: g.set_index("date")["close"] for s, g in panel.items()}
        ).sort_index()
        # A component with no bar on a held day (a calendar gap) carries its last
        # close, i.e. contributes no P&L that day. ffill never fills pre-listing.
        return cls(opens=opens, closes_ffill=closes.ffill())

    def basket_mtm(self, held: Sequence[date]) -> tuple[int, list[float]]:
        """Daily MTM of an equal-notional-at-entry, never-rebalanced basket.

        Members are the symbols with a bar at ``held[0]`` (D+1). Returns the
        member count and one term per held day, per unit of entry notional.
        """
        d1 = held[0]
        if d1 not in self.opens.index:
            return 0, [0.0] * len(held)
        entry = self.opens.loc[d1].dropna()
        cols = list(entry.index)
        if not cols:
            return 0, [0.0] * len(held)
        closes = self.closes_ffill.reindex(list(held))[cols]
        rel = closes.div(entry, axis=1)  # C_{j,t} / O_{j,D+1}
        value = rel.mean(axis=1).to_numpy(dtype=float)
        prev = np.concatenate([[1.0], value[:-1]])
        return len(cols), list(value - prev)


def build_positions(
    panel: dict[str, pd.DataFrame],
    hedge: HedgeBook,
    costs: Costs,
    *,
    sample_end: date = SAMPLE_END,
) -> tuple[list[Position], BuildStats]:
    """Every in-sample E841 position on ``panel``, hedged against ``hedge``."""
    stats = BuildStats()
    positions: list[Position] = []
    leg_cost = costs.per_leg_round_trip
    for sym, g in panel.items():
        f = signal_frame(g)
        held_through = -1  # last held bar index of the open position
        n_before = len(positions)
        for i in np.flatnonzero(f["trigger"].to_numpy(dtype=bool)):
            stats.triggers += 1
            row = f.iloc[i]
            if not bool(row["listed_ok"]):
                stats.ineligible_listing += 1
                continue
            if not (bool(row["liquid_ok"]) and bool(pd.notna(row["sigma20"]))):
                stats.ineligible_liquidity += 1
                continue
            if i <= held_through:
                stats.skipped_held += 1
                continue
            last = i + HOLD_BARS
            if last >= len(f) or f["date"].iloc[last] > sample_end:
                stats.out_of_sample += 1
                continue
            held = list(f["date"].iloc[i + 1 : last + 1])
            if (held[-1] - held[0]).days != HOLD_BARS - 1:
                stats.calendar_gaps += 1
            entry_open = float(f["open"].iloc[i + 1])
            closes = f["close"].iloc[i + 1 : last + 1].to_numpy(dtype=float)
            prev = np.concatenate([[entry_open], closes[:-1]])
            long_mtm = (closes - prev) / entry_open
            hedge_n, basket = hedge.basket_mtm(held)
            if hedge_n == 0:
                stats.notes.append(f"{sym} {row['date']}: no hedge member at D+1")
            sigma = float(row["sigma20"])
            notional = 1.0 / sigma
            gross = [
                notional * (lg - hb) for lg, hb in zip(long_mtm, basket, strict=True)
            ]
            cost = [0.0] * HOLD_BARS
            # Each leg pays 2(fee+slip)·N per round trip: half on entry, half on exit.
            cost[0] += leg_cost * notional  # long + hedge entry
            cost[-1] += leg_cost * notional  # long + hedge exit
            positions.append(
                Position(
                    symbol=sym,
                    trigger_date=row["date"],
                    sigma20=sigma,
                    notional=notional,
                    hedge_n=hedge_n,
                    dates=held,
                    gross_r=gross,
                    cost_r=cost,
                )
            )
            held_through = int(last)
        if len(positions) > n_before:
            stats.symbols_with_positions += 1
    stats.positions = len(positions)
    return positions, stats


def book_days(positions: Sequence[Position]) -> pd.DataFrame:
    """One row per UTC day with an open position: mean net R across those open."""
    rows = [(d, r) for p in positions for d, r in zip(p.dates, p.net_r, strict=True)]
    if not rows:
        return pd.DataFrame(columns=["net_r", "n_open"])
    df = pd.DataFrame(rows, columns=["date", "r"])
    g = df.groupby("date")["r"]
    out = pd.DataFrame({"net_r": g.mean(), "n_open": g.size()})
    return out.sort_index()


# --- K1 ---------------------------------------------------------------------------


def holdout_n_eff(panel: dict[str, pd.DataFrame]) -> tuple[float, float]:
    """``effective_independent_series`` over per-symbol daily returns (disclosure)."""
    rets = {s: g.set_index("date")["close"].pct_change() for s, g in panel.items()}
    return effective_independent_series(rets)


def required_annual_sharpe(n_obs: int, sr_variance: float) -> float:
    """The annualised Sharpe the DSR leg needs — the number distil_power prints."""
    sr = required_sharpe(n_obs, n_trials=N_TRIALS, sr_variance=sr_variance)
    return sr * math.sqrt(PERIODS_PER_YEAR)


def breakeven_sr_variance(n_obs: int, yardstick: float) -> float | None:
    """The ``--sr-variance`` at which the required Sharpe meets the yardstick.

    Disclosure only: K1 is priced at ``max(measured, floor)``, and the measured
    variance across round 1's columns can only RAISE the bar. ``None`` when even
    the floor fails, ``inf`` when no variance below 10 per obs reaches it.
    """
    if required_annual_sharpe(n_obs, SR_VARIANCE_FLOOR) > yardstick:
        return None
    lo, hi = SR_VARIANCE_FLOOR, 10.0
    if required_annual_sharpe(n_obs, hi) <= yardstick:
        return math.inf
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        if required_annual_sharpe(n_obs, mid) <= yardstick:
            lo = mid
        else:
            hi = mid
    return lo


def distil_power_argv(n_obs: int, sd: float, sr_variance: float) -> list[str]:
    """§5's K1 invocation, verbatim: no ``--n-eff``."""
    return [
        "--units",
        "per_book_day",
        "--sr-footing",
        "per_obs",
        "--periods-per-year",
        f"{PERIODS_PER_YEAR:g}",
        "--n-obs",
        str(n_obs),
        "--n-trials",
        str(N_TRIALS),
        "--sd",
        repr(sd),
        "--sr-variance",
        repr(sr_variance),
    ]


# --- gate -------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    n: int
    mean_r: float
    sd_r: float
    sharpe_per_obs: float
    sharpe_annual: float
    sr_variance: float
    dsr: float
    boot_lo: float
    boot_hi: float
    mean_ci_lo: float
    mean_ci_hi: float
    required_effect_r: float
    powered_null: bool
    legs_clear_if_pbo_passes: bool


def gate(r: npt.NDArray[np.float64], sr_variance: float) -> GateResult:
    """DSR at N = 3 and boot_lo on E841's own series; PBO is not computed here."""
    ann = math.sqrt(PERIODS_PER_YEAR)
    sr = per_period_sharpe(r)

    def _ann(x: npt.NDArray[np.float64]) -> float:
        return ann_sharpe(x, ann)

    def _mean(x: npt.NDArray[np.float64]) -> float:
        return float(np.mean(x))

    boot = block_bootstrap_ci(r, _ann, block=BOOT_BLOCK, seed=BOOT_SEED)
    mean_ci = block_bootstrap_ci(r, _mean, block=BOOT_BLOCK, seed=BOOT_SEED)
    dsr = deflated_sharpe_ratio(sr, len(r), n_trials=N_TRIALS, sr_variance=sr_variance)
    sd = float(np.std(r, ddof=1))
    bar = required_sharpe(len(r), n_trials=N_TRIALS, sr_variance=sr_variance) * sd
    return GateResult(
        n=len(r),
        mean_r=float(np.mean(r)),
        sd_r=sd,
        sharpe_per_obs=sr,
        sharpe_annual=sr * ann,
        sr_variance=sr_variance,
        dsr=dsr,
        boot_lo=boot.lo,
        boot_hi=boot.hi,
        mean_ci_lo=mean_ci.lo,
        mean_ci_hi=mean_ci.hi,
        required_effect_r=bar,
        powered_null=powered_null(mean_ci.lo, mean_ci.hi, bar=bar),
        # PBO is PENDING (round 1's other columns do not exist yet). Evaluated at
        # the ceiling itself, this asks "do the two legs E841 owns clear?"
        legs_clear_if_pbo_passes=passes_gate(dsr, GATE_PBO, boot.lo),
    )


# --- driver -----------------------------------------------------------------------


def _fmt_k2(k: K2Result | K2Target) -> str:
    return (
        f"{k.symbol_days:,} symbol-days, {k.dates} dates, "
        f"{k.event_mean_pct:+.3f}% per-date mean vs {k.baseline_pct:+.3f}% baseline"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="e841_holdout", description=__doc__.split("\n")[0])
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path (read-only)")
    p.add_argument("--out-dir", default=None, help="write series CSV + JSON here")
    p.add_argument(
        "--sr-variance-measured",
        type=float,
        default=None,
        help="measured Sharpe variance across round 1's columns, once they exist",
    )
    p.add_argument(
        "--k2-explained",
        default=None,
        help="the explanation of a K2 difference; without it a difference stops the run",
    )
    p.add_argument(
        "--k2-only", action="store_true", help="run K2 and stop (reads no holdout bar)"
    )
    return p.parse_args(argv)


def run(
    argv: Sequence[str] | None = None, *, emit: Callable[[str], None] = print
) -> int:
    args = parse_args(argv)
    universe = load_universe()
    holdout = load_holdout_symbols()
    if len(holdout) != HOLDOUT_SIZE:
        raise ValueError(f"Appendix A holds {len(holdout)} symbols, not {HOLDOUT_SIZE}")
    if overlap := sorted(set(holdout) & set(universe)):
        raise ValueError(f"holdout overlaps the universe: {overlap}")
    costs = load_costs()
    conn = duckdb.connect(args.db, read_only=True)
    order = conn.execute(
        "SELECT value FROM db_meta WHERE key = 'read_venue_order'"
    ).fetchone()
    emit(f"E841 holdout driver - db {args.db}, venue {VENUE} (read order {order})")
    emit(
        f"costs from {COSTS_PATH}: fee_pct {costs.fee_pct}, slippage_pct "
        f"{costs.slippage_pct} -> 2(fee+slip) {costs.per_leg_round_trip:.6f} per leg"
    )

    # ---- K2 ------------------------------------------------------------------
    raw_uni = load_panel(conn, universe)
    if args.k2_only:
        # #920 ran on 2026-10-08 against the FORMING 2026-10-08 bar, so a K2-only
        # diagnostic reproduces that state; it reads no holdout bar.
        uni_panel = cut_at(raw_uni)
        emit("K2-only: the 2026-10-08 bar is NOT verified closed (#920's own state)")
    else:
        uni_panel = require_closed_and_cut(raw_uni)
    k2 = k2_raw(uni_panel)
    target = K2Target()
    emit("")
    emit("K2 fidelity (raw trigger, 25 universe perps, cut at the 2026-10-08 bar)")
    emit(f"  measured  {_fmt_k2(k2)}")
    emit(f"  #920      {_fmt_k2(target)}")
    k2_ok = k2.matches(target)
    emit(f"  K2        {'REPRODUCED' if k2_ok else 'DIFFERS'}")
    if not k2_ok:
        if args.k2_explained is None:
            emit(
                "  STOP: explain the difference (--k2-explained) before anything runs."
            )
            return 3
        emit(f"  explained: {args.k2_explained}")
    emit("  truncation test: tests/test_e841_holdout.py::TestTruncation")
    if args.k2_only:
        return 0

    # ---- holdout positions (no mean read yet) -----------------------------------
    raw_hold = load_panel(conn, holdout)
    missing = sorted(set(holdout) - set(raw_hold))
    hold_panel = require_closed_and_cut(raw_hold)
    hedge = HedgeBook.from_panel(uni_panel)
    positions, st = build_positions(hold_panel, hedge, costs)
    series = book_days(positions)
    r = series["net_r"].to_numpy(dtype=float)
    emit("")
    emit(f"Holdout: {len(hold_panel)} of {HOLDOUT_SIZE} symbols with bars")
    emit(f"  no bars (dropped): {missing if missing else 'none'}")
    emit(
        f"  triggers {st.triggers:,} | listing<60d {st.ineligible_listing:,} | "
        f"liquidity<$5M {st.ineligible_liquidity:,} | skipped while held "
        f"{st.skipped_held:,} | exit after sample end {st.out_of_sample:,}"
    )
    emit(
        f"  positions {st.positions:,} on {st.symbols_with_positions} symbols | "
        f"held windows spanning a calendar gap {st.calendar_gaps}"
    )
    for note in st.notes[:10]:
        emit(f"  note: {note}")

    # ---- K1 ------------------------------------------------------------------------
    n_obs = len(r)
    if n_obs < 2:
        emit("K1: fewer than 2 book-days - KILLED (unpriceable)")
        return 0
    sd = float(np.std(r, ddof=1))
    n_eff, deflator = holdout_n_eff(hold_panel)
    measured = args.sr_variance_measured
    sr_var = (
        max(measured, SR_VARIANCE_FLOOR) if measured is not None else SR_VARIANCE_FLOOR
    )
    yard_pos, _ = build_positions(uni_panel, hedge, costs)
    yard = book_days(yard_pos)["net_r"].to_numpy(dtype=float)
    yardstick = per_period_sharpe(yard) * math.sqrt(PERIODS_PER_YEAR)
    req = required_annual_sharpe(n_obs, sr_var)
    emit("")
    emit("K1 power (printed before any mean of the holdout series)")
    emit(f"  holdout book-days n {n_obs:,} | sd {sd:.6f} R")
    emit(
        f"  holdout n_eff {n_eff:.3f} (deflator {deflator:.3f}) - disclosure only, "
        "not passed to distil_power"
    )
    emit(
        f"  sr_variance {sr_var} = max(measured "
        f"{'NOT YET MEASURABLE (one column)' if measured is None else measured}, "
        f"{SR_VARIANCE_FLOOR})"
    )
    emit(
        f"  yardstick: s3 on the 25 universe perps, {len(yard_pos):,} positions, "
        f"{len(yard):,} book-days, annualised Sharpe {yardstick:+.4f}"
    )
    emit("  tools/distil_power.py " + " ".join(distil_power_argv(n_obs, sd, sr_var)))
    for line in distil_power.price(
        distil_power.parse_args(distil_power_argv(n_obs, sd, sr_var))
    ):
        emit("    " + line)
    be = breakeven_sr_variance(n_obs, yardstick) if yardstick > 0 else None
    emit(f"  required annualised Sharpe {req:.4f} vs yardstick {yardstick:+.4f}")
    if be is not None:
        emit(
            f"  break-even sr_variance {be:.5f} (a measured variance above it kills K1)"
        )
    k1_ok = yardstick > 0 and req <= yardstick
    emit(f"  K1        {'PASS' if k1_ok else 'KILLED'}")

    summary: dict[str, object] = {
        "k2": asdict(k2),
        "k2_reproduced": k2_ok,
        "k2_explained": args.k2_explained,
        "holdout_missing": missing,
        "build": asdict(st),
        "k1": {
            "n": n_obs,
            "sd": sd,
            "n_eff": n_eff,
            "deflator": deflator,
            "sr_variance": sr_var,
            "sr_variance_measured": measured,
            "yardstick_annual_sharpe": yardstick,
            "yardstick_positions": len(yard_pos),
            "yardstick_book_days": len(yard),
            "required_annual_sharpe": req,
            "breakeven_sr_variance": be,
            "pass": k1_ok,
        },
    }
    out_dir = Path(args.out_dir) if args.out_dir else None
    if not k1_ok:
        emit(
            "VERDICT: KILLED at K1 - a failure to clear, never a null. Slot stays empty."
        )
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "e841_summary.json").write_text(
                json.dumps(summary, indent=2, default=str), encoding="utf-8"
            )
        return 0

    # ---- gate ----------------------------------------------------------------------
    g = gate(r, sr_var)
    emit("")
    emit("Gate (E841's own series; PBO across round 1's columns is PENDING)")
    emit(
        f"  mean {g.mean_r:+.5f} R/book-day | Sharpe {g.sharpe_per_obs:+.5f} per obs "
        f"= {g.sharpe_annual:+.4f} annualised"
    )
    emit(f"  DSR (N={N_TRIALS}, sr_variance {g.sr_variance}) {g.dsr:.4f}")
    emit(
        f"  boot (block {BOOT_BLOCK}, ann. Sharpe) [{g.boot_lo:+.4f}, {g.boot_hi:+.4f}]"
        f" | mean CI [{g.mean_ci_lo:+.5f}, {g.mean_ci_hi:+.5f}] R"
    )
    emit(
        f"  powered_null at bar +/-{g.required_effect_r:.5f} R: "
        f"{'LICENSED' if g.powered_null else 'NOT LICENSED'}"
    )
    emit(
        "  E841's legs (DSR, boot_lo) "
        f"{'CLEAR - verdict waits on PBO' if g.legs_clear_if_pbo_passes else 'FAIL'}"
    )
    summary["gate"] = asdict(g)
    summary["pbo"] = "PENDING"
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        series.to_csv(out_dir / "e841_bookday.csv", index_label="date")
        (out_dir / "e841_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        emit(f"  wrote {out_dir / 'e841_bookday.csv'} and e841_summary.json")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
