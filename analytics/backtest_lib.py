"""Pure backtest simulation engine.

Accepts OHLCV DataFrame and signals DataFrame, simulates trades, and returns
a BacktestResult with statistics.

No database access, no network calls. No module-level side effects.
"""

import functools
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


def _compute_atr14(
    highs: "np.ndarray[Any, np.dtype[np.float64]]",
    lows: "np.ndarray[Any, np.dtype[np.float64]]",
    closes: "np.ndarray[Any, np.dtype[np.float64]]",
    idx: int,
) -> float | None:
    """Return ATR14 at candle idx (mean true range over up to 14 candles ending at idx).

    True Range at candle i: max(high-low, |high-prev_close|, |low-prev_close|).
    Returns None when idx < 1 (no prior close for TR) or when ATR is zero.
    """
    if idx < 1:
        return None
    start = max(1, idx - 13)  # need prev_close, so minimum start is 1
    tr_vals: list[float] = []
    for i in range(start, idx + 1):
        hl = float(highs[i]) - float(lows[i])
        hc = abs(float(highs[i]) - float(closes[i - 1]))
        lc = abs(float(lows[i]) - float(closes[i - 1]))
        tr_vals.append(max(hl, hc, lc))
    if not tr_vals:
        return None
    atr = sum(tr_vals) / len(tr_vals)
    return atr if atr > 0.0 else None


def _is_low_volume(
    ohlcv: pd.DataFrame,
    idx: int,
    multiplier: float = 1.5,
    lookback: int = 20,
) -> bool:
    """Return True if the candle at idx has volume below multiplier × rolling mean.

    Uses the lookback candles *before* idx (no lookahead). Returns False when
    volume data is unavailable (safe default — no false suppression).
    """
    if "volume" not in ohlcv.columns or idx < 1:
        return False
    start = max(0, idx - lookback)
    prior_vols = ohlcv["volume"].iloc[start:idx].astype(float)
    if prior_vols.empty:
        return False
    avg = float(prior_vols.mean())
    if avg == 0.0:
        return False
    return float(ohlcv["volume"].iloc[idx]) < multiplier * avg


def _is_volume_spike(
    ohlcv: pd.DataFrame,
    idx: int,
    multiplier: float = 3.0,
    lookback: int = 20,
) -> bool:
    """Return True if the candle at idx has volume above multiplier × rolling mean.

    Uses the lookback candles *before* idx (no lookahead). Returns False when
    volume data is unavailable (safe default — no false boost).
    """
    if "volume" not in ohlcv.columns or idx < 1:
        return False
    start = max(0, idx - lookback)
    prior_vols = ohlcv["volume"].iloc[start:idx].astype(float)
    if prior_vols.empty:
        return False
    avg = float(prior_vols.mean())
    if avg == 0.0:
        return False
    return float(ohlcv["volume"].iloc[idx]) > multiplier * avg


@dataclass
class Trade:
    """A single simulated trade."""

    signal_time: int
    entry_time: int
    entry_price: float
    direction: str  # "long" | "short"
    sl_price: float
    tp_price: float
    exit_time: int | None = None
    exit_price: float | None = None
    outcome: str = "open"  # "win" | "loss" | "open"
    fee_pct: float = 0.0
    low_volume: bool = False  # True when signal candle volume < 1.5× rolling mean
    volume_spike: bool = False  # True when signal candle volume > 3× rolling mean

    @property
    def pnl_r(self) -> float | None:
        """P&L expressed in R multiples (1R = amount risked), after fees.

        Fee drag: each leg (entry + exit) costs fee_pct of notional.
        Position is sized to risk 1R at the actual SL distance, so:
          fee_drag_r = 2 * fee_pct * entry_price / risk

        This correctly penalises tight-SL strategies (e.g. wick_fill on 15m)
        where fees consume a large fraction of the actual risk taken.
        """
        if self.exit_price is None:
            return None
        risk = abs(self.entry_price - self.sl_price)
        if risk == 0.0:
            return None
        if self.direction == "long":
            raw_r = (self.exit_price - self.entry_price) / risk
        else:
            raw_r = (self.entry_price - self.exit_price) / risk
        fee_drag_r = 2.0 * self.fee_pct * self.entry_price / risk
        return raw_r - fee_drag_r


