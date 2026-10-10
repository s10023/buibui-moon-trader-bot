"""E850 — short a 20-day breakout made on rising open interest (#850).

Implements ``docs/superpowers/specs/2026-10-09-e850-oi-breakout-flush-preregistration.md``
§3-§6 and nothing else. The spec is FROZEN: a change to the construction, the gate
or the kill-switches after an E850 number is read is a new trial, not a fix.

Run order is the spec's, and the code enforces it rather than trusting the reader:

1. **K2 fidelity** — (3) every hourly |Δ ln ``oi_contracts``| > ln 3 is listed and
   classified before any decision is built; (1) the truncation test re-derives the
   treatment and control flags at real decision days from inputs cut at D's close
   (the fixture half lives in ``tests/test_e850_oi_breakout_flush.py``); (2) event
   counts per symbol and per year, and treatment ⊆ control event by event.
2. **K1 power** — the treatment's book-day n and sd, the Sharpe variance across the
   columns and ``tools/distil_power.py`` at N = 3 with no ``--n-eff``. Printed before
   any mean of either column. A required Sharpe above 2.5 annualised kills it.
3. **Gate** — DSR at N = 3 and boot_lo on E850's own book-day series, PBO across
   E850 and the control on the union of UTC calendar days (0R when idle, #921).
4. **§6 readings** — powered null, beta guard, XS overlap, attribution, then the
   verdict. Funding is printed beside, never inside, the gated series.

Readings of the spec fixed here, before any E850 return was read:

- **The archive's 23:00 row is a snapshot taken at ~22:55 UTC** (``analytics/oi_archive.py``:
  the row stamped ``T`` is the last 5-minute snapshot at or before ``T − 5 min``), so it
  precedes D's close and §10's "stamp marks the end of the interval" reversal does
  not apply.
- **A zero ``oi_contracts`` row is a missing row.** The archive writes 0 contracts AND
  0 USD as a placeholder (14 of 14 symbols on 2022-03-07 16:00 → 03-08 01:00, all
  present symbols on 2023-11-23/26, 2025-04-11/15, 2025-07-21, plus isolated hours),
  and ``ln 0`` is not a reading. It is skipped like any missing row, never filled.
- **"Nothing is decided on D" binds both columns**: a day without OI_D and OI_{D−5}
  carries no control decision either. The control still decides on a day whose
  180-day window holds fewer than 90 values; the treatment does not.
- **The percentile is pandas' linear rolling quantile** over the 180 calendar days
  D−180 … D−1 (NaNs skipped), which equals ``numpy.percentile``'s default.
- **Held bars are D+1 … D+5; a trigger on any of them, D+5 included, is skipped** —
  E841's reading of the same rule.
- **K1's variance spans E850 and the control**, the round-2 columns that exist
  (#983 is open), each on its own book-day series, ddof 1. It is printed without
  either column's mean, and the gate's DSR uses the same value.
- **An unexplained jump blocks decision D** when it falls in (OI stamp of D−185,
  OI stamp of D] — every OI row that feeds g_D or its 180-day window.
- **The XS book** is ``replay_xs`` over the full 25-symbol universe (the deploy core
  as it runs), costs from ``config/strategy_params.toml``.
- **Attribution's t** is Welch's over the two event sets. Treatment ⊂ control, so
  the true covariance is positive and Welch's SE is the larger one: the raw t is
  conservative.
- **Bootstrap** block 5 book-days (the hold), seed 7, the sleeves' seed.

The sample-end bar must be CLOSED: ``sync`` stores the forming bar, so the driver
refuses a panel in which any symbol lacks a bar dated after 2026-10-08.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Runnable as a bare script (`python tools/e850_oi_breakout_flush.py`), not only with
# PYTHONPATH=. — `tests/test_e850_oi_breakout_flush.py::test_bare_invocation_works`
# is the guarantee, not this line.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import numpy.typing as npt  # noqa: E402
import pandas as pd  # noqa: E402

from analytics.audit_guard import powered_null  # noqa: E402
from analytics.backtest_config import load_backtest_config  # noqa: E402
from analytics.forecast import effective_independent_series  # noqa: E402
from analytics.forecast.config import ForecastConfig  # noqa: E402
from analytics.research_guards import (  # noqa: E402
    ann_sharpe,
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    passes_gate,
    per_period_sharpe,
    required_sharpe,
)
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.oi_archive import ARCHIVE_SOURCE  # noqa: E402
from analytics.universe import load_universe  # noqa: E402
from analytics.xsmom import replay_xs  # noqa: E402
from analytics.xsmom.diagnostics import equal_weight_market_return  # noqa: E402
from tools import distil_power  # noqa: E402

SPEC_PATH = Path(
    "docs/superpowers/specs/2026-10-09-e850-oi-breakout-flush-preregistration.md"
)
COSTS_PATH = Path("config/strategy_params.toml")

# --- §3, frozen ---------------------------------------------------------------
SAMPLE_END = date(2026, 10, 8)
EXCLUDED = ("PAXGUSDT",)
PANEL_SIZE = 24
DONCHIAN = 20
SIGMA_WINDOW = 20
OI_LOOKBACK = 5
PCTL = 0.67
PCTL_WINDOW = 180
PCTL_MIN = 90
HOLD_BARS = 5
DAY_MS = 86_400_000
OI_STAMP_MS = 23 * 3_600_000  # OI_D: the row stamped 23:00 UTC of D
JUMP_LN = math.log(3.0)

# Non-zero jumps over ln 3 explained BEFORE the run (K2.3). Key (symbol, ms).
EXPLAINED_JUMPS: dict[tuple[str, int], str] = {
    ("ENAUSDT", 1_712_066_400_000): (
        "listing day: the archive's first row is 13:00 UTC on 2024-04-02 and OI "
        "triples in the first hour, oi_usd moving with it, so it is a listing ramp, "
        "not a redenomination"
    ),
}

# --- §4/§5 ---------------------------------------------------------------------
N_TRIALS = 3
SR_VARIANCE_FLOOR = 0.005
K1_MAX_ANNUAL_SHARPE = 2.5
PERIODS_PER_YEAR = 365.0
BOOT_BLOCK = HOLD_BARS
BOOT_SEED = 7  # the sleeves' seed (analytics/xsmom/report.py)
PBO_SPLITS = 14
RHO_MAX = 0.5
ATTRIBUTION_T = 1.96
TRUNCATION_STRIDE = 25  # every Nth decidable non-event day is also truncation-checked


@dataclass(frozen=True)
class Costs:
    fee_pct: float
    slippage_pct: float

    @property
    def round_trip(self) -> float:
        """2(fee + slip) per unit of notional, per round trip."""
        return 2.0 * (self.fee_pct + self.slippage_pct)


def load_costs(path: Path = COSTS_PATH) -> Costs:
    """Production fee and slippage, read at run time and never restated."""
    cfg = load_backtest_config(path)
    return Costs(fee_pct=cfg.fee_pct, slippage_pct=cfg.slippage_pct)


# --- inputs ---------------------------------------------------------------------


def connect(db: str) -> duckdb.DuckDBPyConnection:
    """Read-only connection pinned to UTC before any date cast (§3 Clock)."""
    conn = duckdb.connect(db, read_only=True)
    conn.execute("SET TimeZone='UTC'")
    return conn


def panel_symbols() -> list[str]:
    syms = [s for s in load_universe() if s not in EXCLUDED]
    if len(syms) != PANEL_SIZE:
        raise ValueError(f"panel holds {len(syms)} symbols, not {PANEL_SIZE}")
    return syms


def day_ms(d: date) -> int:
    """00:00 UTC of ``d`` in Unix ms."""
    return int(datetime.combine(d, datetime.min.time(), UTC).timestamp() * 1000)


def ms_to_date(ms: pd.Series) -> pd.Series:
    return pd.to_datetime(ms, unit="ms", utc=True).dt.date


def load_bars(
    conn: duckdb.DuckDBPyConnection, symbols: Sequence[str]
) -> dict[str, pd.DataFrame]:
    """1d bars per symbol through the ``ohlcv`` view, oldest first."""
    df = conn.execute(
        """
        SELECT symbol, open_time, open, high, low, close
        FROM ohlcv
        WHERE timeframe = '1d' AND symbol IN (SELECT unnest(?))
        ORDER BY symbol, open_time
        """,
        [list(symbols)],
    ).df()
    df["date"] = ms_to_date(df["open_time"])
    return {
        str(s): g.drop(columns=["symbol", "open_time"]).reset_index(drop=True)
        for s, g in df.groupby("symbol", sort=True)
    }


def load_hourly_oi(
    conn: duckdb.DuckDBPyConnection, symbols: Sequence[str]
) -> dict[str, pd.DataFrame]:
    """Every archive hourly row (zeros included) per symbol: timestamp, oi_contracts."""
    df = conn.execute(
        """
        SELECT symbol, timestamp, oi_contracts
        FROM open_interest_archive
        WHERE source = ? AND symbol IN (SELECT unnest(?))
        ORDER BY symbol, timestamp
        """,
        [ARCHIVE_SOURCE, list(symbols)],
    ).df()
    return {
        str(s): g.drop(columns=["symbol"]).reset_index(drop=True)
        for s, g in df.groupby("symbol", sort=True)
    }


def load_daily_funding(
    conn: duckdb.DuckDBPyConnection, symbols: Sequence[str]
) -> dict[str, pd.Series]:
    """Summed funding rate per UTC date (the rate a short receives, signed)."""
    df = conn.execute(
        """
        SELECT symbol, funding_time, funding_rate FROM funding_rates
        WHERE symbol IN (SELECT unnest(?))
        """,
        [list(symbols)],
    ).df()
    df["date"] = ms_to_date(df["funding_time"])
    return {
        str(s): g.groupby("date")["funding_rate"].sum()
        for s, g in df.groupby("symbol", sort=True)
    }


def require_closed_and_cut(
    panel: dict[str, pd.DataFrame], sample_end: date = SAMPLE_END
) -> dict[str, pd.DataFrame]:
    """Refuse a panel whose sample-end bar may still be forming, then cut it there."""
    unclosed = sorted(s for s, g in panel.items() if g["date"].max() <= sample_end)
    if unclosed:
        raise RuntimeError(
            f"no bar after {sample_end} for {unclosed}, so the {sample_end} bar may "
            "be the forming one. Sync 1d after it closes, then re-run."
        )
    return {
        s: g[g["date"] <= sample_end].reset_index(drop=True) for s, g in panel.items()
    }


# --- K2.3: hourly jumps ----------------------------------------------------------


@dataclass(frozen=True)
class Jump:
    symbol: str
    timestamp: int
    prev_contracts: float
    contracts: float
    explanation: str | None

    @property
    def when(self) -> str:
        return f"{datetime.fromtimestamp(self.timestamp / 1000, UTC):%Y-%m-%d %H:%M}"


@dataclass
class JumpReport:
    zero_rows: int = 0
    zero_hours: dict[str, int] = field(default_factory=dict)  # hour -> symbols at 0
    jumps: list[Jump] = field(default_factory=list)

    @property
    def unexplained(self) -> list[Jump]:
        return [j for j in self.jumps if j.explanation is None]


def classify_jumps(
    hourly: dict[str, pd.DataFrame],
    explained: dict[tuple[str, int], str] = EXPLAINED_JUMPS,
) -> JumpReport:
    """Zero placeholders, then every |Δ ln contracts| > ln 3 between positive rows."""
    rep = JumpReport()
    zero_hours: dict[int, int] = {}
    for sym, h in hourly.items():
        zero = h["oi_contracts"] <= 0
        rep.zero_rows += int(zero.sum())
        for ts in h.loc[zero, "timestamp"]:
            zero_hours[int(ts)] = zero_hours.get(int(ts), 0) + 1
        pos = h.loc[~zero].sort_values("timestamp")
        c = pos["oi_contracts"].to_numpy(dtype=float)
        ts_arr = pos["timestamp"].to_numpy(dtype=np.int64)
        if len(c) < 2:
            continue
        d = np.abs(np.diff(np.log(c)))
        for k in np.flatnonzero(d > JUMP_LN):
            ts = int(ts_arr[k + 1])
            rep.jumps.append(
                Jump(sym, ts, float(c[k]), float(c[k + 1]), explained.get((sym, ts)))
            )
    rep.zero_hours = {
        f"{datetime.fromtimestamp(ts / 1000, UTC):%Y-%m-%d %H:%M}": n
        for ts, n in sorted(zero_hours.items())
    }
    return rep


# --- §3 triggers -------------------------------------------------------------------


def oi_daily(hourly: pd.DataFrame) -> pd.Series:
    """OI_D per UTC day: the POSITIVE row stamped 23:00 UTC of D. No interpolation."""
    h = hourly[
        (hourly["timestamp"] % DAY_MS == OI_STAMP_MS) & (hourly["oi_contracts"] > 0)
    ]
    return pd.Series(
        h["oi_contracts"].to_numpy(dtype=float),
        index=list(ms_to_date(h["timestamp"])),
        dtype=float,
    ).sort_index()


def oi_trigger(oi: pd.Series) -> pd.DataFrame:
    """g_D, its own-history 67th percentile over D−180 … D−1, and the window count."""
    cols = ["oi_d", "oi_d5", "g", "g_thr", "g_count"]
    if oi.empty:
        return pd.DataFrame(columns=cols, dtype=float)
    cal = pd.date_range(min(oi.index), max(oi.index), freq="D").date
    s = oi.reindex(cal)
    s5 = s.shift(OI_LOOKBACK)
    g = pd.Series(np.log((s / s5).to_numpy(dtype=float)), index=s.index)
    prior = g.shift(1)
    thr = prior.rolling(PCTL_WINDOW, min_periods=PCTL_MIN).quantile(PCTL)
    cnt = prior.rolling(PCTL_WINDOW, min_periods=1).count().fillna(0.0)
    return pd.DataFrame(
        {"oi_d": s, "oi_d5": s5, "g": g, "g_thr": thr, "g_count": cnt}, index=cal
    )


def signal_frame(
    bars: pd.DataFrame, oi: pd.Series, jump_ts: Sequence[int] = ()
) -> pd.DataFrame:
    """Break, OI and decision columns per bar, each a function of data <= D's close."""
    f = bars.reset_index(drop=True).copy()
    f["ret"] = f["close"].pct_change()
    f["sigma20"] = f["ret"].rolling(SIGMA_WINDOW).std(ddof=1)  # the 20 ending at D
    f["donchian"] = f["high"].shift(1).rolling(DONCHIAN).max()  # highs D−20 … D−1
    f["brk"] = (f["close"] > f["donchian"]).fillna(False).astype(bool)
    trig = oi_trigger(oi)
    f = f.join(trig, on="date")
    f["oi_ok"] = f["oi_d"].notna() & f["oi_d5"].notna()
    stamps = np.array([day_ms(d) + OI_STAMP_MS for d in f["date"]], dtype=np.int64)
    blocked = np.zeros(len(f), dtype=bool)
    if len(jump_ts):
        jt = np.sort(np.asarray(jump_ts, dtype=np.int64))
        lo = stamps - (OI_LOOKBACK + PCTL_WINDOW) * DAY_MS
        blocked = np.searchsorted(jt, stamps, side="right") > np.searchsorted(
            jt, lo, side="right"
        )
    f["jump_blocked"] = blocked
    f["decidable"] = (
        f["oi_ok"]
        & f["sigma20"].notna()
        & (f["sigma20"] > 0)
        & f["donchian"].notna()
        & ~f["jump_blocked"]
    )
    f["control"] = f["brk"] & f["decidable"]
    rising = (f["g"] >= f["g_thr"]) & (f["g_count"] >= PCTL_MIN)
    f["treatment"] = f["control"] & rising.fillna(False).astype(bool)
    return f


