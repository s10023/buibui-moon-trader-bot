"""Forward-walk OHLCV to resolve outstanding signal_alert_outcomes rows.

P1 (commit a21681a) made every row carry `tp_price` + `rr_ratio` at fire time.
This module is the matching reader: scan rows where `outcome IS NULL` and
walk forward through OHLCV to record whether TP or SL was touched first,
mirroring the backtest engine semantics in `analytics/backtest/engine.py`:

  - long  win:  `high >= tp_price` before `low <= sl_price`
  - long  loss: `low  <= sl_price` before `high >= tp_price` (or same-bar tie)
  - short win:  `low  <= tp_price` before `high >= sl_price`
  - short loss: `high >= sl_price` before `low  <= tp_price` (or same-bar tie)

Same-bar TP+SL resolves to "loss" (conservative, matches the engine).

Outcomes:
  - "win"     — TP hit first. outcome_r = +rr_ratio
  - "loss"    — SL hit first or same-bar tie. outcome_r = -1.0
  - "expired" — exceeded `max_hold_bars` without hitting either.
                outcome_r = mark-to-market at the last in-window bar.
  - (NULL)    — still within hold window; retry on the next cycle.

Pure function over conn + now_ms + config dict; no clock or network I/O.
"""

import logging
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from analytics.data_store import get_ohlcv
from analytics.signal._common import parse_timeframe_secs

logger = logging.getLogger(__name__)


# Sensible defaults — roughly the median hold horizon per TF observed in
# `backtest_trades`. Override per-TF via the `[outcome_backfill]` TOML block.
DEFAULT_MAX_HOLD_BARS: dict[str, int] = {
    "15m": 96,  # 24h
    "1h": 48,  # 2d
    "4h": 30,  # 5d
    "1d": 14,  # 2w
}


def _net_outcome_r(
    raw_r: float,
    *,
    direction: str,
    entry: float,
    sl_price: float,
    entry_ts: int,
    exit_ts: int,
    fee_pct: float,
    slippage_pct: float,
    funding_times: "np.ndarray[Any, np.dtype[np.int64]] | None",
    funding_rates: "np.ndarray[Any, np.dtype[np.float64]] | None",
) -> float:
    """net_R = raw_R − fee_R − slippage_R − funding_R, mirroring Trade.pnl_r.

    Fee/slippage: 2 legs × pct × entry / risk (engine.py::Trade.pnl_r).
    Funding: stamps in (entry_ts, exit_ts] via searchsorted side="right" on
    both ends; long pays positive rates (+side_sign), short receives (−)
    — engine.py run_backtest's at-close funding block. Zero-risk rows return
    raw_r untouched: costs in R are undefined when nothing is risked (the
    engine returns pnl_r=None there; the ledger keeps the row scoreable).
    """
    risk = abs(entry - sl_price)
    if risk <= 0.0:
        return raw_r
    drag_r = 2.0 * (fee_pct + slippage_pct) * entry / risk
    funding_r = 0.0
    if funding_times is not None and funding_rates is not None:
        lo_i = int(np.searchsorted(funding_times, entry_ts, side="right"))
        hi_i = int(np.searchsorted(funding_times, exit_ts, side="right"))
        if hi_i > lo_i:
            funding_sum = float(funding_rates[lo_i:hi_i].sum())
            side_sign = 1.0 if direction == "long" else -1.0
            funding_r = side_sign * funding_sum * entry / risk
    return raw_r - drag_r - funding_r


def _scan_forward(
    bars: pd.DataFrame,
    candle_ts_ms: int,
    direction: str,
    entry: float,
    sl_price: float,
    tp_price: float,
    rr_ratio: float,
    max_hold_bars: int,
    *,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
    funding_times: "np.ndarray[Any, np.dtype[np.int64]] | None" = None,
    funding_rates: "np.ndarray[Any, np.dtype[np.float64]] | None" = None,
) -> tuple[str | None, float | None, int | None]:
    """Decide outcome for one signal given pre-fetched OHLCV bars for its TF.

    Resolved outcome_r is net of costs (P0b PR-3): the same
    net_R = raw_R − fee_R − slippage_R − funding_R the engine applies in
    Trade.pnl_r. Defaults (zero costs, no funding) reproduce the historical
    raw behaviour byte-for-byte.
    """
    post = bars[bars["open_time"] > candle_ts_ms].reset_index(drop=True)
    if post.empty:
        return None, None, None

    window = post.iloc[:max_hold_bars]
    h = window["high"].to_numpy()
    lo = window["low"].to_numpy()
    t = window["open_time"].to_numpy()

    if direction == "long":
        sl_idxs = np.nonzero(lo <= sl_price)[0]
        tp_idxs = np.nonzero(h >= tp_price)[0]
        sign = 1.0
    else:
        sl_idxs = np.nonzero(h >= sl_price)[0]
        tp_idxs = np.nonzero(lo <= tp_price)[0]
        sign = -1.0

    sl_first = int(sl_idxs[0]) if len(sl_idxs) else len(t)
    tp_first = int(tp_idxs[0]) if len(tp_idxs) else len(t)

    # Entry fills at the open of the first post-signal bar — the same
    # next-bar-open convention as the engine's Trade.entry_time. Anchors
    # the funding window (entry_ts, exit_ts].
    entry_ts = int(t[0])

    def _net(raw_r: float, exit_ts: int) -> float:
        return _net_outcome_r(
            raw_r,
            direction=direction,
            entry=entry,
            sl_price=sl_price,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
            funding_times=funding_times,
            funding_rates=funding_rates,
        )

    if sl_first <= tp_first and sl_first < len(t):
        exit_ts = int(t[sl_first])
        return "loss", _net(-1.0, exit_ts), exit_ts
    if tp_first < len(t):
        exit_ts = int(t[tp_first])
        return "win", _net(float(rr_ratio), exit_ts), exit_ts

    # Neither hit within the window so far.
    if len(window) < max_hold_bars:
        return None, None, None

    sl_dist = abs(entry - sl_price)
    last_close = float(window["close"].iloc[-1])
    mtm_r = (last_close - entry) / sl_dist * sign if sl_dist > 0 else 0.0
    exit_ts_exp = int(t[-1])
    return "expired", _net(float(mtm_r), exit_ts_exp), exit_ts_exp


