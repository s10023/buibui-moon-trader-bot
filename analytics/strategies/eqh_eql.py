"""Detector: Equal Highs / Equal Lows (EQH / EQL) — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import numpy as np
import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df


def detect_eqh_eql(
    df: pd.DataFrame,
    lookback: int = 50,
    tolerance_pct: float = 0.003,
    swing_n: int = 5,
) -> pd.DataFrame:
    """Detect Equal Highs / Equal Lows liquidity sweep signals.

    Equal Highs (EQH): two swing highs within tolerance_pct of each other form a
    liquidity pool. When a candle wicks above that level (high > EQH) but closes
    below it, a liquidity raid has occurred → short signal.

    Equal Lows (EQL): two swing lows within tolerance_pct form a pool below price.
    When a candle wicks below that level (low < EQL) but closes above it → long signal.

    Swing highs/lows are identified using a centred window of 2×swing_n+1 candles
    (default swing_n=5 → 11-candle window, i.e. 5 candles each side). A candle is a
    swing high if its high is the max of that window. Wider swing_n = fewer, more
    structurally significant pivot levels.

    Signals are generated across the full history (rolling window): each candle
    from index `lookback` onward is evaluated as a potential signal candle.

    Performance: swing highs/lows are precomputed globally using pandas rolling
    max/min (O(n)); per-candle work uses numpy searchsorted (O(log n)) to find
    swings in the window, avoiding per-iteration DataFrame creation.
    """
    n = len(df)
    if n < lookback + 1:
        return _empty_signals()

    swing_side = swing_n  # candles on each side of the pivot candidate
    win = 2 * swing_side + 1

    # Precompute arrays — no pandas operations inside the main loop.
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    # Rolling max/min with center=True + min_periods=1 matches the original
    # truncated-neighbourhood behaviour at window boundaries (confirmed below):
    # for any candle k with lookback candles on both sides, the centred rolling
    # window is fully within [k-swing_side, k+swing_side+1], identical to the
    # per-slice neighbourhood used in the original implementation.
    roll_max = (
        pd.Series(highs).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min = pd.Series(lows).rolling(win, center=True, min_periods=1).min().to_numpy()
    sh_idx: np.ndarray = np.where(highs >= roll_max)[0]
    sl_idx: np.ndarray = np.where(lows <= roll_min)[0]
    sh_prices = highs[sh_idx]
    sl_prices = lows[sl_idx]

    signals: list[dict[str, object]] = []

    for sig_i in range(lookback, n):
        ws = sig_i - lookback
        sig_h = highs[sig_i]
        sig_l = lows[sig_i]
        sig_c = closes[sig_i]
        sig_t = open_times[sig_i]

        # --- EQH: swing highs in [ws, sig_i) via binary search ---
        lo = int(np.searchsorted(sh_idx, ws))
        hi = int(np.searchsorted(sh_idx, sig_i))
        sw_h_idx = sh_idx[lo:hi]
        sw_h_pri = sh_prices[lo:hi]

        if len(sw_h_pri) >= 2:
            best_eqh: tuple[int, int, float, float] | None = None
            for a in range(len(sw_h_pri)):
                for b in range(a + 1, len(sw_h_pri)):
                    h1, h2 = sw_h_pri[a], sw_h_pri[b]
                    level = max(h1, h2)
                    if abs(h1 - h2) / level <= tolerance_pct:
                        # Reject if price already broke above the EQH level
                        # between the two pivots — the pool was already raided.
                        between = highs[sw_h_idx[a] + 1 : sw_h_idx[b]]
                        if len(between) > 0 and np.any(between > level):
                            continue
                        if sig_h <= level or sig_c >= level:
                            continue
                        if best_eqh is None or level > max(best_eqh[2], best_eqh[3]):
                            best_eqh = (
                                int(sw_h_idx[a]),
                                int(sw_h_idx[b]),
                                h1,
                                h2,
                            )

            if best_eqh is not None:
                ai, bi, h1, h2 = best_eqh
                eqh_level = max(h1, h2)
                later = max(ai, bi)
                post = highs[later : sig_i + 1]
                above = post[post > eqh_level]
                sl_price = float(above.max()) if len(above) > 0 else sig_h
                signals.append(
                    {
                        "open_time": sig_t,
                        "direction": "short",
                        "reason": f"eqh_short@{h1:.2f}-{h2:.2f}",
                        "sl_price": sl_price,
                        "context": (
                            f"EQH: {_fmt_time(open_times[ai])} @ {h1:,.2f}"
                            f" · {_fmt_time(open_times[bi])} @ {h2:,.2f}"
                        ),
                    }
                )

        # --- EQL: swing lows in [ws, sig_i) via binary search ---
        lo = int(np.searchsorted(sl_idx, ws))
        hi = int(np.searchsorted(sl_idx, sig_i))
        sw_l_idx = sl_idx[lo:hi]
        sw_l_pri = sl_prices[lo:hi]

        if len(sw_l_pri) >= 2:
            best_eql: tuple[int, int, float, float] | None = None
            for a in range(len(sw_l_pri)):
                for b in range(a + 1, len(sw_l_pri)):
                    l1, l2 = sw_l_pri[a], sw_l_pri[b]
                    level = min(l1, l2)
                    if level == 0.0:
                        continue
                    if abs(l1 - l2) / level <= tolerance_pct:
                        # Reject if price already broke below the EQL level
                        # between the two pivots — the pool was already raided.
                        between = lows[sw_l_idx[a] + 1 : sw_l_idx[b]]
                        if len(between) > 0 and np.any(between < level):
                            continue
                        if sig_l >= level or sig_c <= level:
                            continue
                        if best_eql is None or level < min(best_eql[2], best_eql[3]):
                            best_eql = (
                                int(sw_l_idx[a]),
                                int(sw_l_idx[b]),
                                l1,
                                l2,
                            )

            if best_eql is not None:
                ai, bi, l1, l2 = best_eql
                eql_level = min(l1, l2)
                later = max(ai, bi)
                post = lows[later : sig_i + 1]
                below = post[post < eql_level]
                sl_price = float(below.min()) if len(below) > 0 else sig_l
                signals.append(
                    {
                        "open_time": sig_t,
                        "direction": "long",
                        "reason": f"eql_long@{l1:.2f}-{l2:.2f}",
                        "sl_price": sl_price,
                        "context": (
                            f"EQL: {_fmt_time(open_times[ai])} @ {l1:,.2f}"
                            f" · {_fmt_time(open_times[bi])} @ {l2:,.2f}"
                        ),
                    }
                )

    return _signals_to_df(signals)