def truncated_flags(
    bars: pd.DataFrame, hourly: pd.DataFrame, d: date, jump_ts: Sequence[int] = ()
) -> tuple[bool, bool]:
    """(control, treatment) at D re-derived from bars and OI cut at D's close."""
    cut = day_ms(d + timedelta(days=1))
    b = bars[bars["date"] <= d]
    h = hourly[hourly["timestamp"] < cut]
    f = signal_frame(b, oi_daily(h), [t for t in jump_ts if t < cut])
    row = f.iloc[-1]
    if row["date"] != d:
        raise ValueError(f"no bar on {d}")
    return bool(row["control"]), bool(row["treatment"])


# --- §3 positions -----------------------------------------------------------------


@dataclass
class Position:
    symbol: str
    trigger_date: date
    sigma20: float
    notional: float
    dates: list[date]
    gross_r: list[float]
    cost_r: list[float]

    @property
    def net_r(self) -> list[float]:
        return [g - c for g, c in zip(self.gross_r, self.cost_r, strict=True)]

    @property
    def event_r(self) -> float:
        return float(sum(self.net_r))


@dataclass
class BuildStats:
    triggers: int = 0
    skipped_held: int = 0
    out_of_sample: int = 0
    positions: int = 0
    calendar_gaps: int = 0


