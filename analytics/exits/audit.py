"""Re-resolve the live ledger under exit policies and A/B via the P1 paper book.

The exit-replay driver (exit spec §4–§5). For each resolved alert it walks the
forward OHLCV window (entry → entry + `max_hold_bars`) under a policy via
`replay_exits`, rebuilds a `LedgerTrade` carrying the re-resolved
`(realized_r, exit_ts_ms, outcome)`, and feeds the list through the **same**
`portfolio.book_from_trades` machinery the P1 baseline used — so the headline
metric is portfolio Sharpe / max-DD, not per-trade avg_r, and #0 vs the
composite is apples-to-apples (identical entries + SL; only exit management
differs, runner targets the alert's own `rr_ratio`).

Per-tf `max_hold_bars` (the original expiry caps) and the time-stop floor come
from the 2026-06-15 MFE-timing study (`docs/audits/2026-06-15-mfe-timing.md`):
the time-stop floor = winner bars-to-1R p90, below which a time-stop clips
winners. Both are overridable (testing / sweeps).
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from analytics.data_store import get_ohlcv
from analytics.exits.policies import ExitPolicyConfig, composite, fixed
from analytics.exits.replay import replay_exits
from portfolio import metrics
from portfolio.book import BookResult, LedgerTrade
from portfolio.replay import book_from_trades
from portfolio.sizing import SizingConfig

_TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}

# Original expiry caps + safe time-stop floors per tf (2026-06-15 MFE-timing study).
MAX_HOLD_BY_TF = {"15m": 96, "1h": 48, "4h": 30, "1d": 14}
TIME_STOP_FLOOR_BY_TF = {"15m": 65, "1h": 17, "4h": 7, "1d": 3}

_LEDGER_SQL = (
    "SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms, "
    "       entry_price, sl_price, rr_ratio "
    "FROM signal_alert_outcomes "
    "WHERE outcome IN ('win', 'loss', 'expired') AND candle_ts_ms IS NOT NULL "
    "  AND entry_price IS NOT NULL AND sl_price IS NOT NULL "
    "  AND rr_ratio IS NOT NULL "
    "ORDER BY candle_ts_ms"
)


def _policy_for(
    kind: str,
    *,
    tf: str,
    rr: float,
    max_hold_by_tf: dict[str, int],
    time_stop_by_tf: dict[str, int],
) -> ExitPolicyConfig | None:
    """Build the per-alert policy (tp_r = the alert's own rr_ratio)."""
    mh = max_hold_by_tf.get(tf)
    if mh is None:
        return None
    if kind == "fixed":
        return fixed(tp_r=rr, max_hold_bars=mh)
    if kind == "composite":
        ts = min(time_stop_by_tf.get(tf, mh), mh)
        return composite(tp_r=rr, max_hold_bars=mh, time_stop_bars=ts)
    raise ValueError(f"unknown policy kind {kind!r}")


@dataclass(frozen=True)
class PolicyResult:
    """Re-resolved trades + per-trade cohort stats under one policy."""

    name: str
    trades: list[LedgerTrade]
    n: int
    expiry_rate: float
    win_rate: float
    avg_hold_bars: float
    avg_r: float


def resolve_ledger_under_policy(
    conn: duckdb.DuckDBPyConnection,
    kind: str,
    *,
    max_hold_by_tf: dict[str, int] | None = None,
    time_stop_by_tf: dict[str, int] | None = None,
) -> PolicyResult:
    """Re-resolve every scoreable alert under `kind` ('fixed' | 'composite')."""
    max_hold = max_hold_by_tf if max_hold_by_tf is not None else MAX_HOLD_BY_TF
    time_stop = (
        time_stop_by_tf if time_stop_by_tf is not None else TIME_STOP_FLOOR_BY_TF
    )

    rows = conn.execute(_LEDGER_SQL).fetchall()
    by_group: dict[tuple[str, str], list[tuple]] = {}
    for r in rows:
        by_group.setdefault((str(r[1]), str(r[2])), []).append(r)

    trades: list[LedgerTrade] = []
    holds: list[int] = []
    rs: list[float] = []
    n_exp = 0
    n_win = 0

    for (sym, tf), grp in by_group.items():
        mh = max_hold.get(tf)
        if mh is None or tf not in _TF_MS:
            continue
        start = min(int(g[5]) for g in grp)
        end = max(int(g[5]) for g in grp) + (mh + 2) * _TF_MS[tf]
        bars = get_ohlcv(conn, sym, tf, start, end)
        if bars.empty:
            continue
        ot = bars["open_time"].to_numpy(dtype=np.int64)
        hi = bars["high"].to_numpy(dtype=np.float64)
        lo = bars["low"].to_numpy(dtype=np.float64)
        cl = bars["close"].to_numpy(dtype=np.float64)

        for sid, _s, _t, strat, direction, cts, entry, sl, rr in grp:
            pol = _policy_for(
                kind,
                tf=tf,
                rr=float(rr),
                max_hold_by_tf=max_hold,
                time_stop_by_tf=time_stop,
            )
            if pol is None:
                continue
            a = int(np.searchsorted(ot, int(cts), side="right"))
            fwd_hi = hi[a : a + mh]
            fwd_lo = lo[a : a + mh]
            fwd_cl = cl[a : a + mh]
            fwd_ot = ot[a : a + mh]
            if len(fwd_hi) == 0 or abs(float(entry) - float(sl)) <= 0.0:
                continue
            eo = replay_exits(
                fwd_hi,
                fwd_lo,
                fwd_cl,
                direction=str(direction),
                entry=float(entry),
                sl_price=float(sl),
                policy=pol,
            )
            trades.append(
                LedgerTrade(
                    signal_id=str(sid),
                    symbol=sym,
                    tf=tf,
                    strategy=str(strat),
                    direction=str(direction),
                    entry_ts_ms=int(cts),
                    exit_ts_ms=int(fwd_ot[eo.exit_bar]),
                    entry_price=float(entry),
                    sl_price=float(sl),
                    outcome=eo.outcome,
                    realized_r=eo.realized_r,
                )
            )
            holds.append(eo.exit_bar + 1)
            rs.append(eo.realized_r)
            n_exp += eo.outcome == "expired"
            n_win += eo.outcome == "win"

    n = len(trades)
    return PolicyResult(
        name=kind,
        trades=trades,
        n=n,
        expiry_rate=n_exp / n if n else 0.0,
        win_rate=n_win / n if n else 0.0,
        avg_hold_bars=float(np.mean(holds)) if holds else 0.0,
        avg_r=float(np.mean(rs)) if rs else 0.0,
    )


@dataclass(frozen=True)
class ExitAbRow:
    """One policy's portfolio + cohort line in the A/B table."""

    name: str
    n_sized: int
    n_skipped: int
    sharpe: float
    sortino: float
    max_dd: float
    expiry_rate: float
    win_rate: float
    avg_hold_bars: float
    avg_r: float


def _fixed_curve(result: BookResult, cfg: SizingConfig) -> pd.Series:
    if len(result.daily_index) == 0:
        return pd.Series(dtype=float)
    return pd.Series(
        cfg.capital + result.pnl_fixed,
        index=pd.to_datetime(result.daily_index, unit="ms"),
    )


def run_exit_ab(
    conn: duckdb.DuckDBPyConnection,
    cfg: SizingConfig,
    *,
    kinds: tuple[str, ...] = ("fixed", "composite"),
    max_hold_by_tf: dict[str, int] | None = None,
    time_stop_by_tf: dict[str, int] | None = None,
) -> list[ExitAbRow]:
    """A/B each policy through the P1 paper book; headline = fixed-basis Sharpe."""
    out: list[ExitAbRow] = []
    for kind in kinds:
        pr = resolve_ledger_under_policy(
            conn,
            kind,
            max_hold_by_tf=max_hold_by_tf,
            time_stop_by_tf=time_stop_by_tf,
        )
        book = book_from_trades(conn, cfg, pr.trades)
        curve = _fixed_curve(book, cfg)
        out.append(
            ExitAbRow(
                name=kind,
                n_sized=len(book.sized),
                n_skipped=len(book.skipped),
                sharpe=metrics.sharpe(curve),
                sortino=metrics.sortino(curve),
                max_dd=metrics.max_drawdown(curve),
                expiry_rate=pr.expiry_rate,
                win_rate=pr.win_rate,
                avg_hold_bars=pr.avg_hold_bars,
                avg_r=pr.avg_r,
            )
        )
    return out