@dataclass
class BacktestResult:
    """Aggregated results for a strategy backtest."""

    symbol: str
    timeframe: str
    strategy: str
    fee_pct: float = 0.0
    trades: list[Trade] = field(default_factory=list)

    @functools.cached_property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.outcome != "open"]

    @functools.cached_property
    def long_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if t.direction == "long"]

    @functools.cached_property
    def short_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if t.direction == "short"]

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.closed_trades if t.outcome == "win")

    @property
    def loss_count(self) -> int:
        return sum(1 for t in self.closed_trades if t.outcome == "loss")

    @property
    def win_rate(self) -> float:
        closed = len(self.closed_trades)
        return self.win_count / closed if closed > 0 else 0.0

    @property
    def long_win_count(self) -> int:
        return sum(1 for t in self.long_closed_trades if t.outcome == "win")

    @property
    def long_win_rate(self) -> float | None:
        n = len(self.long_closed_trades)
        return self.long_win_count / n if n > 0 else None

    @property
    def long_avg_r(self) -> float | None:
        r_values = [t.pnl_r for t in self.long_closed_trades if t.pnl_r is not None]
        return sum(r_values) / len(r_values) if r_values else None

    @property
    def short_win_count(self) -> int:
        return sum(1 for t in self.short_closed_trades if t.outcome == "win")

    @property
    def short_win_rate(self) -> float | None:
        n = len(self.short_closed_trades)
        return self.short_win_count / n if n > 0 else None

    @property
    def short_avg_r(self) -> float | None:
        r_values = [t.pnl_r for t in self.short_closed_trades if t.pnl_r is not None]
        return sum(r_values) / len(r_values) if r_values else None

    @property
    def avg_r(self) -> float:
        r_values = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(r_values) / len(r_values) if r_values else 0.0

    @property
    def total_r(self) -> float:
        r_values = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(r_values)

    @property
    def long_total_r(self) -> float:
        r_values = [t.pnl_r for t in self.long_closed_trades if t.pnl_r is not None]
        return sum(r_values)

    @property
    def short_total_r(self) -> float:
        r_values = [t.pnl_r for t in self.short_closed_trades if t.pnl_r is not None]
        return sum(r_values)

    @functools.cached_property
    def low_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if t.low_volume]

    @functools.cached_property
    def normal_vol_closed_trades(self) -> list[Trade]:
        return [
            t for t in self.closed_trades if not t.low_volume and not t.volume_spike
        ]

    @functools.cached_property
    def spike_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if t.volume_spike]

    @property
    def low_vol_avg_r(self) -> float | None:
        r_vals = [t.pnl_r for t in self.low_vol_closed_trades if t.pnl_r is not None]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def normal_vol_avg_r(self) -> float | None:
        r_vals = [t.pnl_r for t in self.normal_vol_closed_trades if t.pnl_r is not None]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def spike_vol_avg_r(self) -> float | None:
        r_vals = [t.pnl_r for t in self.spike_vol_closed_trades if t.pnl_r is not None]
        return sum(r_vals) / len(r_vals) if r_vals else None

    # --- Directional × volume cross-tab ---

    @functools.cached_property
    def long_low_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.long_closed_trades if t.low_volume]

    @functools.cached_property
    def long_normal_vol_closed_trades(self) -> list[Trade]:
        return [
            t
            for t in self.long_closed_trades
            if not t.low_volume and not t.volume_spike
        ]

    @functools.cached_property
    def long_spike_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.long_closed_trades if t.volume_spike]

    @functools.cached_property
    def short_low_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.short_closed_trades if t.low_volume]

    @functools.cached_property
    def short_normal_vol_closed_trades(self) -> list[Trade]:
        return [
            t
            for t in self.short_closed_trades
            if not t.low_volume and not t.volume_spike
        ]

    @functools.cached_property
    def short_spike_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.short_closed_trades if t.volume_spike]

    @property
    def long_low_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.long_low_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def long_normal_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.long_normal_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def long_spike_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.long_spike_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def short_low_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.short_low_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def short_normal_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.short_normal_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def short_spike_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.short_spike_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def durations_h(self) -> list[float]:
        """Hold time in hours for each closed trade."""
        return [
            (t.exit_time - t.entry_time) / 3_600_000
            for t in self.closed_trades
            if t.exit_time is not None
        ]

    @property
    def avg_duration_h(self) -> float | None:
        d = self.durations_h
        return sum(d) / len(d) if d else None

    @property
    def median_duration_h(self) -> float | None:
        d = sorted(self.durations_h)
        n = len(d)
        if not n:
            return None
        mid = n // 2
        return (d[mid - 1] + d[mid]) / 2 if n % 2 == 0 else d[mid]

    @property
    def long_median_duration_h(self) -> float | None:
        d = sorted(
            (t.exit_time - t.entry_time) / 3_600_000
            for t in self.long_closed_trades
            if t.exit_time is not None
        )
        n = len(d)
        if not n:
            return None
        mid = n // 2
        return (d[mid - 1] + d[mid]) / 2 if n % 2 == 0 else d[mid]

    @property
    def short_median_duration_h(self) -> float | None:
        d = sorted(
            (t.exit_time - t.entry_time) / 3_600_000
            for t in self.short_closed_trades
            if t.exit_time is not None
        )
        n = len(d)
        if not n:
            return None
        mid = n // 2
        return (d[mid - 1] + d[mid]) / 2 if n % 2 == 0 else d[mid]

    @property
    def max_drawdown_r(self) -> float:
        """Largest peak-to-trough drawdown in cumulative R."""
        r_values = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        if not r_values:
            return 0.0
        peak = 0.0
        cumulative = 0.0
        max_dd = 0.0
        for r in r_values:
            cumulative += r
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def recovery_factor(self) -> float:
        """Total R divided by max drawdown. 0.0 when max drawdown is zero."""
        dd = self.max_drawdown_r
        return self.total_r / dd if dd > 0 else 0.0