def build_positions(
    sym: str,
    frame: pd.DataFrame,
    column: str,
    costs: Costs,
    stats: BuildStats,
    *,
    sample_end: date = SAMPLE_END,
) -> list[Position]:
    """Shorts on ``frame[column]``: enter D+1 open, cover D+5 close, never stacked."""
    out: list[Position] = []
    half = costs.round_trip / 2.0
    held_through = -1  # last held bar index of the open position in this column
    for i in np.flatnonzero(frame[column].to_numpy(dtype=bool)):
        stats.triggers += 1
        if i <= held_through:
            stats.skipped_held += 1
            continue
        last = i + HOLD_BARS
        if last >= len(frame) or frame["date"].iloc[last] > sample_end:
            stats.out_of_sample += 1
            continue
        held = list(frame["date"].iloc[i + 1 : last + 1])
        if (held[-1] - held[0]).days != HOLD_BARS - 1:
            stats.calendar_gaps += 1
        entry_open = float(frame["open"].iloc[i + 1])
        closes = frame["close"].iloc[i + 1 : last + 1].to_numpy(dtype=float)
        prev = np.concatenate([[entry_open], closes[:-1]])
        bar_ret = closes / prev - 1.0  # D+1 open-to-close, then close-to-close
        sigma = float(frame["sigma20"].iloc[i])
        notional = 1.0 / sigma
        gross = list(-notional * bar_ret)
        cost = [0.0] * HOLD_BARS
        cost[0] += half * notional  # entry half of 2(fee + slip)·N
        cost[-1] += half * notional  # exit half
        out.append(
            Position(sym, frame["date"].iloc[i], sigma, notional, held, gross, cost)
        )
        stats.positions += 1
        held_through = int(last)
    return out


