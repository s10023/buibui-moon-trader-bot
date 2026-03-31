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

    @functools.cached_property
    def low_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if t.low_volume]

    @functools.cached_property
    def normal_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if not t.low_volume]

    @property
    def low_vol_avg_r(self) -> float | None:
        r_vals = [t.pnl_r for t in self.low_vol_closed_trades if t.pnl_r is not None]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def normal_vol_avg_r(self) -> float | None:
        r_vals = [t.pnl_r for t in self.normal_vol_closed_trades if t.pnl_r is not None]
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
) -> BacktestResult:
    """Simulate trades from signals on historical OHLCV.

    Entry:  next candle's open after the signal candle.
    SL:     per-signal sl_price from the signals DataFrame when present (structural);
            otherwise atr_sl_multiplier × ATR14 when set (volatility-adaptive);
            otherwise sl_pct fraction of entry price (fixed fallback).
            min_sl_pct enforces a minimum SL distance from entry (e.g. 0.005 = 0.5%),
            widening SLs that land too close to entry.
    TP:     tp_r × risk distance from entry price.
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

        entry_time = int(ohlcv_times_np[entry_idx])
        entry_price = opens_np[entry_idx]

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
                tp_price = entry_price + tp_r * abs(entry_price - sl_price)
            else:
                tp_price = entry_price - tp_r * abs(entry_price - sl_price)
        elif atr_sl_multiplier is not None:
            atr = _compute_atr14(highs_np, lows_np, closes_np, sig_idx)
            if atr is not None:
                sl_dist = atr_sl_multiplier * atr
                if min_sl_pct > 0.0:
                    sl_dist = max(sl_dist, entry_price * min_sl_pct)
                if direction == "long":
                    sl_price = entry_price - sl_dist
                    tp_price = entry_price + tp_r * sl_dist
                else:
                    sl_price = entry_price + sl_dist
                    tp_price = entry_price - tp_r * sl_dist
            else:
                # Fallback to sl_pct when ATR unavailable (e.g. signal at candle 0)
                if direction == "long":
                    sl_price = entry_price * (1.0 - sl_pct)
                    tp_price = entry_price + tp_r * (entry_price - sl_price)
                else:
                    sl_price = entry_price * (1.0 + sl_pct)
                    tp_price = entry_price - tp_r * (sl_price - entry_price)
        elif direction == "long":
            sl_price = entry_price * (1.0 - sl_pct)
            tp_price = entry_price + tp_r * (entry_price - sl_price)
        else:
            sl_price = entry_price * (1.0 + sl_pct)
            tp_price = entry_price - tp_r * (sl_price - entry_price)

        trade = Trade(
            signal_time=signal_time,
            entry_time=entry_time,
            entry_price=entry_price,
            direction=direction,
            sl_price=sl_price,
            tp_price=tp_price,
            fee_pct=fee_pct,
            low_volume=_is_low_volume(ohlcv, sig_idx),
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
    """Show avg R split by low-volume vs normal-volume trades, aggregated by strategy.

    Delta = normal_avg_r − low_vol_avg_r.
    Positive delta means normal-volume trades outperform → volume filter would help.
    """
    from collections import defaultdict

    low_by_strat: dict[str, list[Trade]] = defaultdict(list)
    norm_by_strat: dict[str, list[Trade]] = defaultdict(list)
    for r in results:
        low_by_strat[r.strategy].extend(r.low_vol_closed_trades)
        norm_by_strat[r.strategy].extend(r.normal_vol_closed_trades)

    strategies = sorted(set(low_by_strat) | set(norm_by_strat))

    def _avg(trades: list[Trade]) -> float | None:
        vals = [t.pnl_r for t in trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else None

    col = (22, 8, 7, 8, 7, 7)
    header = (
        f"  {'Strategy':<{col[0]}}"
        f"{'Low-vol':>{col[1]}}"
        f"{'Avg R':>{col[2]}}"
        f"  {'Normal':>{col[3]}}"
        f"{'Avg R':>{col[4]}}"
        f"  {'Delta':>{col[5]}}"
    )
    sep = "  " + "─" * (sum(col) + 4)
    thick = "═" * (sum(col) + 6)

    lines = [
        "\nVolume Impact (aggregated across all symbols × TFs)",
        thick,
        header,
        sep,
    ]

    for s in strategies:
        low = low_by_strat[s]
        norm = norm_by_strat[s]
        low_r = _avg(low)
        norm_r = _avg(norm)
        delta = (norm_r - low_r) if (low_r is not None and norm_r is not None) else None
        low_r_s = f"{low_r:+.2f}R" if low_r is not None else "  n/a"
        norm_r_s = f"{norm_r:+.2f}R" if norm_r is not None else "  n/a"
        delta_s = f"{delta:+.2f}R" if delta is not None else "  n/a"
        lines.append(
            f"  {s:<{col[0]}}"
            f"{len(low):>{col[1]}}"
            f"{low_r_s:>{col[2]}}"
            f"  {len(norm):>{col[3]}}"
            f"{norm_r_s:>{col[4]}}"
            f"  {delta_s:>{col[5]}}"
        )

    lines.append(sep)
    lines.append(
        "  Delta = normal_avg_r − low_vol_avg_r  "
        "(positive = volume filter would help, negative = hurts)"
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

        for _, row in subset.iterrows():
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
