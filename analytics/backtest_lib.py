"""Pure backtest simulation engine.

Accepts OHLCV DataFrame and signals DataFrame, simulates trades, and returns
a BacktestResult with statistics.

No database access, no network calls. No module-level side effects.
"""

from dataclasses import dataclass, field

import pandas as pd


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

    @property
    def pnl_r(self) -> float | None:
        """P&L expressed in R multiples (1R = amount risked)."""
        if self.exit_price is None:
            return None
        risk = abs(self.entry_price - self.sl_price)
        if risk == 0.0:
            return None
        if self.direction == "long":
            return (self.exit_price - self.entry_price) / risk
        return (self.entry_price - self.exit_price) / risk


@dataclass
class BacktestResult:
    """Aggregated results for a strategy backtest."""

    symbol: str
    timeframe: str
    strategy: str
    trades: list[Trade] = field(default_factory=list)

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.outcome != "open"]

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
    def avg_r(self) -> float:
        r_values = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(r_values) / len(r_values) if r_values else 0.0

    @property
    def total_r(self) -> float:
        r_values = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(r_values)

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
) -> BacktestResult:
    """Simulate trades from signals on historical OHLCV.

    Entry:  next candle's open after the signal candle.
    SL:     sl_pct from entry price.
    TP:     tp_r × risk distance from entry price.

    A trade closes when a candle's high or low touches the SL or TP level.
    Trades still open at end of data are marked as outcome="open".
    """
    result = BacktestResult(symbol=symbol, timeframe=timeframe, strategy=strategy)

    if signals.empty or ohlcv.empty:
        return result

    ohlcv_times: list[int] = list(ohlcv["open_time"].astype("int64"))
    time_to_idx = {t: i for i, t in enumerate(ohlcv_times)}

    for _, sig in signals.iterrows():
        signal_time = int(sig["open_time"])
        direction = str(sig["direction"])

        if signal_time not in time_to_idx:
            continue
        sig_idx = time_to_idx[signal_time]
        entry_idx = sig_idx + 1

        if entry_idx >= len(ohlcv_times):
            continue

        entry_time = ohlcv_times[entry_idx]
        entry_price = float(ohlcv.iloc[entry_idx]["open"])

        if direction == "long":
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
        )

        for i in range(entry_idx, len(ohlcv_times)):
            candle = ohlcv.iloc[i]
            candle_high = float(candle["high"])
            candle_low = float(candle["low"])
            candle_time = int(candle["open_time"])

            if direction == "long":
                if candle_low <= sl_price:
                    trade.exit_time = candle_time
                    trade.exit_price = sl_price
                    trade.outcome = "loss"
                    break
                if candle_high >= tp_price:
                    trade.exit_time = candle_time
                    trade.exit_price = tp_price
                    trade.outcome = "win"
                    break
            else:
                if candle_high >= sl_price:
                    trade.exit_time = candle_time
                    trade.exit_price = sl_price
                    trade.outcome = "loss"
                    break
                if candle_low <= tp_price:
                    trade.exit_time = candle_time
                    trade.exit_price = tp_price
                    trade.outcome = "win"
                    break

        result.trades.append(trade)

    return result


def format_result(result: BacktestResult) -> str:
    """Format a BacktestResult as a human-readable text summary."""
    lines = [
        f"Backtest: {result.symbol} {result.timeframe} — {result.strategy}",
        "─" * 52,
        f"Signals:     {len(result.trades)} total, {len(result.closed_trades)} closed",
        f"Win rate:    {result.win_rate:.1%}  ({result.win_count}W / {result.loss_count}L)",
        f"Avg R:       {result.avg_r:+.2f}R",
        f"Total R:     {result.total_r:+.2f}R",
        f"Max DD:      -{result.max_drawdown_r:.2f}R",
    ]
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