def run_backtest(
    ohlcv: pd.DataFrame,
    signals: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    fee_pct: float = 0.0,
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    volume_suppress: bool = False,
    volume_spike_boost: bool = False,
    volume_suppress_long: bool | None = None,
    volume_suppress_short: bool | None = None,
    volume_spike_boost_long: bool | None = None,
    volume_spike_boost_short: bool | None = None,
    tp_r_long: float | None = None,
    tp_r_short: float | None = None,
) -> BacktestResult:
    """Simulate trades from signals on historical OHLCV.

    Entry:  next candle's open after the signal candle.
    SL:     per-signal sl_price from the signals DataFrame when present (structural);
            otherwise atr_sl_multiplier × ATR14 when set (volatility-adaptive);
            otherwise sl_pct fraction of entry price (fixed fallback).
            min_sl_pct enforces a minimum SL distance from entry (e.g. 0.005 = 0.5%),
            widening SLs that land too close to entry.
    TP:     tp_r × risk distance from entry price. When tp_r_long / tp_r_short are set,
            the directional value is used instead of tp_r for that trade direction.
    fee_pct: taker fee fraction applied on both entry and exit legs
             (e.g. 0.0005 for 0.05%). Fee drag is deducted from pnl_r.

    A trade closes when a candle's high or low touches the SL or TP level.
    Trades still open at end of data are marked as outcome="open".
    """
    result = BacktestResult(
        symbol=symbol, timeframe=timeframe, strategy=strategy, fee_pct=fee_pct
    )

    if signals.empty or ohlcv.empty:
        return result

    has_per_signal_sl = "sl_price" in signals.columns

    # Pre-extract OHLCV arrays once — avoids per-row pandas overhead in inner loops.
    ohlcv_times_np = ohlcv["open_time"].to_numpy(dtype=np.int64)
    opens_np = ohlcv["open"].to_numpy(dtype=float)
    highs_np = ohlcv["high"].to_numpy(dtype=float)
    lows_np = ohlcv["low"].to_numpy(dtype=float)
    closes_np = ohlcv["close"].to_numpy(dtype=float)
    time_to_idx: dict[int, int] = {int(t): i for i, t in enumerate(ohlcv_times_np)}
    n_candles = len(ohlcv_times_np)

    # Pre-extract signal arrays once — avoids creating a pandas Series per row.
    sig_times_np = signals["open_time"].to_numpy(dtype=np.int64)
    sig_dirs_np = signals["direction"].to_numpy(dtype=object)
    sig_sl_np = signals["sl_price"].to_numpy(dtype=float) if has_per_signal_sl else None

    for si in range(len(sig_times_np)):
        signal_time = int(sig_times_np[si])
        direction = str(sig_dirs_np[si])

        if signal_time not in time_to_idx:
            continue
        sig_idx = time_to_idx[signal_time]
        entry_idx = sig_idx + 1

        if entry_idx >= n_candles:
            continue

        # Volume classification — computed once, used for both the suppress gate
        # and the Trade tag so they are always consistent.
        is_spike = _is_volume_spike(ohlcv, sig_idx)
        is_low_vol = _is_low_volume(ohlcv, sig_idx)

        # Volume suppression: skip low-volume signal candles when enabled.
        # Directional params (volume_suppress_long / volume_suppress_short) take
        # precedence over the symmetric volume_suppress for their respective direction.
        # Spike boost: exempt high-conviction candles (> 3× mean) from suppression.
        # Directional spike boost params override the symmetric volume_spike_boost.
        _suppress = (
            volume_suppress_long
            if direction == "long" and volume_suppress_long is not None
            else volume_suppress_short
            if direction == "short" and volume_suppress_short is not None
            else volume_suppress
        )
        _boost = (
            volume_spike_boost_long
            if direction == "long" and volume_spike_boost_long is not None
            else volume_spike_boost_short
            if direction == "short" and volume_spike_boost_short is not None
            else volume_spike_boost
        )
        if _suppress and is_low_vol:
            if not (_boost and is_spike):
                continue

        entry_time = int(ohlcv_times_np[entry_idx])
        entry_price = opens_np[entry_idx]

        # Resolve direction-split TP multiple for this trade.
        if direction == "long" and tp_r_long is not None:
            eff_tp_r = tp_r_long
        elif direction == "short" and tp_r_short is not None:
            eff_tp_r = tp_r_short
        else:
            eff_tp_r = tp_r

        # SL priority: structural (per-signal) → ATR-based → fixed sl_pct fraction.
        if sig_sl_np is not None:
            sl_price = sig_sl_np[si]
            # Enforce minimum SL distance — widening structural SLs that land
            # too close to entry (prevents fee-drag explosion on tight SLs).
            if min_sl_pct > 0.0:
                min_dist = entry_price * min_sl_pct
                if direction == "long":
                    sl_price = min(sl_price, entry_price - min_dist)
                else:
                    sl_price = max(sl_price, entry_price + min_dist)
            if direction == "long":
                tp_price = entry_price + eff_tp_r * abs(entry_price - sl_price)
            else:
                tp_price = entry_price - eff_tp_r * abs(entry_price - sl_price)
        elif atr_sl_multiplier is not None:
            atr = _compute_atr14(highs_np, lows_np, closes_np, sig_idx)
            if atr is not None:
                sl_dist = atr_sl_multiplier * atr
                if min_sl_pct > 0.0:
                    sl_dist = max(sl_dist, entry_price * min_sl_pct)
                if direction == "long":
                    sl_price = entry_price - sl_dist
                    tp_price = entry_price + eff_tp_r * sl_dist
                else:
                    sl_price = entry_price + sl_dist
                    tp_price = entry_price - eff_tp_r * sl_dist
            else:
                # Fallback to sl_pct when ATR unavailable (e.g. signal at candle 0)
                if direction == "long":
                    sl_price = entry_price * (1.0 - sl_pct)
                    tp_price = entry_price + eff_tp_r * (entry_price - sl_price)
                else:
                    sl_price = entry_price * (1.0 + sl_pct)
                    tp_price = entry_price - eff_tp_r * (sl_price - entry_price)
        elif direction == "long":
            sl_price = entry_price * (1.0 - sl_pct)
            tp_price = entry_price + eff_tp_r * (entry_price - sl_price)
        else:
            sl_price = entry_price * (1.0 + sl_pct)
            tp_price = entry_price - eff_tp_r * (sl_price - entry_price)

        trade = Trade(
            signal_time=signal_time,
            entry_time=entry_time,
            entry_price=entry_price,
            direction=direction,
            sl_price=sl_price,
            tp_price=tp_price,
            fee_pct=fee_pct,
            low_volume=is_low_vol,
            volume_spike=is_spike,
        )

        # Vectorized candle scan: find first SL-hit and first TP-hit index using
        # numpy nonzero (C loop) instead of a Python for-loop over ohlcv.iloc[i].
        h = highs_np[entry_idx:]
        lo = lows_np[entry_idx:]
        t = ohlcv_times_np[entry_idx:]

        if direction == "long":
            sl_idxs = np.nonzero(lo <= sl_price)[0]
            tp_idxs = np.nonzero(h >= tp_price)[0]
        else:
            sl_idxs = np.nonzero(h >= sl_price)[0]
            tp_idxs = np.nonzero(lo <= tp_price)[0]

        sl_first = int(sl_idxs[0]) if len(sl_idxs) else len(t)
        tp_first = int(tp_idxs[0]) if len(tp_idxs) else len(t)

        # SL takes priority on a same-candle tie (mirrors the original sequential check).
        if sl_first <= tp_first and sl_first < len(t):
            trade.exit_time = int(t[sl_first])
            trade.exit_price = sl_price
            trade.outcome = "loss"
        elif tp_first < len(t):
            trade.exit_time = int(t[tp_first])
            trade.exit_price = tp_price
            trade.outcome = "win"
        # else: neither hit → trade remains open

        result.trades.append(trade)

    return result


@dataclass
class ComboBacktestResult:
    """Co-firing confluence backtest: two strategies must fire within ±window candles."""

    strategy_a: str
    strategy_b: str
    window: int
    result: BacktestResult  # underlying result; result.strategy = "a+b"