def book_days(positions: Sequence[Position]) -> pd.Series:
    """One row per UTC day with an open position: mean net R across those open."""
    rows = [(d, r) for p in positions for d, r in zip(p.dates, p.net_r, strict=True)]
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["date", "r"])
    return df.groupby("date")["r"].mean().sort_index()


def union_calendar_matrix(columns: Sequence[pd.Series]) -> npt.NDArray[np.float64]:
    """(UTC calendar days, columns) over the UNION of spans, 0R on idle days (#921)."""
    start = min(min(c.index) for c in columns)
    end = max(max(c.index) for c in columns)
    days = pd.date_range(start, end, freq="D").date
    return np.column_stack(
        [c.reindex(days, fill_value=0.0).to_numpy(dtype=float) for c in columns]
    )


# --- K1 ---------------------------------------------------------------------------


def measured_sr_variance(*series: npt.NDArray[np.float64]) -> float:
    """Sample variance (ddof 1) of the columns' per-obs Sharpes."""
    return float(np.var([per_period_sharpe(s) for s in series], ddof=1))


def required_annual_sharpe(n_obs: int, sr_variance: float) -> float:
    """The annualised Sharpe the DSR leg needs — the number distil_power prints."""
    sr = required_sharpe(n_obs, n_trials=N_TRIALS, sr_variance=sr_variance)
    return sr * math.sqrt(PERIODS_PER_YEAR)


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
    control_sharpe_annual: float
    sr_variance: float
    dsr: float
    pbo: float
    pbo_days: int
    boot_lo: float
    boot_hi: float
    mean_ci_lo: float
    mean_ci_hi: float
    required_effect_r: float
    powered_null: bool
    passes: bool


