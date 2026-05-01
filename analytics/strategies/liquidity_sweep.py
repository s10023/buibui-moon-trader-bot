"""Detector: Liquidity Sweep — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import numpy as np
import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df


def detect_liquidity_sweep(
    df: pd.DataFrame,
    lookback: int = 50,
    swing_n: int = 5,
    use_fib_extension: bool = True,
    require_close_rejection: bool = True,
    fib_require_range_close: bool = False,
) -> pd.DataFrame:
    """Detect liquidity sweep fakeout reversal signals.

    Two entry modes controlled by use_fib_extension:

    use_fib_extension=True (default — fib-extension mode):
        Price breaks above a genuine pivot swing high (fakeout), extends to the
        1.13 or 1.27 Fibonacci extension of the prior swing range, then closes
        back below that level — that is the reversal entry.

        Fib levels (measured from swing_high, outward from range):
            fib_1.13 = swing_high + 0.13 × (swing_high − swing_low)
            fib_1.27 = swing_high + 0.27 × (swing_high − swing_low)
        1.27 is checked first; 1.13 is the fallback.

    use_fib_extension=False (pivot-sweep mode):
        Entry fires when price wicks above the pivot swing high and closes back
        below it — no fib extension required. Useful as a baseline to compare
        against fib-mode via backtest.

    require_close_rejection (default True, applies to both modes):
        If True, candle must CLOSE below the trigger level (fib level in fib
        mode; swing high in pivot mode) — confirming a rejection candle.
        If False, a wick touch alone suffices. This choice is a named param so
        it can be toggled without touching the detection logic.

    fib_require_range_close (default False, applies to fib mode only):
        If True, the close must come back BELOW the original swing_high (fully
        inside the prior range), not just below the fib extension level. This
        is a stricter confirmation — the candle wicks into the fib zone but the
        body closes back inside the range. Ignored when use_fib_extension=False.

    Pivot detection: a candle is a swing high/low if its high/low is the
    extreme of the [k−swing_n, k+swing_n] centred window (default swing_n=5,
    i.e. 11-candle window). Anchors signals to structurally significant levels
    rather than arbitrary rolling extremes. Both modes use proper pivots.

    sl_price = the candle's wick high (for shorts) / wick low (for longs).
    """
    n = len(df)
    win = 2 * swing_n + 1
    if n < win + lookback:
        return _empty_signals()

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    # Precompute pivot highs/lows with a centred window (uses swing_n candles
    # on each side to confirm the pivot — acceptable lookahead for structural
    # levels; consistent with detect_eqh_eql).
    roll_max = (
        pd.Series(highs).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min = pd.Series(lows).rolling(win, center=True, min_periods=1).min().to_numpy()
    sh_idx: np.ndarray = np.where(highs >= roll_max)[0]
    sl_idx: np.ndarray = np.where(lows <= roll_min)[0]

    signals: list[dict[str, object]] = []

    for sig_i in range(lookback, n):
        ws = sig_i - lookback
        sig_h = highs[sig_i]
        sig_l = lows[sig_i]
        sig_c = closes[sig_i]
        sig_t = open_times[sig_i]

        # --- Short: fakeout above pivot swing high ---
        hi_sh = int(np.searchsorted(sh_idx, sig_i))
        lo_sh = int(np.searchsorted(sh_idx, ws))
        if hi_sh > lo_sh:
            pivot_sh_i = int(sh_idx[hi_sh - 1])
            swing_high = highs[pivot_sh_i]

            # Anchor: most recent pivot swing low before the swing high
            hi_sl = int(np.searchsorted(sl_idx, pivot_sh_i))
            lo_sl = int(np.searchsorted(sl_idx, ws))
            if hi_sl > lo_sl:
                swing_low = lows[int(sl_idx[hi_sl - 1])]
                rng = swing_high - swing_low

                if rng > 0:
                    fired = False
                    reason_s = ""
                    context_s = ""

                    if use_fib_extension:
                        fib_127 = swing_high + 0.27 * rng
                        fib_113 = swing_high + 0.13 * rng
                        fib_hit: float | None = None
                        fib_label: str | None = None
                        # close threshold: range boundary (stricter) or fib level
                        close_127 = swing_high if fib_require_range_close else fib_127
                        close_113 = swing_high if fib_require_range_close else fib_113
                        if sig_h >= fib_127 and (
                            not require_close_rejection or sig_c < close_127
                        ):
                            fib_hit, fib_label = fib_127, "1.27"
                        elif sig_h >= fib_113 and (
                            not require_close_rejection or sig_c < close_113
                        ):
                            fib_hit, fib_label = fib_113, "1.13"
                        if fib_hit is not None and fib_label is not None:
                            fired = True
                            reason_s = (
                                f"sweep_high@{swing_high:.2f}"
                                f"_fib{fib_label}@{fib_hit:.2f}"
                            )
                            context_s = (
                                f"range [{swing_low:.2f}–{swing_high:.2f}]"
                                f" · fib{fib_label}={fib_hit:.2f}"
                            )
                    else:
                        # Pivot-sweep mode: wick above swing_high, close inside
                        if sig_h > swing_high and (
                            not require_close_rejection or sig_c < swing_high
                        ):
                            fired = True
                            reason_s = f"sweep_high@{swing_high:.2f}"
                            context_s = f"range [{swing_low:.2f}–{swing_high:.2f}]"

                    if fired:
                        signals.append(
                            {
                                "open_time": sig_t,
                                "direction": "short",
                                "reason": reason_s,
                                "sl_price": sig_h,
                                "context": context_s,
                            }
                        )

        # --- Long: fakeout below pivot swing low ---
        hi_sl2 = int(np.searchsorted(sl_idx, sig_i))
        lo_sl2 = int(np.searchsorted(sl_idx, ws))
        if hi_sl2 > lo_sl2:
            pivot_sl_i = int(sl_idx[hi_sl2 - 1])
            swing_low2 = lows[pivot_sl_i]

            # Anchor: most recent pivot swing high before the swing low
            hi_sh2 = int(np.searchsorted(sh_idx, pivot_sl_i))
            lo_sh2 = int(np.searchsorted(sh_idx, ws))
            if hi_sh2 > lo_sh2:
                swing_high2 = highs[int(sh_idx[hi_sh2 - 1])]
                rng2 = swing_high2 - swing_low2

                if rng2 > 0:
                    fired_l = False
                    reason_l = ""
                    context_l = ""

                    if use_fib_extension:
                        fib_127_l = swing_low2 - 0.27 * rng2
                        fib_113_l = swing_low2 - 0.13 * rng2
                        fib_hit_l: float | None = None
                        fib_label_l: str | None = None
                        close_127_l = (
                            swing_low2 if fib_require_range_close else fib_127_l
                        )
                        close_113_l = (
                            swing_low2 if fib_require_range_close else fib_113_l
                        )
                        if sig_l <= fib_127_l and (
                            not require_close_rejection or sig_c > close_127_l
                        ):
                            fib_hit_l, fib_label_l = fib_127_l, "1.27"
                        elif sig_l <= fib_113_l and (
                            not require_close_rejection or sig_c > close_113_l
                        ):
                            fib_hit_l, fib_label_l = fib_113_l, "1.13"
                        if fib_hit_l is not None and fib_label_l is not None:
                            fired_l = True
                            reason_l = (
                                f"sweep_low@{swing_low2:.2f}"
                                f"_fib{fib_label_l}@{fib_hit_l:.2f}"
                            )
                            context_l = (
                                f"range [{swing_low2:.2f}–{swing_high2:.2f}]"
                                f" · fib{fib_label_l}={fib_hit_l:.2f}"
                            )
                    else:
                        # Pivot-sweep mode: wick below swing_low, close inside
                        if sig_l < swing_low2 and (
                            not require_close_rejection or sig_c > swing_low2
                        ):
                            fired_l = True
                            reason_l = f"sweep_low@{swing_low2:.2f}"
                            context_l = f"range [{swing_low2:.2f}–{swing_high2:.2f}]"

                    if fired_l:
                        signals.append(
                            {
                                "open_time": sig_t,
                                "direction": "long",
                                "reason": reason_l,
                                "sl_price": sig_l,
                                "context": context_l,
                            }
                        )

    return _signals_to_df(signals)