def _find_cofire_signals(
    signals_a: pd.DataFrame,
    signals_b: pd.DataFrame,
    ohlcv: pd.DataFrame,
    window: int = 5,
    min_signals: int = 3,
) -> pd.DataFrame:
    """Return a signals DataFrame from co-firing pairs within ±window candles.

    For each signal in A (sorted by time), find the nearest unused signal in B
    with the same direction within ±window candles. Entry uses the later signal's
    candle (open_time, sl_price, tp_price). Each B signal is matched at most once.
    Returns an empty DataFrame when either strategy has fewer than min_signals.
    """
    from analytics.indicators_lib import SIGNAL_COLUMNS

    empty = pd.DataFrame(columns=SIGNAL_COLUMNS)
    if signals_a.empty or signals_b.empty:
        return empty
    if len(signals_a) < min_signals or len(signals_b) < min_signals:
        return empty

    # Positional index: open_time → row position in ohlcv for distance calculation.
    time_to_idx: dict[int, int] = {int(t): i for i, t in enumerate(ohlcv["open_time"])}

    b_times = [int(t) for t in signals_b["open_time"]]
    b_dirs = list(signals_b["direction"])
    b_indices = [time_to_idx.get(t) for t in b_times]

    used_b: set[int] = set()
    matched: list[dict[str, object]] = []

    for _, row_a in signals_a.iterrows():
        t_a = int(row_a["open_time"])
        dir_a = str(row_a["direction"])
        idx_a = time_to_idx.get(t_a)
        if idx_a is None:
            continue

        best_j: int | None = None
        best_dist = window + 1
        for j, (t_b, dir_b, idx_b) in enumerate(zip(b_times, b_dirs, b_indices)):
            if j in used_b or dir_b != dir_a or idx_b is None:
                continue
            dist = abs(idx_b - idx_a)
            if dist <= window and dist < best_dist:
                best_j = j
                best_dist = dist

        if best_j is None:
            continue

        used_b.add(best_j)
        row_b = signals_b.iloc[best_j]
        t_b = b_times[best_j]

        # Entry at the later signal's next open — carry its SL/TP metadata.
        later = row_b if t_b >= t_a else row_a
        matched.append(
            {
                "open_time": int(later["open_time"]),
                "direction": dir_a,
                "reason": f"{row_a.get('reason', '')} ↔ {row_b.get('reason', '')}",
                "sl_price": float(later["sl_price"])
                if "sl_price" in later.index and later["sl_price"]
                else 0.0,
                "context": f"combo|{row_a.get('context', '')}",
                "low_volume": bool(later.get("low_volume", False)),
                "tp_price": float(later["tp_price"])
                if "tp_price" in later.index and later["tp_price"]
                else 0.0,
            }
        )

    if not matched:
        return empty
    return pd.DataFrame(matched)[SIGNAL_COLUMNS].reset_index(drop=True)


def run_combo_backtest(
    ohlcv: pd.DataFrame,
    signals_a: pd.DataFrame,
    signals_b: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy_a: str,
    strategy_b: str,
    window: int = 5,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    fee_pct: float = 0.0,
    min_sl_pct: float = 0.0,
    min_signals: int = 3,
) -> ComboBacktestResult:
    """Run a co-firing confluence backtest for a pair of strategies.

    Detects co-firing pairs within ±window candles and simulates trades using
    the later signal's candle as entry. Dead strategies (< min_signals signals)
    are auto-skipped — the result will have zero trades.
    """
    combo_label = f"{strategy_a}+{strategy_b}"
    combo_signals = _find_cofire_signals(
        signals_a, signals_b, ohlcv, window=window, min_signals=min_signals
    )
    result = run_backtest(
        ohlcv,
        combo_signals,
        symbol,
        timeframe,
        combo_label,
        sl_pct,
        tp_r,
        fee_pct,
        min_sl_pct=min_sl_pct,
    )
    return ComboBacktestResult(
        strategy_a=strategy_a,
        strategy_b=strategy_b,
        window=window,
        result=result,
    )


@dataclass
class CrossTfComboBacktestResult:
    """Cross-timeframe co-firing backtest: HTF sets context, LTF provides entry.

    strategy_htf fired on tf_htf; strategy_ltf fired on tf_ltf within window_hours.
    The underlying BacktestResult is run on the filtered LTF signals.
    """

    strategy_htf: str
    strategy_ltf: str
    tf_htf: str
    tf_ltf: str
    window_hours: float
    result: BacktestResult  # result.timeframe == tf_ltf (entry TF)


def _find_cross_tf_signals(
    signals_htf: pd.DataFrame,
    signals_ltf: pd.DataFrame,
    window_hours: float,
    min_signals: int = 3,
) -> pd.DataFrame:
    """Return filtered LTF signals that have an HTF signal within the lookback window.

    For each LTF signal, checks whether a same-direction HTF signal fired within
    [ltf_time - window_hours, ltf_time]. The most recent qualifying HTF signal is
    used (no exclusivity — one HTF signal can confirm multiple LTF signals).
    Returns an empty DataFrame when either input has fewer than min_signals.
    """
    from analytics.indicators_lib import SIGNAL_COLUMNS

    empty = pd.DataFrame(columns=SIGNAL_COLUMNS)
    if signals_htf.empty or signals_ltf.empty:
        return empty
    if len(signals_htf) < min_signals or len(signals_ltf) < min_signals:
        return empty

    window_ms = int(window_hours * 3600 * 1000)

    htf_times = signals_htf["open_time"].astype("int64").tolist()
    htf_dirs = list(signals_htf["direction"])

    matched: list[dict[str, object]] = []

    for _, ltf_row in signals_ltf.iterrows():
        ltf_time = int(ltf_row["open_time"])
        ltf_dir = str(ltf_row["direction"])
        window_start = ltf_time - window_ms

        # Find the most recent HTF signal in the window with matching direction.
        best_htf_time: int | None = None
        for htf_t, htf_d in zip(htf_times, htf_dirs):
            if htf_d != ltf_dir:
                continue
            if window_start <= htf_t <= ltf_time:
                if best_htf_time is None or htf_t > best_htf_time:
                    best_htf_time = htf_t

        if best_htf_time is None:
            continue

        matched.append(
            {
                "open_time": ltf_time,
                "direction": ltf_dir,
                "reason": str(ltf_row.get("reason", "")),
                "sl_price": float(ltf_row["sl_price"])
                if "sl_price" in ltf_row.index and ltf_row["sl_price"]
                else 0.0,
                "context": f"cross_tf|{ltf_row.get('context', '')}",
                "low_volume": bool(ltf_row.get("low_volume", False)),
                "tp_price": float(ltf_row["tp_price"])
                if "tp_price" in ltf_row.index and ltf_row["tp_price"]
                else 0.0,
            }
        )

    if not matched:
        return empty
    return pd.DataFrame(matched)[SIGNAL_COLUMNS].reset_index(drop=True)