def gate(
    r: npt.NDArray[np.float64],
    control: npt.NDArray[np.float64],
    matrix: npt.NDArray[np.float64],
    sr_variance: float,
) -> GateResult:
    ann = math.sqrt(PERIODS_PER_YEAR)
    sr = per_period_sharpe(r)

    def _ann(x: npt.NDArray[np.float64]) -> float:
        return ann_sharpe(x, ann)

    def _mean(x: npt.NDArray[np.float64]) -> float:
        return float(np.mean(x))

    boot = block_bootstrap_ci(r, _ann, block=BOOT_BLOCK, seed=BOOT_SEED)
    mean_ci = block_bootstrap_ci(r, _mean, block=BOOT_BLOCK, seed=BOOT_SEED)
    dsr = deflated_sharpe_ratio(sr, len(r), n_trials=N_TRIALS, sr_variance=sr_variance)
    pbo = cscv_pbo(matrix, n_splits=PBO_SPLITS).pbo
    sd = float(np.std(r, ddof=1))
    bar = required_sharpe(len(r), n_trials=N_TRIALS, sr_variance=sr_variance) * sd
    return GateResult(
        n=len(r),
        mean_r=float(np.mean(r)),
        sd_r=sd,
        sharpe_per_obs=sr,
        sharpe_annual=sr * ann,
        control_sharpe_annual=per_period_sharpe(control) * ann,
        sr_variance=sr_variance,
        dsr=dsr,
        pbo=pbo,
        pbo_days=int(matrix.shape[0]),
        boot_lo=boot.lo,
        boot_hi=boot.hi,
        mean_ci_lo=mean_ci.lo,
        mean_ci_hi=mean_ci.hi,
        required_effect_r=bar,
        powered_null=powered_null(mean_ci.lo, mean_ci.hi, bar=bar),
        passes=passes_gate(dsr, pbo, boot.lo),
    )


# --- §6 readings --------------------------------------------------------------------


@dataclass(frozen=True)
class BetaGuard:
    n: int
    intercept: float
    intercept_t: float
    slope: float


def beta_guard(r: pd.Series, market: pd.Series) -> BetaGuard:
    """OLS of book-day returns on the same-day equal-weight universe return."""
    df = pd.concat({"r": r, "m": market}, axis=1, join="inner").dropna()
    y = df["r"].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(df)), df["m"].to_numpy(dtype=float)])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ coef
    dof = len(y) - 2
    s2 = float(resid @ resid) / dof
    se = math.sqrt(s2 * float(np.linalg.inv(x.T @ x)[0, 0]))
    return BetaGuard(len(y), float(coef[0]), float(coef[0]) / se, float(coef[1]))


@dataclass(frozen=True)
class XsOverlap:
    rho_open_days: float
    n_open_days: int
    rho_full_axis: float
    n_full_axis: int


def xs_overlap(r: pd.Series, xs: pd.Series) -> XsOverlap:
    """Pearson ρ to the XS book on E850's open days and on the full UTC axis."""
    both = pd.concat({"r": r, "x": xs}, axis=1, join="inner").dropna()
    days = pd.date_range(min(r.index), max(r.index), freq="D").date
    full = pd.DataFrame(
        {"r": r.reindex(days, fill_value=0.0), "x": xs.reindex(days, fill_value=0.0)}
    )
    return XsOverlap(
        float(both["r"].corr(both["x"])),
        len(both),
        float(full["r"].corr(full["x"])),
        len(full),
    )


@dataclass(frozen=True)
class Attribution:
    n_treatment: int
    n_control: int
    mean_treatment_r: float
    mean_control_r: float
    diff_r: float
    t_raw: float
    deflator: float
    t_deflated: float


def attribution(
    treatment: Sequence[float], control: Sequence[float], deflator: float
) -> Attribution:
    """Mean per-event net R, treatment minus control; Welch t deflated by sqrt(k/n_eff)."""
    t = np.asarray(treatment, dtype=float)
    c = np.asarray(control, dtype=float)
    diff = float(t.mean() - c.mean())
    se = math.sqrt(t.var(ddof=1) / len(t) + c.var(ddof=1) / len(c))
    t_raw = diff / se
    return Attribution(
        len(t),
        len(c),
        float(t.mean()),
        float(c.mean()),
        diff,
        t_raw,
        deflator,
        t_raw / deflator,
    )


def funding_received(
    positions: Sequence[Position], funding: dict[str, pd.Series]
) -> tuple[float, float, int]:
    """(mean R, mean rate) a short receives per held position-day; signed."""
    r: list[float] = []
    rate: list[float] = []
    for p in positions:
        f = funding.get(p.symbol, pd.Series(dtype=float))
        for d in p.dates:
            x = float(f.get(d, 0.0))
            rate.append(x)
            r.append(p.notional * x)
    if not r:
        return 0.0, 0.0, 0
    return float(np.mean(r)), float(np.mean(rate)), len(r)


