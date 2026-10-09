"""H13 — 15m opening-range breakout at the NYSE cash open (#848).

Implements ``docs/superpowers/specs/2026-10-08-h13-us-open-orb-preregistration.md``
§3-§6 and nothing else. The spec is FROZEN: a change to the construction, the gate
or the kill-switches after an H13 number is read is a new trial, not a fix.

Run order is the spec's, and the code enforces it rather than trusting the reader:

1. **K3 fidelity** — the detector at its default 00:00-UTC anchor must reproduce the
   shipped detector's signals on the 15m fixture byte for byte
   (``tests/fixtures/orb_utc_anchor_k3_signals.json``, frozen from the pre-anchor
   code). The ``test_lookahead`` half lives in ``tests/test_h13_us_open_orb.py``.
2. **K1 power** — the control column's book-day sd and H13's book-day count go to
   ``tools/distil_power.py`` at N = 3 with no ``--n-eff``. Printed before any mean of
   H13's series is computed. A required effect above +0.15R per book-day kills it.
3. **K2 + gate** — DSR at N = 3, PBO against the control column on UTC calendar
   days (0R on no-trade days, #921), and the block-bootstrap lower bound, under
   BOTH same-bar tie-breaks. A verdict that differs between them is INDETERMINATE.

Readings of the spec fixed here, before any H13 outcome was read:

- **The control column** (§4) is the shipped 00:00-UTC ``orb_breakout`` on the same
  panel with §3's target, stop floor, costs and gates — and the shipped detector's
  own lifetime, so no session-close exit. A control trade still open at the sample
  end is dropped. Its book-day is the UTC date of its signal bar.
- **K1's "available columns"** are the control alone, so ``--sr-variance`` is the
  0.005 floor. At the gate, the measured variance is the sample variance (ddof 1)
  of the two columns' per-book-day Sharpes, each on its own book-day series.
- **Bootstrap block** 5 book-days (one trading week); seed 7, the sleeves' seed.
- **A signal bar must close strictly before the session close** (see
  ``analytics/strategies/orb_breakout.py::_detect_nyse_open``).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

# Runnable as a bare script (`python tools/h13_us_open_orb.py`), not only with
# PYTHONPATH=. — `tests/test_h13_us_open_orb.py::test_bare_invocation_works` is the
# guarantee, not this line.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import numpy.typing as npt  # noqa: E402
import pandas as pd  # noqa: E402

from analytics.audit_guard import powered_null  # noqa: E402
from analytics.backtest.engine import run_backtest  # noqa: E402
from analytics.backtest_config import load_backtest_config  # noqa: E402
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
from analytics.strategies.orb_breakout import (  # noqa: E402
    OrbAnchor,
    detect_orb_breakout,
)
from analytics.trading_calendar import nyse_sessions  # noqa: E402
from tools import distil_power  # noqa: E402

SPEC_PATH = Path("docs/superpowers/specs/2026-10-08-h13-us-open-orb-preregistration.md")
COSTS_PATH = Path("config/strategy_params.toml")
K3_FIXTURE = Path("tests/fixtures/btc_15m_200d.parquet")
K3_GOLDEN = Path("tests/fixtures/orb_utc_anchor_k3_signals.json")
VENUE = "binance"

# --- §3, frozen ---------------------------------------------------------------
SAMPLE_END = date(2026, 10, 8)
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME = "15m"
RANGE_CANDLES = 2
TP_R = 1.5

# --- §4/§5 ---------------------------------------------------------------------
N_TRIALS = 3
SR_VARIANCE_FLOOR = 0.005
K1_MAX_EFFECT_R = 0.15
PERIODS_PER_YEAR = 252.0  # NYSE sessions
BOOT_BLOCK = 5
BOOT_SEED = 7  # the sleeves' seed (analytics/xsmom/report.py)
PBO_SPLITS = 14

TieBreak = Literal["adverse", "target"]
TIE_BREAKS: tuple[TieBreak, ...] = ("adverse", "target")
_DAY_MS = 86_400_000


@dataclass(frozen=True)
class Costs:
    fee_pct: float
    slippage_pct: float
    min_sl_pct: float


def load_costs(path: Path = COSTS_PATH) -> Costs:
    """Production fee, slippage and stop floor, read at run time, never restated."""
    cfg = load_backtest_config(path)
    return Costs(
        fee_pct=cfg.fee_pct, slippage_pct=cfg.slippage_pct, min_sl_pct=cfg.min_sl_pct
    )


# --- data ------------------------------------------------------------------------


def cut_ms(sample_end: date = SAMPLE_END) -> int:
    """First millisecond AFTER the sample: 00:00 UTC on the day after ``sample_end``."""
    d = datetime.combine(sample_end + timedelta(days=1), datetime.min.time())
    return int(d.replace(tzinfo=UTC).timestamp() * 1000)


def load_panel(conn: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    """15m bars per symbol from ``ohlcv_all`` on the pinned venue, oldest first."""
    df = conn.execute(
        """
        SELECT symbol, open_time, open, high, low, close, volume
        FROM ohlcv_all
        WHERE timeframe = ? AND venue = ? AND symbol IN (SELECT unnest(?))
        ORDER BY symbol, open_time
        """,
        [TIMEFRAME, VENUE, list(SYMBOLS)],
    ).df()
    return {
        str(s): g.drop(columns=["symbol"]).reset_index(drop=True)
        for s, g in df.groupby("symbol", sort=True)
    }


def require_closed_and_cut(
    panel: dict[str, pd.DataFrame], sample_end: date = SAMPLE_END
) -> dict[str, pd.DataFrame]:
    """Refuse a panel without a bar past the sample, then cut it at the sample end.

    ``sync`` stores the forming bar, so a bar opening after the cut proves every
    bar inside the sample had closed when it was last written.
    """
    cut = cut_ms(sample_end)
    missing = sorted(set(SYMBOLS) - set(panel))
    unclosed = sorted(s for s, g in panel.items() if int(g["open_time"].max()) < cut)
    if missing or unclosed:
        raise RuntimeError(
            f"no 15m bars for {missing}; no bar after {sample_end} for {unclosed}. "
            "Sync 15m, then re-run."
        )
    return {s: g[g["open_time"] < cut].reset_index(drop=True) for s, g in panel.items()}


def session_close_lookup(first: date, last: date) -> Callable[[int], int | None]:
    """Signal open_time -> the close of the NYSE session it fired in (or None)."""
    sessions = nyse_sessions(first, last)
    opens = np.array([s.open_ms for s in sessions], dtype=np.int64)
    closes = np.array([s.close_ms for s in sessions], dtype=np.int64)

    def lookup(t: int) -> int | None:
        i = int(np.searchsorted(opens, t, side="right")) - 1
        if i < 0 or t >= closes[i]:
            return None
        return int(closes[i])

    return lookup


# --- K3 --------------------------------------------------------------------------


def k3_fidelity(
    fixture: Path = K3_FIXTURE, golden: Path = K3_GOLDEN
) -> tuple[bool, int, int]:
    """(reproduced, shipped signal count, measured count) at the 00:00-UTC anchor."""
    df = pd.read_parquet(fixture)
    want = json.loads(golden.read_text(encoding="utf-8"))["signals"]
    sig = detect_orb_breakout(df, RANGE_CANDLES)
    got = [
        {
            "open_time": int(t),
            "direction": str(d),
            "reason": str(why),
            "sl_price": float(sl),
            "context": str(ctx),
        }
        for t, d, why, sl, ctx in zip(
            sig["open_time"].astype("int64"),
            sig["direction"],
            sig["reason"],
            sig["sl_price"].astype(float),
            sig["context"],
            strict=True,
        )
    ]
    return got == want, len(want), len(got)


# --- trades and book-days ------------------------------------------------------------


def simulate(
    panel: dict[str, pd.DataFrame],
    anchor: OrbAnchor,
    tie_break: TieBreak,
    costs: Costs,
    time_exit: Callable[[int], int | None] | None,
) -> pd.DataFrame:
    """Every RESOLVED trade on the panel, one row each, net R per the engine."""
    rows: list[dict[str, object]] = []
    for sym, df in panel.items():
        sig = detect_orb_breakout(df, RANGE_CANDLES, anchor=anchor)
        res = run_backtest(
            df,
            sig,
            sym,
            TIMEFRAME,
            "orb",
            tp_r=TP_R,
            fee_pct=costs.fee_pct,
            min_sl_pct=costs.min_sl_pct,
            volume_suppress=False,
            slippage_pct=costs.slippage_pct,
            time_exit_ms=time_exit,
            tie_break=tie_break,
        )
        for t in res.trades:
            if t.outcome == "open" or t.pnl_r is None:
                continue
            rows.append(
                {
                    "symbol": sym,
                    "signal_time": t.signal_time,
                    "direction": t.direction,
                    "outcome": t.outcome,
                    "net_r": float(t.pnl_r),
                    "day": pd.Timestamp(t.signal_time, unit="ms", tz="UTC").date(),
                }
            )
    cols = ["symbol", "signal_time", "direction", "outcome", "net_r", "day"]
    return pd.DataFrame(rows, columns=cols)


def ambiguous_count(adverse: pd.DataFrame, target: pd.DataFrame) -> int:
    """Trades whose outcome flips between the two tie-breaks.

    A tie-break only changes a trade whose FIRST stop touch and FIRST target touch
    share a bar, and on that bar it always flips loss <-> win, so the flipped rows
    are exactly the ambiguous ones that decided an exit.
    """
    key = ["symbol", "signal_time", "direction"]
    m = adverse.merge(target, on=key, suffixes=("_a", "_t"))
    return int((m["outcome_a"] != m["outcome_t"]).sum())


def book_days(trades: pd.DataFrame) -> pd.Series:
    """One row per day with at least one resolved trade: mean net R."""
    if trades.empty:
        return pd.Series(dtype=float)
    return trades.groupby("day")["net_r"].mean().sort_index()


def calendar_matrix(columns: Sequence[pd.Series]) -> npt.NDArray[np.float64]:
    """(UTC calendar days, columns) over the span every column covers, 0R if idle."""
    start = max(c.index.min() for c in columns)
    end = min(c.index.max() for c in columns)
    days = pd.date_range(start, end, freq="D").date
    return np.column_stack(
        [c.reindex(days, fill_value=0.0).to_numpy(dtype=float) for c in columns]
    )


# --- K1 ---------------------------------------------------------------------------


def distil_power_argv(n_obs: int, sd: float, sr_variance: float) -> list[str]:
    """§5's K1 invocation: per book-day, per-obs footing, N = 3, no ``--n-eff``."""
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