def run_cross_tf_combo_backtest(
    ohlcv_ltf: pd.DataFrame,
    signals_htf: pd.DataFrame,
    signals_ltf: pd.DataFrame,
    symbol: str,
    tf_htf: str,
    tf_ltf: str,
    strategy_htf: str,
    strategy_ltf: str,
    window_hours: float = 4.0,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    fee_pct: float = 0.0,
    min_sl_pct: float = 0.0,
    min_signals: int = 3,
) -> CrossTfComboBacktestResult:
    """Run a cross-TF co-firing backtest: HTF context + LTF entry.

    Filters LTF signals to those where a same-direction HTF signal fired within
    window_hours. Trade entry/SL/TP follow the LTF signal. Dead strategies
    (< min_signals) are auto-skipped — result will have zero trades.
    """
    combo_label = f"{strategy_htf}@{tf_htf}+{strategy_ltf}@{tf_ltf}"
    filtered_ltf = _find_cross_tf_signals(
        signals_htf, signals_ltf, window_hours=window_hours, min_signals=min_signals
    )
    result = run_backtest(
        ohlcv_ltf,
        filtered_ltf,
        symbol,
        tf_ltf,
        combo_label,
        sl_pct,
        tp_r,
        fee_pct,
        min_sl_pct=min_sl_pct,
    )
    return CrossTfComboBacktestResult(
        strategy_htf=strategy_htf,
        strategy_ltf=strategy_ltf,
        tf_htf=tf_htf,
        tf_ltf=tf_ltf,
        window_hours=window_hours,
        result=result,
    )


def format_cross_tf_combo_table(
    results: "list[CrossTfComboBacktestResult]",
    min_trades: int = 3,
) -> str:
    """Format cross-TF combo results as a sorted table (avg_r descending)."""
    rows = [
        (
            f"{r.strategy_htf}@{r.tf_htf}+{r.strategy_ltf}@{r.tf_ltf}",
            r.result.symbol,
            r.window_hours,
            len(r.result.closed_trades),
            r.result.win_rate,
            r.result.avg_r,
            r.result.total_r,
        )
        for r in results
        if len(r.result.closed_trades) >= min_trades
    ]
    if not rows:
        return f"No cross-TF combos with >= {min_trades} trades."
    rows.sort(key=lambda x: x[5], reverse=True)
    header = f"{'Pair':<42} {'Symbol':<10} {'Win%':>5} {'W':>3} {'N':>4} {'AvgR':>6} {'TotR':>6}"
    lines = [header, "-" * len(header)]
    for pair, sym, _wh, n, wr, avg_r, tot_r in rows:
        lines.append(
            f"{pair:<42} {sym:<10} {wr:>4.0%} {int(wr * n):>3} {n:>4} {avg_r:>6.2f} {tot_r:>6.2f}"
        )
    return "\n".join(lines)


def format_combo_table(
    combo_results: list[ComboBacktestResult],
    min_trades: int = 3,
) -> str:
    """Format combo backtest results as a sorted table (avg_r descending)."""
    rows = [
        (
            f"{c.strategy_a}+{c.strategy_b}",
            c.result.symbol,
            c.result.timeframe,
            len(c.result.closed_trades),
            c.result.win_rate,
            c.result.avg_r,
            c.result.total_r,
            c.result.max_drawdown_r,
        )
        for c in combo_results
        if len(c.result.closed_trades) >= min_trades
    ]
    rows.sort(key=lambda r: r[5], reverse=True)  # sort by avg_r

    col = (32, 10, 5, 8, 8, 8, 9, 8)
    header = (
        f"  {'Combo':<{col[0]}}"
        f"{'Symbol':<{col[1]}}"
        f"{'TF':<{col[2]}}"
        f"{'Trades':>{col[3]}}"
        f"{'Win%':>{col[4]}}"
        f"{'AvgR':>{col[5]}}"
        f"{'TotalR':>{col[6]}}"
        f"{'MaxDD':>{col[7]}}"
    )
    sep = "  " + "─" * (sum(col) + 2)
    thick = "═" * (sum(col) + 4)
    lines = [
        f"\nStrategy Co-firing Results (±{combo_results[0].window if combo_results else 5} candles, min_trades={min_trades})",
        thick,
        header,
        sep,
    ]
    for combo, sym, tf, n, wr, avg_r, tot_r, dd in rows:
        lines.append(
            f"  {combo:<{col[0]}}"
            f"{sym:<{col[1]}}"
            f"{tf:<{col[2]}}"
            f"{n:>{col[3]}}"
            f"{wr:>{col[4] - 1}.1%} "
            f"{avg_r:>+{col[5]}.2f}R"
            f"{tot_r:>+{col[6]}.2f}R"
            f"{dd:>{col[7]}.2f}R"
        )
    if not rows:
        lines.append("  No combos met min_trades threshold.")
    lines.append(sep)
    return "\n".join(lines)