def verdict(
    g: GateResult, beta: BetaGuard, xs: XsOverlap, attr: Attribution
) -> tuple[str, str]:
    """§6 in order: FAIL, beta guard, XS overlap, attribution, PASS."""
    if not g.passes:
        if g.powered_null and g.mean_ci_lo > 0:
            return "FAIL", (
                "powered null LICENSED and the mean CI excludes zero: a positive "
                "effect smaller than the bar, never 'no effect'"
            )
        if g.mean_ci_hi < 0:
            # Reporting only, added after the first run: the verdict is FAIL either
            # way, but a CI wholly below zero says the short LOSES, which "no
            # effect found" would misreport. The opposite trade is a new trial.
            return "FAIL", (
                "the mean CI excludes zero BELOW: the short loses as constructed "
                "(confirmed-bad), never 'no effect'; the long side is a separate trial"
            )
        if g.powered_null:
            return "FAIL", "powered null LICENSED: a filed NO on the OI axis"
        return "FAIL", "no effect found; NOT LICENSED as a filed NO"
    if beta.intercept <= 0:
        return "BETA", "a pass whose intercept is <= 0: beta, not an edge"
    if max(abs(xs.rho_open_days), abs(xs.rho_full_axis)) >= RHO_MAX:
        return "XS-OVERLAP", f"|rho| >= {RHO_MAX} to the XS book disqualifies a pass"
    if abs(attr.t_deflated) < ATTRIBUTION_T:
        return "BREAKOUT-REVERSAL", "breakout reversal; OI not shown to add"
    return "PASS", "an OI edge; the s7 tradeable test is owed"


# --- driver -----------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="e850_oi_breakout_flush", description=__doc__.split("\n")[0]
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path (read-only)")
    p.add_argument("--out-dir", default=None, help="write series CSV + JSON here")
    p.add_argument(
        "--k2-only", action="store_true", help="run K2 and stop (reads no return)"
    )
    return p.parse_args(argv)


