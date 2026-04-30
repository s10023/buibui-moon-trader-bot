"""Same-TF co-firing confluence backtest."""

from dataclasses import dataclass

import pandas as pd

from analytics.backtest.engine import BacktestResult, run_backtest


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
        for j, (_t_b, dir_b, idx_b) in enumerate(
            zip(b_times, b_dirs, b_indices, strict=False)
        ):
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