def format_result(result: BacktestResult) -> str:
    """Format a BacktestResult as a human-readable text summary."""
    fee_label = f"{result.fee_pct * 100:.4f}% taker" if result.fee_pct > 0.0 else "none"
    lines = [
        f"Backtest: {result.symbol} {result.timeframe} — {result.strategy}",
        "─" * 52,
        f"Signals:     {len(result.trades)} total, {len(result.closed_trades)} closed",
        f"Win rate:    {result.win_rate:.1%}  ({result.win_count}W / {result.loss_count}L)",
        f"Avg R:       {result.avg_r:+.2f}R",
        f"Total R:     {result.total_r:+.2f}R",
        f"Max DD:      -{result.max_drawdown_r:.2f}R",
        f"Fees:        {fee_label}",
    ]
    return "\n".join(lines)


def filter_signals_by_day(
    signals: pd.DataFrame, allowed_weekdays: list[int] | None = None
) -> pd.DataFrame:
    """Filter signals to only those whose open_time falls on allowed weekdays (UTC).

    allowed_weekdays: list of Python weekday ints (Mon=0 … Sun=6).
    None means no filter (all days pass).
    """
    if signals.empty or allowed_weekdays is None:
        return signals
    weekdays = pd.to_datetime(signals["open_time"], unit="ms", utc=True).dt.weekday
    return signals[weekdays.isin(allowed_weekdays)].reset_index(drop=True)