def k1_required_effect(n_obs: int, sd: float, sr_variance: float) -> float:
    """The per-book-day mean the DSR leg needs: required per-obs Sharpe x sd."""
    return required_sharpe(n_obs, n_trials=N_TRIALS, sr_variance=sr_variance) * sd


# --- gate -------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    tie_break: str
    n: int
    mean_r: float
    sd_r: float
    sharpe_per_obs: float
    sharpe_annual: float
    control_sharpe_per_obs: float
    sr_variance: float
    dsr: float
    pbo: float
    boot_lo: float
    boot_hi: float
    mean_ci_lo: float
    mean_ci_hi: float
    required_effect_r: float
    powered_null: bool
    passes: bool


def measured_sr_variance(*series: npt.NDArray[np.float64]) -> float:
    """Sample variance (ddof 1) of the columns' per-obs Sharpes."""
    return float(np.var([per_period_sharpe(s) for s in series], ddof=1))


def gate(
    tie_break: str,
    r: npt.NDArray[np.float64],
    control: npt.NDArray[np.float64],
    matrix: npt.NDArray[np.float64],
) -> GateResult:
    ann = math.sqrt(PERIODS_PER_YEAR)
    sr = per_period_sharpe(r)
    sr_var = max(measured_sr_variance(r, control), SR_VARIANCE_FLOOR)

    def _ann(x: npt.NDArray[np.float64]) -> float:
        return ann_sharpe(x, ann)

    def _mean(x: npt.NDArray[np.float64]) -> float:
        return float(np.mean(x))

    boot = block_bootstrap_ci(r, _ann, block=BOOT_BLOCK, seed=BOOT_SEED)
    mean_ci = block_bootstrap_ci(r, _mean, block=BOOT_BLOCK, seed=BOOT_SEED)
    dsr = deflated_sharpe_ratio(sr, len(r), n_trials=N_TRIALS, sr_variance=sr_var)
    pbo = cscv_pbo(matrix, n_splits=PBO_SPLITS).pbo
    sd = float(np.std(r, ddof=1))
    bar = required_sharpe(len(r), n_trials=N_TRIALS, sr_variance=sr_var) * sd
    return GateResult(
        tie_break=tie_break,
        n=len(r),
        mean_r=float(np.mean(r)),
        sd_r=sd,
        sharpe_per_obs=sr,
        sharpe_annual=sr * ann,
        control_sharpe_per_obs=per_period_sharpe(control),
        sr_variance=sr_var,
        dsr=dsr,
        pbo=pbo,
        boot_lo=boot.lo,
        boot_hi=boot.hi,
        mean_ci_lo=mean_ci.lo,
        mean_ci_hi=mean_ci.hi,
        required_effect_r=bar,
        powered_null=powered_null(mean_ci.lo, mean_ci.hi, bar=bar),
        passes=passes_gate(dsr, pbo, boot.lo),
    )