def backfill_outcomes(
    conn: duckdb.DuckDBPyConnection,
    now_ms: int,
    max_hold_bars_by_tf: dict[str, int] | None = None,
    *,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
) -> dict[str, int]:
    """Resolve unresolved signal_alert_outcomes rows by walking OHLCV forward.

    Returns a counts dict: {"win": N, "loss": N, "expired": N, "open": N,
                            "no_ohlcv": N}.  "open" means the row was inspected
    but the hold window hasn't elapsed yet — it stays NULL so the next cycle
    can retry. "no_ohlcv" means we found no candles after the signal bar.

    Only rows with both `tp_price` and `sl_price` set are eligible — that
    matches the P1 fire-time persistence rule.

    Resolved `outcome_r` is net of costs — `fee_pct` / `slippage_pct` are
    per-leg fractions (same semantics as `BacktestFilterConfig`); defaults
    0.0 keep raw behaviour.
    """
    hold_map = {**DEFAULT_MAX_HOLD_BARS, **(max_hold_bars_by_tf or {})}

    rows = conn.execute(
        "SELECT signal_id, symbol, tf, direction, candle_ts_ms, "
        "entry_price, sl_price, tp_price, rr_ratio "
        "FROM signal_alert_outcomes "
        "WHERE outcome IS NULL "
        "AND tp_price IS NOT NULL "
        "AND sl_price IS NOT NULL "
        "AND entry_price IS NOT NULL "
        "AND rr_ratio IS NOT NULL"
    ).fetchall()

    counts = {"win": 0, "loss": 0, "expired": 0, "open": 0, "no_ohlcv": 0}
    if not rows:
        return counts

    # Group by (symbol, tf) so each TF's OHLCV is fetched once.
    by_tf: dict[tuple[str, str], list[tuple]] = {}
    for r in rows:
        by_tf.setdefault((r[1], r[2]), []).append(r)

    for (symbol, tf), tf_rows in by_tf.items():
        earliest_candle = min(r[4] for r in tf_rows)
        tf_secs = parse_timeframe_secs(tf)
        # Pull bars from one TF-bar after the earliest signal up to now.
        bars = get_ohlcv(conn, symbol, tf, earliest_candle + tf_secs * 1000, now_ms)
        if bars.empty:
            counts["no_ohlcv"] += len(tf_rows)
            continue

        max_hold = hold_map.get(tf, max(hold_map.values()))
        updates: list[tuple[str, float, int, str]] = []

        for (
            signal_id,
            _sym,
            _tf,
            direction,
            candle_ts_ms,
            entry_price,
            sl_price,
            tp_price,
            rr_ratio,
        ) in tf_rows:
            outcome, outcome_r, filled_at = _scan_forward(
                bars,
                int(candle_ts_ms),
                str(direction),
                float(entry_price),
                float(sl_price),
                float(tp_price),
                float(rr_ratio),
                max_hold,
                fee_pct=fee_pct,
                slippage_pct=slippage_pct,
            )
            if outcome is None:
                counts["open"] += 1
                continue
            counts[outcome] += 1
            assert outcome_r is not None and filled_at is not None
            updates.append((outcome, outcome_r, filled_at, str(signal_id)))

        if updates:
            conn.executemany(
                "UPDATE signal_alert_outcomes "
                "SET outcome = ?, outcome_r = ?, outcome_filled_at_ms = ? "
                "WHERE signal_id = ?",
                updates,
            )

    total_resolved = counts["win"] + counts["loss"] + counts["expired"]
    if total_resolved:
        logger.info(
            "Outcome backfill: %d resolved (W%d L%d E%d), %d still open, %d no-ohlcv",
            total_resolved,
            counts["win"],
            counts["loss"],
            counts["expired"],
            counts["open"],
            counts["no_ohlcv"],
        )
    return counts
