"""Detector: SMT Divergence — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import numpy as np
import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df


def detect_smt_divergence(
    df_primary: pd.DataFrame,
    df_secondary: pd.DataFrame,
    lookback: int = 50,
    trend_filter: int = 1,
    swing_n: int = 5,
) -> pd.DataFrame:
    """Detect Smart Money Technique (SMT) divergence between two correlated assets.

    Bearish SMT: primary makes a confirmed new swing high but secondary does NOT →
    the primary's new high is a likely stop hunt → short signal.

    Bullish SMT: primary makes a confirmed new swing low but secondary does NOT →
    the primary's new low is a likely stop hunt → long signal.

    Swing highs/lows are identified using a centred window of 2×swing_n+1 candles
    (default swing_n=5 → 11-candle window). A candle is a swing high if its high
    equals the rolling max of the centred window. This is the same approach used by
    detect_eqh_eql() to find structurally significant pivot levels.

    A swing is "confirmed" only when swing_n candles have formed to its right.
    The signal fires at the first candle after confirmation (i >= pivot + swing_n).

    Signals are tagged on the primary asset's open_time.
    Both DataFrames must share open_time values (inner join used).

    When trend_filter=1 (default), signals are only taken with the trend:
    - LONG signals require close > EMA(50) on the primary asset.
    - SHORT signals require close < EMA(50) on the primary asset.
    """
    if df_primary.empty or df_secondary.empty:
        return _empty_signals()

    primary = df_primary.set_index("open_time")[["high", "low", "close"]].copy()
    primary.columns = pd.Index(["high_p", "low_p", "close_p"])
    secondary = df_secondary.set_index("open_time")[["high", "low"]].copy()
    secondary.columns = pd.Index(["high_s", "low_s"])

    merged = primary.join(secondary, how="inner")
    min_len = lookback + swing_n + 1
    if len(merged) < min_len:
        return _empty_signals()

    merged = merged.reset_index()
    n = len(merged)

    win = 2 * swing_n + 1

    # Precompute swing pivot indices using centred rolling window (same as detect_eqh_eql).
    highs_p = merged["high_p"].to_numpy(dtype=float)
    lows_p = merged["low_p"].to_numpy(dtype=float)
    highs_s = merged["high_s"].to_numpy(dtype=float)
    lows_s = merged["low_s"].to_numpy(dtype=float)
    open_times = merged["open_time"].to_numpy(dtype=int)
    closes_p = merged["close_p"].to_numpy(dtype=float)

    roll_max_p = (
        pd.Series(highs_p).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min_p = (
        pd.Series(lows_p).rolling(win, center=True, min_periods=1).min().to_numpy()
    )
    roll_max_s = (
        pd.Series(highs_s).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min_s = (
        pd.Series(lows_s).rolling(win, center=True, min_periods=1).min().to_numpy()
    )

    # Swing pivot candle indices — confirmed at candle k when k + swing_n has passed.
    sh_p_idx: np.ndarray = np.where(highs_p >= roll_max_p)[0]
    sl_p_idx: np.ndarray = np.where(lows_p <= roll_min_p)[0]
    sh_s_idx: np.ndarray = np.where(highs_s >= roll_max_s)[0]
    sl_s_idx: np.ndarray = np.where(lows_s <= roll_min_s)[0]

    ema50: np.ndarray | None = None
    if trend_filter:
        ema50 = merged["close_p"].ewm(span=50, adjust=False).mean().to_numpy()

    signals: list[dict[str, object]] = []

    for i in range(lookback, n):
        close_p = float(closes_p[i])
        ema_val = float(ema50[i]) if ema50 is not None else 0.0

        # Window start (inclusive) for confirmed pivots: confirmed means pivot_k + swing_n <= i
        # i.e. pivot_k <= i - swing_n.  Window also bounded by lookback from signal candle.
        win_start = i - lookback
        win_end_confirmed = i - swing_n  # pivot must be <= this to be confirmed

        if win_end_confirmed < win_start:
            continue

        # ---- Bearish SMT ------------------------------------------------
        # Find confirmed swing highs on primary within [win_start, win_end_confirmed].
        lo_p = int(np.searchsorted(sh_p_idx, win_start))
        hi_p = int(np.searchsorted(sh_p_idx, win_end_confirmed + 1))
        sh_p_window = sh_p_idx[lo_p:hi_p]

        if len(sh_p_window) >= 2:
            # Most recent swing high on primary
            latest_p_sh_idx = int(sh_p_window[-1])
            latest_p_sh_val = float(highs_p[latest_p_sh_idx])
            # Prior swing high on primary (any earlier one)
            prior_p_sh_val = float(highs_p[sh_p_window[:-1]].max())

            if latest_p_sh_val > prior_p_sh_val:
                # Primary made a new structural swing high.
                # Check secondary: find confirmed swing highs on secondary in same window.
                lo_s = int(np.searchsorted(sh_s_idx, win_start))
                hi_s = int(np.searchsorted(sh_s_idx, win_end_confirmed + 1))
                sh_s_window = sh_s_idx[lo_s:hi_s]

                secondary_also_new_high = False
                if len(sh_s_window) >= 2:
                    latest_s_sh_val = float(highs_s[sh_s_window[-1]])
                    prior_s_sh_val = float(highs_s[sh_s_window[:-1]].max())
                    secondary_also_new_high = latest_s_sh_val > prior_s_sh_val

                if (
                    i == latest_p_sh_idx + swing_n
                    and not secondary_also_new_high
                    and (not trend_filter or close_p < ema_val)
                ):
                    signals.append(
                        {
                            "open_time": int(open_times[i]),
                            "direction": "short",
                            "reason": f"smt_bearish@{latest_p_sh_val:.2f}",
                            "sl_price": latest_p_sh_val,
                            "context": "",
                        }
                    )

        # ---- Bullish SMT ------------------------------------------------
        lo_p2 = int(np.searchsorted(sl_p_idx, win_start))
        hi_p2 = int(np.searchsorted(sl_p_idx, win_end_confirmed + 1))
        sl_p_window = sl_p_idx[lo_p2:hi_p2]

        if len(sl_p_window) >= 2:
            latest_p_sl_idx = int(sl_p_window[-1])
            latest_p_sl_val = float(lows_p[latest_p_sl_idx])
            prior_p_sl_val = float(lows_p[sl_p_window[:-1]].min())

            if latest_p_sl_val < prior_p_sl_val:
                # Primary made a new structural swing low.
                lo_s2 = int(np.searchsorted(sl_s_idx, win_start))
                hi_s2 = int(np.searchsorted(sl_s_idx, win_end_confirmed + 1))
                sl_s_window = sl_s_idx[lo_s2:hi_s2]

                secondary_also_new_low = False
                if len(sl_s_window) >= 2:
                    latest_s_sl_val = float(lows_s[sl_s_window[-1]])
                    prior_s_sl_val = float(lows_s[sl_s_window[:-1]].min())
                    secondary_also_new_low = latest_s_sl_val < prior_s_sl_val

                if (
                    i == latest_p_sl_idx + swing_n
                    and not secondary_also_new_low
                    and (not trend_filter or close_p > ema_val)
                ):
                    signals.append(
                        {
                            "open_time": int(open_times[i]),
                            "direction": "long",
                            "reason": f"smt_bullish@{latest_p_sl_val:.2f}",
                            "sl_price": latest_p_sl_val,
                            "context": "",
                        }
                    )

    return _signals_to_df(signals)
