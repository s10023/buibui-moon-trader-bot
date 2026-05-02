"""Cross-timeframe co-firing backtest: HTF context + LTF entry."""

from dataclasses import dataclass

import pandas as pd

from analytics.backtest.engine import BacktestResult, run_backtest


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
    from analytics.strategies import SIGNAL_COLUMNS

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
        for htf_t, htf_d in zip(htf_times, htf_dirs, strict=False):
            if htf_d != ltf_dir:
                continue
            if window_start <= htf_t <= ltf_time and (
                best_htf_time is None or htf_t > best_htf_time
            ):
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