def format_volume_split(results: list[BacktestResult]) -> str:
    """Show avg R split by low-volume / normal / spike trades, aggregated by strategy.

    Delta = normal_avg_r − low_vol_avg_r.
    Positive delta means normal-volume trades outperform → volume filter would help.
    Spike avg R shows whether high-conviction candles have additional edge.
    """
    from collections import defaultdict

    low_by_strat: dict[str, list[Trade]] = defaultdict(list)
    norm_by_strat: dict[str, list[Trade]] = defaultdict(list)
    spike_by_strat: dict[str, list[Trade]] = defaultdict(list)
    for r in results:
        low_by_strat[r.strategy].extend(r.low_vol_closed_trades)
        norm_by_strat[r.strategy].extend(r.normal_vol_closed_trades)
        spike_by_strat[r.strategy].extend(r.spike_vol_closed_trades)

    strategies = sorted(set(low_by_strat) | set(norm_by_strat) | set(spike_by_strat))

    def _avg(trades: list[Trade]) -> float | None:
        vals = [t.pnl_r for t in trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else None

    col = (22, 8, 7, 8, 7, 8, 7, 7)
    header = (
        f"  {'Strategy':<{col[0]}}"
        f"{'Low-vol':>{col[1]}}"
        f"{'Avg R':>{col[2]}}"
        f"  {'Normal':>{col[3]}}"
        f"{'Avg R':>{col[4]}}"
        f"  {'Spike':>{col[5]}}"
        f"{'Avg R':>{col[6]}}"
        f"  {'Delta':>{col[7]}}"
    )
    sep = "  " + "─" * (sum(col) + 6)
    thick = "═" * (sum(col) + 8)

    lines = [
        "\nVolume Impact (aggregated across all symbols × TFs)",
        thick,
        header,
        sep,
    ]

    for s in strategies:
        low = low_by_strat[s]
        norm = norm_by_strat[s]
        spike = spike_by_strat[s]
        low_r = _avg(low)
        norm_r = _avg(norm)
        spike_r = _avg(spike)
        delta = (norm_r - low_r) if (low_r is not None and norm_r is not None) else None
        low_r_s = f"{low_r:+.2f}R" if low_r is not None else "  n/a"
        norm_r_s = f"{norm_r:+.2f}R" if norm_r is not None else "  n/a"
        spike_r_s = f"{spike_r:+.2f}R" if spike_r is not None else "  n/a"
        delta_s = f"{delta:+.2f}R" if delta is not None else "  n/a"
        lines.append(
            f"  {s:<{col[0]}}"
            f"{len(low):>{col[1]}}"
            f"{low_r_s:>{col[2]}}"
            f"  {len(norm):>{col[3]}}"
            f"{norm_r_s:>{col[4]}}"
            f"  {len(spike):>{col[5]}}"
            f"{spike_r_s:>{col[6]}}"
            f"  {delta_s:>{col[7]}}"
        )

    lines.append(sep)
    lines.append(
        "  Delta = normal_avg_r − low_vol_avg_r  "
        "(positive = volume filter would help, negative = hurts)"
    )
    return "\n".join(lines)


def format_directional_volume_split(results: list[BacktestResult]) -> str:
    """Show avg R split by volume tier × direction (LONG / SHORT), aggregated by strategy.

    Use this to decide whether volume_suppress_long / volume_suppress_short
    or volume_spike_boost_long / volume_spike_boost_short are warranted.
    A large per-direction delta signals a directional suppress/boost opportunity.
    """
    from collections import defaultdict

    long_low: dict[str, list[Trade]] = defaultdict(list)
    long_norm: dict[str, list[Trade]] = defaultdict(list)
    long_spike: dict[str, list[Trade]] = defaultdict(list)
    short_low: dict[str, list[Trade]] = defaultdict(list)
    short_norm: dict[str, list[Trade]] = defaultdict(list)
    short_spike: dict[str, list[Trade]] = defaultdict(list)

    for r in results:
        long_low[r.strategy].extend(r.long_low_vol_closed_trades)
        long_norm[r.strategy].extend(r.long_normal_vol_closed_trades)
        long_spike[r.strategy].extend(r.long_spike_vol_closed_trades)
        short_low[r.strategy].extend(r.short_low_vol_closed_trades)
        short_norm[r.strategy].extend(r.short_normal_vol_closed_trades)
        short_spike[r.strategy].extend(r.short_spike_vol_closed_trades)

    strategies = sorted(
        set(long_low)
        | set(long_norm)
        | set(long_spike)
        | set(short_low)
        | set(short_norm)
        | set(short_spike)
    )

    def _avg(trades: list[Trade]) -> float | None:
        vals = [t.pnl_r for t in trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else None

    def _r(v: float | None) -> str:
        return f"{v:+.2f}R" if v is not None else "  n/a"

    thick = "═" * 90
    lines = [
        "\nDirectional Volume Split (LONG / SHORT × Low / Normal / Spike avg R)",
        thick,
        f"  {'Strategy':<22}  {'Dir':>4}  {'Low-vol':>7}  {'n':>4}  "
        f"{'Normal':>7}  {'n':>4}  {'Spike':>7}  {'n':>4}  {'Δ(norm-low)':>11}",
        "  " + "─" * 87,
    ]

    for s in strategies:
        for label, low, norm, spike in [
            ("↑", long_low[s], long_norm[s], long_spike[s]),
            ("↓", short_low[s], short_norm[s], short_spike[s]),
        ]:
            low_r = _avg(low)
            norm_r = _avg(norm)
            spike_r = _avg(spike)
            delta = (
                (norm_r - low_r) if (low_r is not None and norm_r is not None) else None
            )
            lines.append(
                f"  {s:<22}  {label:>4}  {_r(low_r):>7}  {len(low):>4}  "
                f"{_r(norm_r):>7}  {len(norm):>4}  {_r(spike_r):>7}  {len(spike):>4}  "
                f"{_r(delta):>11}"
            )

    lines.append("  " + "─" * 87)
    lines.append(
        "  Δ(norm-low) > 0 → suppressing lows for that direction would help. "
        "Spike > norm → boost that direction."
    )
    return "\n".join(lines)


_TF_ORDER = {
    "1m": 0,
    "3m": 1,
    "5m": 2,
    "15m": 3,
    "30m": 4,
    "1h": 5,
    "2h": 6,
    "4h": 7,
    "1d": 8,
    "1w": 9,
}


def _tf_sort_key(tf: str) -> int:
    return _TF_ORDER.get(tf, 99)


def format_duration_table(results: list[BacktestResult]) -> str:
    """Show avg and median hold time per strategy × TF, aggregated across symbols."""
    from collections import defaultdict

    durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in results:
        durations[(r.strategy, r.timeframe)].extend(r.durations_h)

    keys = sorted(durations, key=lambda k: (k[0], _tf_sort_key(k[1])))

    def _median(vals: list[float]) -> float | None:
        s = sorted(vals)
        n = len(s)
        if not n:
            return None
        mid = n // 2
        return (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else s[mid]

    def _fmt(h: float | None) -> str:
        if h is None:
            return "  n/a"
        if h < 24:
            return f"{h:.1f}h"
        return f"{h / 24:.1f}d"

    col = (22, 6, 8, 8, 8, 8)
    header = (
        f"  {'Strategy':<{col[0]}}"
        f"{'TF':<{col[1]}}"
        f"{'Trades':>{col[2]}}"
        f"{'Avg':>{col[3]}}"
        f"{'Median':>{col[4]}}"
        f"{'Max':>{col[5]}}"
    )
    sep = "  " + "─" * (sum(col) + 2)
    thick = "═" * (sum(col) + 4)

    lines = ["\nTrade Duration (aggregated across symbols)", thick, header, sep]

    prev_strat = ""
    for strat, tf in keys:
        d = durations[(strat, tf)]
        if not d:
            continue
        avg = sum(d) / len(d)
        med = _median(d)
        label = strat if strat != prev_strat else ""
        prev_strat = strat
        lines.append(
            f"  {label:<{col[0]}}"
            f"{tf:<{col[1]}}"
            f"{len(d):>{col[2]}}"
            f"{_fmt(avg):>{col[3]}}"
            f"{_fmt(med):>{col[4]}}"
            f"{_fmt(max(d)):>{col[5]}}"
        )

    lines.append(sep)
    return "\n".join(lines)


def format_tp_sweep_table(
    results_by_tp: dict[float, list[BacktestResult]],
) -> str:
    """Show avg R per strategy × TF for each tp_r value, aggregated across symbols.

    results_by_tp: {tp_r_value: list[BacktestResult]}
    """
    tp_values = sorted(results_by_tp)

    keys: set[tuple[str, str]] = set()
    for results in results_by_tp.values():
        for r in results:
            keys.add((r.strategy, r.timeframe))
    sorted_keys = sorted(keys, key=lambda k: (k[0], _tf_sort_key(k[1])))

    def _collect(
        results: list[BacktestResult], strategy: str, tf: str
    ) -> tuple[float | None, float | None]:
        """Return (avg_r, win_rate) aggregated across all symbols for this strategy×TF."""
        trades: list[Trade] = []
        for r in results:
            if r.strategy == strategy and r.timeframe == tf:
                trades.extend(r.closed_trades)
        if not trades:
            return None, None
        r_vals = [t.pnl_r for t in trades if t.pnl_r is not None]
        avg = sum(r_vals) / len(r_vals) if r_vals else None
        wins = sum(1 for t in trades if t.outcome == "win")
        wr = wins / len(trades) if trades else None
        return avg, wr

    tp_col_w = 8
    name_col_w = 22
    tf_col_w = 6
    header = f"  {'Strategy':<{name_col_w}}{'TF':<{tf_col_w}}" + "".join(
        f"{tp_r:.1f}R".rjust(tp_col_w) for tp_r in tp_values
    )
    total_w = name_col_w + tf_col_w + tp_col_w * len(tp_values) + 2
    sep = "  " + "─" * total_w
    thick = "═" * (total_w + 2)

    lines = ["\nTP Ratio Comparison (aggregated across symbols)", thick, header, sep]

    prev_strat = ""
    for strat, tf in sorted_keys:
        label = strat if strat != prev_strat else ""
        prev_strat = strat
        row_r = f"  {label:<{name_col_w}}{tf:<{tf_col_w}}"
        row_w = f"  {'':>{name_col_w}}{'':>{tf_col_w}}"
        for tp in tp_values:
            avg, wr = _collect(results_by_tp[tp], strat, tf)
            cell_r = f"{avg:+.2f}R" if avg is not None else "  n/a"
            cell_w = f"{wr:.0%}" if wr is not None else ""
            row_r += f"{cell_r:>{tp_col_w}}"
            row_w += f"{cell_w:>{tp_col_w}}"
        lines.append(row_r)
        lines.append(row_w)

    lines.append(sep)
    lines.append("  Pick the tp_r column where avg R peaks per strategy × TF row.")
    lines.append(
        "  Watch win% — declining win% at high TP = lottery-ticket bias (discard)."
    )
    return "\n".join(lines)


def format_atr_sl_sweep_table(
    results_by_atr: dict[float, list[BacktestResult]],
) -> str:
    """Show avg R per strategy × TF for each atr_sl_multiplier value, aggregated across symbols.

    results_by_atr: {atr_sl_multiplier_value: list[BacktestResult]}
    """
    atr_values = sorted(results_by_atr)

    keys: set[tuple[str, str]] = set()
    for results in results_by_atr.values():
        for r in results:
            keys.add((r.strategy, r.timeframe))
    sorted_keys = sorted(keys, key=lambda k: (k[0], _tf_sort_key(k[1])))

    def _collect(
        results: list[BacktestResult], strategy: str, tf: str
    ) -> tuple[float | None, float | None]:
        trades: list[Trade] = []
        for r in results:
            if r.strategy == strategy and r.timeframe == tf:
                trades.extend(r.closed_trades)
        if not trades:
            return None, None
        r_vals = [t.pnl_r for t in trades if t.pnl_r is not None]
        avg = sum(r_vals) / len(r_vals) if r_vals else None
        wins = sum(1 for t in trades if t.outcome == "win")
        wr = wins / len(trades) if trades else None
        return avg, wr

    atr_col_w = 8
    name_col_w = 22
    tf_col_w = 6
    header = f"  {'Strategy':<{name_col_w}}{'TF':<{tf_col_w}}" + "".join(
        f"{v:.1f}×".rjust(atr_col_w) for v in atr_values
    )
    total_w = name_col_w + tf_col_w + atr_col_w * len(atr_values) + 2
    sep = "  " + "─" * total_w
    thick = "═" * (total_w + 2)

    lines = [
        "\nATR SL Multiplier Comparison (aggregated across symbols)",
        thick,
        header,
        sep,
    ]

    prev_strat = ""
    for strat, tf in sorted_keys:
        label = strat if strat != prev_strat else ""
        prev_strat = strat
        row_r = f"  {label:<{name_col_w}}{tf:<{tf_col_w}}"
        row_w = f"  {'':>{name_col_w}}{'':>{tf_col_w}}"
        for atr in atr_values:
            avg, wr = _collect(results_by_atr[atr], strat, tf)
            cell_r = f"{avg:+.2f}R" if avg is not None else "  n/a"
            cell_w = f"{wr:.0%}" if wr is not None else ""
            row_r += f"{cell_r:>{atr_col_w}}"
            row_w += f"{cell_w:>{atr_col_w}}"
        lines.append(row_r)
        lines.append(row_w)

    lines.append(sep)
    lines.append(
        "  Pick the multiplier column where avg R peaks per strategy × TF row."
    )
    return "\n".join(lines)


def format_seasonality(stats: pd.DataFrame) -> str:
    """Format a seasonality stats DataFrame as a human-readable text table."""
    if stats.empty:
        return "No seasonality data available."

    DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    header = f"  {'Value':<10} {'Avg Return':>12} {'Win Rate':>10} {'Count':>8}"
    sep = "  " + "─" * 44

    lines: list[str] = ["Seasonality Analysis", "═" * 52]

    for period_type in ["day_of_week", "hour_of_day", "week_of_month"]:
        subset = stats[stats["period_type"] == period_type].sort_values("period_value")
        if subset.empty:
            continue

        title = period_type.replace("_", " ").title()
        lines.append(f"\n{title}:")
        lines.append(header)
        lines.append(sep)

        for row in subset.to_dict("records"):
            val = int(row["period_value"])
            if period_type == "day_of_week":
                label = DAY_NAMES[val] if val < 7 else str(val)
            else:
                label = str(val)
            avg_ret = float(row["avg_return_pct"])
            win_rate = float(row["win_rate"])
            count = int(row["count"])
            lines.append(
                f"  {label:<10} {avg_ret:>+11.3f}% {win_rate:>9.1%} {count:>8}"
            )

    return "\n".join(lines)


def format_sweep_table(
    results: list[BacktestResult],
    min_trades: int = 20,
    min_trades_per_tf: dict[str, int] | None = None,
) -> str:
    """Format a ranked backtest sweep table as a string.

    Rows with fewer than the effective min_trades for their TF are excluded and counted
    in the footer. Per-TF thresholds in min_trades_per_tf take precedence over the
    global min_trades fallback. Results are sorted by avg_r descending.
    """

    def _eff_min(tf: str) -> int:
        return (
            min_trades_per_tf.get(tf, min_trades) if min_trades_per_tf else min_trades
        )

    qualifying = [r for r in results if len(r.closed_trades) >= _eff_min(r.timeframe)]
    hidden = len(results) - len(qualifying)

    qualifying.sort(key=lambda r: r.avg_r, reverse=True)

    col_w = (14, 6, 18, 8, 8, 8)
    header = (
        f"{'Symbol':<{col_w[0]}}"
        f"{'TF':<{col_w[1]}}"
        f"{'Strategy':<{col_w[2]}}"
        f"{'Win%':>{col_w[3]}}"
        f"{'Trades':>{col_w[4]}}"
        f"{'Avg R':>{col_w[5]}}"
    )
    sep = "─" * sum(col_w)
    thick_sep = "═" * sum(col_w)

    lines = [thick_sep, header, sep]

    if not qualifying:
        lines.append("  No results meet the minimum trade threshold.")
    else:
        for r in qualifying:
            win_pct = f"{r.win_rate * 100:.1f}%"
            avg_r = f"{r.avg_r:+.2f}R"
            lines.append(
                f"{r.symbol:<{col_w[0]}}"
                f"{r.timeframe:<{col_w[1]}}"
                f"{r.strategy:<{col_w[2]}}"
                f"{win_pct:>{col_w[3]}}"
                f"{len(r.closed_trades):>{col_w[4]}}"
                f"{avg_r:>{col_w[5]}}"
            )

    lines.append(sep)
    if hidden > 0:
        lines.append(f"  Hidden: {hidden} combo(s) with < {min_trades} trades")

    return "\n".join(lines)