def verdict(results: Sequence[GateResult]) -> str:
    """§6: PASS only under both tie-breaks; a split is INDETERMINATE (K2)."""
    passed = {g.passes for g in results}
    if passed == {True}:
        return "PASS"
    if passed == {False}:
        return "FAIL"
    return "INDETERMINATE"


# --- driver -----------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="h13_us_open_orb", description=__doc__.split("\n")[0]
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path (read-only)")
    p.add_argument("--out-dir", default=None, help="write series CSV + JSON here")
    p.add_argument(
        "--k3-only", action="store_true", help="run K3 and stop (reads no DB bar)"
    )
    return p.parse_args(argv)


def run(
    argv: Sequence[str] | None = None, *, emit: Callable[[str], None] = print
) -> int:
    args = parse_args(argv)

    # ---- K3 ------------------------------------------------------------------
    ok, want, got = k3_fidelity()
    emit("K3 fidelity (00:00-UTC anchor vs the shipped detector, 15m fixture)")
    emit(
        f"  shipped {want} signals | measured {got} | {'REPRODUCED' if ok else 'DIFFERS'}"
    )
    emit("  lookahead: tests/test_h13_us_open_orb.py::TestNyseAnchorIsCausal")
    if not ok:
        emit("VERDICT: KILLED at K3 - the build is wrong and nothing runs.")
        return 3
    if args.k3_only:
        return 0

    costs = load_costs()
    conn = duckdb.connect(args.db, read_only=True)
    panel = require_closed_and_cut(load_panel(conn))
    conn.close()
    emit("")
    emit(
        f"H13 driver - db {args.db}, venue {VENUE}, {TIMEFRAME}, sample <= {SAMPLE_END}"
    )
    emit(
        f"costs from {COSTS_PATH}: fee_pct {costs.fee_pct}, slippage_pct "
        f"{costs.slippage_pct}, min_sl_pct {costs.min_sl_pct} | tp_r {TP_R}"
    )
    for sym, bars in panel.items():
        first = pd.Timestamp(int(bars["open_time"].min()), unit="ms", tz="UTC")
        emit(f"  {sym}: {len(bars):,} bars from {first:%Y-%m-%d %H:%M} UTC")
    first_day = min(
        pd.Timestamp(int(g["open_time"].min()), unit="ms", tz="UTC").date()
        for g in panel.values()
    )
    time_exit = session_close_lookup(first_day, SAMPLE_END)

    trades = {
        tb: simulate(panel, "nyse_open", tb, costs, time_exit) for tb in TIE_BREAKS
    }
    control = {
        tb: simulate(panel, "utc_midnight", tb, costs, None) for tb in TIE_BREAKS
    }
    h13_days = {tb: book_days(trades[tb]) for tb in TIE_BREAKS}
    ctrl_days = {tb: book_days(control[tb]) for tb in TIE_BREAKS}
    adv = trades["adverse"]
    emit(
        f"  H13 trades {len(adv):,} on {len(h13_days['adverse']):,} sessions | "
        f"exits: {adv['outcome'].value_counts().to_dict()}"
    )
    emit(
        f"  control trades {len(control['adverse']):,} on "
        f"{len(ctrl_days['adverse']):,} UTC days"
    )

    # ---- K1 (no H13 mean is computed before this verdict) -----------------------
    n_obs = len(h13_days["adverse"])
    ctrl_r = ctrl_days["adverse"].to_numpy(dtype=float)
    sd = float(np.std(ctrl_r, ddof=1))
    sr_var = SR_VARIANCE_FLOOR
    need = k1_required_effect(n_obs, sd, sr_var)
    k1_ok = need <= K1_MAX_EFFECT_R
    emit("")
    emit("K1 power (printed before any mean of the H13 series)")
    emit(f"  H13 book-days n {n_obs:,} | control book-day sd {sd:.6f} R")
    emit(
        f"  sr_variance {sr_var} = max(NOT YET MEASURABLE (one column), "
        f"{SR_VARIANCE_FLOOR})"
    )
    emit("  tools/distil_power.py " + " ".join(distil_power_argv(n_obs, sd, sr_var)))
    for line in distil_power.price(
        distil_power.parse_args(distil_power_argv(n_obs, sd, sr_var))
    ):
        emit("    " + line)
    emit(f"  required effect {need:+.5f} R/book-day vs ceiling +{K1_MAX_EFFECT_R}")
    emit(f"  K1        {'PASS' if k1_ok else 'KILLED'}")

    summary: dict[str, object] = {
        "k3": {"reproduced": ok, "shipped": want, "measured": got},
        "k1": {
            "n": n_obs,
            "control_sd": sd,
            "sr_variance": sr_var,
            "required_effect_r": need,
            "ceiling_r": K1_MAX_EFFECT_R,
            "pass": k1_ok,
        },
    }
    out_dir = Path(args.out_dir) if args.out_dir else None
    if not k1_ok:
        emit("VERDICT: KILLED at K1 - a failure to clear, never a null.")
        _write(out_dir, summary, None)
        return 0

    # ---- K2 + gate ---------------------------------------------------------------
    emit("")
    emit("K2 same-bar ties (trades whose outcome flips between the resolutions)")
    emit(
        f"  H13 {ambiguous_count(trades['adverse'], trades['target']):,} of "
        f"{len(adv):,} | control "
        f"{ambiguous_count(control['adverse'], control['target']):,} of "
        f"{len(control['adverse']):,}"
    )
    results: list[GateResult] = []
    for tb in TIE_BREAKS:
        r = h13_days[tb].to_numpy(dtype=float)
        c = ctrl_days[tb].to_numpy(dtype=float)
        matrix = calendar_matrix([h13_days[tb], ctrl_days[tb]])
        g = gate(tb, r, c, matrix)
        results.append(g)
        emit("")
        emit(f"Gate - {tb}-first ties (PBO on {matrix.shape[0]:,} UTC calendar days)")
        emit(
            f"  mean {g.mean_r:+.5f} R/book-day | sd {g.sd_r:.5f} | Sharpe "
            f"{g.sharpe_per_obs:+.5f} per obs = {g.sharpe_annual:+.4f} annualised"
        )
        emit(
            f"  control Sharpe {g.control_sharpe_per_obs:+.5f} per obs | "
            f"sr_variance {g.sr_variance:.6f}"
        )
        emit(f"  DSR (N={N_TRIALS}) {g.dsr:.4f} | PBO {g.pbo:.4f}")
        emit(
            f"  boot (block {BOOT_BLOCK}, ann. Sharpe) [{g.boot_lo:+.4f}, "
            f"{g.boot_hi:+.4f}] | mean CI [{g.mean_ci_lo:+.5f}, {g.mean_ci_hi:+.5f}] R"
        )
        emit(
            f"  powered_null at bar +/-{g.required_effect_r:.5f} R: "
            f"{'LICENSED' if g.powered_null else 'NOT LICENSED'}"
        )
        emit(f"  gate      {'PASS' if g.passes else 'FAIL'}")

    v = verdict(results)
    emit("")
    emit(f"VERDICT: {v}")
    if v == "PASS":
        emit("  s7 tradeable test is owed (minimum lot at local equity, survival, XS).")
    elif v == "FAIL" and all(g.powered_null and g.mean_ci_lo > 0 for g in results):
        # Contained within +/-bar AND above zero: real, and smaller than the gate
        # can act on. "No effect" would be the wrong sentence to file.
        emit(
            "  powered null LICENSED and the mean CI EXCLUDES zero under both "
            "tie-breaks - a positive effect smaller than the bar, never 'no effect'."
        )
    elif v == "FAIL" and all(g.powered_null for g in results):
        emit("  powered null LICENSED under both tie-breaks - a filed NO is allowed.")
    elif v == "FAIL":
        emit("  a failure to clear the gate - 'no effect found', never a filed NO.")
    else:
        emit("  K2 split - a failure to clear, never a null.")
    summary["k2"] = {
        "h13_ambiguous": ambiguous_count(trades["adverse"], trades["target"]),
        "control_ambiguous": ambiguous_count(control["adverse"], control["target"]),
    }
    summary["gate"] = [asdict(g) for g in results]
    summary["verdict"] = v
    _write(out_dir, summary, h13_days)
    return 0


def _write(
    out_dir: Path | None,
    summary: dict[str, object],
    series: dict[TieBreak, pd.Series] | None,
) -> None:
    if out_dir is None:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    if series is not None:
        pd.DataFrame({f"net_r_{k}": v for k, v in series.items()}).to_csv(
            out_dir / "h13_bookday.csv", index_label="date"
        )
    (out_dir / "h13_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