def run(
    argv: Sequence[str] | None = None, *, emit: Callable[[str], None] = print
) -> int:
    args = parse_args(argv)
    symbols = panel_symbols()
    costs = load_costs()
    conn = connect(args.db)
    tz = conn.execute("SELECT current_setting('TimeZone')").fetchone()
    bars = require_closed_and_cut(load_bars(conn, symbols))
    if missing := sorted(set(symbols) - set(bars)):
        raise RuntimeError(f"no 1d bars for {missing}")
    hourly = load_hourly_oi(conn, symbols)
    emit(f"E850 driver - db {args.db}, TimeZone {tz[0] if tz else '?'}")
    emit(f"  panel {len(symbols)} symbols (universe minus {', '.join(EXCLUDED)})")
    emit(
        f"  costs from {COSTS_PATH}: fee_pct {costs.fee_pct}, slippage_pct "
        f"{costs.slippage_pct} -> 2(fee+slip) {costs.round_trip:.6f} x N per round trip"
    )

    # ---- K2.3 jumps ------------------------------------------------------------
    jr = classify_jumps(hourly)
    emit("")
    emit("K2.3 hourly |d ln oi_contracts| > ln 3")
    emit(
        f"  zero placeholder rows (0 contracts, read as MISSING): {jr.zero_rows} on "
        f"{len(jr.zero_hours)} hours"
    )
    for hour, n in jr.zero_hours.items():
        if n > 1:
            emit(f"    {hour} UTC: {n} symbols at zero")
    singles = sum(1 for n in jr.zero_hours.values() if n == 1)
    emit(f"    + {singles} single-symbol zero hours, each resuming the next hour")
    for j in jr.jumps:
        emit(
            f"  jump {j.symbol} {j.when} UTC {j.prev_contracts:,.0f} -> "
            f"{j.contracts:,.0f}: {j.explanation or 'UNEXPLAINED'}"
        )
    unexplained: dict[str, list[int]] = {}
    for j in jr.unexplained:
        unexplained.setdefault(j.symbol, []).append(j.timestamp)

    frames = {
        s: signal_frame(
            bars[s], oi_daily(hourly.get(s, _empty_hourly())), unexplained.get(s, [])
        )
        for s in symbols
    }
    blocked = {s: int(f["jump_blocked"].sum()) for s, f in frames.items()}
    emit(
        "  decisions removed by unexplained jumps: "
        + (", ".join(f"{s} {n}" for s, n in blocked.items() if n) or "none")
    )

    # ---- K2.1 truncation ---------------------------------------------------------
    checked = 0
    mismatches: list[str] = []
    for s, f in frames.items():
        h = hourly.get(s, _empty_hourly())
        dec = np.flatnonzero(f["decidable"].to_numpy(dtype=bool))
        ev = set(np.flatnonzero(f["control"].to_numpy(dtype=bool)))
        rows = sorted(ev | set(dec[::TRUNCATION_STRIDE]))
        for i in rows:
            d = f["date"].iloc[i]
            want = (bool(f["control"].iloc[i]), bool(f["treatment"].iloc[i]))
            got = truncated_flags(bars[s], h, d, unexplained.get(s, []))
            checked += 1
            if got != want:
                mismatches.append(f"{s} {d}: full {want} truncated {got}")
    emit("")
    emit("K2.1 truncation (bars and OI cut at D's close; control + treatment flags)")
    emit(f"  days checked {checked:,} | mismatches {len(mismatches)}")
    for m in mismatches[:10]:
        emit(f"    {m}")

    # ---- K2.2 counts ------------------------------------------------------------
    emit("")
    emit("K2.2 events before re-trigger skips (treatment / control)")
    subset_ok = all(not (f["treatment"] & ~f["control"]).any() for f in frames.values())
    by_year: dict[int, list[int]] = {}
    for s, f in frames.items():
        n_t, n_c = int(f["treatment"].sum()), int(f["control"].sum())
        first = f.loc[f["oi_ok"], "date"].min()
        emit(f"  {s:<13} {n_t:>4} / {n_c:>4}   decidable from {first}")
        for y, yr in f.groupby(pd.to_datetime(f["date"]).dt.year):
            acc = by_year.setdefault(int(y), [0, 0])
            acc[0] += int(yr["treatment"].sum())
            acc[1] += int(yr["control"].sum())
    for y, (yt, yc) in sorted(by_year.items()):
        emit(f"  {y}  {yt:>5} / {yc:>5}")
    emit(f"  treatment subset of control, event by event: {subset_ok}")
    gaps = {
        s: int((pd.to_datetime(b["date"]).diff().dt.days > 1).sum())
        for s, b in bars.items()
    }
    emit(
        "  1d calendar gaps: "
        + (", ".join(f"{s} {n}" for s, n in gaps.items() if n) or "none")
    )
    k2_ok = not mismatches and subset_ok
    emit(f"  K2        {'PASS' if k2_ok else 'KILLED'}")
    summary: dict[str, object] = {
        "k2": {
            "zero_rows": jr.zero_rows,
            "zero_hours": jr.zero_hours,
            "jumps": [asdict(j) for j in jr.jumps],
            "blocked": blocked,
            "truncation_checked": checked,
            "truncation_mismatches": mismatches,
            "treatment_subset_of_control": subset_ok,
            "events_by_year": by_year,
            "calendar_gaps": gaps,
        }
    }
    out_dir = Path(args.out_dir) if args.out_dir else None
    if not k2_ok:
        emit("VERDICT: KILLED at K2 - a defect, never a null.")
        _write(out_dir, summary, None)
        return 3
    if args.k2_only:
        _write(out_dir, summary, None)
        return 0

    # ---- positions (no mean read yet) ----------------------------------------------
    st_t, st_c = BuildStats(), BuildStats()
    pos_t: list[Position] = []
    pos_c: list[Position] = []
    for s, f in frames.items():
        pos_t += build_positions(s, f, "treatment", costs, st_t)
        pos_c += build_positions(s, f, "control", costs, st_c)
    bd_t, bd_c = book_days(pos_t), book_days(pos_c)
    emit("")
    for name, st, bd in (("treatment", st_t, bd_t), ("control", st_c, bd_c)):
        emit(
            f"  {name:<9} triggers {st.triggers:,} | skipped while held "
            f"{st.skipped_held:,} | exit after sample end {st.out_of_sample:,} | "
            f"positions {st.positions:,} on {len(bd):,} book-days | held windows "
            f"spanning a calendar gap {st.calendar_gaps}"
        )

    # ---- K1 (no mean of either column is printed before this verdict) --------------
    r = bd_t.to_numpy(dtype=float)
    c = bd_c.to_numpy(dtype=float)
    n_obs = len(r)
    if n_obs < 30:
        emit(f"K1: {n_obs} book-days - KILLED (unpriceable)")
        return 0
    sd = float(np.std(r, ddof=1))
    measured = measured_sr_variance(r, c)
    sr_var = max(measured, SR_VARIANCE_FLOOR)
    closes = {s: b.set_index("date")["close"] for s, b in bars.items()}
    n_eff, deflator = effective_independent_series(
        {s: x.pct_change() for s, x in closes.items()}
    )
    req = required_annual_sharpe(n_obs, sr_var)
    k1_ok = req <= K1_MAX_ANNUAL_SHARPE
    emit("")
    emit("K1 power (printed before any mean of either column)")
    emit(f"  E850 book-days n {n_obs:,} | sd {sd:.6f} R")
    emit(
        f"  sr_variance {sr_var:.6f} = max(measured {measured:.6f} across E850 + "
        f"control, floor {SR_VARIANCE_FLOOR})"
    )
    emit(
        f"  n_eff over the {len(closes)} symbols' daily returns {n_eff:.3f} "
        f"(deflator {deflator:.3f}) - disclosure only, not passed to distil_power"
    )
    emit("  tools/distil_power.py " + " ".join(distil_power_argv(n_obs, sd, sr_var)))
    for line in distil_power.price(
        distil_power.parse_args(distil_power_argv(n_obs, sd, sr_var))
    ):
        emit("    " + line)
    emit(f"  required annualised Sharpe {req:.4f} vs ceiling {K1_MAX_ANNUAL_SHARPE}")
    emit(f"  K1        {'PASS' if k1_ok else 'KILLED'}")
    summary["build"] = {"treatment": asdict(st_t), "control": asdict(st_c)}
    summary["k1"] = {
        "n": n_obs,
        "sd": sd,
        "sr_variance_measured": measured,
        "sr_variance": sr_var,
        "n_eff": n_eff,
        "deflator": deflator,
        "required_annual_sharpe": req,
        "ceiling": K1_MAX_ANNUAL_SHARPE,
        "pass": k1_ok,
    }
    if not k1_ok:
        emit("VERDICT: KILLED at K1 - a failure to clear, never a null.")
        _write(out_dir, summary, None)
        return 0

    # ---- gate ------------------------------------------------------------------------
    matrix = union_calendar_matrix([bd_t, bd_c])
    g = gate(r, c, matrix, sr_var)
    emit("")
    emit(f"Gate - E850's own series; PBO across E850 + control on {g.pbo_days:,} days")
    emit(
        f"  mean {g.mean_r:+.5f} R/book-day | sd {g.sd_r:.5f} | Sharpe "
        f"{g.sharpe_per_obs:+.5f} per obs = {g.sharpe_annual:+.4f} annualised"
    )
    emit(f"  control Sharpe {g.control_sharpe_annual:+.4f} annualised")
    emit(f"  DSR (N={N_TRIALS}, sr_variance {g.sr_variance:.6f}) {g.dsr:.4f}")
    emit(f"  PBO {g.pbo:.4f}")
    emit(
        f"  boot (block {BOOT_BLOCK}, ann. Sharpe) [{g.boot_lo:+.4f}, {g.boot_hi:+.4f}]"
        f" | mean CI [{g.mean_ci_lo:+.5f}, {g.mean_ci_hi:+.5f}] R"
    )
    emit(
        f"  powered_null at bar +/-{g.required_effect_r:.5f} R: "
        f"{'LICENSED' if g.powered_null else 'NOT LICENSED'}"
    )
    emit(f"  gate      {'PASS' if g.passes else 'FAIL'}")

    # ---- §6 -------------------------------------------------------------------------
    market = equal_weight_market_return(
        {
            s: x.set_axis(pd.DatetimeIndex(pd.to_datetime(list(x.index))))
            for s, x in closes.items()
        }
    )
    market.index = pd.DatetimeIndex(market.index).date
    bg = beta_guard(bd_t, market)
    xs_res = replay_xs(conn, ForecastConfig.from_toml(COSTS_PATH))
    xs = pd.Series(
        xs_res.portfolio_return, index=pd.DatetimeIndex(xs_res.daily_index).date
    )
    ov = xs_overlap(bd_t, xs)
    at = attribution([p.event_r for p in pos_t], [p.event_r for p in pos_c], deflator)
    fr_r, fr_rate, fr_n = funding_received(pos_t, load_daily_funding(conn, symbols))
    conn.close()
    emit("")
    emit("s6 readings")
    emit(
        f"  beta guard: intercept {bg.intercept:+.5f} R (t {bg.intercept_t:+.2f}), "
        f"slope {bg.slope:+.3f} R per unit EW return, n {bg.n:,}"
    )
    emit(
        f"  XS overlap: rho {ov.rho_open_days:+.4f} on {ov.n_open_days:,} open days | "
        f"{ov.rho_full_axis:+.4f} on {ov.n_full_axis:,} calendar days (0R idle)"
    )
    emit(
        f"  attribution: treatment {at.n_treatment:,} events {at.mean_treatment_r:+.4f}"
        f" R minus control {at.n_control:,} events {at.mean_control_r:+.4f} R = "
        f"{at.diff_r:+.4f} R/event | t raw {at.t_raw:+.3f}, "
        f"deflated {at.t_deflated:+.3f} (/ {at.deflator:.3f})"
    )
    fund_note = (
        "positive: omitting it UNDERSTATES the short, gate conservative"
        if fr_r > 0
        else "negative: the gate was OPTIMISTIC by this amount"
    )
    emit(
        f"  funding received per held position-day {fr_r:+.5f} R "
        f"({fr_rate * 1e4:+.3f} bps of notional, n {fr_n:,}) - {fund_note}"
    )
    v, why = verdict(g, bg, ov, at)
    emit("")
    emit(f"VERDICT: {v} - {why}")
    summary["gate"] = asdict(g)
    summary["s6"] = {
        "beta": asdict(bg),
        "xs": asdict(ov),
        "attribution": asdict(at),
        "funding_r_per_held_day": fr_r,
        "funding_rate_per_held_day": fr_rate,
        "funding_held_days": fr_n,
    }
    summary["verdict"] = v
    summary["verdict_reason"] = why
    _write(out_dir, summary, {"treatment": bd_t, "control": bd_c})
    return 0


def _empty_hourly() -> pd.DataFrame:
    return pd.DataFrame({"timestamp": pd.Series(dtype="int64"), "oi_contracts": []})


def _write(
    out_dir: Path | None,
    summary: dict[str, object],
    series: dict[str, pd.Series] | None,
) -> None:
    if out_dir is None:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    if series is not None:
        pd.DataFrame({f"net_r_{k}": v for k, v in series.items()}).to_csv(
            out_dir / "e850_bookday.csv", index_label="date"
        )
    (out_dir / "e850_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
