"""Detector: CVD Divergence — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import numpy as np
import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df


def detect_cvd_divergence(
    df: pd.DataFrame,
    lookback: int = 10,
    cvd_lookback: int = 50,
) -> pd.DataFrame:
    """Detect CVD divergence signals.

    Bearish: price higher swing high + CVD lower swing high → short.
    Bullish: price lower swing low + CVD higher swing low → long.

    CVD = cumsum(taker_buy_volume - taker_sell_volume)
        = cumsum(2 * taker_buy_volume - volume)

    taker_buy_volume NULLs are dropped gracefully.
    SL = structural swing extreme (high for short, low for long).

    Signals are generated across the full history (rolling window): each candle
    from index `cvd_lookback - 1` onward is evaluated. Each divergence pair fires
    exactly once (deduplicated by the 2nd swing peak's timestamp).

    Performance: global CVD and swing arrays are precomputed once (O(n)); the
    main loop uses numpy searchsorted (O(log n)) avoiding per-window DataFrame
    creation. CVD comparisons use global offsets — window-relative and global
    CVD orderings are identical (ch2 < ch1 ↔ CVD_global[i2] < CVD_global[i1]).
    """
    if "taker_buy_volume" not in df.columns or df["taker_buy_volume"].isna().all():
        return _empty_signals()
    df = df.dropna(subset=["taker_buy_volume"]).reset_index(drop=True)
    n = len(df)
    if n < lookback * 2 + 1:
        return _empty_signals()

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)
    tbv = df["taker_buy_volume"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)

    # Global CVD — window-relative orderings are preserved (offset cancels in
    # the ch2 < ch1 comparison, so global values can be used directly).
    cvd_global: np.ndarray = (2.0 * tbv - vol).cumsum()

    # Precompute confirmed swing highs/lows using rolling max/min.
    # A swing at absolute index k is confirmed iff it has `lookback` candles on
    # each side — the searchsorted step below restricts to [ws+lookback, end_i-lookback+1).
    win = 2 * lookback + 1
    roll_max = (
        pd.Series(highs).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min = pd.Series(lows).rolling(win, center=True, min_periods=1).min().to_numpy()
    sh_idx: np.ndarray = np.where(highs >= roll_max)[0]
    sl_idx: np.ndarray = np.where(lows <= roll_min)[0]

    signals: list[dict[str, object]] = []
    seen_pairs: set[tuple[int, str]] = set()

    for end_i in range(cvd_lookback - 1, n):
        ws = max(0, end_i - cvd_lookback + 1)
        sig_time = int(open_times[end_i])

        # Confirmed swing region: [ws+lookback, end_i-lookback+1)
        # This matches range(lookback, wn-lookback) in window coordinates.
        c_start = ws + lookback
        c_end = end_i - lookback + 1

        lo = int(np.searchsorted(sh_idx, c_start))
        hi = int(np.searchsorted(sh_idx, c_end))
        wsh = sh_idx[lo:hi]

        lo = int(np.searchsorted(sl_idx, c_start))
        hi = int(np.searchsorted(sl_idx, c_end))
        wsl = sl_idx[lo:hi]

        # Dedup consecutive plateaus (keep first of each run)
        def _dedup(arr: np.ndarray) -> list[int]:
            out: list[int] = []
            prev = -2
            for idx in arr:
                if idx > prev + 1:
                    out.append(int(idx))
                prev = idx
            return out

        sh_peaks = _dedup(wsh)
        sl_peaks = _dedup(wsl)

        if len(sh_peaks) >= 2:
            i1, i2 = sh_peaks[-2], sh_peaks[-1]
            peak2_time = int(open_times[i2])
            pair_key: tuple[int, str] = (peak2_time, "short")
            if pair_key not in seen_pairs:
                ph1, ph2 = highs[i1], highs[i2]
                ch1, ch2 = cvd_global[i1], cvd_global[i2]
                if ph2 > ph1 and ch2 < ch1:
                    seen_pairs.add(pair_key)
                    signals.append(
                        {
                            "open_time": sig_time,
                            "direction": "short",
                            "reason": f"cvd_div_bear@{ph2:.2f}",
                            "sl_price": ph2,
                            "context": (
                                f"CVD div: price H {ph1:.2f}→{ph2:.2f}, "
                                f"CVD {ch1:.0f}→{ch2:.0f} at {_fmt_time(sig_time)}"
                            ),
                        }
                    )

        if len(sl_peaks) >= 2:
            i1, i2 = sl_peaks[-2], sl_peaks[-1]
            peak2_time = int(open_times[i2])
            pair_key = (peak2_time, "long")
            if pair_key not in seen_pairs:
                pl1, pl2 = lows[i1], lows[i2]
                cl1, cl2 = cvd_global[i1], cvd_global[i2]
                if pl2 < pl1 and cl2 > cl1:
                    seen_pairs.add(pair_key)
                    signals.append(
                        {
                            "open_time": sig_time,
                            "direction": "long",
                            "reason": f"cvd_div_bull@{pl2:.2f}",
                            "sl_price": pl2,
                            "context": (
                                f"CVD div: price L {pl1:.2f}→{pl2:.2f}, "
                                f"CVD {cl1:.0f}→{cl2:.0f} at {_fmt_time(sig_time)}"
                            ),
                        }
                    )

    return _signals_to_df(signals)
